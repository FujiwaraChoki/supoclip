"""The edge review: one small model call per clip proposes where it opens and closes; code validates."""

import json
from types import SimpleNamespace

import pytest

from src import ai
from src.services import video_service
from src.services.video_service import VideoService

SENTENCES = [
    ("A", "Welcome back to the show.", 0.0, 0.3),                       # 1
    ("A", "Bruno did not offer to be here today.", 2.0, 0.3),      # 2
    ("A", "We put a chair down for him anyway, more or less.", 5.0, 0.3),  # 3
    ("A", "Nobody is asking him to stay in that room.", 9.0, 0.3),    # 4
    ("B", "Why can you not speak right now?", 13.0, 0.3),               # 5
    ("C", "I was going to fetch the ladder.", 16.0, 0.3),                 # 6
    ("B", "That is a different story for another day.", 19.0, 0.3),     # 7
    ("A", "Anyway, let us carry on to the next part.", 23.0, 0.3),      # 8
]


def _video(tmp_path):
    words = []
    for speaker, text, start, pace in SENTENCES:
        for position, token in enumerate(text.split()):
            begin = start + position * pace
            words.append({"text": token, "start": int(begin * 1000), "end": int((begin + pace * 0.8) * 1000),
                          "confidence": 0.99, "speaker": speaker})
    video = tmp_path / "show.mp4"
    video.with_suffix(".transcript_cache.json").write_text(
        json.dumps({"version": 2, "language_code": "en", "utterances": [], "words": words}), encoding="utf-8")
    return video


@pytest.fixture
def review(monkeypatch):
    state = {"requests": [], "answer": {}}

    async def fake(clips):
        state["requests"].extend(clips)
        return dict(state["answer"])

    monkeypatch.setattr(ai, "review_clip_edges", fake)
    monkeypatch.setattr(video_service, "get_config", lambda: SimpleNamespace(clip_edge_review=True))
    return state


async def test_a_reviewed_clip_stores_the_edges_the_model_and_the_code_agree_on(tmp_path, review):
    video = _video(tmp_path)
    # Picked 00:05-00:15: opens on "We put a chair down for him" (for whom?) and closes on an unanswered question.
    segments = [{"start_time": "00:05", "end_time": "00:15", "text": "", "hook_title": "He never offered",
                 "reasoning": "A reluctant guest and a ladder.", "hook_start_time": "00:02", "hook_end_time": "00:04"}]
    review["answer"] = {1: (2, 6)}
    out = await VideoService._review_segment_edges(segments, video, "auto")
    assert out[0]["edge_seconds"] == [2.0, pytest.approx(18.04, abs=0.01)]
    assert out[0]["start_time"] == "00:02" and out[0]["end_time"] == "00:18"
    assert out[0]["text"].startswith("Bruno did not offer") and out[0]["text"].endswith("fetch the ladder.")
    # What the model was shown: what the clip is about (a leftover of the topic before, or a payoff one line
    # after the cut, can only be told against it), and the sentences around both edges.
    request = review["requests"][0]
    assert request["title"] == "He never offered" and request["about"] == "A reluctant guest and a ladder."
    assert request["first"] == 3 and request["last"] == 6    # the reply rule already took the answer in
    assert [s["id"] for s in request["opening"]][0] == 1


async def test_an_unchanged_review_still_pins_the_resolved_edges(tmp_path, review):
    video = _video(tmp_path)
    segments = [{"start_time": "00:05", "end_time": "00:12", "text": "as picked"}]
    review["answer"] = {}                                   # the call failed or said nothing
    out = await VideoService._review_segment_edges(segments, video, "auto")
    assert out[0]["start_time"] == "00:05" and out[0]["text"] == "as picked"
    assert out[0]["edge_seconds"][0] == 5.0


async def test_segments_with_explicit_ranges_or_reviewed_edges_are_not_reviewed_again(tmp_path, review):
    video = _video(tmp_path)
    segments = [
        {"start_time": "00:05", "end_time": "00:12", "edge_seconds": [5.0, 11.6]},
        {"start_time": "00:05", "end_time": "00:12", "source_ranges": [(5.0, 12.0)]},
        {"start_time": "00:00", "end_time": "00:30", "hook_type": "fallback"},
    ]
    await VideoService._review_segment_edges(segments, video, "auto")
    assert review["requests"] == []


