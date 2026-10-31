from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.services import clip_package_service as svc
from src.services.clip_copy_service import ClipCopyError
from src.services.clip_package_service import normalize_package, route_status


def norm(raw=None, score=78, duration=30.0, breakdown=None):
    return normalize_package(raw or {}, raw_score=score, duration=duration, breakdown=breakdown)


@pytest.mark.parametrize(
    "score,status",
    [(0, "REJECTED"), (69, "REJECTED"), (70, "RESCUE_REQUIRED"), (85, "RESCUE_REQUIRED"),
     (86, "VIRAL_READY"), (100, "VIRAL_READY")],
)
def test_route_status_boundaries(score, status):
    assert route_status(score) == status
    assert norm({"clip_status": "VIRAL_READY"}, score=score)["clip_status"] == status


def test_scoring_overrides_and_projected_clamp():
    bd = {"hook": 20, "narrative_arc": 15, "emotional_trigger": 10, "pacing": 9,
          "loop_potential": 5, "penalties_applied": -15}
    raw = {"scoring": {"raw_virality_score": 5, "projected_score_after_rescue": 40,
                       "breakdown": {"hook_3s": 1}, "primary_flaw_fixed": " slow  open "}}
    out = norm(raw, score=75, breakdown=bd)["scoring"]
    assert out["raw_virality_score"] == 75 and out["projected_score_after_rescue"] == 75
    assert out["breakdown"]["hook_3s"] == 20 and out["breakdown"]["pacing_density"] == 9
    assert out["breakdown"]["penalties_applied"] == -15
    assert out["primary_flaw_fixed"] == "slow open"
    raw["scoring"]["projected_score_after_rescue"] = 400
    assert norm(raw)["scoring"]["projected_score_after_rescue"] == 100
    assert norm(raw)["scoring"]["breakdown"]["hook_3s"] == 1


def test_timestamps_clamped_and_formatted():
    edits = norm({"surgical_edits": {"new_start": "00:02.5", "new_end": "5:00"}})["surgical_edits"]
    assert edits["original_start"] == "00:00.000" and edits["original_end"] == "00:30.000"
    assert edits["new_start"] == "00:02.500" and edits["new_end"] == "00:30.000"
    assert edits["total_seconds_shaved"] == 2.5
    edits = norm({"surgical_edits": {"new_start": "4", "new_end": "28"}})["surgical_edits"]
    assert edits["new_start"] == "00:04.000" and edits["new_end"] == "00:28.000"


def test_inverted_or_unparseable_resets_to_originals():
    for start, end in (("00:20.000", "00:10.000"), ("00:10", "00:10"), ("junk", "00:10")):
        edits = norm({"surgical_edits": {"new_start": start, "new_end": end}})["surgical_edits"]
        assert (edits["new_start"], edits["new_end"]) == ("00:00.000", "00:30.000")
        assert edits["total_seconds_shaved"] == 0


def test_cut_filtering_and_shaved_recompute():
    cuts = [
        {"cut_start": "00:20", "cut_end": "00:21", "reason": "b"},
        {"cut_start": "00:10", "cut_end": "00:11.5", "reason": "a"},
        {"cut_start": "00:10.5", "cut_end": "00:12", "reason": "overlap"},
        {"cut_start": "00:01", "cut_end": "00:02", "reason": "before start"},
        {"cut_start": "00:25", "cut_end": "00:24", "reason": "inverted"},
        {"cut_start": "00:28", "cut_end": "00:29.5", "reason": "after end"},
    ]
    edits = norm({"surgical_edits": {"new_start": "00:03", "new_end": "00:27", "internal_cuts": cuts,
                                     "total_seconds_shaved": 99}})["surgical_edits"]
    assert [c["reason"] for c in edits["internal_cuts"]] == ["a", "b"]
    assert edits["internal_cuts"][0]["cut_start"] == "00:10.000"
    assert edits["total_seconds_shaved"] == 3 + 3 + 1.5 + 1


