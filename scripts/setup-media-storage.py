#!/usr/bin/env python3
"""Create SupoClip's private media bucket and a bucket-scoped runtime identity.

Run with backend/.venv/bin/python. Administrative access comes from aws login;
runtime keys are written only to the requested private file, never stdout.
"""

import argparse
import json
import os
from pathlib import Path
import subprocess

import boto3
from botocore.exceptions import ClientError


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--region", default="eu-central-1")
    parser.add_argument("--bucket")
    parser.add_argument("--profile", default="default")
    parser.add_argument("--iam-user", default="supoclip-media-storage")
    parser.add_argument("--origin", action="append", default=[])
    parser.add_argument("--credentials-file", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    exported = subprocess.run(["aws", "configure", "export-credentials", "--profile", args.profile, "--format", "process"],
        check=True, capture_output=True, text=True)
    credentials = json.loads(exported.stdout)
    session = boto3.Session(aws_access_key_id=credentials["AccessKeyId"], aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials.get("SessionToken"), region_name=args.region)
    account = session.client("sts").get_caller_identity()["Account"]
    bucket = args.bucket or f"supoclip-media-{account}"
    origins = args.origin or ["https://supoclip.com", "https://www.supoclip.com", "http://localhost:3107"]
    print(json.dumps({"bucket": bucket, "region": args.region, "prefix": "media", "origins": origins,
        "iam_user": args.iam_user, "incomplete_multipart_expiry_days": 1, "apply": args.apply}))
    if not args.apply:
        return
    if not args.credentials_file:
        parser.error("--credentials-file is required with --apply")
    if args.credentials_file.exists():
        parser.error("Credentials file already exists; refusing to overwrite or create another key")
    s3 = session.client("s3")
    try:
        options = {"Bucket": bucket, "ObjectOwnership": "BucketOwnerEnforced"}
        if args.region != "us-east-1":
            options["CreateBucketConfiguration"] = {"LocationConstraint": args.region}
        s3.create_bucket(**options)
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "BucketAlreadyOwnedByYou":
            raise
    s3.put_public_access_block(Bucket=bucket, PublicAccessBlockConfiguration={
        "BlockPublicAcls": True, "IgnorePublicAcls": True, "BlockPublicPolicy": True, "RestrictPublicBuckets": True})
    s3.put_bucket_encryption(Bucket=bucket, ServerSideEncryptionConfiguration={
        "Rules": [{"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]})
    arn = f"arn:aws:s3:::{bucket}"
    s3.put_bucket_policy(Bucket=bucket, Policy=json.dumps({"Version": "2012-10-17", "Statement": [{
        "Sid": "RequireTLS", "Effect": "Deny", "Principal": "*", "Action": "s3:*",
        "Resource": [arn, arn + "/*"], "Condition": {"Bool": {"aws:SecureTransport": "false"}}}]}))
    s3.put_bucket_cors(Bucket=bucket, CORSConfiguration={"CORSRules": [{
        "AllowedOrigins": origins, "AllowedMethods": ["GET", "HEAD", "PUT"],
        "AllowedHeaders": ["*"], "ExposeHeaders": ["ETag", "Content-Length", "Content-Range"], "MaxAgeSeconds": 3600}]})
    s3.put_bucket_lifecycle_configuration(Bucket=bucket, LifecycleConfiguration={"Rules": [{
        "ID": "AbortAbandonedMultipartUploads", "Status": "Enabled", "Filter": {"Prefix": "media/"},
        "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": 1}}]})
    iam = session.client("iam")
    try:
        iam.create_user(UserName=args.iam_user, Tags=[{"Key": "Application", "Value": "SupoClip"}])
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "EntityAlreadyExists":
            raise
    iam.put_user_policy(UserName=args.iam_user, PolicyName="SupoClipMediaOnly", PolicyDocument=json.dumps({
        "Version": "2012-10-17", "Statement": [
            {"Effect": "Allow", "Action": "s3:GetBucketLocation", "Resource": arn},
            {"Effect": "Allow", "Action": "s3:ListBucket", "Resource": arn,
             "Condition": {"StringLike": {"s3:prefix": ["media", "media/*"]}}},
            {"Effect": "Allow", "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:AbortMultipartUpload", "s3:ListMultipartUploadParts"],
             "Resource": arn + "/media/*"},
        ]}))
    if iam.list_access_keys(UserName=args.iam_user)["AccessKeyMetadata"]:
        raise RuntimeError("Runtime identity already has an access key; retrieve it from its existing secret store")
    args.credentials_file.parent.mkdir(parents=True, exist_ok=True)
    # Create the destination before generating a key to avoid losing the secret.
    fd = os.open(args.credentials_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as output:
        key = iam.create_access_key(UserName=args.iam_user)["AccessKey"]
        output.write(f"MEDIA_STORAGE=s3\nMEDIA_S3_BUCKET={bucket}\nMEDIA_S3_REGION={args.region}\nMEDIA_S3_PREFIX=media\n")
        output.write(f"MEDIA_S3_ACCESS_KEY_ID={key['AccessKeyId']}\nMEDIA_S3_SECRET_ACCESS_KEY={key['SecretAccessKey']}\n")
    print(f"Private runtime configuration written to {args.credentials_file}")


if __name__ == "__main__":
    main()
