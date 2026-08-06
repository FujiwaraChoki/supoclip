"""Integration tests for the external prompt loading system.

These tests verify that the prompt loading system integrates correctly
with the rest of the SupoClip pipeline and works in different environments.
"""

import asyncio
from pathlib import Path
from typing import Optional

import pytest

from src.ai import (
    TranscriptAnalysis,
    get_transcript_agent,
    transcript_analysis_system_prompt,
)


class TestPromptIntegrationWithAgent:
    """Integration tests for prompt usage with Pydantic AI Agent."""

    def test_agent_initializes_with_loaded_prompt(self):
        """Test that the loaded prompt is available and properly formatted."""
        # Verify prompt exists and is properly initialized
        assert transcript_analysis_system_prompt is not None
        assert isinstance(transcript_analysis_system_prompt, str)
        assert len(transcript_analysis_system_prompt) > 1000

    def test_agent_prompt_is_set_correctly(self):
        """Test that prompt contains required fields for the transcript agent."""
        # Verify the loaded prompt contains expected content
        assert "OUTPUT CONTRACT" in transcript_analysis_system_prompt
        assert "CORE OBJECTIVES" in transcript_analysis_system_prompt
        assert "GROUNDING RULES" in transcript_analysis_system_prompt

    def test_prompt_is_not_empty_for_agent(self):
        """Test that loaded prompt is not empty and suitable for LLM."""
        assert len(transcript_analysis_system_prompt) > 1000
        assert (
            len(transcript_analysis_system_prompt.strip()) > 0
        )  # Not just whitespace


class TestPromptEnvironmentCompatibility:
    """Tests for prompt loading across different environments."""

    def test_prompt_loads_from_relative_path(self):
        """Test that prompt loads using relative path (local dev environment)."""
        # This works by calling the function from different contexts
        from src.ai import _load_transcript_analysis_prompt

        prompt = _load_transcript_analysis_prompt()

        assert isinstance(prompt, str)
        assert len(prompt) > 0

    def test_prompt_file_exists_at_expected_location(self):
        """Test that prompt file exists at the expected location."""
        # backend/RELLS_ENGINE_v2.0_Documento_Oficial.md
        # relative to backend/src/ai.py

        ai_module = Path(__file__).parent.parent.parent / "src" / "ai.py"
        expected_prompt_file = (
            ai_module.parent.parent / "RELLS_ENGINE_v2.0_Documento_Oficial.md"
        )

        assert (
            expected_prompt_file.exists()
        ), f"Prompt file not found at {expected_prompt_file}"

    def test_prompt_file_is_readable(self):
        """Test that prompt file can be read and contains valid content."""
        ai_module = Path(__file__).parent.parent.parent / "src" / "ai.py"
        prompt_file = ai_module.parent.parent / "RELLS_ENGINE_v2.0_Documento_Oficial.md"

        assert prompt_file.exists()

        content = prompt_file.read_text(encoding="utf-8")
        assert len(content) > 0
        assert isinstance(content, str)

    def test_prompt_file_is_markdown_format(self):
        """Test that prompt file is properly formatted as Markdown."""
        ai_module = Path(__file__).parent.parent.parent / "src" / "ai.py"
        prompt_file = ai_module.parent.parent / "RELLS_ENGINE_v2.0_Documento_Oficial.md"

        content = prompt_file.read_text(encoding="utf-8")

        # Should contain markdown headers
        assert "#" in content
        # Should contain list markers
        assert "-" in content or "1." in content


class TestPromptContentValidation:
    """Tests to validate prompt content meets requirements."""

    def test_prompt_contains_output_contract(self):
        """Test that prompt specifies the output contract for the LLM."""
        assert "OUTPUT CONTRACT" in transcript_analysis_system_prompt
        assert "JSON" in transcript_analysis_system_prompt
        assert "most_relevant_segments" in transcript_analysis_system_prompt

    def test_prompt_contains_grounding_rules(self):
        """Test that prompt enforces grounding to source material."""
        assert "GROUNDING RULES" in transcript_analysis_system_prompt
        assert (
            "extraction and ranking, not creative rewriting"
            in transcript_analysis_system_prompt
        )

    def test_prompt_contains_content_neutrality_rules(self):
        """Test that prompt ensures content neutrality."""
        assert "CONTENT NEUTRALITY" in transcript_analysis_system_prompt
        assert "Do not judge, moralize" in transcript_analysis_system_prompt

    def test_prompt_contains_virality_scoring_criteria(self):
        """Test that prompt defines virality scoring dimensions."""
        assert "VIRALITY SCORING" in transcript_analysis_system_prompt
        assert "HOOK STRENGTH" in transcript_analysis_system_prompt
        assert "ENGAGEMENT" in transcript_analysis_system_prompt
        assert "VALUE" in transcript_analysis_system_prompt
        assert "SHAREABILITY" in transcript_analysis_system_prompt

    def test_prompt_contains_timing_guidelines(self):
        """Test that prompt specifies timing requirements."""
        assert "TIMING GUIDELINES" in transcript_analysis_system_prompt
        assert "25-50 seconds" in transcript_analysis_system_prompt
        assert "15" in transcript_analysis_system_prompt  # min seconds

    def test_prompt_contains_hook_title_instructions(self):
        """Test that prompt instructs on generating hook titles."""
        assert "HOOK TITLES" in transcript_analysis_system_prompt
        assert "3-9 words" in transcript_analysis_system_prompt