def test_cuts_capped_at_20():
    cuts = [{"cut_start": f"00:{i:02d}.0", "cut_end": f"00:{i:02d}.5", "reason": "x"} for i in range(30)]
    assert len(norm({"surgical_edits": {"internal_cuts": cuts}})["surgical_edits"]["internal_cuts"]) == 20


@pytest.mark.parametrize(
    "value,expected",
    [("00:10.000 - 00:12.000", "00:10.000 - 00:12.000"), ("00:10 - 00:13", "00:10.000 - 00:13.000"),
     ("00:10 - 00:13.5", None), ("00:12 - 00:10", None), ("00:10 - 00:40", None),
     ("00:10", None), (None, None), ("null", None)],
)
def test_cold_open_validation(value, expected):
    edits = norm({"surgical_edits": {"cold_open_tease_timestamp": value}})["surgical_edits"]
    assert edits["cold_open_tease_timestamp"] == expected


def test_hooks_and_titles():
    raw = {
        "on_screen_text_hooks": {"curiosity_gap": '  "one two three four five six seven eight nine ten" '},
        "seo_packaging": {"titles": {
            "search_curiosity_hybrid": "He did what?. ",
            "shock_browse_fyp": "😱 No way 🔥 really 😂",
            "high_stakes_value": "word " * 20,
        }},
    }
    out = norm(raw)
    assert out["on_screen_text_hooks"]["curiosity_gap"] == "one two three four five six seven eight"
    titles = out["seo_packaging"]["titles"]
    assert titles["search_curiosity_hybrid"] == "He did what?"
    assert titles["shock_browse_fyp"] == "😱 No way really"
    assert len(titles["high_stakes_value"]) <= 49 and titles["high_stakes_value"].endswith("word")


def test_hashtags_normalised_capped_and_broad_forced():
    raw = {"seo_packaging": {"hashtags": {
        "entity_specific": ["Elon Musk", "#Tesla!", "elonmusk", "#a", "#b"],
        "niche_category": ["#Tesla", "EV-Cars", "#shorts", "c", "d", "e"],
        "platform_broad": ["#viral"],
    }}}
    tags = norm(raw)["seo_packaging"]["hashtags"]
    assert tags["entity_specific"] == ["#elonmusk", "#tesla", "#a"]
    assert tags["niche_category"] == ["#evcars", "#c", "#d"]
    assert tags["platform_broad"] == ["#shorts", "#fyp"]
    assert sum(len(v) for v in tags.values()) <= 8


def test_pinned_comment_and_keywords():
    raw = {"seo_packaging": {"pinned_comment_bait": " ".join(f"w{i}" for i in range(20)),
                             "target_search_keywords": ["a b", "A B", "c", "d", "e", "f", "g"]}}
    seo = norm(raw)["seo_packaging"]
    assert len(seo["pinned_comment_bait"].split()) == 14
    assert seo["target_search_keywords"] == ["a b", "c", "d", "e", "f"]


def test_caption_rebuilt():
    raw = {"seo_packaging": {
        "description": {"engagement_hook_line": "Hook?", "seo_keyword_block": "Keywords here.",
                        "full_copy_paste_caption": "ignored"},
        "hashtags": {"entity_specific": ["Foo"], "niche_category": ["bar"]},
    }}
    assert norm(raw)["seo_packaging"]["description"]["full_copy_paste_caption"] == (
        "Hook?\nKeywords here.\n\n#foo #bar #shorts #fyp"
    )
    raw["seo_packaging"]["description"]["seo_keyword_block"] = ""
    assert norm(raw)["seo_packaging"]["description"]["full_copy_paste_caption"].startswith("Hook?\n\n#foo")


