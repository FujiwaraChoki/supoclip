import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

import src.broll as broll
from src.broll import (
    BRollSession,
    choose_video,
    max_cues_for_duration,
    pick_video_file,
    plan_clip_broll,
    search_pexels_videos,
    select_cues,
    shift_broll_placements,
    timed_lines_from_words,
)
from src.clip_source_map import (
    load_clip_broll,
    load_clip_caption_settings,
    save_clip_broll,
    save_clip_caption_settings,
    save_clip_source_ranges,
)
from src.media.broll_overlay import broll_overlay_graph, normalize_broll_overlays


requires_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg is required")


def _solid(path: Path, color: str, seconds: float, size: str):
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-y",
            "-f", "lavfi", "-i", f"color=c={color}:s={size}:r=30:d={seconds}",
            "-f", "lavfi", "-i", f"sine=d={seconds}",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path),
        ],
        check=True,
    )


def _pixel(path: Path, at: float) -> tuple[int, int, int]:
    raw = subprocess.run(
        [
            "ffmpeg", "-v", "error", "-ss", f"{at}", "-i", str(path),
            "-frames:v", "1", "-vf", "scale=1:1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
        ],
        check=True,
        capture_output=True,
    ).stdout
    return raw[0], raw[1], raw[2]


def _file(width, height, quality=None, link=None):
    return {
        "quality": quality,
        "width": width,
        "height": height,
        "file_type": "video/mp4",
        "link": link or f"https://videos.example/{width}x{height}.mp4",
    }


def _video(video_id, duration=10, width=1080, height=1920, files=None):
    return {
        "id": video_id,
        "duration": duration,
        "width": width,
        "height": height,
        "url": f"https://www.pexels.com/video/{video_id}/",
        "user": {"name": "Some Filmmaker"},
        "video_files": files if files is not None else [_file(1080, 1920)],
    }


@pytest.fixture
def pexels_config(monkeypatch, tmp_path):
    config = SimpleNamespace(pexels_api_key="test-key", temp_dir=str(tmp_path))
    monkeypatch.setattr(broll, "get_config", lambda: config)
    monkeypatch.setattr(broll, "_search_cache", {})
    monkeypatch.setattr(broll, "_rate_limited_until", 0.0)
    return config


# --- file and video choice -------------------------------------------------


def test_pick_video_file_uses_real_dimensions_not_quality_labels():
    # Newer Pexels uploads have no quality label; "hd" covers 720p and 1080p.
    video = _video(
        1,
        files=[
            _file(720, 1280, "hd"),
            _file(360, 640),
            _file(2160, 3840, "uhd"),
            _file(1080, 1920, None),
        ],
    )

    chosen = pick_video_file(video, 1080, "portrait")

    assert (chosen["width"], chosen["height"]) == (1080, 1920)


def test_pick_video_file_matches_a_720p_plan_and_falls_back_to_largest():
    video = _video(1, files=[_file(540, 960), _file(720, 1280), _file(1080, 1920)])
    assert pick_video_file(video, 720, "portrait")["height"] == 1280

    small_only = _video(2, files=[_file(360, 640), _file(540, 960)])
    assert pick_video_file(small_only, 1080, "portrait")["height"] == 960


def test_pick_video_file_prefers_the_requested_orientation():
    video = _video(1, files=[_file(1920, 1080), _file(1080, 1920)])
    assert pick_video_file(video, 1080, "landscape")["width"] == 1920
    assert pick_video_file(video, 1080, "portrait")["width"] == 1080


def test_choose_video_skips_used_short_and_misoriented_results():
    videos = [
        _video(1),
        _video(2, duration=2),
        _video(3, width=1920, height=1080),
        _video(4),
    ]

    assert choose_video(videos, 3.0, "portrait", used_ids={1})["id"] == 4


# --- planning ---------------------------------------------------------------


def _words(text, start=0.0, step=0.4):
    words = []
    for index, token in enumerate(text.split()):
        words.append({"text": token, "start": start + index * step, "end": start + index * step + 0.3})
    return words


def test_timed_lines_break_on_sentences_and_pauses():
    words = _words("I was stuck. So I tried") + _words("biking to work", start=5.0)

    lines = timed_lines_from_words(words).splitlines()

    assert lines == ["[0.0] I was stuck.", "[1.2] So I tried", "[5.0] biking to work"]


