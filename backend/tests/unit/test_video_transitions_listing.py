"""Tests for the lightweight listing helper in ``_video_transitions``.

The heavier ``apply_transition_effect`` path is already exercised by
``tests/unit/test_video_utils_effects.py``; this file focuses on the
filesystem-based ``get_available_transitions`` listing.
"""

from unittest.mock import patch

from src._video_transitions import get_available_transitions


class TestGetAvailableTransitions:
    def test_missing_dir_returns_empty(self, tmp_path, monkeypatch):
        import src._video_transitions as vt

        fake_file = tmp_path / "nothere" / "_video_transitions.py"
        # Point __file__ to a location whose parent.parent / "transitions" does not exist.
        monkeypatch.setattr(vt, "__file__", str(fake_file))
        assert get_available_transitions() == []

    def test_lists_mp4_files(self, tmp_path, monkeypatch):
        """Wire the module's ``__file__`` so transitions/ sits under tmp_path."""
        import src._video_transitions as vt

        transitions_dir = tmp_path / "transitions"
        transitions_dir.mkdir()
        (transitions_dir / "fade.mp4").write_bytes(b"\x00")
        (transitions_dir / "swipe.mp4").write_bytes(b"\x00")
        (transitions_dir / "notes.txt").write_text("ignore me")

        # __file__ lives one level deeper than the transitions dir in production.
        fake_file = tmp_path / "src" / "_video_transitions.py"
        (tmp_path / "src").mkdir()
        monkeypatch.setattr(vt, "__file__", str(fake_file))

        result = get_available_transitions()
        names = sorted(str(p).split("\\")[-1].split("/")[-1] for p in result)
        assert names == ["fade.mp4", "swipe.mp4"]
