"""Centralized prompt management system with automatic file reloading.

Provides a PromptManager class that handles:
- Loading prompts from external markdown files
- Caching in memory for performance
- Automatic file change detection via mtime (no external watchers)
- Support for multiple prompts
- UTF-8 encoding and validation
- Thread-safe operations
"""

import logging
import threading
from pathlib import Path
from typing import Dict

logger = logging.getLogger(__name__)


class PromptManager:
    """Centralized prompt management with caching and mtime-based reloading."""

    _instance = None
    _lock = threading.RLock()
    _prompts_dir = Path(__file__).parent.parent / "prompts"

    def __new__(cls):
        """Singleton pattern for PromptManager."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        """Initialize PromptManager with cache and mtime tracking."""
        if self._initialized:
            return

        self._cache: Dict[str, str] = {}
        self._mtimes: Dict[str, float] = {}
        self._initialized = True

        logger.info(f"PromptManager initialized, prompts directory: {self._prompts_dir}")

    @classmethod
    def get(cls, prompt_name: str) -> str:
        """Get a prompt by name, using cache or loading from file.

        Args:
            prompt_name: Name of the prompt (without .md extension)

        Returns:
            The prompt content as a string

        Raises:
            FileNotFoundError: If prompt file doesn't exist
            ValueError: If prompt file is empty
        """
        manager = cls()
        return manager._get_prompt(prompt_name)

    def _get_prompt(self, prompt_name: str) -> str:
        """Get prompt from cache or load from file.

        Automatically reloads if file has been modified (based on mtime).
        """
        with self._lock:
            prompt_path = self._prompts_dir / f"{prompt_name}.md"

            if not prompt_path.exists():
                raise FileNotFoundError(
                    f"Prompt file not found at: {prompt_path.resolve()}\n"
                    f"Expected location: backend/prompts/{prompt_name}.md\n"
                    f"Please ensure the file exists in the prompts directory."
                )

            # Check if file has been modified
            current_mtime = prompt_path.stat().st_mtime
            cached_mtime = self._mtimes.get(prompt_name)

            # Return cached content if file hasn't changed
            if prompt_name in self._cache and cached_mtime == current_mtime:
                return self._cache[prompt_name]

            # Load or reload from file
            try:
                content = prompt_path.read_text(encoding="utf-8")
                if not content.strip():
                    raise ValueError("Prompt file is empty")

                self._cache[prompt_name] = content
                self._mtimes[prompt_name] = current_mtime

                if cached_mtime is None:
                    logger.info(f"Loaded prompt '{prompt_name}' from {prompt_path.name}")
                else:
                    logger.info(f"Reloaded prompt '{prompt_name}' from {prompt_path.name}")

                return content

            except Exception as e:
                raise RuntimeError(
                    f"Failed to load prompt '{prompt_name}' from {prompt_path}: {str(e)}"
                ) from e


    @classmethod
    def list_prompts(cls) -> list[str]:
        """List all available prompts.

        Returns:
            List of prompt names (without .md extension)
        """
        if not cls._prompts_dir.exists():
            return []

        return [f.stem for f in cls._prompts_dir.glob("*.md")]

    @classmethod
    def clear_cache(cls) -> None:
        """Clear the prompt cache (for testing)."""
        manager = cls()
        with manager._lock:
            manager._cache.clear()
            logger.debug("Prompt cache cleared")

    @classmethod
    def stop_watchers(cls) -> None:
        """Clear mtime tracking (for testing/shutdown)."""
        manager = cls()
        with manager._lock:
            manager._mtimes.clear()
            logger.debug("Prompt mtime tracking cleared")

    @classmethod
    def get_cache_info(cls) -> dict:
        """Get cache statistics (for testing/monitoring)."""
        manager = cls()
        with manager._lock:
            return {
                "cached_prompts": list(manager._cache.keys()),
                "cache_size": len(manager._cache),
                "tracked_mtimes": list(manager._mtimes.keys()),
            }