def test_select_cues_snaps_spaces_and_caps():
    words = _words("one two three four five six seven eight nine ten " * 8)  # 32s
    cue = lambda start, duration=3.0, query="mountain summit": SimpleNamespace(
        start=start, duration=duration, query=query, fallback_query="mountain"
    )

    selected = select_cues(
        [
            cue(0.5),  # pushed past the opening seconds
            cue(4.0),  # overlaps the first after the push
            cue(12.15, duration=9),  # snapped to a word, capped at 4s
            cue(30.0),  # runs into the final second
            cue(20.0, query="  "),  # no query
        ],
        words,
        clip_duration=32.0,
        max_cues=3,
    )

    assert [c["start"] for c in selected] == [3.0, 12.0 - 0.1]
    assert selected[1]["duration"] == 4.0
    assert all(c["query"] == "mountain summit" for c in selected)


def test_select_cues_limits_total_coverage():
    words = _words("word " * 40)
    cues = [SimpleNamespace(start=s, duration=4.0, query="city", fallback_query="") for s in (3, 9, 15)]

    selected = select_cues(cues, words, clip_duration=16.0, max_cues=3)

    assert sum(c["duration"] for c in selected) <= 16.0 * broll.MAX_COVERAGE


def test_max_cues_scales_with_clip_length():
    assert max_cues_for_duration(10) == 1
    assert max_cues_for_duration(36) == 3
    assert max_cues_for_duration(90) == broll.MAX_CUES_PER_CLIP


@pytest.mark.asyncio
async def test_plan_clip_broll_sources_unique_footage_across_clips(pexels_config, monkeypatch):
    async def fake_plan(lines, duration, max_cues, earliest):
        return [SimpleNamespace(start=4.0, duration=3.0, query="mountain summit", fallback_query="mountain")]

    searched = []

    async def fake_search(query, orientation):
        searched.append(query)
        return [_video(11), _video(12)]

    async def fake_ensure(placement):
        return Path("/tmp/footage.mp4")

    monkeypatch.setattr("src.ai.plan_broll_cues", fake_plan)
    monkeypatch.setattr(broll, "search_pexels_videos", fake_search)
    monkeypatch.setattr(broll, "ensure_broll_file", fake_ensure)
    words = _words("we climbed the highest mountain in Africa last year " * 3)
    session = BRollSession()

    first = await plan_clip_broll(words, 12.0, session=session)
    second = await plan_clip_broll(words, 12.0, session=session)

    assert [p["pexels_id"] for p in first + second] == [11, 12]
    assert first[0]["file_url"].endswith("1080x1920.mp4")
    assert first[0]["credit"] == "Some Filmmaker"
    assert first[0]["start"] == pytest.approx(3.9)


@pytest.mark.asyncio
async def test_plan_clip_broll_tries_the_fallback_query(pexels_config, monkeypatch):
    async def fake_plan(*_args):
        return [SimpleNamespace(start=4.0, duration=3.0, query="obscure thing", fallback_query="desk")]

    async def fake_search(query, orientation):
        return [_video(7)] if query == "desk" else []

    async def fake_ensure(placement):
        return Path("/tmp/footage.mp4")

    monkeypatch.setattr("src.ai.plan_broll_cues", fake_plan)
    monkeypatch.setattr(broll, "search_pexels_videos", fake_search)
    monkeypatch.setattr(broll, "ensure_broll_file", fake_ensure)

    placements = await plan_clip_broll(_words("word " * 30), 12.0)

    assert [p["query"] for p in placements] == ["desk"]


@pytest.mark.asyncio
async def test_plan_clip_broll_never_fails_the_clip(pexels_config, monkeypatch):
    async def broken_plan(*_args):
        raise RuntimeError("model down")

    monkeypatch.setattr("src.ai.plan_broll_cues", broken_plan)

    assert await plan_clip_broll(_words("word " * 30), 12.0) == []


@pytest.mark.asyncio
async def test_plan_clip_broll_is_off_without_a_pexels_key(pexels_config, monkeypatch):
    pexels_config.pexels_api_key = None

    async def must_not_plan(*_args):
        raise AssertionError("planned without a key")

    monkeypatch.setattr("src.ai.plan_broll_cues", must_not_plan)

    assert await plan_clip_broll(_words("word " * 30), 12.0) == []


