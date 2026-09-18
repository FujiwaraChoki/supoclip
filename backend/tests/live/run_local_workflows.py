"""Opt-in real API/worker checks; run against disposable local services only.

See docs/testing-local.md. No provider calls are mocked by this script.
"""

import os, time, hmac, hashlib, json, subprocess, asyncio
from pathlib import Path
import requests
from arq.connections import create_pool, RedisSettings
from arq.jobs import Job
from urllib.parse import urlparse

base = os.environ.get("LOCAL_TEST_BACKEND_URL", "http://127.0.0.1:8100").rstrip("/")
if urlparse(base).hostname not in {"127.0.0.1", "localhost", "::1"}:
    raise SystemExit("Live checks must run against an isolated local test backend")
uid = os.environ["LOCAL_TEST_USER_ID"]
secret = os.environ["BACKEND_AUTH_SECRET"].encode()
fixture = Path(os.environ["LOCAL_TEST_VIDEO_PATH"])
artifacts = Path(os.environ.get("LOCAL_TEST_ARTIFACTS", "/tmp/supoclip-live-tests"))
artifacts.mkdir(parents=True, exist_ok=True)
redis_port = int(os.environ.get("REDIS_PORT", "6379"))
checks = []


def request(method, path, expected=200, **kwargs):
    ts = str(int(time.time()))
    headers = {
        "x-supoclip-user-id": uid,
        "x-supoclip-ts": ts,
        "x-supoclip-signature": hmac.new(
            secret, f"{uid}:{ts}".encode(), hashlib.sha256
        ).hexdigest(),
    }
    r = requests.request(method, base + path, headers=headers, timeout=180, **kwargs)
    assert r.status_code == expected, (method, path, r.status_code, r.text[:500])
    return r.json() if "json" in r.headers.get("content-type", "") else r


def done(tid):
    deadline = time.time() + 300
    while time.time() < deadline:
        d = request("GET", "/tasks/" + tid)
        assert d["status"] != "error", d.get("progress_message")
        if d["status"] == "completed":
            return d
        time.sleep(1)
    raise AssertionError("Timed out " + tid)


def check(label):
    checks.append(label)
    print("PASS", label, flush=True)
    (artifacts / "api-results.json").write_text(json.dumps(checks, indent=2))


async def wait_job(jid):
    pool = await create_pool(RedisSettings(host="127.0.0.1", port=redis_port))
    try:
        for _ in range(180):
            status = await Job(jid, pool, _queue_name="supoclip_tasks").status()
            if status.value in ["complete", "not_found"]:
                return
            await asyncio.sleep(1)
        raise AssertionError("Worker did not release cancelled job")
    finally:
        await pool.aclose()


# Failures should terminate instead of remaining stuck in processing.
u = request(
    "POST", "/upload", files={"video": ("broken.mp4", b"invalid media", "video/mp4")}
)
t = request("POST", "/tasks/", json={"source": {"url": u["video_path"]}})
asyncio.run(wait_job(t["job_id"]))
d = request("GET", "/tasks/" + t["task_id"])
assert d["status"] == "error", d
request("DELETE", "/tasks/" + t["task_id"])
check("invalid media records terminal error and can be deleted")

# Exercise cancellation then wait for that worker job to exit before resuming it.
t = request(
    "POST",
    "/tasks/",
    json={"source": {"url": "https://www.youtube.com/watch?v=jNQXAC9IVRw"}},
)
request("POST", f"/tasks/{t['task_id']}/cancel")
asyncio.run(wait_job(t["job_id"]))
assert request("GET", "/tasks/" + t["task_id"])["status"] == "cancelled"
request("POST", f"/tasks/{t['task_id']}/resume")
d = done(t["task_id"])
assert len(d["clips"]) >= 1
request(
    "PATCH", f"/tasks/{t['task_id']}", json={"title": "Local test renamed generation"}
)
assert (
    request("GET", "/tasks/" + t["task_id"])["source_title"]
    == "Local test renamed generation"
)
check("cancel, worker exit, resume, successful completion and rename")
request(
    "POST",
    f"/tasks/{t['task_id']}/settings",
    json={
        "font_size": 30,
        "font_color": "#FFFF00",
        "caption_template": "hormozi",
        "apply_to_existing": True,
    },
)
updated = request("GET", "/tasks/" + t["task_id"])
assert updated["clips"] and updated["font_size"] == 30
assert updated["font_color"] == "#FFFF00"
check("task settings regenerate existing clips")
request("DELETE", "/tasks/" + t["task_id"])

for framing in ["original", "vertical_pan", "vertical_split"]:
    with fixture.open("rb") as f:
        u = request("POST", "/upload", files={"video": ("fixture.mp4", f, "video/mp4")})
    t = request(
        "POST",
        "/tasks/",
        json={
            "source": {"url": u["video_path"]},
            "output_format": framing,
            "add_subtitles": False,
            "cut_long_pauses": True,
            "pause_threshold_ms": 500,
            "remove_filler_words": True,
            "filtered_words": ["cool"],
        },
    )
    d = done(t["task_id"])
    c = d["clips"][0]
    media = request("GET", f"/tasks/{t['task_id']}/clips/{c['id']}/file")
    dest = artifacts / f"{framing}.mp4"
    dest.write_bytes(media.content)
    probe = json.loads(
        subprocess.check_output(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_streams",
                "-show_format",
                "-of",
                "json",
                str(dest),
            ]
        )
    )
    video = next(s for s in probe["streams"] if s["codec_type"] == "video")
    assert any(s["codec_type"] == "audio" for s in probe["streams"])
    assert float(probe["format"]["duration"]) > 1
    if framing == "original":
        source_probe = json.loads(
            subprocess.check_output(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-select_streams",
                    "v:0",
                    "-show_streams",
                    "-of",
                    "json",
                    str(fixture),
                ]
            )
        )["streams"][0]
        assert (video["width"], video["height"]) == (
            source_probe["width"],
            source_probe["height"],
        )
    else:
        assert video["height"] > video["width"]
    request(
        "GET",
        f"/tasks/{t['task_id']}/clips/{c['id']}/export?preset=bogus",
        expected=400,
    )
    # Duplicate merge IDs must not duplicate content.
    request(
        "POST",
        f"/tasks/{t['task_id']}/clips/merge",
        expected=400,
        json={"clip_ids": [c["id"], c["id"]]},
    )
    request("DELETE", f"/tasks/{t['task_id']}/clips/{c['id']}")
    updated = request("GET", "/tasks/" + t["task_id"])
    assert updated["clips"] == []
    request("DELETE", "/tasks/" + t["task_id"])
    check(
        f"{framing}, no captions, cleanup, playable media, validation and clip deletion"
    )
