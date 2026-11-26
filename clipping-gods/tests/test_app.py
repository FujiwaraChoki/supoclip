import asyncio
import os
from types import SimpleNamespace

os.environ.setdefault("DATA_DIR", "/tmp/clipping-gods-tests")

from fastapi.testclient import TestClient
import pytest

import app as clipping_app
from app import TEAM_ROLES, _word_windows, detect_source


def test_source_detection_accepts_public_video_links():
    assert detect_source("https://www.youtube.com/watch?v=abc_12345") == "youtube"
    assert detect_source("https://youtu.be/abc_12345") == "youtube"
    assert detect_source("https://youtube.com/live/abc_12345") == "youtube"
    assert detect_source("https://www.tiktok.com/@maker/video/12345") == "tiktok"
    assert detect_source("https://vm.tiktok.com/AbCd/") == "tiktok"
    assert detect_source("https://www.facebook.com/reel/12345") == "facebook"
    assert detect_source("https://fb.watch/AbCd/") == "facebook"


def test_source_detection_rejects_non_video_and_unsafe_urls():
    assert detect_source("https://youtube.com/channel/UC123") is None
    assert detect_source("https://youtube.com/watch?v=x") is None
    assert detect_source("https://www.tiktok.com/@maker") is None
    assert detect_source("https://facebook.com/profile.php?id=123") is None
    assert detect_source("https://youtube.com.example.org/watch?v=abc") is None
    assert detect_source("https://user:pass@youtube.com/watch?v=abc") is None
    assert detect_source("http://127.0.0.1:8000/watch?v=abc") is None
    assert detect_source("file:///etc/passwd") is None


def test_transcript_windows_keep_word_timestamps_and_limit_span():
    words = [
        {"start": 0, "end": 1000, "text": "first"},
        {"start": 7 * 60 * 1000, "end": 7 * 60 * 1000 + 1000, "text": "last"},
        {"start": 9 * 60 * 1000, "end": 9 * 60 * 1000 + 1000, "text": "next"},
    ]

    windows = list(_word_windows(words, window_ms=8 * 60 * 1000))

    assert [start for start, _ in windows] == [0, 9 * 60 * 1000]
    assert [[word["text"] for word in group] for _, group in windows] == [
        ["first", "last"],
        ["next"],
    ]


def test_five_reviewer_team_has_one_scout_and_five_reviewers():
    assert len(TEAM_ROLES) == 5
    assert clipping_app.MIN_SUCCESSFUL_REVIEWERS == 4
    assert {role for role, _ in TEAM_ROLES} == {
        "hook editor",
        "context editor",
        "story editor",
        "audience editor",
        "source-fidelity editor",
    }


def test_job_api_requires_password_and_sets_clip_playback_cookie(monkeypatch, tmp_path):
    monkeypatch.setattr(clipping_app, "APP_PASSWORD", "test-password")
    monkeypatch.setenv("COOKIE_SECURE", "true")
    monkeypatch.setattr(clipping_app, "DATA_DIR", tmp_path)
    monkeypatch.setattr(clipping_app, "DATABASE_PATH", tmp_path / "jobs.sqlite3")
    monkeypatch.setattr(clipping_app, "JOBS_DIR", tmp_path / "jobs")

    with TestClient(clipping_app.app, base_url="https://testserver") as client:
        homepage = client.get("/")
        assert homepage.status_code == 200
        assert "Clipping Gods" in homepage.text
        assert client.get("/static/app.js").status_code == 200
        assert client.get("/static/styles.css").status_code == 200

        denied = client.get("/api/jobs")
        assert denied.status_code == 401

        login = client.post(
            "/api/login",
            headers={"X-App-Password": "test-password"},
        )
        assert login.status_code == 200
        assert "cg_access=" in login.headers["set-cookie"]
        assert "httponly" in login.headers["set-cookie"].lower()
        assert "secure" in login.headers["set-cookie"].lower()

        accepted = client.post("/api/jobs", json={"url": "https://example.com/video"})
        assert accepted.status_code == 422


