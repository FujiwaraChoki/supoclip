"""LLM=manual: the user's own AI chat picks the clips."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from src import ai, manual_analysis
from src.api.routes import tasks
from src.services import video_service as module
from src.services.video_service import VideoService

TRANSCRIPT = "\n".join(
    f"[{m:02d}:{s:02d} - {m:02d}:{s + 10:02d}] line {m}{s} has some spoken words in it"
    for m in range(0, 3)
    for s in (0, 10, 20, 30, 40)
)


def answer(start="00:10", end="00:45", text="line 010 has some spoken words in it and more"):
    return {
        "most_relevant_segments": [
            {
                "start_time": start,
                "end_time": end,
                "text": text,
                "relevance_score": 0.9,
                "reasoning": "A complete thought.",
                "virality": {
                    "hook_score": 20, "engagement_score": 18, "value_score": 17,
                    "shareability_score": 16, "total_score": 71,
                    "hook_type": "question", "virality_reasoning": "Strong opener.",
                },
                "hook_title": "A short title",
            }
        ],
        "summary": "A test video.",
        "key_topics": ["testing"],
    }


def test_the_prompt_bundle_carries_the_transcript_and_the_json_shape():
    bundle = manual_analysis.build_prompt_bundle(TRANSCRIPT, instructions="Pick funny parts")
    assert "[00:10 - 00:20] line 010" in bundle
    assert "most_relevant_segments" in bundle and "virality" in bundle
    assert "ONE JSON object" in bundle
    assert "Pick funny parts" in bundle
    # The chat is told which hook types are allowed, not left to guess.
    for hook_type in ("question", "statement", "statistic", "story", "contrast", "none"):
        assert hook_type in bundle


def test_the_prompt_is_compact_so_free_chats_accept_it():
    # Free chats limit what can be pasted: the fixed part stays a few thousand characters.
    assert len(manual_analysis.build_prompt_bundle("")) < 5000


def test_the_prompt_states_the_clip_length_of_the_task():
    short = manual_analysis.build_prompt_bundle(TRANSCRIPT, clip_length="short")
    assert "15-30 seconds" in short
    assert "60-120 seconds" in manual_analysis.build_prompt_bundle(TRANSCRIPT, clip_length="long")


def test_the_allowed_hook_types_follow_the_model():
    assert set(manual_analysis._HOOK_TYPES) == {"question", "statement", "statistic", "story", "contrast", "none"}


def test_only_the_manual_value_is_manual():
    assert manual_analysis.is_manual_llm(" Manual ")
    assert not manual_analysis.is_manual_llm("ollama:qwen2.5:7b")
    assert not manual_analysis.is_manual_llm(None)


@pytest.mark.parametrize("wrapper", ["{}", "```json\n{}\n```", "Here you go!\n```\n{}\n```\nHope it helps", "Sure: {}"])
def test_chat_answers_with_fences_or_chatter_are_parsed(wrapper):
    raw = wrapper.replace("{}", json.dumps(answer()))
    assert manual_analysis.parse_analysis(raw).summary == "A test video."


def test_a_decoded_object_is_accepted():
    assert len(manual_analysis.parse_analysis(answer()).most_relevant_segments) == 1


@pytest.mark.parametrize(
    "raw, fragment",
    [
        ("", "empty"),
        ("no json here", "No JSON object"),
        ('{"most_relevant_segments": [', "cut off"),
        ('{"most_relevant_segments": [}', "not valid JSON"),
        ('{"summary": "x"}', "most_relevant_segments"),
        ("[1, 2]", "No JSON object"),
    ],
)
def test_unusable_answers_get_a_clear_message(raw, fragment):
    with pytest.raises(manual_analysis.ManualAnalysisError, match=fragment):
        manual_analysis.parse_analysis(raw)


def test_segments_are_grounded_on_the_transcript_like_a_models():
    analysis = manual_analysis.parse_analysis(answer(start="00:11", end="00:44"))
    final = manual_analysis.finalize_analysis(analysis, TRANSCRIPT, max_clips=5)
    segment = final.most_relevant_segments[0]
    # Timestamps snap onto transcript line boundaries.
    assert segment.start_time == "00:10" and segment.end_time in {"00:40", "00:50"}


def test_a_segment_that_cannot_be_used_is_rejected_with_the_reason():
    analysis = manual_analysis.parse_analysis(answer(start="00:10", end="00:45", text="hello"))
    with pytest.raises(manual_analysis.ManualAnalysisError, match="None of the segments"):
        manual_analysis.finalize_analysis(analysis, TRANSCRIPT, max_clips=5)


def test_a_too_short_span_is_repaired_to_a_real_clip_not_dropped():
    # The model path's repair widens a short span onto the surrounding transcript.
    analysis = manual_analysis.parse_analysis(answer(start="00:10", end="00:12", text="too short span here"))
    final = manual_analysis.finalize_analysis(analysis, TRANSCRIPT, max_clips=5)
    assert final.most_relevant_segments


def test_overlapping_segments_are_deduplicated_and_capped():
    data = answer()
    data["most_relevant_segments"] *= 5  # the same span five times
    final = manual_analysis.finalize_analysis(manual_analysis.parse_analysis(data), TRANSCRIPT, max_clips=5)
    assert len(final.most_relevant_segments) == 1


def test_manual_has_a_clear_message_where_a_model_is_needed():
    message = ai._get_missing_llm_key_error("manual", SimpleNamespace())
    assert "LLM=manual" in message and "needs a real model" in message


# --- the pipeline pauses instead of calling a model -------------------------------------


def select_kwargs(**overrides):
    values = dict(
        video_path=None, transcript=TRANSCRIPT, cached_analysis_json=None,
        performance_context=None, clip_length="auto", processing_mode="balanced",
        file_duration=180.0, progress_callback=None, should_cancel=None,
        runtime_config=SimpleNamespace(llm="manual", max_clips=5, fast_mode_max_clips=3),
        checkpoint={}, save_checkpoint=AsyncMock(),
    )
    values.update(overrides)
    return values


@pytest.fixture
def no_audio_signals(monkeypatch):
    monkeypatch.setattr(module, "build_clip_signal_summary", lambda *_: None)
    monkeypatch.setattr(module, "load_cached_transcript_data", lambda _path: None)
    monkeypatch.setattr(VideoService, "_refine_segment_openings", lambda s, *_: s)


@pytest.mark.asyncio
async def test_manual_mode_saves_the_prompt_and_pauses_without_a_model(no_audio_signals, monkeypatch):
    model = AsyncMock()
    monkeypatch.setattr(VideoService, "analyze_transcript", model)
    save = AsyncMock()
    with pytest.raises(manual_analysis.ManualAnalysisPending):
        await VideoService._select_highlight_segments(**select_kwargs(save_checkpoint=save))
    model.assert_not_awaited()
    saved = save.await_args.args[0]
    assert "[00:10 - 00:20] line 010" in saved["manual_prompt"]


@pytest.mark.asyncio
async def test_a_submitted_analysis_skips_the_pause_and_the_model(no_audio_signals, monkeypatch):
    model = AsyncMock()
    monkeypatch.setattr(VideoService, "analyze_transcript", model)
    final = manual_analysis.finalize_analysis(
        manual_analysis.parse_analysis(answer()), TRANSCRIPT, max_clips=5
    )
    checkpoint = {"analysis": final.model_dump(mode="json")}
    _parts, segments, _limit, _final = await VideoService._select_highlight_segments(
        **select_kwargs(checkpoint=checkpoint)
    )
    model.assert_not_awaited()
    assert segments and segments[0]["hook_title"] == "A short title"


@pytest.mark.asyncio
async def test_a_real_model_never_pauses(no_audio_signals, monkeypatch):
    analysis = SimpleNamespace(summary="s", key_topics=[], most_relevant_segments=[])
    monkeypatch.setattr(VideoService, "analyze_transcript", AsyncMock(return_value=analysis))
    monkeypatch.setattr(VideoService, "_build_fallback_segment",
                        lambda *a, **k: {"start_time": "00:00", "end_time": "00:30", "text": "x",
                                         "fallback_seconds": 30, "hook_type": "fallback"})
    config = SimpleNamespace(llm="ollama:qwen2.5:7b", max_clips=5, fast_mode_max_clips=3, clip_duration=30)
    await VideoService._select_highlight_segments(**select_kwargs(runtime_config=config))


# --- the endpoints -----------------------------------------------------------------------


@pytest.fixture
def paused(monkeypatch):
    task = {"id": "task-1", "user_id": "user-1", "status": "awaiting_analysis"}
    monkeypatch.setattr(tasks, "TaskService", lambda _db: SimpleNamespace())
    monkeypatch.setattr(tasks, "_require_task_owner", AsyncMock(return_value=task))
    monkeypatch.setattr(tasks, "_load_task_source_metadata", AsyncMock(return_value={}))
    stored = ("key-1", {"transcript": TRANSCRIPT, "manual_prompt": "# the prompt"})
    monkeypatch.setattr(tasks.TaskCheckpointRepository, "get_latest", AsyncMock(return_value=stored))
    save = AsyncMock()
    monkeypatch.setattr(tasks.TaskCheckpointRepository, "save", save)
    resume = AsyncMock(return_value={"job_id": "job-1"})
    monkeypatch.setattr(tasks, "resume_task", resume)
    return task, save, resume


@pytest.mark.asyncio
async def test_the_prompt_downloads_as_markdown(paused):
    response = await tasks.get_analysis_prompt("task-1", SimpleNamespace(), db=None)
    assert response.body == b"# the prompt"
    assert response.media_type.startswith("text/markdown")
    assert "attachment" in response.headers["content-disposition"]


@pytest.mark.asyncio
async def test_the_prompt_is_only_served_to_a_waiting_task(paused):
    task, _save, _resume = paused
    task["status"] = "processing"
    with pytest.raises(HTTPException) as error:
        await tasks.get_analysis_prompt("task-1", SimpleNamespace(), db=None)
    assert error.value.status_code == 409


@pytest.mark.asyncio
async def test_a_valid_answer_is_stored_as_the_analysis_and_resumes_the_task(paused):
    _task, save, resume = paused
    body = tasks.ManualAnalysisSubmission(response="```json\n" + json.dumps(answer()) + "\n```")
    result = await tasks.submit_analysis("task-1", body, SimpleNamespace(), db=None)
    assert result["segments"] == 1 and result["job_id"] == "job-1"
    stored_key, patch = save.await_args.args[2], save.await_args.args[3]
    assert stored_key == "key-1" and patch["analysis"]["most_relevant_segments"]
    resume.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        tasks.ManualAnalysisSubmission(response="not json at all"),
        tasks.ManualAnalysisSubmission(analysis={"summary": "missing segments"}),
        tasks.ManualAnalysisSubmission(analysis=answer(start="00:10", end="00:45", text="hello")),
    ],
)
async def test_an_unusable_answer_is_rejected_and_nothing_resumes(paused, body):
    _task, save, resume = paused
    with pytest.raises(HTTPException) as error:
        await tasks.submit_analysis("task-1", body, SimpleNamespace(), db=None)
    assert error.value.status_code == 422
    save.assert_not_awaited()
    resume.assert_not_awaited()


@pytest.mark.asyncio
async def test_submitting_without_an_answer_or_to_another_status_fails(paused):
    task, _save, resume = paused
    with pytest.raises(HTTPException) as empty:
        await tasks.submit_analysis("task-1", tasks.ManualAnalysisSubmission(), SimpleNamespace(), db=None)
    assert empty.value.status_code == 422
    task["status"] = "completed"
    with pytest.raises(HTTPException) as wrong:
        await tasks.submit_analysis(
            "task-1", tasks.ManualAnalysisSubmission(analysis=answer()), SimpleNamespace(), db=None
        )
    assert wrong.value.status_code == 409
    resume.assert_not_awaited()
