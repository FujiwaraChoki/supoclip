#!/usr/bin/env python3
"""Webhook route script: file a signed SupoClip cancellation report in a durable inbox.

Install as ~/.hermes/profiles/supoclip/scripts/cancellation-report-inbox.py and bind it
to the ``cancellation-reports-supoclip`` webhook route (docs/cancellation-reviews.md).
The gateway runs it only after the route's HMAC signature checked out, then fires the
no-agent job that posts the inbox to the SupoClip Bot Chat.

One file per delivery id. A delivery that is already filed, or already posted, is a
success without a second copy, so the sender's retries never post twice. Exit 1 makes
the gateway answer ``ignored``, which the sender treats as "not delivered, retry".
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

PAYLOAD_TYPE = "supoclip.cancellation_report"
DELIVERY_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
MAX_TEXT = 12000


def folder(home: Path, profile: str) -> Path:
    return home / "files" / profile / "monitoring" / "cancellation-reports"


def receive(payload: object, profile: str, home: Path) -> dict:
    if profile != "supoclip":
        raise ValueError("Wrong profile")
    if not isinstance(payload, dict) or payload.get("type") != PAYLOAD_TYPE or payload.get("version") != 1:
        raise ValueError("Unsupported payload")
    delivery_id, kind, text = payload.get("delivery_id"), payload.get("kind"), payload.get("text")
    if not isinstance(delivery_id, str) or not DELIVERY_ID.match(delivery_id):
        raise ValueError("Invalid delivery id")
    if kind not in ("report", "update") or not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT:
        raise ValueError("Invalid report")
    root = folder(home, profile)
    inbox, sent = root / "inbox", root / "sent"
    for directory in (inbox, sent):
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    name = f"{delivery_id}.json"
    if (inbox / name).exists() or (sent / name).exists():
        return {"delivery_id": delivery_id, "filed": "already"}
    record = {"delivery_id": delivery_id, "kind": kind, "text": text,
              "received_at": datetime.now(timezone.utc).isoformat()}
    fd, temporary = tempfile.mkstemp(prefix=".incoming-", dir=inbox)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(record, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, inbox / name)  # fails if a concurrent copy won: never overwrite
    except FileExistsError:
        pass
    finally:
        os.unlink(temporary)
    directory = os.open(inbox, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    return {"delivery_id": delivery_id, "filed": "new"}


if __name__ == "__main__":
    try:
        result = receive(json.load(sys.stdin), Path(__file__).resolve().parent.parent.name, Path.home())
    except Exception as exc:
        print(type(exc).__name__ + ": " + str(exc), file=sys.stderr)
        raise SystemExit(1)
    print(json.dumps(result))
