"""GET /fx/assets and GET /fx/assets/{sha256}.{ext} (§5.6, WP13)."""

from __future__ import annotations

import hashlib
import json
import shutil
import wave
from pathlib import Path

import numpy as np
import pytest
from fastapi.routing import APIRoute

from src.database import get_db
from src.edit_fx import assets

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
BUILTIN = assets.builtin_dir()
MANIFEST = json.loads((BUILTIN / "manifest.json").read_text(encoding="utf-8"))
IMMUTABLE = "public, max-age=31536000, immutable"


def _tone(path: Path, seconds: float = 0.4) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    t = np.arange(int(48_000 * seconds)) / 48_000
    pcm = np.round(np.sin(2 * np.pi * 440 * t) * 0.1 * 32767).astype("<i2")
    with wave.open(str(path), "wb") as fh:
        fh.setnchannels(1)
        fh.setsampwidth(2)
        fh.setframerate(48_000)
        fh.writeframes(pcm.tobytes())
    return path


@pytest.fixture
def library(tmp_path, monkeypatch):
    root = tmp_path / "fx_library"
    (root / "sfx").mkdir(parents=True)
    (root / "music").mkdir(parents=True)
    monkeypatch.setenv("FX_LIBRARY_DIR", str(root))
    assets.load_library(refresh=True)
    yield root
    assets.load_library(refresh=True)


def _strings(value):
    if isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)
    elif isinstance(value, str):
        yield value


def _urls(manifest: dict) -> list[str]:
    urls = [e["url"] for e in manifest["sfx"]] + [e["url"] for e in manifest["music"]]
    return urls + [manifest["emoji"]["atlas"], manifest["emoji"]["map"]]


# ----------------------------------------------------------------------------- auth


async def test_both_routes_require_auth_like_fonts(client):
    sha = MANIFEST["sfx"][0]["sha256"]
    assert (await client.get("/fx/assets")).status_code == 401
    assert (await client.get(f"/fx/assets/{sha}.wav")).status_code == 401
    assert (await client.get("/fonts")).status_code == 401


@pytest.mark.parametrize("path", ["/fx/assets", "/fx/assets/{filename}"])
def test_routes_release_the_db_session_before_streaming(app, path):
    route = next(r for r in app.routes if isinstance(r, APIRoute) and r.path == path)
    dependency = next(d for d in route.dependant.dependencies if d.call is get_db)
    assert dependency.scope == "function"


# ----------------------------------------------------------------------------- manifest


async def test_manifest_lists_builtins_without_paths(client, auth_headers, library):
    response = await client.get("/fx/assets", headers=auth_headers)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-cache"
    body = response.json()

    assert body["version"] == 1
    assert {e["id"] for e in body["sfx"]} == {e["id"] for e in MANIFEST["sfx"]}
    assert {e["id"] for e in body["music"]} == {e["id"] for e in MANIFEST["music"]}
    assert body["emoji"]["cell"] == MANIFEST["emoji"]["cell"]
    sfx = body["sfx"][0]
    assert set(sfx) == {"id", "sound", "variant", "url", "duration", "gain_db"}
    assert set(body["music"][0]) == {"id", "mood", "url", "duration", "loop_start", "loop_seconds"}

    # No file names or paths anywhere: every URL is content-addressed and nothing names a directory.
    for url in _urls(body):
        assert url.startswith("/fx/assets/") and len(url.rsplit("/", 1)[1].split(".")[0]) == 64
    strings = list(_strings(body))
    assert "file" not in strings and "path" not in strings and "sha256" not in strings
    for text in strings:
        assert str(BUILTIN) not in text and str(library) not in text
        assert not text.startswith(("sfx/", "music/", "emoji/")) and "fx_assets" not in text