# --- relevance and tone -----------------------------------------------------

# Real Pexels results from the 2026-10-27 audit (Huberman Lab sleep clip, #293).
DEPRESSED_ON_BED = "https://www.pexels.com/video/a-depressed-woman-lying-on-bed-with-pills-7279741/"
ORANGE_HAT = "https://www.pexels.com/video/a-stylish-man-in-street-fashion-wear-8126383/"
THERAPIST = "https://www.pexels.com/video/therapist-writing-notes-7698897/"
TREES = "https://www.pexels.com/video/sunlight-streaming-through-lush-green-tree-branches-32109629/"
# What the speaker says around the "dark bedroom at night" cutaway.
MELATONIN_LINE = (
    "inhibits the pineal. It prevents it from releasing melatonin. Darkness allows "
    "the pineal to release melatonin. So the pineal is not"
)


def _described(video_id, url, **extra):
    return {**_video(video_id), "url": url, **extra}


def test_footage_description_reads_the_pexels_slug_and_tags():
    video = _described(7279741, DEPRESSED_ON_BED, tags=["bed", {"name": "night"}])

    assert broll.footage_description(video) == "bed night a depressed woman lying on bed with pills"
    assert broll.footage_description(_video(5)) == ""


def test_footage_with_a_sensitive_tone_is_rejected_for_a_neutral_line():
    video = _described(7279741, DEPRESSED_ON_BED)

    assert broll.footage_mismatch(video, "dark bedroom at night bed", MELATONIN_LINE) == (
        "implies distress, medical"
    )


def test_sensitive_footage_is_allowed_when_the_speaker_talks_about_it():
    video = _described(7279741, DEPRESSED_ON_BED)
    context = "when I was depressed I could not sleep and the doctor gave me sleeping pills"

    assert broll.footage_mismatch(video, "woman lying on bed", context) is None


def test_footage_sharing_no_descriptive_word_with_the_query_is_off_topic():
    hat = _described(8126383, ORANGE_HAT)
    therapist = _described(7698897, THERAPIST)

    # "person" and "wearing" say nothing about the picture.
    assert broll.footage_mismatch(hat, "person wearing orange glasses") == "off-topic"
    # A "light therapy lamp" is not a therapist, whatever the shared stem.
    assert broll.footage_mismatch(therapist, "bright light therapy lamp") is not None
    assert broll.footage_mismatch(_described(1, TREES), "sunlight through trees") is None
    # Results without a description cannot be judged and stay usable.
    assert broll.footage_mismatch(_video(2), "sunlight through trees") is None


def test_choose_video_skips_wrong_tone_and_prefers_the_closest_match():
    videos = [
        _described(7279741, DEPRESSED_ON_BED),
        _described(2, "https://www.pexels.com/video/cozy-bedroom-2/"),
        _described(3, "https://www.pexels.com/video/dark-bedroom-at-night-with-lamp-off-3/"),
    ]

    chosen = choose_video(
        videos, 3.0, "portrait", query="dark bedroom at night", context=MELATONIN_LINE
    )

    assert chosen["id"] == 3
    assert choose_video(videos[:1], 3.0, "portrait", query="dark bedroom at night") is None


@pytest.mark.asyncio
async def test_plan_clip_broll_leaves_a_neutral_line_uncovered_rather_than_misleading(
    pexels_config, monkeypatch
):
    async def fake_plan(*_args):
        return [
            SimpleNamespace(start=4.0, duration=3.0, query="dark bedroom at night", fallback_query="bedroom"),
            SimpleNamespace(start=9.5, duration=3.0, query="sunlight through trees", fallback_query="sunlight"),
        ]

    async def fake_search(query, orientation):
        if query in ("dark bedroom at night", "bedroom"):
            return [_described(7279741, DEPRESSED_ON_BED)]
        return [_described(32109629, TREES)]

    async def fake_ensure(placement):
        return Path("/tmp/footage.mp4")

    monkeypatch.setattr("src.ai.plan_broll_cues", fake_plan)
    monkeypatch.setattr(broll, "search_pexels_videos", fake_search)
    monkeypatch.setattr(broll, "ensure_broll_file", fake_ensure)
    words = _words(f"Sunlight {MELATONIN_LINE} {MELATONIN_LINE}", step=0.3)

    placements = await plan_clip_broll(words, 24.0)

    assert [p["pexels_id"] for p in placements] == [32109629]


