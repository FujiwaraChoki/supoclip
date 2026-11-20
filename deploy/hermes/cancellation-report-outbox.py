#!/usr/bin/env python3
"""No-agent job script: print filed SupoClip cancellation reports, oldest first.

Install as ~/.hermes/profiles/supoclip/scripts/cancellation-report-outbox.py for the
"SupoClip cancellation reports" job (``--no-agent``, delivered to ``bot-chat:supoclip``).
The job's stdout is the Bot Chat message, posted verbatim; empty stdout is a silent run.

Printed reports move to ``sent/`` only after stdout is flushed. The gateway's durable
Bot Chat delivery takes it from there. The webhook fires this job per report and the
schedule catches anything a busy or restarting gateway did not run.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

PER_RUN = 3
SEPARATOR = "\n\n— — —\n\n"


def pending(inbox: Path) -> list[tuple[str, Path, dict]]:
    records = []
    for path in inbox.glob("*.json"):
        try:
            record = json.loads(path.read_text())
        except (OSError, ValueError):
            print(f"Unreadable SupoClip cancellation report file {path.name}; left in the inbox.", file=sys.stderr)
            continue
        records.append((record.get("received_at", ""), path, record))
    return sorted(records, key=lambda item: item[0])


def main(home: Path, profile: str) -> int:
    root = home / "files" / profile / "monitoring" / "cancellation-reports"
    inbox, sent = root / "inbox", root / "sent"
    if not inbox.is_dir():
        return 0
    sent.mkdir(parents=True, exist_ok=True, mode=0o700)
    batch = pending(inbox)[:PER_RUN]
    if not batch:
        return 0
    sys.stdout.write(SEPARATOR.join(record["text"].strip() for _, _, record in batch) + "\n")
    sys.stdout.flush()
    for _, path, _ in batch:
        os.replace(path, sent / path.name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(Path.home(), Path(__file__).resolve().parent.parent.name))
