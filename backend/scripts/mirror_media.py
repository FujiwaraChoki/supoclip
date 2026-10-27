#!/usr/bin/env python3
"""Copy existing media to R2 without changing database references or originals.

The default is a read-only inventory. --apply --verify copies missing/changed
objects and checks every target's SHA-256 with a complete read. Restarts skip
unchanged revisions, and per-key locks coordinate with live application writes.
No credentials or signed URLs are printed.
"""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.media_storage import MediaStorage


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--workers", type=int, default=2, choices=range(1, 9))
    parser.add_argument("--prefix", default="")
    args = parser.parse_args()
    storage = MediaStorage()
    if not storage.config.media_fallback_enabled:
        parser.error("MEDIA_FALLBACK configuration is required")
    prefix = storage.key() + "/"
    if args.prefix:
        prefix = storage.key(args.prefix.strip("/")) + "/"
    client = storage.client
    primary, bucket = client.stores["primary"]
    objects = []
    for page in primary.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
        objects.extend(page.get("Contents", []))
    # Protect final clips and originals before disposable caches.
    priorities = {"clips": 0, "sources": 1, "exports": 2, "editor": 3, "transcripts": 4}
    objects.sort(key=lambda obj: priorities.get(obj["Key"].removeprefix(storage.key() + "/").split("/")[0], 5))
    print(json.dumps({"objects": len(objects), "bytes": sum(obj["Size"] for obj in objects),
                      "apply": args.apply, "verify": args.verify}), flush=True)
    if not args.apply:
        return
    counts = Counter()
    fallback, target_bucket = client.stores["fallback"]
    targets = {}
    for page in fallback.get_paginator("list_objects_v2").paginate(Bucket=target_bucket, Prefix=prefix):
        targets.update((obj["Key"], obj) for obj in page.get("Contents", []))

    def copy(item):
        try:
            if args.verify and client.replica_matches(item, targets.get(item["Key"])):
                return "verified"
            return client.mirror_existing(item["Key"], verify=args.verify)
        except Exception as error:
            # Object names can contain user-supplied filenames; keep logs private.
            return "error:" + type(error).__name__

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        processed_bytes = 0
        for index, result in enumerate(pool.map(copy, objects), 1):
            counts[result] += 1
            processed_bytes += objects[index - 1]["Size"]
            if index % 25 == 0 or index == len(objects):
                print(json.dumps({"processed": index, "total": len(objects), "bytes_verified": processed_bytes,
                                  "results": dict(counts)}), flush=True)
    print(json.dumps({"complete": True, "results": dict(counts), "replication": client.pending_status()}), flush=True)
    if any(name.startswith("error:") or name in {"changed", "pending"} for name in counts):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
