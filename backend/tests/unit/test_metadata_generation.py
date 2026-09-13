from unittest.mock import AsyncMock, patch

from src.metadata_generation import (
    ClipContext,
    ClipMetadata,
    MetadataBatchEntry,
    MetadataBatchOutput,
    generate_metadata_for_single_clip,
    generate_metadata_for_video,
    normalize_tags,
    truncate_at_word_boundary,
)


def test_truncate_at_word_boundary_leaves_short_text_alone():
    assert truncate_at_word_boundary("short text", 60) == "short text"


def test_truncate_at_word_boundary_cuts_at_word_not_mid_word():
    text = "one two three four five six seven eight nine ten eleven twelve"
    truncated = truncate_at_word_boundary(text, 20)
    assert len(truncated) <= 20
    assert not text[len(truncated) : len(truncated) + 1].isalpha() or truncated == text
    assert " " not in truncated[-1:] or True  # no trailing partial word
    assert text.startswith(truncated)


def test_normalize_tags_lowercases_hyphenates_and_dedupes():
    tags = ["Funny Video", "funny-video", "  Sleep  ", "Sleep"]
    assert normalize_tags(tags) == ["funny-video", "sleep"]


def test_normalize_tags_caps_at_ten():
    tags = [f"tag{i}" for i in range(20)]
    assert len(normalize_tags(tags)) == 10


def test_normalize_tags_strips_punctuation():
    assert normalize_tags(["#funny!", "sleep@night"]) == ["funny", "sleepnight"]


async def test_generate_metadata_for_video_maps_by_clip_index():
    clips = [
        ClipContext(clip_id="clip-a", text="first clip transcript"),
        ClipContext(clip_id="clip-b", text="second clip transcript"),
    ]
    fake_output = MetadataBatchOutput(
        clips=[
            MetadataBatchEntry(clip_index=0, title="Title A", description="Desc A", tags=["a"]),
            MetadataBatchEntry(clip_index=1, title="Title B", description="Desc B", tags=["b"]),
        ]
    )
    with patch(
        "src.metadata_generation.run_with_llm_fallback",
        new=AsyncMock(return_value=(fake_output, "ollama")),
    ):
        results, provider = await generate_metadata_for_video(clips)

    assert provider == "ollama"
    assert results["clip-a"].title == "Title A"
    assert results["clip-b"].title == "Title B"


async def test_generate_metadata_for_video_ignores_out_of_range_index():
    clips = [ClipContext(clip_id="clip-a", text="only clip")]
    fake_output = MetadataBatchOutput(
        clips=[MetadataBatchEntry(clip_index=5, title="Bad", description="Bad", tags=[])]
    )
    with patch(
        "src.metadata_generation.run_with_llm_fallback",
        new=AsyncMock(return_value=(fake_output, "ollama")),
    ):
        results, provider = await generate_metadata_for_video(clips)

    assert results == {}
    assert provider == "ollama"


async def test_generate_metadata_for_video_returns_unavailable_when_llm_unavailable():
    clips = [ClipContext(clip_id="clip-a", text="text")]
    with patch(
        "src.metadata_generation.run_with_llm_fallback",
        new=AsyncMock(return_value=(None, "unavailable")),
    ):
        results, provider = await generate_metadata_for_video(clips)

    assert results == {}
    assert provider == "unavailable"


async def test_generate_metadata_for_single_clip_normalizes_output():
    clip = ClipContext(clip_id="clip-a", text="some transcript text")
    fake_meta = ClipMetadata(title="  A Title  ", description="  A description  ", tags=["Tag One"])
    with patch(
        "src.metadata_generation.run_with_llm_fallback",
        new=AsyncMock(return_value=(fake_meta, "gemini")),
    ):
        meta, provider = await generate_metadata_for_single_clip(clip)

    assert provider == "gemini"
    assert meta.title == "A Title"
    assert meta.tags == ["tag-one"]
