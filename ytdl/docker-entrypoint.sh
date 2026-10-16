#!/bin/sh
# YouTube breaks yt-dlp often and fixes land in nightly builds first, so upgrade
# on every start. The service restarts itself when PyPI has a newer build.
set -e
if [ "${YTDL_UPDATE_ON_START:-true}" = "true" ]; then
    spec='yt-dlp[default]'
    # A pre-release lower bound opts only yt-dlp (not its dependencies) into nightlies.
    [ "${YTDL_UPDATE_CHANNEL:-nightly}" = "nightly" ] && spec='yt-dlp[default]>=2026.1.1.dev0'
    timeout 180 uv pip install --python /app/.venv/bin/python --quiet --upgrade "$spec" \
        || echo "yt-dlp upgrade failed; starting with the installed version" >&2
fi
exec "$@"