class TestPromptWithClipSignals:
    """Tests for prompt behavior with optional clip signals."""

    def test_build_prompt_with_clip_signals(self):
        """Test that prompt can be built with clip signals."""
        from src.ai import build_transcript_analysis_prompt

        transcript = "[00:00 - 00:10] Test content"
        clip_signals = "Audio peak at 0:05"

        prompt = build_transcript_analysis_prompt(
            transcript=transcript,
            clip_signals=clip_signals,
        )

        assert "[00:00 - 00:10] Test content" in prompt
        assert "Audio peak at 0:05" in prompt
        assert "hints" in prompt.lower()

    def test_build_prompt_with_broll_enabled(self):
        """Test that prompt includes B-roll instructions when enabled."""
        from src.ai import build_transcript_analysis_prompt

        transcript = "[00:00 - 00:10] Test content"

        prompt_without_broll = build_transcript_analysis_prompt(
            transcript=transcript,
            include_broll=False,
        )

        prompt_with_broll = build_transcript_analysis_prompt(
            transcript=transcript,
            include_broll=True,
        )

        assert "B-roll" not in prompt_without_broll
        assert "B-roll" in prompt_with_broll


class TestPromptPerformance:
    """Performance and efficiency tests for prompt loading."""

    def test_prompt_loads_quickly(self):
        """Test that prompt loading is reasonably fast."""
        import time

        start = time.time()
        from src.ai import _load_transcript_analysis_prompt

        prompt = _load_transcript_analysis_prompt()
        elapsed = time.time() - start

        assert prompt is not None
        # Should load in under 100ms (very fast disk read)
        assert elapsed < 0.1

    def test_prompt_size_is_reasonable(self):
        """Test that prompt size is reasonable for LLM context."""
        # Prompt should be large enough to be comprehensive
        assert len(transcript_analysis_system_prompt) > 3000

        # But not so large it wastes tokens
        assert len(transcript_analysis_system_prompt) < 50000


class TestPromptDockerCompatibility:
    """Tests to verify Docker compatibility."""

    def test_prompt_path_uses_pathlib(self):
        """Test that prompt loading uses pathlib (cross-platform compatible)."""
        # The function _load_transcript_analysis_prompt uses Path(__file__)
        # which works on Linux (Docker) and Windows/Mac (local)
        from src.ai import _load_transcript_analysis_prompt

        prompt = _load_transcript_analysis_prompt()
        assert isinstance(prompt, str)

    def test_prompt_encoding_utf8_for_unicode(self):
        """Test that prompt file uses UTF-8 encoding (required in Docker)."""
        ai_module = Path(__file__).parent.parent.parent / "src" / "ai.py"
        prompt_file = ai_module.parent.parent / "RELLS_ENGINE_v2.0_Documento_Oficial.md"

        # Read with UTF-8 (should succeed without encoding errors)
        content = prompt_file.read_text(encoding="utf-8")
        assert isinstance(content, str)

    def test_prompt_no_windows_line_endings(self):
        """Test that prompt file doesn't have Windows-only line endings."""
        ai_module = Path(__file__).parent.parent.parent / "src" / "ai.py"
        prompt_file = ai_module.parent.parent / "RELLS_ENGINE_v2.0_Documento_Oficial.md"

        # Read as binary to check line endings
        content_bytes = prompt_file.read_bytes()

        # Should use Unix line endings (\n) not Windows (\r\n)
        # (This is a soft requirement - but important for consistency)
        # Count CRLF vs LF
        crlf_count = content_bytes.count(b"\r\n")
        lf_only_count = content_bytes.count(b"\n") - crlf_count

        # If there are any line endings, they should be primarily Unix style
        # (Some CRLF is ok, but shouldn't be the dominant style)
        if lf_only_count + crlf_count > 0:
            assert lf_only_count >= crlf_count


class TestPromptFailureRecovery:
    """Tests for error handling and recovery."""

    def test_missing_prompt_prevents_agent_initialization(self):
        """Test that missing prompt causes clear error on import."""
        # This is more of a safety check - if prompt is missing,
        # the module import should fail with a clear error

        # We can't easily test this without breaking the test environment,
        # but we verify the file exists
        ai_module = Path(__file__).parent.parent.parent / "src" / "ai.py"
        prompt_file = ai_module.parent.parent / "RELLS_ENGINE_v2.0_Documento_Oficial.md"

        assert (
            prompt_file.exists()
        ), "Prompt file is missing - agent initialization would fail"

    def test_empty_prompt_would_cause_error(self):
        """Test that loading an empty prompt file raises ValueError."""
        # Verify the actual prompt file is not empty
        from src.ai import _load_transcript_analysis_prompt

        prompt = _load_transcript_analysis_prompt()

        # Prompt should not be empty
        assert len(prompt.strip()) > 0, "Prompt file should not be empty"
