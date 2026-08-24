"""
Unit tests for non-English audio language detection and caption transliteration.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch
import json
import pytest

from src.video_utils import (
    transliterate_text,
    LANGUAGE_NAMES,
    probe_audio_snippet_language,
    build_hook_title_ass,
    build_assemblyai_ass_subtitles,
    cache_transcript_data,
    WhisperTranscriptResult,
    WhisperWord,
    WhisperUtterance,
)
from src.emoji_captions import normalize_token
from src.ai import _normalize_transcript_text


def test_transliterate_text_hindi():
    hindi_text = "नमस्ते दुनिया"
    result = transliterate_text(hindi_text)
    # Phonetic Romanization: 'namaste duniya'
    assert result.lower().startswith("namaste")
    assert "duniya" in result.lower() or "duni" in result.lower()


def test_transliterate_text_japanese():
    japanese_text = "こんにちは世界"
    result = transliterate_text(japanese_text)
    # Romaji: 'konnichihaShi Jie' / 'konnichiwa'
    assert len(result) > 0
    assert result.isascii()


def test_transliterate_text_cyrillic():
    russian_text = "Привет мир"
    result = transliterate_text(russian_text)
    # 'Privet mir'
    assert result.isascii()
    assert "privet" in result.lower() or "priv" in result.lower()


def test_transliterate_text_english_and_empty():
    assert transliterate_text("") == ""
    assert transliterate_text("Hello World 123!") == "Hello World 123!"


def test_unicode_aware_token_normalization():
    # Accented/Unicode tokens should not be stripped into empty strings
    token_hindi = normalize_token("नमस्ते")
    assert len(token_hindi) > 0

    token_spanish = normalize_token("¡Canción!")
    assert "canción" in token_spanish or "cancion" in token_spanish

    transcript_norm = _normalize_transcript_text("Bonjour le monde! Ça va?")
    assert "ça" in transcript_norm or "ca" in transcript_norm


def test_language_names_map():
    assert LANGUAGE_NAMES.get("hi") == "Hindi"
    assert LANGUAGE_NAMES.get("es") == "Spanish"
    assert LANGUAGE_NAMES.get("ja") == "Japanese"
    assert LANGUAGE_NAMES.get("en") == "English"
    assert LANGUAGE_NAMES.get("fr") == "French"


def test_language_default_fonts():
    from src.font_registry import LANGUAGE_DEFAULT_FONTS

    assert LANGUAGE_DEFAULT_FONTS.get("hi") == "NotoSansDevanagari-Bold"
    assert LANGUAGE_DEFAULT_FONTS.get("ja") == "NotoSansJP-Bold"
    assert LANGUAGE_DEFAULT_FONTS.get("ar") == "NotoSansArabic-Bold"


def test_probe_audio_snippet_language_empty():
    res = probe_audio_snippet_language("")
    assert res["language_code"] == "en"
    assert res["is_english"] is True
    assert res["default_native_font"] is None



def test_build_hook_title_ass_transliteration():
    template = {
        "font_family": "Montserrat",
        "font_size": 48,
        "font_color": "#FFFFFF",
        "highlight_color": "#FFE000",
        "stroke_color": "#000000",
        "stroke_width": 3,
        "uppercase": True,
    }
    style_line, events = build_hook_title_ass(
        hook_title="नमस्ते भारत",
        template=template,
        video_width=1080,
        video_height=1920,
        output_duration=15.0,
        font_name="Montserrat",
        caption_font_px=54,
        transliterate=True,
    )
    assert len(events) > 0
    event_text = events[0]
    # Should be transliterated into ASCII/Latin
    assert "NAMASTE" in event_text.upper() or "BHARAT" in event_text.upper()


def test_build_assemblyai_ass_subtitles_transliteration(tmp_path: Path):
    video_file = tmp_path / "hindi_video.mp4"
    video_file.touch()
    ass_out = tmp_path / "output.ass"

    word1 = WhisperWord("नमस्ते", 0, 500, 0.95)
    word2 = WhisperWord("दोस्त", 500, 1000, 0.99)
    utterance = WhisperUtterance("नमस्ते दोस्त", 0, 1000, [word1, word2])
    transcript = WhisperTranscriptResult("नमस्ते दोस्त", [word1, word2], [utterance])
    cache_transcript_data(video_file, transcript)

    success = build_assemblyai_ass_subtitles(
        video_path=video_file,
        clip_start=0.0,
        clip_end=1.5,
        video_width=1080,
        video_height=1920,
        output_ass_path=ass_out,
        caption_template="default",
        transliterate_captions=True,
    )

    assert success is True
    assert ass_out.exists()
    content = ass_out.read_text(encoding="utf-8")
    assert "[Events]" in content
    # Words should be transliterated
    assert "namaste" in content.lower() or "dost" in content.lower()


@pytest.mark.asyncio
async def test_verify_transliteration_with_llm_fallback():
    from src.ai import verify_transliteration_with_llm

    # When no LLM key is configured or error occurs, falls back cleanly to raw
    raw = "nmste duniya"
    native = "नमस्ते दुनिया"
    result = await verify_transliteration_with_llm(native, raw, "hi")
    assert len(result) > 0
    assert result.isascii()


@pytest.mark.asyncio
async def test_verify_transliteration_with_llm_mocked():
    from src.ai import (
        verify_transliteration_with_llm,
        TransliterationBatchResponse,
        TransliteratedSegmentVerification,
    )

    mock_agent = MagicMock()
    mock_res = MagicMock()
    mock_res.output = TransliterationBatchResponse(
        segments=[
            TransliteratedSegmentVerification(
                id=0,
                verified_transliteration="Namaste Duniya",
            )
        ]
    )

    async def mock_run(*args, **kwargs):
        return mock_res

    mock_agent.run = mock_run

    with patch("src.ai.get_transliteration_agent", return_value=mock_agent):
        verified = await verify_transliteration_with_llm(
            "नमस्ते दुनिया", "nmste duniya", "hi"
        )
        assert verified == "Namaste Duniya"


@pytest.mark.asyncio
async def test_batch_verify_transliterations_with_llm_mocked():
    from src.ai import (
        batch_verify_transliterations_with_llm,
        TransliterationBatchResponse,
        TransliteratedSegmentVerification,
    )

    mock_agent = MagicMock()
    mock_res = MagicMock()
    mock_res.output = TransliterationBatchResponse(
        segments=[
            TransliteratedSegmentVerification(
                id=0,
                verified_transliteration="Namaste Bharat",
            ),
            TransliteratedSegmentVerification(
                id=1,
                verified_transliteration="Konnichiwa Sekai",
            ),
        ]
    )

    async def mock_run(*args, **kwargs):
        return mock_res

    mock_agent.run = mock_run

    with patch("src.ai.get_transliteration_agent", return_value=mock_agent):
        items = [
            {"id": 0, "native": "नमस्ते भारत", "raw": "nmste bhart"},
            {"id": 1, "native": "こんにちは世界", "raw": "konnichihaShi Jie"},
        ]
        results = await batch_verify_transliterations_with_llm(items)
        assert results == ["Namaste Bharat", "Konnichiwa Sekai"]


def test_sync_verify_transliteration():
    from src.ai import sync_verify_transliteration

    result = sync_verify_transliteration("Hello", "Hello", "en")
    assert result == "Hello"

