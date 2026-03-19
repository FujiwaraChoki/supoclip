from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src import video_utils


class FakeClip:
    def __init__(self, size=(240, 60), duration=1.0):
        self.size = size
        self.duration = duration
        self.effects = []
        self.position = None
        self.start = None
        self.write_calls = []
        self.closed = False

    def with_duration(self, duration):
        self.duration = duration
        return self

    def with_start(self, start):
        self.start = start
        return self

    def with_position(self, position):
        self.position = position
        return self

    def with_effects(self, effects):
        self.effects = effects
        return self

    def subclipped(self, start, end=None):
        if end is not None:
            self.duration = end - start
        return self

    def resized(self, size):
        self.size = size
        return self

    def write_videofile(self, *args, **kwargs):
        self.write_calls.append((args, kwargs))

    def close(self):
        self.closed = True


def test_create_fade_subtitles_uses_moviepy_effect_objects_for_background():
    template = {
        "font_size": 48,
        "font_color": "#FFFFFF",
        "position_y": 0.75,
        "background": True,
        "background_color": "#00000080",
    }
    relevant_words = [
        {"text": "hello", "start": 0.0, "end": 0.4},
        {"text": "world", "start": 0.4, "end": 1.0},
    ]
    text_clip = FakeClip(size=(280, 70))
    background_clip = FakeClip(size=(300, 80))

    with (
        patch("src.video_utils.VideoProcessor") as mock_processor,
        patch("src.video_utils.TextClip", return_value=text_clip),
        patch("src.video_utils.ColorClip", return_value=background_clip),
        patch("src.video_utils.get_scaled_font_size", return_value=48),
        patch("src.video_utils.get_subtitle_max_width", return_value=700),
        patch("src.video_utils.get_safe_vertical_position", return_value=800),
    ):
        mock_processor.return_value = SimpleNamespace(font_path="font.ttf")

        clips = video_utils.create_fade_subtitles(
            relevant_words,
            video_width=1080,
            video_height=1920,
            template=template,
            font_family="TikTokSans-Regular",
        )

    assert clips[0] is background_clip
    assert [effect.__class__.__name__ for effect in background_clip.effects] == [
        "CrossFadeIn",
        "CrossFadeOut",
    ]
    assert all(hasattr(effect, "copy") for effect in background_clip.effects)


def test_apply_transition_effect_uses_moviepy_fade_effect_objects(tmp_path):
    clip1 = FakeClip(size=(1080, 1920), duration=4.0)
    clip2 = FakeClip(size=(1080, 1920), duration=4.0)
    transition = FakeClip(size=(720, 1280), duration=1.2)
    final_clip = FakeClip(size=(1080, 1920), duration=9.2)

    with (
        patch("moviepy.VideoFileClip", side_effect=[clip1, clip2, transition]),
        patch("moviepy.concatenate_videoclips", return_value=final_clip),
        patch("src.video_utils.VideoProcessor") as mock_processor,
    ):
        mock_processor.return_value.get_optimal_encoding_settings.return_value = {}

        success = video_utils.apply_transition_effect(
            tmp_path / "clip1.mp4",
            tmp_path / "clip2.mp4",
            tmp_path / "transition.mp4",
            tmp_path / "out.mp4",
        )

    assert success is True
    assert [effect.__class__.__name__ for effect in clip1.effects] == ["FadeOut"]
    assert [effect.__class__.__name__ for effect in clip2.effects] == ["FadeIn"]
    assert all(hasattr(effect, "copy") for effect in clip1.effects + clip2.effects)
    assert final_clip.write_calls
