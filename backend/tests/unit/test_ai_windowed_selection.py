from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src import ai


def _label(seconds: int) -> str:
    minutes, secs = divmod(seconds, 60)
    return f"{minutes:02d}:{secs:02d}"


def _transcript(total_seconds: int, line_seconds: int = 10) -> str:
    return "\n".join(
        f"[{_label(start)} - {_label(start + line_seconds)}] Speaker A: Line at {start} seconds is here."
        for start in range(0, total_seconds, line_seconds)
    )


def _segment(start: str, end: str, total: int = 60, text: str = "Some useful words here.") -> ai.TranscriptSegment:
    quarter = total // 4
    return ai.TranscriptSegment(
        start_time=start,
        end_time=end,
        text=text,
        virality=ai.ViralityAnalysis(
            hook_score=quarter,
            engagement_score=quarter,
            value_score=quarter,
            shareability_score=total - 3 * quarter,
            total_score=total,
        ),
    )


def test_short_transcript_is_a_single_unchanged_window():
    transcript = _transcript(10 * 60)

    windows = ai.split_transcript_into_windows(transcript)

    assert len(windows) == 1
    assert windows[0]["transcript"] == transcript


def test_long_transcript_splits_into_overlapping_windows():
    transcript = _transcript(60 * 60)

    windows = ai.split_transcript_into_windows(transcript)

    assert [(w["start"], w["end"]) for w in windows] == [
        (0, 720), (600, 1320), (1200, 1920), (1800, 2520), (2400, 3120), (3000, 3600),
    ]
    first_lines = windows[0]["transcript"].splitlines()
    assert first_lines[0].startswith("[00:00 - 00:10]")
    assert first_lines[-1].startswith("[11:50 - 12:00]")
    # The overlap repeats the last two minutes of each window in the next one.
    assert windows[1]["transcript"].splitlines()[0].startswith("[10:00 - 10:10]")
    assert windows[-1]["transcript"].splitlines()[-1].startswith("[59:50 - 60:00]")


def test_short_trailing_window_folds_into_previous():
    windows = ai.split_transcript_into_windows(_transcript(23 * 60))

    assert [(w["start"], w["end"]) for w in windows] == [(0, 720), (600, 1380)]


def test_windowing_handles_three_digit_minutes():
    windows = ai.split_transcript_into_windows(_transcript(110 * 60))

    assert windows[-1]["end"] == 110 * 60
    assert windows[-1]["transcript"].splitlines()[-1].startswith("[109:50 - 110:00]")


def test_three_digit_minute_lines_are_parsed_and_grounded():
    transcript = "\n".join([
        "[100:00 - 100:20] Speaker A: First long sentence past the hundred minute mark.",
        "[100:20 - 100:40] Speaker A: Second sentence with the payoff.",
    ])

    lines = ai._parse_transcript_lines(transcript)
    spans = ai._parse_transcript_spans(transcript)

    assert lines[0]["start_seconds"] == 6000 and lines[1]["end_seconds"] == 6040
    assert spans[1]["end"] == 6040
    assert ai._extract_transcript_text_for_segment(lines, "100:00", "100:40") == (
        "First long sentence past the hundred minute mark. Second sentence with the payoff."
    )


def test_validation_snaps_near_miss_timestamps_instead_of_dropping():
    transcript = _transcript(120)
    lines = ai._parse_transcript_lines(transcript)
    spans = ai._parse_transcript_spans(transcript)
    segment = _segment("00:12", "00:47")

    validated = ai._validate_segments([segment], lines, spans)

    assert len(validated) == 1
    assert (validated[0].start_time, validated[0].end_time) == ("00:10", "00:50")
    assert validated[0].text.startswith("Line at 10 seconds")


def test_validation_still_drops_timestamps_far_from_any_line():
    lines = [
        {"start_time": "00:00", "end_time": "00:40", "start_seconds": 0, "end_seconds": 40, "text": "a b c"},
        {"start_time": "00:40", "end_time": "01:20", "start_seconds": 40, "end_seconds": 80, "text": "d e f"},
    ]

    assert ai._snap_bounds_to_transcript_lines(lines, 20, 60) is None


def test_repaired_bounds_past_an_hour_snap_back_to_transcript_labels():
    transcript = _transcript(70 * 60)
    lines = ai._parse_transcript_lines(transcript)
    spans = ai._parse_transcript_spans(transcript)
    # 90 seconds is too long; repair trims it and must keep MM:SS labels.
    segment = _segment("65:00", "66:30")

    validated = ai._validate_segments([segment], lines, spans)

    assert len(validated) == 1
    assert validated[0].start_time == "65:00"
    assert ai._parse_transcript_timestamp_seconds(validated[0].end_time) <= 65 * 60 + 60
    assert ":" in validated[0].end_time and validated[0].end_time.count(":") == 1


def test_dedupe_keeps_higher_scored_of_overlapping_candidates():
    weaker = _segment("01:00", "01:40", total=60)
    stronger = _segment("01:10", "01:50", total=80)
    separate = _segment("05:00", "05:40", total=50)

    kept = ai.dedupe_overlapping_segments([weaker, separate, stronger])

    assert kept == [stronger, separate]


