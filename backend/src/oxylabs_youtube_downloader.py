"""Oxylabs Push-Pull downloads, delivered through a private S3 bucket."""
from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.parse import quote, urlsplit

import boto3
import requests
from botocore.config import Config as S3Config

from .config import get_config

logger = logging.getLogger(__name__)
API_URL = "https://data.oxylabs.io/v1/queries"


class OxylabsDownloadError(RuntimeError):
    """A safe-to-display download failure; never contains provider payloads."""


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise OxylabsDownloadError("YouTube download timed out. Please try again later.")
    return remaining


def _storage(config):
    bucket = config.oxylabs_storage_bucket
    prefix = config.oxylabs_storage_prefix.strip("/")
    if not bucket or not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", bucket):
        raise OxylabsDownloadError("YouTube download storage is not configured.")
    if any(p in {".", ".."} for p in prefix.split("/")):
        raise OxylabsDownloadError("YouTube download storage prefix is invalid.")
    path = f"{bucket}/{prefix}/" if prefix else f"{bucket}/"
    endpoint = config.oxylabs_storage_endpoint
    if endpoint:
        parsed = urlsplit(endpoint)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username
                or parsed.password or parsed.path not in {"", "/"}
                or parsed.query or parsed.fragment):
            raise OxylabsDownloadError("YouTube download storage requires a secure S3 endpoint.")
        if not config.oxylabs_storage_write_key or not config.oxylabs_storage_write_secret:
            raise OxylabsDownloadError("YouTube download storage writer is not configured.")
        key = quote(config.oxylabs_storage_write_key, safe="")
        secret = quote(config.oxylabs_storage_write_secret, safe="")
        storage_url = f"https://{key}:{secret}@{parsed.netloc}/{quote(path, safe='/')}"
        storage_type = "s3_compatible"
    else:
        storage_url, storage_type = path, "s3"
    client = boto3.client(
        "s3", endpoint_url=endpoint,
        region_name=config.oxylabs_storage_region,
        aws_access_key_id=config.oxylabs_storage_read_key,
        aws_secret_access_key=config.oxylabs_storage_read_secret,
        config=S3Config(connect_timeout=10, read_timeout=30,
                        retries={"max_attempts": 2}, s3={"addressing_style": "path"}),
    )
    return client, storage_type, storage_url, bucket, prefix


def _api(session, method, url, deadline, **kwargs):
    # Never surface response text: it can echo the credential-bearing storage URL.
    try:
        response = session.request(method, url, timeout=min(30, _remaining(deadline)),
                                   allow_redirects=False, **kwargs)
        if response.status_code not in {200, 201, 202}:
            raise OxylabsDownloadError(
                f"YouTube download provider rejected the request (HTTP {response.status_code})."
            )
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("Invalid response")
        return payload
    except (requests.RequestException, ValueError):
        raise OxylabsDownloadError("YouTube download provider could not be reached.") from None


def _fetch(client, bucket, key, target, deadline, max_bytes):
    part = target.with_suffix(target.suffix + ".part")
    try:
        response = client.get_object(Bucket=bucket, Key=key)
        with response["Body"] as body:
            expected = response.get("ContentLength", 0)
            if expected <= 0 or expected > max_bytes:
                raise OxylabsDownloadError("YouTube download file is empty or exceeds the size limit.")
            total = 0
            with part.open("wb") as output:
                while True:
                    _remaining(deadline)
                    chunk = body.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > max_bytes:
                        raise OxylabsDownloadError("YouTube download exceeds the size limit.")
                    output.write(chunk)
            if total != expected:
                raise OxylabsDownloadError("YouTube download was incomplete.")
        part.replace(target)
        return total
    finally:
        part.unlink(missing_ok=True)


def _streams(path, deadline):
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "json", str(path)],
        capture_output=True, check=True, timeout=min(30, _remaining(deadline)),
    )
    return {s["codec_type"] for s in json.loads(result.stdout).get("streams", [])}