def test_five_specialists_average_scores_and_keep_candidate_mapping(monkeypatch):
    candidates = [
        {
            "score": 50,
            "start_seconds": float(index * 30),
            "end_seconds": float(index * 30 + 30),
            "title": str(index),
            "transcript": f"Candidate transcript {index}",
        }
        for index in range(3)
    ]
    review_scores = {
        "hook editor": [80, 90, 50],
        "context editor": [70, 100, 70],
        "story editor": [60, 60, 90],
        "audience editor": [90, 90, 70],
        "source-fidelity editor": [80, 80, 80],
    }

    async def fake_chat_json(_client, system, _prompt):
        role = next(role for role, _ in TEAM_ROLES if role in system)
        return {
            "rankings": [
                {"candidate_id": index, "score": score}
                for index, score in enumerate(review_scores[role])
            ]
        }

    monkeypatch.setattr(clipping_app, "_chat_json", fake_chat_json)
    monkeypatch.setattr(clipping_app, "_update_job", lambda *_args, **_kwargs: None)

    ranked = asyncio.run(
        clipping_app._review_candidates(
            SimpleNamespace(),
            candidates,
            "0123456789abcdef0123456789abcdef",
        )
    )

    assert [candidate["title"] for candidate in ranked] == ["1", "0", "2"]
    assert [candidate["score"] for candidate in ranked] == [84, 76, 72]


def test_four_successful_reviewers_continue_when_one_reviewer_fails(monkeypatch):
    candidates = [
        {
            "score": 50,
            "start_seconds": float(index * 30),
            "end_seconds": float(index * 30 + 30),
            "title": f"candidate-{index}",
            "transcript": f"An independently understandable moment {index}.",
        }
        for index in range(2)
    ]
    calls = []

    async def fake_chat_json(_client, system, _prompt):
        role = next(role for role, _ in TEAM_ROLES if role in system)
        calls.append(role)
        if role == "source-fidelity editor":
            raise TimeoutError("reviewer did not finish")
        return {
            "rankings": [
                {"candidate_id": index, "score": 80 - index}
                for index in range(len(candidates))
            ]
        }

    monkeypatch.setattr(clipping_app, "_chat_json", fake_chat_json)
    monkeypatch.setattr(clipping_app, "_update_job", lambda *_args, **_kwargs: None)

    ranked = asyncio.run(
        clipping_app._review_candidates(
            SimpleNamespace(),
            candidates,
            "0123456789abcdef0123456789abcdef",
        )
    )

    assert len(calls) == 5
    assert len(ranked) == 2
    assert ranked[0]["score"] == 80


def test_reviewer_team_fails_when_two_reviewers_fail(monkeypatch):
    candidates = [
        {
            "score": 50,
            "start_seconds": float(index * 30),
            "end_seconds": float(index * 30 + 30),
            "title": f"candidate-{index}",
            "transcript": f"An independently understandable moment {index}.",
        }
        for index in range(2)
    ]

    async def fake_chat_json(_client, system, _prompt):
        role = next(role for role, _ in TEAM_ROLES if role in system)
        if role in {"source-fidelity editor", "audience editor"}:
            raise TimeoutError("reviewer did not finish")
        return {
            "rankings": [
                {"candidate_id": index, "score": 80 - index}
                for index in range(len(candidates))
            ]
        }

    monkeypatch.setattr(clipping_app, "_chat_json", fake_chat_json)
    monkeypatch.setattr(clipping_app, "_update_job", lambda *_args, **_kwargs: None)

    with pytest.raises(RuntimeError, match="at least 4 are required"):
        asyncio.run(
            clipping_app._review_candidates(
                SimpleNamespace(),
                candidates,
                "0123456789abcdef0123456789abcdef",
            )
        )