def test_everyday_words_sharing_a_sensitive_stem_are_not_flagged():
    # "pillow" is bedding, not medication; "hospitality" is not a hospital.
    pillow = _described(9, "https://www.pexels.com/video/woman-sleeping-on-a-white-pillow-9/")
    hotel = _described(10, "https://www.pexels.com/video/hospitality-staff-at-a-hotel-desk-10/")

    assert broll.sensitive_themes("woman sleeping on a white pillow") == set()
    assert broll.footage_mismatch(pillow, "person sleeping pillow", MELATONIN_LINE) is None
    assert broll.footage_mismatch(hotel, "hotel desk") is None


def test_a_compound_still_matches_its_first_word():
    texting = _described(11, "https://www.pexels.com/video/a-woman-texting-while-lying-on-the-bed-11/")

    # The audit's "bedroom night phone scrolling" cue picked this bed shot.
    assert broll.footage_mismatch(texting, "bedroom night phone scrolling") is None
    assert broll.footage_mismatch(_described(1, TREES), "sun through trees") is None


def test_choose_video_uses_a_result_it_cannot_judge(monkeypatch):
    def broken(*_args, **_kwargs):
        raise TypeError("unexpected metadata")

    monkeypatch.setattr(broll, "footage_mismatch", broken)

    chosen = choose_video([_described(1, TREES)], 3.0, "portrait", query="sunlight through trees")

    assert chosen["id"] == 1


def test_cue_context_is_the_speech_around_the_cutaway():
    words = _words("one two three four five six seven eight nine ten", step=1.0)

    assert broll.cue_context(words, 6.0, 1.0) == "two three four five six seven eight nine ten"


# --- Pexels client ---------------------------------------------------------


def _mock_pexels(monkeypatch, handler):
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        broll.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )


@pytest.mark.asyncio
async def test_search_caches_results(pexels_config, monkeypatch):
    calls = []

    def handler(request):
        calls.append(request)
        assert request.headers["Authorization"] == "test-key"
        assert request.url.params["orientation"] == "portrait"
        return httpx.Response(200, json={"videos": [_video(1)]})

    _mock_pexels(monkeypatch, handler)

    assert (await search_pexels_videos("coffee shop"))[0]["id"] == 1
    assert (await search_pexels_videos("Coffee  Shop"))[0]["id"] == 1
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_rate_limit_pauses_further_searches(pexels_config, monkeypatch):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(429, headers={"X-Ratelimit-Reset": "9999999999"})

    _mock_pexels(monkeypatch, handler)

    assert await search_pexels_videos("coffee") == []
    assert await search_pexels_videos("mountain") == []
    assert len(calls) == 1


@requires_ffmpeg
@pytest.mark.asyncio
async def test_ensure_broll_file_downloads_once(pexels_config, monkeypatch, tmp_path):
    calls = []
    footage = tmp_path / "footage.mp4"
    _solid(footage, "blue", 1, "64x64")
    video_bytes = footage.read_bytes()

    def handler(request):
        calls.append(request)
        return httpx.Response(200, content=video_bytes)

    _mock_pexels(monkeypatch, handler)
    placement = {"pexels_id": 5, "width": 1080, "height": 1920, "file_url": "https://videos.example/a.mp4"}

    first = await broll.ensure_broll_file(placement)
    second = await broll.ensure_broll_file(placement)

    assert first == second and first.read_bytes() == video_bytes
    assert first.parent == Path(pexels_config.temp_dir) / "broll_cache"
    assert len(calls) == 1
    assert not list(first.parent.glob("*.part"))


@pytest.mark.asyncio
async def test_failed_download_leaves_nothing_behind(pexels_config, monkeypatch):
    _mock_pexels(monkeypatch, lambda request: httpx.Response(404))
    placement = {"pexels_id": 6, "width": 1080, "height": 1920, "file_url": "https://videos.example/b.mp4"}

    assert await broll.ensure_broll_file(placement) is None
    assert not any((Path(pexels_config.temp_dir) / "broll_cache").iterdir())


