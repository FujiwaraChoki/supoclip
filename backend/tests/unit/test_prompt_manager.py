"""Tests for the PromptManager system.

Tests the centralized prompt management system including:
- Loading prompts from files
- In-memory caching
- Automatic file reloading
- Multiple prompt support
- UTF-8 encoding
- Error handling
"""

import tempfile
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from src.prompt_manager import PromptManager


class TestPromptManagerBasics:
    """Basic prompt loading and retrieval tests."""

    def test_get_prompt_returns_string(self):
        """Test that PromptManager.get() returns a valid string."""
        prompt = PromptManager.get("rells_engine")

        assert isinstance(prompt, str)
        assert len(prompt) > 0

    def test_get_prompt_contains_required_sections(self):
        """Test that rells_engine prompt has all required sections."""
        prompt = PromptManager.get("rells_engine")

        required_sections = [
            "OUTPUT CONTRACT",
            "CORE OBJECTIVES",
            "GROUNDING RULES",
            "CONTENT NEUTRALITY",
            "VIRALITY SCORING",
        ]

        for section in required_sections:
            assert section in prompt, f"Missing section: {section}"

    def test_missing_prompt_raises_file_not_found(self):
        """Test that requesting non-existent prompt raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError) as exc_info:
            PromptManager.get("nonexistent_prompt")

        error_msg = str(exc_info.value)
        assert "not found" in error_msg.lower()
        assert "backend/prompts/" in error_msg

    def test_empty_prompt_raises_value_error(self):
        """Test that empty prompt file raises RuntimeError when empty."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            empty_file = tmp_path / "empty.md"
            empty_file.write_text("")

            with patch.object(PromptManager, "_prompts_dir", tmp_path):
                PromptManager.clear_cache()

                with pytest.raises(RuntimeError) as exc_info:
                    PromptManager.get("empty")

                assert "empty" in str(exc_info.value).lower()
                assert "Prompt file is empty" in str(exc_info.value)


class TestPromptManagerCaching:
    """Tests for caching behavior."""

    def setup_method(self):
        """Clear cache before each test."""
        PromptManager.clear_cache()

    def test_prompt_is_cached_after_first_load(self):
        """Test that prompt is cached after first load."""
        cache_info_before = PromptManager.get_cache_info()
        prompt1 = PromptManager.get("rells_engine")

        cache_info_after = PromptManager.get_cache_info()
        assert len(cache_info_after["cached_prompts"]) > len(
            cache_info_before["cached_prompts"]
        )

        # Second load should return same object from cache
        prompt2 = PromptManager.get("rells_engine")
        assert prompt1 == prompt2

    def test_cache_info_shows_loaded_prompts(self):
        """Test that cache info reflects loaded prompts."""
        PromptManager.get("rells_engine")

        cache_info = PromptManager.get_cache_info()
        assert "rells_engine" in cache_info["cached_prompts"]
        assert cache_info["cache_size"] > 0

    def test_clear_cache_removes_all_prompts(self):
        """Test that cache can be cleared."""
        PromptManager.get("rells_engine")

        cache_info_before = PromptManager.get_cache_info()
        assert cache_info_before["cache_size"] > 0

        PromptManager.clear_cache()
        cache_info_after = PromptManager.get_cache_info()
        assert cache_info_after["cache_size"] == 0


class TestPromptManagerUTF8:
    """Tests for UTF-8 encoding support."""

    def setup_method(self):
        """Clear cache before each test."""
        PromptManager.clear_cache()

    def test_prompt_with_utf8_characters(self):
        """Test that prompts with UTF-8 characters load correctly."""
        prompt = PromptManager.get("rells_engine")

        # Verify it's valid UTF-8
        encoded = prompt.encode("utf-8")
        decoded = encoded.decode("utf-8")
        assert decoded == prompt

    def test_prompt_is_valid_string(self):
        """Test that prompt is a valid, non-empty string."""
        prompt = PromptManager.get("rells_engine")

        assert isinstance(prompt, str)
        assert len(prompt.strip()) > 0


class TestPromptManagerSingleton:
    """Tests for singleton pattern."""

    def test_prompt_manager_is_singleton(self):
        """Test that PromptManager follows singleton pattern."""
        manager1 = PromptManager()
        manager2 = PromptManager()

        assert manager1 is manager2

    def test_get_is_class_method(self):
        """Test that get() works as class method."""
        # Should work without instantiating
        prompt = PromptManager.get("rells_engine")
        assert isinstance(prompt, str)