def download_video_via_oxylabs(video_id: str) -> Path:
    config = get_config()
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
        raise OxylabsDownloadError("Invalid YouTube download video ID.")
    if not config.oxylabs_username or not config.oxylabs_password:
        raise OxylabsDownloadError("YouTube download provider credentials are not configured.")
    work_dir = None
    client = None
    try:
        client, storage_type, storage_url, bucket, prefix = _storage(config)
        # Validate the reader before creating a potentially billable job.
        client.head_bucket(Bucket=bucket)
        deadline = time.monotonic() + config.oxylabs_download_timeout_seconds
        with requests.Session() as session:
            session.auth = (config.oxylabs_username, config.oxylabs_password)
            payload = _api(session, "POST", API_URL, deadline, json={
                "source": "youtube_download", "query": video_id,
                "storage_type": storage_type, "storage_url": storage_url,
                "context": [
                    {"key": "download_type", "value": "audio_video"},
                    {"key": "video_quality", "value": config.oxylabs_video_quality},
                    {"key": "audio_language", "value": "original"},
                ],
            })
            job_id = str(payload.get("id", ""))
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", job_id):
                raise OxylabsDownloadError("YouTube download provider returned an invalid job ID.")
            logger.info("Oxylabs download submitted: video=%s job=%s", video_id, job_id)
            # Submit exactly once. Retrying a POST can create duplicate paid jobs.
            while payload.get("status") != "done":
                if payload.get("status") in {"faulted", "failed"}:
                    raise OxylabsDownloadError("YouTube download failed at the provider. Please try another video.")
                time.sleep(min(5, _remaining(deadline)))
                try:
                    payload = _api(session, "GET", f"{API_URL}/{job_id}", deadline)
                except OxylabsDownloadError as exc:
                    if "could not be reached" in str(exc):
                        continue
                    raise
        # Reserve time for transferring and, if needed, combining separate tracks.
        deadline = time.monotonic() + config.oxylabs_transfer_timeout_seconds
        Path(config.temp_dir).mkdir(parents=True, exist_ok=True)
        work_dir = Path(tempfile.mkdtemp(prefix=f"oxy-{video_id}-", dir=config.temp_dir))
        video = work_dir / f"{video_id}.mp4"
        key_base = f"{prefix}/" if prefix else ""
        key_base += f"{video_id}_{job_id}"
        size = _fetch(client, bucket, key_base + ".mp4", video, deadline, config.max_video_upload_bytes)
        streams = _streams(video, deadline)
        if "video" not in streams:
            raise OxylabsDownloadError("YouTube download has no video track.")
        if "audio" not in streams:
            audio = work_dir / f"{video_id}.m4a"
            _fetch(client, bucket, key_base + ".m4a", audio, deadline, config.max_video_upload_bytes - size)
            merged = work_dir / "merged.mp4"
            subprocess.run(
                ["ffmpeg", "-nostdin", "-v", "error", "-i", str(video), "-i", str(audio),
                 "-map", "0:v:0", "-map", "1:a:0", "-c", "copy", "-movflags", "+faststart", str(merged)],
                capture_output=True, check=True, timeout=_remaining(deadline),
            )
            if not {"audio", "video"}.issubset(_streams(merged, deadline)):
                raise OxylabsDownloadError("YouTube download is missing audio or video.")
            merged.replace(video)
            audio.unlink()
        logger.info("Oxylabs download complete: video=%s job=%s bytes=%s", video_id, job_id, video.stat().st_size)
        return video
    except OxylabsDownloadError:
        if work_dir:
            shutil.rmtree(work_dir, ignore_errors=True)
        raise
    except Exception:
        if work_dir:
            shutil.rmtree(work_dir, ignore_errors=True)
        # SDK/subprocess exceptions can include credentials or untrusted response bodies.
        raise OxylabsDownloadError("YouTube download could not retrieve a complete video from storage.") from None
    finally:
        if client:
            client.close()