# --- persistence -------------------------------------------------------------


def test_shift_broll_placements_for_trims_and_splits():
    placements = [{"start": 4.0, "duration": 3.0}, {"start": 15.0, "duration": 3.0}]

    assert shift_broll_placements(placements, 2.0, 10.0) == [{"start": 2.0, "duration": 3.0}]
    # The second cutaway now starts at 0 and the first no longer fits.
    assert shift_broll_placements(placements, 15.0, 10.0) == [{"start": 0.0, "duration": 3.0}]
    # Merge offsets move placements later.
    assert shift_broll_placements(placements, -10.0, 40.0)[0]["start"] == 14.0


def test_broll_sidecar_survives_source_range_rewrites(tmp_path):
    clip = tmp_path / "clip.mp4"
    save_clip_source_ranges(clip, [(10.0, 20.0)])
    save_clip_caption_settings(clip, {"position_y": 0.7})
    save_clip_broll(clip, [{"start": 4.0, "duration": 3.0, "pexels_id": 1}])

    # A partial download's timeline restore rewrites the ranges in place.
    save_clip_source_ranges(clip, [(110.0, 120.0)])

    assert load_clip_broll(clip) == [{"start": 4.0, "duration": 3.0, "pexels_id": 1}]
    assert load_clip_caption_settings(clip) == {"position_y": 0.7}

    save_clip_broll(clip, [])
    assert load_clip_broll(clip) == []


def test_save_clip_broll_without_placements_creates_no_sidecar(tmp_path):
    clip = tmp_path / "clip.mp4"
    save_clip_broll(clip, [])
    assert not clip.with_suffix(".source_map.json").exists()


# --- compositing -------------------------------------------------------------


def test_normalize_broll_overlays_drops_missing_short_and_overlapping(tmp_path):
    footage = tmp_path / "a.mp4"
    footage.write_bytes(b"x")

    overlays = normalize_broll_overlays(
        [
            {"path": footage, "start": 6.0, "duration": 3.0},
            {"path": footage, "start": 4.0, "duration": 3.0},
            {"path": tmp_path / "missing.mp4", "start": 12.0, "duration": 2.0},
            {"path": footage, "start": 9.8, "duration": 3.0},
        ],
        clip_duration=10.0,
    )

    assert [(o["start"], o["duration"]) for o in overlays] == [(4.0, 3.0)]


def test_overlay_graph_dissolves_each_cutaway_in_place():
    graph, label = broll_overlay_graph(
        "[base]", [{"path": "a", "start": 4.0, "duration": 3.0}], 1080, 1920
    )

    assert label == "[withbroll0]"
    assert "setpts=PTS-STARTPTS+4.000/TB" in graph
    assert "fade=t=in:st=4.000:d=0.250:alpha=1" in graph
    assert "fade=t=out:st=6.750:d=0.250:alpha=1" in graph
    assert "settb=AVTB,trim=start=0.000:duration=3.100" in graph
    assert "[base][broll0]overlay=0:0:eof_action=repeat:enable='between(t,4.000,7.000)'" in graph




@requires_ffmpeg
def test_render_composites_broll_at_its_time(tmp_path):
    from src.media.reframing import render_reframed_clip_ffmpeg

    main = tmp_path / "main.mp4"
    footage = tmp_path / "footage.mp4"
    output = tmp_path / "out.mp4"
    _solid(main, "red", 6, "320x240")
    _solid(footage, "blue", 5, "180x320")

    ok, width, height = render_reframed_clip_ffmpeg(
        main,
        output,
        "original",
        broll_overlays=[{"path": footage, "start": 2.0, "duration": 2.0}],
    )

    assert ok and (width, height) == (320, 240)
    before, during, after = _pixel(output, 1.0), _pixel(output, 3.0), _pixel(output, 5.0)
    assert before[0] > 200 and before[2] < 60
    assert during[2] > 200 and during[0] < 60
    assert after[0] > 200 and after[2] < 60
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(output)],
        capture_output=True, text=True, check=True,
    ).stdout.split()
    assert sorted(probe) == ["audio", "video"]


