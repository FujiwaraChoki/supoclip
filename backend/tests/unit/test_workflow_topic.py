from types import SimpleNamespace

import pytest

from src.ai import CandidateRanking, RankedCandidate
from src.workflows import topic as topic_module
from src.workflows.durations import clip_budget
from src.workflows.errors import WorkflowError
from src.workflows.schemas import ChapterRelevance, TopicExtraction, TopicLocate, TopicMoment
from src.workflows.topic import (
    _Candidate,
    dedupe,
    fold,
    gate_moment,
    hit_units,
    keyword_matchers,
    pack_windows,
    plan_topic,
    recall_regions,
    topic_not_found,
)
from src.workflows.units import build_units, format_clock

from workflow_planner_fakes import ScriptedModel, chapter, make_outline, plan_context, plan_input, story_data

PRICING = {
    20: "We changed our pricing twice that year",
    60: "Pricing is the hardest decision a founder makes",
    61: "He told me pricing was about confidence",
    150: "The pricing page finally converted visitors",
}


def _locate(keywords=("pricing", "price"), chapters=(), suggested=("hiring", "fundraising")):
    return TopicLocate(
        interpretation="How they set prices", canonical="Pricing", counts=["price changes"],
        does_not_count=["discount codes"], keywords=list(keywords),
        chapters=[ChapterRelevance(chapter=c, relevance=r) for c, r in chapters], suggested_topics=list(suggested),
    )


def _moment(first, last, evidence=None, relevance=90, standalone=80, title="Why pricing scares founders", scores=(20, 18, 20, 17)):
    hook, engagement, value, share = scores
    return TopicMoment(
        first_unit=first, last_unit=last, evidence_unit=evidence or first, relevance=relevance, standalone=standalone,
        hook_title=title, hook_type="statement", hook_score=hook, engagement_score=engagement, value_score=value,
        shareability_score=share, why="Explains how they chose a price",
    )


def _answering(moments):
    """Extraction that returns each moment only from the window showing its evidence."""
    return lambda prompt: TopicExtraction(
        window_notes="", moments=[m for m in moments if f"\n#{m.evidence_unit} " in "\n" + prompt]
    )


def _ranking(order, scores):
    return CandidateRanking(ranking=[RankedCandidate(candidate_id=i, score=s, reason="") for i, s in zip(order, scores)])


def test_fold_and_keywords_match_meaning_not_accents():
    assert fold("Préços DEMAIS") == "precos demais"
    matchers = keyword_matchers(["preço", "seed round", "x"], no_space=False)
    assert len(matchers) == 2
    index = build_units(
        story_data(3, texts={1: "O preco subiu muito", 2: "We closed a seed  round fast", 3: "Priceless advice here"}), ""
    )
    assert hit_units(index, matchers) == [1, 2]
    assert hit_units(index, keyword_matchers(["price"], no_space=False)) == []


def test_no_space_languages_match_substrings():
    index = build_units(
        {"words": [{"text": "我们的钱不够。", "start": 0, "end": 900, "speaker": "A"},
                   {"text": "后来好了。", "start": 1500, "end": 2000, "speaker": "A"}], "language_code": "zh"},
        "",
    )
    assert hit_units(index, keyword_matchers(["钱"], no_space=True)) == [1]


def test_regions_surround_hits_and_merge_with_relevant_chapters():
    index = build_units(story_data(200), "")
    regions = recall_regions(index, [20, 24, 150], [(100, 110)])
    # Hits 20 and 24 share one ±90 s region; the chapter keeps its own.
    assert len(regions) == 3
    first = regions[0]
    assert first[0] <= 20 and first[1] >= 24
    assert index.unit(20).start - index.unit(first[0]).start <= 95


def test_windows_pack_whole_regions_up_to_twelve_minutes():
    index = build_units(story_data(400), "")
    windows = pack_windows(index, [(1, 30), (60, 90), (120, 330)])
    durations = []
    for window in windows:
        runs, start = [], window[0]
        for previous, uid in zip(window, window[1:] + [None]):
            if uid != previous + 1:
                runs.append((start, previous))
                start = uid
        durations.append(sum(index.unit(b).end - index.unit(a).start for a, b in runs))
    assert all(duration <= 12 * 60 + 1 for duration in durations)
    covered = set().union(*map(set, windows))
    assert set(range(1, 31)) <= covered and set(range(120, 331)) <= covered
    # The 16-minute region is split with about a minute of overlap.
    long_pieces = [window for window in windows if 330 in window or 200 in window]
    assert len(windows) >= 3 and len(long_pieces) >= 1