def test_rejected_blank_only_when_untouched():
    raw = {
        "on_screen_text_hooks": {"curiosity_gap": "hook"},
        "scoring": {"primary_flaw_fixed": "weak", "projected_score_after_rescue": 60},
        "seo_packaging": {"titles": {"shock_browse_fyp": "Title"}, "pinned_comment_bait": "q?",
                          "hashtags": {"entity_specific": ["x"]}, "target_search_keywords": ["k"]},
    }
    out = norm(raw, score=50)
    assert out["clip_status"] == "REJECTED"
    assert out["on_screen_text_hooks"] == {"curiosity_gap": "", "polarizing_debate": "", "high_stakes_context": ""}
    seo = out["seo_packaging"]
    assert seo["titles"]["shock_browse_fyp"] == "" and seo["pinned_comment_bait"] == ""
    assert seo["hashtags"] == {"entity_specific": [], "niche_category": [], "platform_broad": []}
    assert seo["description"]["full_copy_paste_caption"] == "" and seo["target_search_keywords"] == []
    assert out["scoring"]["primary_flaw_fixed"] == "weak"
    raw["surgical_edits"] = {"new_start": "00:05", "new_end": "00:30"}
    kept = norm(raw, score=50)
    assert kept["on_screen_text_hooks"]["curiosity_gap"] == "hook"
    assert kept["seo_packaging"]["titles"]["shock_browse_fyp"] == "Title"


def test_prompt_uses_timed_words_or_falls_back():
    words = [{"text": "Hello", "start": 0.5, "end": 0.9}, {"text": "there.", "start": 1.0, "end": 1.4},
             {"text": "Next", "start": 3.0, "end": 3.3}]
    prompt = svc.build_prompt(
        {"duration": 20, "hook_title": "HT", "virality_score": 80, "text": "x"}, words
    )
    assert "[00:00.500 - 00:01.400] Hello there." in prompt and "[00:03.000 - 00:03.300] Next" in prompt
    assert "RESCUE_REQUIRED" in prompt and "Raw virality score: 80" in prompt
    fallback = svc.build_prompt({"duration": 20, "virality_score": 90, "text": "all the words"})
    assert "[00:00.000 - 00:20.000] all the words" in fallback and "VIRAL_READY" in fallback


def test_untimed_resets_edits_but_keeps_copy():
    raw = {
        "surgical_edits": {
            "new_start": "00:03", "new_end": "00:27", "cold_open_tease_timestamp": "00:10 - 00:12",
            "internal_cuts": [{"cut_start": "00:10", "cut_end": "00:11", "reason": "x"}],
        },
        "on_screen_text_hooks": {"curiosity_gap": "hook"},
        "seo_packaging": {"titles": {"shock_browse_fyp": "Title"}},
    }
    timed = norm(raw)["surgical_edits"]
    assert timed["new_start"] == "00:03.000" and timed["internal_cuts"] and timed["total_seconds_shaved"] > 0
    out = normalize_package(raw, raw_score=78, duration=30.0, timed=False)
    edits = out["surgical_edits"]
    assert (edits["new_start"], edits["new_end"]) == ("00:00.000", "00:30.000")
    assert edits["internal_cuts"] == [] and edits["cold_open_tease_timestamp"] is None
    assert edits["total_seconds_shaved"] == 0
    assert out["on_screen_text_hooks"]["curiosity_gap"] == "hook"
    assert out["seo_packaging"]["titles"]["shock_browse_fyp"] == "Title"


def test_prompt_with_words_has_no_untimed_notice():
    words = [{"text": "Hello", "start": 0.5, "end": 0.9}]
    assert "No word timings" not in svc.build_prompt({"duration": 20, "text": "x"}, words)
    assert "No word timings" in svc.build_prompt({"duration": 20, "text": "x"})


def _fake_agent(output=None, exc=None):
    async def run(prompt):
        if exc:
            raise exc
        return SimpleNamespace(output=output)

    return SimpleNamespace(run=AsyncMock(side_effect=run))


CLIP = {"id": "c", "text": "hello world", "duration": 30.0, "virality_score": 80, "hook_title": "H"}


CLIP_WORDS = {**CLIP, "words": [{"text": "Hello", "start": 1.0, "end": 1.4}, {"text": "world.", "start": 1.5, "end": 2.0}]}