def test_prune_drops_stale_footage_then_oldest_over_the_cap(pexels_config):
    import os
    import time

    cache = Path(pexels_config.temp_dir) / "broll_cache"
    cache.mkdir()
    now = time.time()
    for name, age, size in (("pexels_1_a.mp4", 3 * 86400, 10), ("pexels_2_a.mp4", 300, 10), ("pexels_3_a.mp4", 60, 10)):
        path = cache / name
        path.write_bytes(b"x" * size)
        os.utime(path, (now - age, now - age))

    broll.prune_broll_cache(max_bytes=15, in_use_seconds=0)

    assert sorted(p.name for p in cache.iterdir()) == ["pexels_3_a.mp4"]


def test_prune_never_evicts_footage_a_render_is_about_to_use(pexels_config):
    import os
    import time

    cache = Path(pexels_config.temp_dir) / "broll_cache"
    cache.mkdir()
    now = time.time()
    for name, age in (("pexels_1_a.mp4", 1800), ("pexels_2_a.mp4", 60)):
        path = cache / name
        path.write_bytes(b"x" * 10)
        os.utime(path, (now - age, now - age))

    # Over the cap, but both were prepared within the last two hours.
    broll.prune_broll_cache(max_bytes=5)

    assert sorted(p.name for p in cache.iterdir()) == ["pexels_1_a.mp4", "pexels_2_a.mp4"]


@requires_ffmpeg
@pytest.mark.asyncio
async def test_split_carries_each_half_its_own_broll(tmp_path):
    from src.services.clip_service import ClipEditingMixin

    first, second = tmp_path / "first.mp4", tmp_path / "second.mp4"
    _solid(first, "red", 6, "64x64")
    _solid(second, "red", 6, "64x64")
    placements = [{"start": 3.0, "duration": 2.0}, {"start": 8.0, "duration": 3.0}]

    await ClipEditingMixin._carry_broll(placements, [(first, 0.0), (second, 6.0)])

    assert load_clip_broll(first) == [{"start": 3.0, "duration": 2.0}]
    assert load_clip_broll(second) == [{"start": 2.0, "duration": 3.0}]


@pytest.mark.asyncio
async def test_carrying_broll_never_fails_the_edit(tmp_path):
    from src.services.clip_service import ClipEditingMixin

    broken = tmp_path / "not-a-video.mp4"
    broken.write_bytes(b"nope")

    await ClipEditingMixin._carry_broll([{"start": 3.0, "duration": 2.0}], [(broken, 0.0)])

    assert load_clip_broll(broken) == []


@pytest.mark.asyncio
async def test_a_download_that_is_not_a_video_is_discarded(pexels_config, monkeypatch):
    _mock_pexels(monkeypatch, lambda request: httpx.Response(200, content=b"<html>rate limited</html>"))
    placement = {"pexels_id": 8, "width": 1080, "height": 1920, "file_url": "https://videos.example/c.mp4"}

    assert await broll.ensure_broll_file(placement) is None
    assert not any((Path(pexels_config.temp_dir) / "broll_cache").iterdir())


def test_trimming_into_a_cutaway_resumes_its_footage():
    placements = [{"start": 3.0, "duration": 4.0}]

    trimmed = shift_broll_placements(placements, 4.0, 20.0)
    assert trimmed == [{"start": 0.0, "duration": 3.0, "footage_offset": 1.0}]
    # A second trim adds to the offset.
    assert shift_broll_placements(trimmed, 0.5, 20.0)[0]["footage_offset"] == 1.5

    graph, _ = broll_overlay_graph(
        "[base]", [{"path": "a", "start": 0.0, "duration": 3.0, "offset": 1.0}], 1080, 1920
    )
    assert "trim=start=1.000:duration=3.100" in graph


@requires_ffmpeg
def test_render_plays_footage_from_its_offset(tmp_path):
    from src.media.reframing import render_reframed_clip_ffmpeg

    main, footage, output = tmp_path / "main.mp4", tmp_path / "footage.mp4", tmp_path / "out.mp4"
    _solid(main, "red", 6, "320x240")
    green, blue = tmp_path / "green.mp4", tmp_path / "blue.mp4"
    _solid(green, "green", 2, "180x320")
    _solid(blue, "blue", 3, "180x320")
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", str(green), "-i", str(blue),
         "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]", "-map", "[v]", str(footage)],
        check=True,
    )

    ok, _, _ = render_reframed_clip_ffmpeg(
        main, output, "original",
        broll_overlays=[{"path": footage, "start": 1.0, "duration": 2.5, "offset": 2.0}],
    )

    assert ok
    shown = _pixel(output, 1.6)
    assert shown[2] > 200 and shown[1] < 100  # blue: footage from second 2, not green