async def test_the_review_can_be_turned_off_and_needs_word_timings(tmp_path, review, monkeypatch):
    video = _video(tmp_path)
    segments = [{"start_time": "00:05", "end_time": "00:12"}]
    monkeypatch.setattr(video_service, "get_config", lambda: SimpleNamespace(clip_edge_review=False))
    assert "edge_seconds" not in (await VideoService._review_segment_edges(segments, video, "auto"))[0]
    monkeypatch.setattr(video_service, "get_config", lambda: SimpleNamespace(clip_edge_review=True))
    plain = tmp_path / "no-sidecar.mp4"
    assert "edge_seconds" not in (await VideoService._review_segment_edges(segments, plain, "auto"))[0]
    assert review["requests"] == []


def test_the_request_lists_both_edges_with_speakers_and_marks_what_is_in_the_clip():
    prompt = ai.build_clip_edge_prompt([{
        "clip": 1, "title": "", "first": 3, "last": 5,
        "opening": [{"id": 2, "speaker": "A", "text": "Bruno did not offer.", "in_clip": False},
                    {"id": 3, "speaker": "A", "text": "We put a chair down.", "in_clip": True}],
        "closing": [{"id": 5, "speaker": "B", "text": "Why can you not speak?", "in_clip": True},
                    {"id": 6, "speaker": None, "text": "I was fetching the ladder.", "in_clip": False}],
    }])
    assert "CLIP 1\nOPENING sentences (current opening: #3):\n#2 [A] Bruno did not offer.\n#3 (in clip) [A] We put a chair down." in prompt
    told = ai.build_clip_edge_prompt([{
        "clip": 2, "title": "He never offered", "about": "A reluctant guest.", "first": 3, "last": 3,
        "opening": [{"id": 3, "speaker": None, "text": "We put a chair down.", "in_clip": True}],
        "closing": [{"id": 3, "speaker": None, "text": "We put a chair down.", "in_clip": True}],
    }])
    assert "CLIP 2\nTitle: He never offered\nWhy it was picked: A reluctant guest.\nOPENING sentences" in told
    assert "CLOSING sentences (current closing: #5):\n#5 (in clip) [B] Why can you not speak?\n#6 I was fetching the ladder." in prompt


async def test_each_clip_is_reviewed_in_its_own_call_and_bad_answers_are_dropped(monkeypatch):
    calls = []

    async def run(_agent, prompt, timeout_seconds=None):
        calls.append(prompt)
        number = int(prompt.split("CLIP ")[1].split("\n")[0])
        if number == 2:
            raise RuntimeError("provider said no")
        ids = (2, 6) if number == 1 else (99, 6)          # clip 3 answers with an id it was never shown
        return SimpleNamespace(output=ai.ClipEdgeReview(clips=[ai.ClipEdges(clip=number, opening_problem="none", closing_problem="none", opening_id=ids[0], closing_id=ids[1])]))

    monkeypatch.setattr(ai, "get_clip_edge_agent", lambda: object())
    monkeypatch.setattr(ai, "_run_transcript_analysis", run)
    clip = {"title": "", "first": 3, "last": 5,
            "opening": [{"id": i, "speaker": None, "text": "x", "in_clip": i >= 3} for i in (1, 2, 3)],
            "closing": [{"id": i, "speaker": None, "text": "x", "in_clip": i <= 5} for i in (5, 6, 7)]}
    chosen = await ai.review_clip_edges([{**clip, "clip": 1}, {**clip, "clip": 2}, {**clip, "clip": 3}])
    assert len(calls) == 3 and all(prompt.count("CLIP ") == 1 for prompt in calls)
    assert chosen == {1: (2, 6)}
    assert await ai.review_clip_edges([]) == {}


def test_the_review_uses_its_own_model_setting(monkeypatch):
    # The strongest model is cheap for a few hundred tokens per clip: Claude Sonnet when a key is set.
    from src.config import Config

    for name in ("CLIP_EDGE_LLM", "ANTHROPIC_API_KEY", "RERANK_LLM", "CLIP_EDGE_LISTEN", "CLIP_EDGE_LISTEN_MODEL"):
        monkeypatch.delenv(name, raising=False)
    assert Config().clip_edge_llm is None                       # falls back to RERANK_LLM, then LLM
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    assert Config().clip_edge_llm == "anthropic:claude-sonnet-5-5"
    monkeypatch.setenv("CLIP_EDGE_LLM", "openrouter:anthropic/claude-sonnet-5.5")
    assert Config().clip_edge_llm == "openrouter:anthropic/claude-sonnet-5.5"
    assert Config().clip_edge_listen == "auto" and Config().clip_edge_listen_model == "small"