def test_gate_applies_the_relevance_standalone_and_evidence_bars():
    index = build_units(story_data(80, texts={61: "He told me pricing was about confidence"}), "")
    budget = clip_budget("auto")
    window = list(range(1, 81))
    assert gate_moment(index, _moment(10, 17), window, budget, "en", None) is not None
    assert gate_moment(index, _moment(10, 17, relevance=69), window, budget, "en", None) is None
    assert gate_moment(index, _moment(10, 17, standalone=49), window, budget, "en", None) is None
    assert gate_moment(index, _moment(10, 17, evidence=30), window, budget, "en", None) is None
    # Opening on "He ..." needs a clearly standalone moment.
    assert gate_moment(index, _moment(61, 68, standalone=60), window, budget, "en", None) is None
    assert gate_moment(index, _moment(61, 68, standalone=70), window, budget, "en", None) is not None


def test_gate_fits_the_clip_length_and_stays_on_shown_units():
    index = build_units(story_data(80), "")
    budget = clip_budget("auto")
    short = gate_moment(index, _moment(10, 11), list(range(1, 81)), budget, "en", None)
    assert short is not None and index.speech_seconds(short.first, short.last) >= budget.ideal_min
    window = list(range(1, 15)) + list(range(30, 40))
    crossing = gate_moment(index, _moment(5, 35, evidence=8), window, budget, "en", None)
    assert crossing is not None and crossing.last < 30