@requires_ffmpeg
def test_broken_footage_falls_back_to_the_clip_without_broll(tmp_path):
    from src.video_utils import create_optimized_clip

    main, output = tmp_path / "main.mp4", tmp_path / "out.mp4"
    _solid(main, "red", 6, "320x240")
    broken = tmp_path / "broken.mp4"
    broken.write_bytes(b"not a video")
    report: dict = {}

    ok = create_optimized_clip(
        main, 0.0, 6.0, output, add_subtitles=False, output_format="original",
        keep_ranges=[(0.0, 6.0)], extend_to_sentence=False, layout_report=report,
        broll_overlays=[{"path": broken, "start": 2.0, "duration": 2.0}],
    )

    assert ok and output.is_file()
    assert report["broll_applied"] is False


@requires_ffmpeg
def test_original_format_broll_keeps_non_square_pixel_sources_in_shape(tmp_path):
    from src.media.reframing import render_reframed_clip_ffmpeg

    main, footage, output = tmp_path / "main.mp4", tmp_path / "footage.mp4", tmp_path / "out.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=red:s=320x240:r=30:d=4",
         "-vf", "setsar=2", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(main)],
        check=True,
    )
    _solid(footage, "blue", 3, "180x320")

    ok, width, height = render_reframed_clip_ffmpeg(
        main, output, "original", broll_overlays=[{"path": footage, "start": 1.0, "duration": 2.0}]
    )

    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height,sample_aspect_ratio", "-of", "csv=p=0", str(output)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert ok and (width, height) == (640, 240)
    assert probe.startswith("640,240,1:1")


@pytest.mark.asyncio
async def test_regeneration_that_renders_nothing_keeps_the_existing_clips(isolated_clip_edits, tmp_path):
    from unittest.mock import AsyncMock

    from src.services.task_service import TaskService

    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    service = TaskService(db=None)
    service.task_repo.get_task_by_id = AsyncMock(return_value={"source_url": "upload://source.mp4", "source_type": "upload"})
    service.clip_repo.get_clips_by_task = AsyncMock(return_value=[
        {"id": "clip", "task_id": "task", "file_path": str(tmp_path / "clip.mp4"),
         "start_time": "00:00", "end_time": "00:05", "text": "hello"}
    ])
    service.clip_repo.delete_clips_by_task = AsyncMock()
    service._load_task_source_settings = AsyncMock(return_value={"output_format": "vertical", "add_subtitles": True})
    service.video_service.resolve_local_video_path = lambda _url: source
    service.video_service.prepare_source = AsyncMock(return_value=source)
    service.video_service.create_video_clips = AsyncMock(return_value=[])

    with pytest.raises(ValueError, match="existing clips are unchanged"):
        await service.regenerate_all_clips_for_task("task", None, None, None, "default")

    service.clip_repo.delete_clips_by_task.assert_not_awaited()


@requires_ffmpeg
def test_cutaway_from_coarse_timebase_footage_covers_its_whole_window(tmp_path):
    """Pexels files use a 1/25 stream time base; the cutaway must still reach its end."""
    from src.media.reframing import render_reframed_clip_ffmpeg

    main, footage, output = tmp_path / "main.mp4", tmp_path / "footage.mp4", tmp_path / "out.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=red:s=320x240:r=24:d=4",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(main)],
        check=True,
    )
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=blue:s=180x320:r=25:d=4",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-video_track_timescale", "25", str(footage)],
        check=True,
    )

    ok, _, _ = render_reframed_clip_ffmpeg(
        main, output, "original",
        broll_overlays=[{"path": footage, "start": 0.98, "duration": 1.25, "fade_in": False, "fade_out": False}],
    )

    assert ok
    # Frames at 24 fps from 1.0 s up to the cutaway's end at 2.23 s.
    covered = [_pixel(output, 1.0 + i / 24) for i in range(29)]
    assert all(c[2] > 200 and c[0] < 60 for c in covered)
    assert _pixel(output, 2.30)[0] > 200
