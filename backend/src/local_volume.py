"""Identity of the disk behind ``TEMP_DIR``.

The API and the workers on one machine mount the same media volume. A worker
on another machine has its own. Rows that name a local path (a queued
deletion, an upload not yet copied to object storage) record which disk the
path is on, so only a process that can see that disk acts on them.

The id is a random value stored in ``TEMP_DIR/.volume-id``. Every process
that shares the directory reads the same file, so no setting has to be kept
in step between containers, and a second machine cannot pick the same id by
copying an environment file.
"""

from __future__ import annotations

import os
from pathlib import Path
import re
import uuid

from .config import get_config

FILE_NAME = ".volume-id"
# Redis key holding the API's volume id, so a worker can tell whether it
# shares the API's disk.
API_VOLUME_KEY = "supoclip:media-volume:api"
_cache: dict[str, str] = {}


def volume_id(temp_dir: str | os.PathLike | None = None) -> str:
    root = Path(temp_dir or get_config().temp_dir)
    key = str(root)
    cached = _cache.get(key)
    if cached:
        return cached
    root.mkdir(parents=True, exist_ok=True)
    path = root / FILE_NAME
    value = _read(path)
    if not value:
        candidate = uuid.uuid4().hex
        temporary = root / f"{FILE_NAME}.{candidate}"
        try:
            temporary.write_text(candidate)
            with temporary.open("rb+") as handle:
                os.fsync(handle.fileno())
            try:
                # link() publishes the complete file or fails because another
                # process published its id first. A reader never sees half.
                os.link(temporary, path)
            except FileExistsError:
                pass
            except OSError:
                # No hard links here. Only replace a missing or unreadable file.
                if not _read(path):
                    os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        value = _read(path)
        if not value:
            # Guessing here would let two processes on one disk disagree.
            raise OSError(f"Could not establish a volume id in {root}")
    _cache[key] = value
    return value


def _read(path: Path) -> str | None:
    try:
        value = path.read_text().strip()
    except OSError:
        return None
    return value if re.fullmatch(r"[a-f0-9]{32}", value) else None