@needs_ffmpeg
async def test_manifest_adds_this_machines_drop_ins_flagged_custom(client, auth_headers, library):
    _tone(library / "sfx" / "whoosh-mine.wav")
    _tone(library / "music" / "hype-mytrack.wav", seconds=1.0)
    (library / "sfx" / "README.txt").write_text("ignored")

    body = (await client.get("/fx/assets", headers=auth_headers)).json()
    custom_sfx = [e for e in body["sfx"] if e.get("custom")]
    custom_music = [e for e in body["music"] if e.get("custom")]
    assert [(e["id"], e["sound"], e["variant"]) for e in custom_sfx] == [("whoosh-mine", "whoosh", 0)]
    assert [(e["id"], e["mood"]) for e in custom_music] == [("hype-mytrack", "hype")]
    assert all("custom" not in e for e in body["sfx"] if e["id"] != "whoosh-mine")
    assert custom_music[0]["duration"] == pytest.approx(1.0, abs=0.05)
    assert "gain_db" in custom_sfx[0] and "gain_db" in custom_music[0]

    # A drop-in is served by its content hash too.
    sha = hashlib.sha256((library / "sfx" / "whoosh-mine.wav").read_bytes()).hexdigest()
    assert custom_sfx[0]["url"] == f"/fx/assets/{sha}.wav"
    served = await client.get(custom_sfx[0]["url"], headers=auth_headers)
    assert served.status_code == 200
    assert served.content == (library / "sfx" / "whoosh-mine.wav").read_bytes()


# ----------------------------------------------------------------------------- files


@pytest.mark.parametrize("entry", [MANIFEST["sfx"][0], MANIFEST["music"][0]], ids=["sfx", "music"])
async def test_files_are_served_by_hash_with_immutable_caching(client, auth_headers, library, entry):
    ext = Path(entry["file"]).suffix.lstrip(".")
    response = await client.get(f"/fx/assets/{entry['sha256']}.{ext}", headers=auth_headers)
    assert response.status_code == 200
    assert response.headers["cache-control"] == IMMUTABLE
    assert response.headers["content-type"].startswith({"wav": "audio/wav", "mp3": "audio/mpeg"}[ext])
    assert response.content == (BUILTIN / entry["file"]).read_bytes()


async def test_every_manifest_url_resolves(client, auth_headers, library):
    body = (await client.get("/fx/assets", headers=auth_headers)).json()
    atlas = await client.get(body["emoji"]["atlas"], headers=auth_headers)
    assert atlas.status_code == 200 and atlas.headers["content-type"] == "image/png"
    atlas_map = await client.get(body["emoji"]["map"], headers=auth_headers)
    assert atlas_map.status_code == 200 and atlas_map.json()["cell"] == MANIFEST["emoji"]["cell"]
    for url in _urls(body):
        assert (await client.get(url, headers=auth_headers)).status_code == 200, url


async def test_range_requests_are_supported_for_media(client, auth_headers, library):
    entry = MANIFEST["music"][0]
    ext = Path(entry["file"]).suffix.lstrip(".")
    response = await client.get(f"/fx/assets/{entry['sha256']}.{ext}", headers={**auth_headers, "range": "bytes=0-99"})
    assert response.status_code == 206
    assert response.headers["content-range"].startswith("bytes 0-99/")
    assert len(response.content) == 100


@pytest.mark.parametrize("path", [
    "/fx/assets/" + "0" * 64 + ".wav",                                    # unknown hash
    "/fx/assets/" + MANIFEST["sfx"][0]["sha256"] + ".png",                # known hash, wrong extension
    "/fx/assets/" + MANIFEST["sfx"][0]["sha256"] + ".exe",
    "/fx/assets/" + MANIFEST["sfx"][0]["sha256"].upper() + ".wav",
    "/fx/assets/" + MANIFEST["sfx"][0]["sha256"],                         # no extension
    "/fx/assets/" + MANIFEST["sfx"][0]["file"].replace("/", "%2F"),       # a manifest file name
    "/fx/assets/..%2F..%2Fmain.py",
    "/fx/assets/..%2Fmanifest.json",
    "/fx/assets/%2E%2E%2Fmanifest.json",
    "/fx/assets/manifest.json",
    "/fx/assets/sfx/whoosh_0.wav",
    "/fx/assets/../../etc/passwd",
    "/fx/assets/....%2F%2Fetc%2Fpasswd",
])
async def test_unknown_hashes_and_traversal_404(client, auth_headers, library, path):
    response = await client.get(path, headers=auth_headers)
    assert response.status_code == 404, response.text
    assert b"RIFF" not in response.content


@needs_ffmpeg
async def test_a_removed_drop_in_is_no_longer_served(client, auth_headers, library):
    path = _tone(library / "sfx" / "pop-gone.wav")
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    assert (await client.get(f"/fx/assets/{sha}.wav", headers=auth_headers)).status_code == 200
    path.unlink()
    assert (await client.get(f"/fx/assets/{sha}.wav", headers=auth_headers)).status_code == 404
