#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

if ! command -v ffmpeg >/dev/null 2>&1 || ! command -v ffprobe >/dev/null 2>&1; then
  printf '%s\n' "Clipping Gods needs ffmpeg and ffprobe. Install FFmpeg, then run this script again." >&2
  exit 1
fi

if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi

if [[ ! -x .venv/bin/uvicorn ]]; then
  .venv/bin/python -m pip install -r requirements.txt
fi
exec .venv/bin/uvicorn app:app --host "${HOST:-127.0.0.1}" --port "${PORT:-8000}"