def _candidate(first, last, relevance, subtotal=60):
    return _Candidate(first, last, first, relevance, 80, "t", "none", subtotal // 4, subtotal // 4, subtotal // 4, subtotal // 4, "")


def test_dedupe_keeps_the_more_relevant_of_overlapping_moments():
    kept = dedupe([_candidate(10, 20, 80), _candidate(14, 22, 95), _candidate(40, 50, 75)])
    assert [(c.first, c.relevance) for c in kept] == [(14, 95), (40, 75)]


def _topic_setup(monkeypatch, answers, *, max_clips=15, sentences=200, texts=None, **ctx):
    monkeypatch.setattr(topic_module, "get_config", lambda: SimpleNamespace(workflow_topic_max_clips=max_clips))
    data = story_data(sentences, texts=PRICING if texts is None else texts)
    outline = make_outline(chapters=[chapter(100), chapter(sentences)], key_topics=["hiring", "pricing", "growth"])
    model = ScriptedModel(answers).install(monkeypatch)
    return plan_input(plan_context("topic", data, topic="pricing", **ctx), outline), model


@pytest.mark.asyncio
async def test_topic_finds_ranks_and_maps_every_strong_moment(monkeypatch):
    extraction = TopicExtraction(
        window_notes="",
        moments=[_moment(18, 25, evidence=20), _moment(58, 66, evidence=60, title="Founders fear this decision"),
                 _moment(148, 155, evidence=150, relevance=75), _moment(30, 37, relevance=40)],
    )
    pi, model = _topic_setup(
        monkeypatch,
        {"TopicLocate": [_locate()], "TopicExtraction": _answering(extraction.moments),
         "CandidateRanking": [_ranking([2, 0, 1], [90, 70, 40])]},
        performance_context="Question hooks win",
    )

    segments, plan = await plan_topic(pi)

    assert model.schemas()[0] == "TopicLocate" and model.schemas()[-1] == "CandidateRanking"
    rerank = model.calls[-1]
    assert "AS A CLIP ABOUT Pricing" in rerank.system and "Question hooks win" in rerank.prompt
    assert [s["workflow_meta"]["topic_relevance"] for s in segments] == [75, 90]  # rank order; 40 is below the bar
    assert segments[0]["virality_score"] == 90 and segments[1]["virality_score"] == 70
    first = segments[0]
    assert first["planned"] and not first["required"] and first["workflow_role"] == "topic"
    assert first["max_seconds"] == 65.0 and first["junction_fades"] == [] and len(first["source_ranges"]) == 1
    assert first["relevance_score"] == 0.75 and first["hook_type"] == "statement"
    assert first["workflow_meta"]["topic"] == "Pricing"
    assert plan["topic"] == {"query": "pricing", "canonical": "Pricing", "coverage": "thin", "found": 2,
                             "suggested_topics": ["hiring", "fundraising", "growth"]}
    assert plan["title"] == "Pricing"
    assert {"code": "TOPIC_THIN", "params": {"count": 2, "found": 2}} in plan["notes"]
    extract_call = next(call for call in model.calls if call.schema == "TopicExtraction")
    assert not extract_call.structural and "Question hooks win" not in extract_call.prompt


@pytest.mark.asyncio
async def test_cap_and_reserve_follow_the_ranking(monkeypatch):
    moments = [_moment(start, start + 7, evidence=start + 1) for start in (10, 30, 50, 70, 90, 110, 130)]
    pi, _ = _topic_setup(
        monkeypatch,
        {"TopicLocate": [_locate(keywords=["w5"])], "TopicExtraction": _answering(moments),
         "CandidateRanking": [_ranking(range(7), [95, 90, 85, 80, 75, 70, 65])]},
        max_clips=2,
    )

    segments, plan = await plan_topic(pi)

    assert len(segments) == 6
    assert [bool(s["workflow_meta"].get("reserve")) for s in segments] == [False, False, True, True, True, True]
    assert plan["topic"]["found"] == 2 and plan["topic"]["coverage"] == "thin"


@pytest.mark.asyncio
async def test_a_failed_rerank_keeps_relevance_order(monkeypatch):
    moments = [_moment(10, 17, relevance=72), _moment(30, 37, relevance=95), _moment(50, 57, relevance=88)]
    pi, _ = _topic_setup(
        monkeypatch,
        {"TopicLocate": [_locate(keywords=["w5"])], "TopicExtraction": _answering(moments),
         "CandidateRanking": [RuntimeError("rerank down")]},
    )

    segments, plan = await plan_topic(pi)

    assert [s["workflow_meta"]["topic_relevance"] for s in segments] == [95, 88, 72]
    assert plan["topic"]["coverage"] == "strong"


@pytest.mark.asyncio
async def test_no_mention_anywhere_is_topic_not_found_with_suggestions(monkeypatch):
    pi, model = _topic_setup(monkeypatch, {"TopicLocate": [_locate(keywords=["crypto"])]}, texts={})

    with pytest.raises(WorkflowError) as error:
        await plan_topic(pi)

    assert error.value.code == "topic_not_found"
    assert error.value.message == 'This video doesn\'t really talk about "pricing". It\'s mostly about hiring, fundraising, growth.'
    assert error.value.details == {"topic": "pricing", "mentions": [], "suggested_topics": ["hiring", "fundraising", "growth"]}
    assert model.schemas() == ["TopicLocate"]


@pytest.mark.asyncio
async def test_passing_mentions_without_moments_name_where_they_are(monkeypatch):
    pi, _ = _topic_setup(
        monkeypatch,
        {"TopicLocate": [_locate()], "TopicExtraction": lambda prompt: TopicExtraction(window_notes="", moments=[])},
    )

    with pytest.raises(WorkflowError) as error:
        await plan_topic(pi)

    index = pi.index
    expected = [format_clock(index.unit(uid).start) for uid in (20, 60, 150)]  # 61 is too close to 60
    message = error.value.message
    assert message.startswith(f'We found only passing mentions of "pricing" (at {", ".join(expected)}), not full moments.')
    assert error.value.details["mentions"] == expected


def test_not_found_without_suggestions_still_reads_well():
    error = topic_not_found("tax", [], [])
    assert error.message == 'This video doesn\'t really talk about "tax".'


@pytest.mark.asyncio
async def test_relevant_chapters_are_searched_even_without_keyword_hits(monkeypatch):
    seen = []

    def extract(prompt):
        seen.append(prompt)
        return TopicExtraction(window_notes="", moments=[_moment(102, 109, evidence=104)])

    pi, _ = _topic_setup(
        monkeypatch,
        {"TopicLocate": [_locate(keywords=["crypto"], chapters=[(2, 3)])], "TopicExtraction": extract},
        texts={},
    )

    segments, _ = await plan_topic(pi)

    assert len(segments) == 1 and "#101 " in seen[0]


@pytest.mark.asyncio
async def test_a_window_that_fails_twice_is_analysis_incomplete(monkeypatch):
    pi, _ = _topic_setup(
        monkeypatch,
        {"TopicLocate": [_locate()], "TopicExtraction": lambda prompt: RuntimeError("model down")},
    )
    with pytest.raises(WorkflowError) as error:
        await plan_topic(pi)
    assert error.value.code == "analysis_incomplete"