def test_dedupe_keeps_lightly_overlapping_candidates():
    first = _segment("01:00", "01:40", total=70)
    second = _segment("01:30", "02:10", total=60)

    assert ai.dedupe_overlapping_segments([first, second]) == [first, second]


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [(5 * 60, 5), (20 * 60, 5), (45 * 60, 9), (90 * 60, 18), (200 * 60, 20)],
)
def test_final_clip_cap_scales_with_source_length(seconds, expected):
    assert ai.max_final_clips_for_duration(seconds) == expected


def test_apply_ranking_reorders_and_calibrates_scores():
    candidates = [_segment("00:00", "00:30", 90), _segment("01:00", "01:30", 80), _segment("02:00", "02:30", 70)]
    ranking = ai.CandidateRanking(ranking=[
        ai.RankedCandidate(candidate_id=2, score=88),
        ai.RankedCandidate(candidate_id=0, score=91),  # clamped to keep order monotonic
        ai.RankedCandidate(candidate_id=7, score=50),  # unknown id ignored
    ])

    reranked = ai.apply_candidate_ranking(candidates, ranking)

    assert [s.start_time for s in reranked] == ["02:00", "00:00", "01:00"]
    assert [s.virality.total_score for s in reranked] == [88, 88, 80]
    for segment in reranked:
        virality = segment.virality
        assert virality.total_score == (
            virality.hook_score + virality.engagement_score
            + virality.value_score + virality.shareability_score
        )
        assert all(0 <= score <= 25 for score in (
            virality.hook_score, virality.engagement_score,
            virality.value_score, virality.shareability_score,
        ))


async def test_rerank_failure_keeps_first_pass_order(monkeypatch):
    candidates = [_segment("00:00", "00:30", 90), _segment("01:00", "01:30", 80)]
    agent = SimpleNamespace(run=AsyncMock(side_effect=ValueError("bad output")))
    monkeypatch.setattr(ai, "get_rerank_agent", lambda: agent)

    result = await ai.rerank_candidates(candidates)

    assert result == candidates
    assert [s.virality.total_score for s in result] == [90, 80]