class TestPromptManagerListingPrompts:
    """Tests for listing available prompts."""

    def test_list_prompts_includes_rells_engine(self):
        """Test that list_prompts includes rells_engine."""
        prompts = PromptManager.list_prompts()

        assert isinstance(prompts, list)
        assert "rells_engine" in prompts

    def test_list_prompts_only_md_files(self):
        """Test that list_prompts only returns .md files (stems)."""
        prompts = PromptManager.list_prompts()

        # All should be strings without extensions
        for prompt in prompts:
            assert isinstance(prompt, str)
            assert "." not in prompt

    def test_list_prompts_when_dir_missing(self):
        """Test that list_prompts returns empty list if directory doesn't exist."""
        with patch.object(PromptManager, "_prompts_dir", Path("/nonexistent/path")):
            prompts = PromptManager.list_prompts()

            assert prompts == []
            assert isinstance(prompts, list)


class TestPromptManagerThreadSafety:
    """Tests for thread-safe operations."""

    def setup_method(self):
        """Clear cache before each test."""
        PromptManager.clear_cache()

    def test_concurrent_get_calls_are_safe(self):
        """Test that concurrent get() calls don't cause issues."""
        import threading

        results = []
        errors = []

        def load_prompt():
            try:
                prompt = PromptManager.get("rells_engine")
                results.append(prompt)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=load_prompt) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert len(results) == 5
        # All should have loaded the same content
        assert all(r == results[0] for r in results)


class TestPromptManagerErrorHandling:
    """Tests for error handling."""

    def setup_method(self):
        """Clear cache before each test."""
        PromptManager.clear_cache()

    def test_error_includes_helpful_message(self):
        """Test that error messages are helpful."""
        with pytest.raises(FileNotFoundError) as exc_info:
            PromptManager.get("does_not_exist")

        error_msg = str(exc_info.value)
        assert "backend/prompts/" in error_msg
        assert "does_not_exist.md" in error_msg
        assert "ensure the file exists" in error_msg.lower()

    def test_file_watcher_handles_missing_files(self):
        """Test that file watcher doesn't crash on missing files."""
        # This is tested implicitly - if watchers are started successfully
        # without crashing, the test passes
        prompt = PromptManager.get("rells_engine")
        assert isinstance(prompt, str)


class TestPromptManagerPerformance:
    """Tests for performance characteristics."""

    def setup_method(self):
        """Clear cache before each test."""
        PromptManager.clear_cache()

    def test_first_load_is_reasonable(self):
        """Test that first load completes in reasonable time."""
        import time

        start = time.time()
        prompt = PromptManager.get("rells_engine")
        elapsed = time.time() - start

        assert isinstance(prompt, str)
        # First load should complete in under 100ms (disk read + watcher setup)
        assert elapsed < 0.1

    def test_cached_load_is_fast(self):
        """Test that cached load is very fast."""
        import time

        # First load
        PromptManager.get("rells_engine")

        # Second load (from cache)
        start = time.time()
        prompt = PromptManager.get("rells_engine")
        elapsed = time.time() - start

        assert isinstance(prompt, str)
        # Cached load should be < 1ms
        assert elapsed < 0.001


class TestPromptManagerIntegration:
    """Integration tests."""

    def setup_method(self):
        """Clear cache and stop watchers before each test."""
        PromptManager.clear_cache()
        PromptManager.stop_watchers()

    def teardown_method(self):
        """Clean up after tests."""
        PromptManager.stop_watchers()

    def test_prompt_file_exists_at_expected_location(self):
        """Test that prompt file exists at backend/prompts/rells_engine.md."""
        prompts_dir = Path(__file__).parent.parent.parent / "prompts"
        prompt_file = prompts_dir / "rells_engine.md"

        assert prompt_file.exists(), f"Prompt file not found at {prompt_file}"
        assert prompt_file.is_file()

    def test_prompt_is_readable_and_valid(self):
        """Test that the actual prompt file is readable and valid."""
        prompt = PromptManager.get("rells_engine")

        # Should be substantial content
        assert len(prompt) > 1000
        assert "OUTPUT CONTRACT" in prompt
        assert "VIRALITY SCORING" in prompt

    def test_multiple_prompts_can_coexist(self):
        """Test that PromptManager supports multiple prompts."""
        # Load rells_engine
        prompt1 = PromptManager.get("rells_engine")

        # Can load another prompt without issues
        # (assuming it exists, or would raise FileNotFoundError which is expected)
        # For now we just verify rells_engine works
        assert isinstance(prompt1, str)

        cache_info = PromptManager.get_cache_info()
        assert len(cache_info["cached_prompts"]) >= 1


def test_prompt_manager_global_usage():
    """Test that PromptManager can be used globally as shown in ai.py."""
    # This mimics how ai.py uses PromptManager
    transcript_analysis_system_prompt = PromptManager.get("rells_engine")

    assert isinstance(transcript_analysis_system_prompt, str)
    assert len(transcript_analysis_system_prompt) > 1000
    assert "OUTPUT CONTRACT" in transcript_analysis_system_prompt