async def test_generate_with_stubbed_agent(monkeypatch):
    out = svc.ClipPackageOutput(
        clip_status="REJECTED",
        scoring=svc.Scoring(projected_score_after_rescue=92),
        surgical_edits=svc.SurgicalEdits(new_start="00:01.000", new_end="00:29.000"),
    )
    agent = _fake_agent(out)
    monkeypatch.setattr(svc, "_build_agent", lambda: agent)
    got = await svc.generate_clip_package(CLIP_WORDS)
    assert got["clip_status"] == "RESCUE_REQUIRED"
    assert got["scoring"]["projected_score_after_rescue"] == 92
    assert got["surgical_edits"]["total_seconds_shaved"] == 2.0
    assert got["generated_at"]
    prompt = agent.run.await_args.args[0]
    assert "Hello world." in prompt and "No word timings" not in prompt


async def test_generate_without_words_discards_trims(monkeypatch):
    out = svc.ClipPackageOutput(
        surgical_edits=svc.SurgicalEdits(
            new_start="00:01.000", new_end="00:29.000", cold_open_tease_timestamp="00:10 - 00:12",
            internal_cuts=[svc.InternalCut(cut_start="00:05", cut_end="00:06", reason="x")],
        ),
    )
    agent = _fake_agent(out)
    monkeypatch.setattr(svc, "_build_agent", lambda: agent)
    edits = (await svc.generate_clip_package(CLIP))["surgical_edits"]
    assert (edits["new_start"], edits["new_end"]) == ("00:00.000", "00:30.000")
    assert edits["total_seconds_shaved"] == 0 and edits["internal_cuts"] == []
    assert "No word timings" in agent.run.await_args.args[0]


async def test_cached_and_regenerate(monkeypatch):
    cached = {"clip_status": "VIRAL_READY"}
    build = AsyncMock()
    monkeypatch.setattr(svc, "_build_agent", build)
    repo = SimpleNamespace(set_clip_package=AsyncMock())
    assert await svc.get_or_generate_clip_package(None, repo, {**CLIP, "package": cached}) is cached
    build.assert_not_called()
    monkeypatch.setattr(svc, "_build_agent", lambda: _fake_agent(svc.ClipPackageOutput()))
    got = await svc.get_or_generate_clip_package("db", repo, {**CLIP, "package": cached}, True)
    repo.set_clip_package.assert_awaited_once_with("db", "c", got)


async def test_model_exception_is_typed(monkeypatch):
    monkeypatch.setattr(svc, "_build_agent", lambda: _fake_agent(exc=RuntimeError("provider-detail")))
    with pytest.raises(ClipCopyError) as e:
        await svc.generate_clip_package(CLIP)
    assert "provider-detail" not in str(e.value)


@pytest.mark.asyncio
async def test_load_clip_words_uses_local_timeline_ranges(monkeypatch, tmp_path):
    from src import youtube_source
    from src.media import timeline, transcription

    seen = {}
    monkeypatch.setattr(youtube_source, "local_ranges_for", lambda src, r: [(1.0, 2.0)])
    monkeypatch.setattr(transcription, "load_cached_transcript_data", lambda src: {"words": [1]})
    monkeypatch.setattr(
        timeline, "get_words_for_keep_ranges",
        lambda t, r, o: seen.update(ranges=r, offsets=o) or ["w"],
    )
    monkeypatch.setattr("src.clip_source_map.load_clip_output_offsets", lambda m, r: [0.0])

    class Storage:
        def local_path(self, ref, **kw):
            return tmp_path / "x.mp4"

    monkeypatch.setattr("src.media_storage.MediaStorage", Storage)
    service = SimpleNamespace(
        _get_clip_source_ranges=lambda clip: [(500.0, 510.0)],
        config=SimpleNamespace(default_processing_mode="fast"),
        _media_limits=AsyncMock(return_value=("1080p", None)),
        _build_cache_key=lambda *a: "k",
        cache_repo=SimpleNamespace(get_cache=AsyncMock(return_value={"video_path": "v"})),
    )
    words = await svc.load_clip_words(service, None, {"source_url": "u"}, {"file_path": "c"})
    assert words == ["w"]
    assert seen["ranges"] == [(1.0, 2.0)]
    assert seen["offsets"] == [0.0]