async def test_long_transcript_runs_windows_then_reranks(monkeypatch):
    transcript = _transcript(30 * 60)

    def window_analysis(prompt: str):
        # Each window returns a clip at its own start plus one shared moment
        # inside the first overlap, which must be deduplicated.
        start = 600 if "[10:00 - 10:10]" in prompt.split("Transcript:")[1].splitlines()[1] else 0
        if "[20:00 - 20:10]" in prompt.split("Transcript:")[1].splitlines()[1]:
            start = 1200
        segments = [_segment(_label(start + 30), _label(start + 60), total=60 + start // 60)]
        if start in (0, 600):
            segments.append(_segment("10:30", "11:00", total=40))
        return SimpleNamespace(output=ai.TranscriptAnalysis(
            most_relevant_segments=segments, summary=f"Window {start}.", key_topics=["topic", f"t{start}"],
        ))

    agent = SimpleNamespace(run=AsyncMock(side_effect=window_analysis))
    rerank_agent = SimpleNamespace(run=AsyncMock(side_effect=lambda prompt: SimpleNamespace(
        output=ai.CandidateRanking(ranking=[
            ai.RankedCandidate(candidate_id=i, score=90 - i) for i in reversed(range(prompt.count("candidate_id:")))
        ])
    )))
    monkeypatch.setattr(ai, "get_transcript_agent", lambda: agent)
    monkeypatch.setattr(ai, "get_rerank_agent", lambda: rerank_agent)

    result = await ai.get_most_relevant_parts_by_transcript(transcript)

    assert agent.run.await_count == 3
    prompts = [call.args[0] for call in agent.run.await_args_list]
    assert all("one window" in prompt and "Choose 3-5 segments total." in prompt for prompt in prompts)
    rerank_agent.run.assert_awaited_once()
    starts = [s.start_time for s in result.most_relevant_segments]
    assert sorted(starts) == ["00:30", "10:30", "20:30"]
    assert len(starts) == len(set(starts))
    assert result.key_topics == ["topic", "t0", "t600", "t1200"]
    scores = [s.virality.total_score for s in result.most_relevant_segments]
    assert scores == sorted(scores, reverse=True)


async def test_window_failures_are_tolerated_when_another_window_succeeds(monkeypatch):
    transcript = _transcript(30 * 60)
    calls = {"count": 0}

    async def flaky(prompt: str):
        calls["count"] += 1
        if "[00:00 - 00:10]" not in prompt:
            raise ValueError("schema failure")
        return SimpleNamespace(output=ai.TranscriptAnalysis(
            most_relevant_segments=[_segment("00:30", "01:00")], summary="ok", key_topics=[],
        ))

    monkeypatch.setattr(ai, "get_transcript_agent", lambda: SimpleNamespace(run=AsyncMock(side_effect=flaky)))
    monkeypatch.setattr(ai, "get_rerank_agent", lambda: (_ for _ in ()).throw(RuntimeError("no key")))

    result = await ai.get_most_relevant_parts_by_transcript(transcript)

    assert calls["count"] == 3
    assert [s.start_time for s in result.most_relevant_segments] == ["00:30"]


def test_window_signals_only_include_lines_inside_the_window():
    signals = "\n".join([
        "Deterministic clip-worthiness signals to consider before ranking:",
        "- [00:30 - 00:40] score=2.0: trigger phrase; Wait what",
        "- [15:00 - 15:10] score=1.5: question/hook; Why?",
        "- [101:00 - 101:10] score=1.0: pause before line; Late",
    ])

    early = ai._filter_clip_signals_for_window(signals, 0, 720)
    late = ai._filter_clip_signals_for_window(signals, 6000, 6720)

    assert early.splitlines() == [signals.splitlines()[0], signals.splitlines()[1]]
    assert late.splitlines()[-1].startswith("- [101:00")
    assert ai._filter_clip_signals_for_window(signals, 1000, 1100) is None


async def test_hook_times_survive_windowing_snapping_and_rerank(monkeypatch):
    transcript = _transcript(30 * 60)

    def window_analysis(prompt: str):
        first_line = prompt.split("Transcript:")[1].splitlines()[1]
        if first_line.startswith("[00:00"):
            # Near-miss bounds snap to 00:30-01:00; the hook stays inside.
            segment = _segment("00:31", "00:58", total=60)
            segment.hook_start_time, segment.hook_end_time = "00:45", "00:49"
        elif first_line.startswith("[10:00"):
            # The hook falls outside the final span, so it must be cleared.
            segment = _segment("12:00", "12:30", total=70)
            segment.hook_start_time, segment.hook_end_time = "12:40", "12:44"
        else:
            segment = _segment("20:30", "21:00", total=80)
            segment.hook_start_time, segment.hook_end_time = "20:50", "20:54"
        return SimpleNamespace(output=ai.TranscriptAnalysis(
            most_relevant_segments=[segment], summary="s", key_topics=[],
        ))

    rerank_agent = SimpleNamespace(run=AsyncMock(return_value=SimpleNamespace(
        output=ai.CandidateRanking(ranking=[
            ai.RankedCandidate(candidate_id=2, score=95),
            ai.RankedCandidate(candidate_id=1, score=85),
            ai.RankedCandidate(candidate_id=0, score=75),
        ])
    )))
    agent = SimpleNamespace(run=AsyncMock(side_effect=window_analysis))
    monkeypatch.setattr(ai, "get_transcript_agent", lambda: agent)
    monkeypatch.setattr(ai, "get_rerank_agent", lambda: rerank_agent)

    result = await ai.get_most_relevant_parts_by_transcript(transcript)

    assert all("hook_start_time" in call.args[0] for call in agent.run.await_args_list)
    rerank_agent.run.assert_awaited_once()
    by_start = {s.start_time: s for s in result.most_relevant_segments}
    assert (by_start["00:30"].hook_start_time, by_start["00:30"].hook_end_time) == ("00:45", "00:49")
    assert (by_start["12:00"].hook_start_time, by_start["12:00"].hook_end_time) == (None, None)
    assert (by_start["20:30"].hook_start_time, by_start["20:30"].hook_end_time) == ("20:50", "20:54")


@pytest.mark.parametrize(
    ("seconds", "max_clips", "expected"),
    [(5 * 60, 10, 5), (45 * 60, 10, 9), (90 * 60, 10, 10), (200 * 60, 10, 10), (90 * 60, 3, 3)],
)
def test_final_clip_cap_respects_max_clips(seconds, max_clips, expected):
    assert ai.max_final_clips_for_duration(seconds, max_clips) == expected


async def test_performance_context_shapes_windows_and_the_final_rerank(monkeypatch):
    transcript = _transcript(30 * 60)
    context = "Based on 6 published clips with audience metrics."
    agent = SimpleNamespace(run=AsyncMock(side_effect=lambda prompt: SimpleNamespace(
        output=ai.TranscriptAnalysis(
            most_relevant_segments=[_segment("00:30", "01:00"), _segment("20:30", "21:00")],
            summary="ok", key_topics=[],
        )
    )))
    rerank_agent = SimpleNamespace(run=AsyncMock(side_effect=lambda prompt: SimpleNamespace(
        output=ai.CandidateRanking(ranking=[
            ai.RankedCandidate(candidate_id=i, score=90 - i) for i in range(prompt.count("candidate_id:"))
        ])
    )))
    monkeypatch.setattr(ai, "get_transcript_agent", lambda: agent)
    monkeypatch.setattr(ai, "get_rerank_agent", lambda: rerank_agent)

    await ai.get_most_relevant_parts_by_transcript(transcript, performance_context=context)

    rerank_prompt = rerank_agent.run.await_args.args[0]
    assert context in rerank_prompt
    # Same guard as the window prompts: history only breaks ties.
    guard = "It never justifies fabricating content, stretching timestamps, or picking a weak moment."
    assert guard in rerank_prompt
    assert all(guard in call.args[0] for call in agent.run.await_args_list)
    assert context not in ai.build_candidate_rerank_prompt([_segment("00:30", "01:00")])
