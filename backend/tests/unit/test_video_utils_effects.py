from types import SimpleNamespace
from unittest.mock import patch

from src import video_utils


class FakeClip:
    def __init__(self, name="clip", size=(240, 60), duration=1.0, audio=None):
        self.name = name
        self.size = size
        self.duration = duration
        self.audio = audio
        self.effects = []
        self.position = None
        self.start = None
        self.write_calls = []
        self.closed = False
        self.subclip_calls = []
        self.audio_source = None

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
        self.subclip_calls.append((start, end))
        clip_duration = self.duration if end is None else end - start
        return FakeClip(
            name=f"{self.name}.subclip{len(self.subclip_calls)}",
            size=self.size,
            duration=clip_duration,
            audio=self.audio,
        )

    def resized(self, size):
        self.size = size
        return self

    def with_audio(self, audio):
        self.audio_source = audio
        self.audio = audio
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
    text_clip = FakeClip(name="text", size=(280, 70))
    background_clip = FakeClip(name="background", size=(300, 80))

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


def test_apply_transition_effect_preserves_current_clip_duration_and_boundary(tmp_path):
    clip1 = FakeClip(name="clip1", size=(1080, 1920), duration=4.0, audio="audio1")
    clip2 = FakeClip(name="clip2", size=(1080, 1920), duration=4.0, audio="audio2")
    transition = FakeClip(name="transition", size=(720, 1280), duration=1.2)
    intro_segment = FakeClip(name="intro", size=(1080, 1920), duration=1.2)
    final_clip = FakeClip(name="final", size=(1080, 1920), duration=4.0)

    def fake_composite_video_clip(clips, size):
        composite = intro_segment
        composite.source_clips = clips
        composite.size = size
        return composite

    concatenated_segments = []

    def fake_concatenate_videoclips(clips, method):
        concatenated_segments.append((clips, method))
        return final_clip

    with (
        patch("moviepy.VideoFileClip", side_effect=[clip1, clip2, transition]),
        patch("moviepy.CompositeVideoClip", side_effect=fake_composite_video_clip),
        patch("moviepy.concatenate_videoclips", side_effect=fake_concatenate_videoclips),
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
    assert clip1.subclip_calls == [(2.8, 4.0)]
    assert clip2.subclip_calls == [(0, 1.2), (1.2, 4.0)]
    assert [effect.__class__.__name__ for effect in intro_segment.source_clips[0].effects] == [
        "FadeOut"
    ]
    assert [effect.__class__.__name__ for effect in intro_segment.source_clips[1].effects] == [
        "FadeIn"
    ]
    assert all(
        hasattr(effect, "copy")
        for effect in intro_segment.source_clips[0].effects
        + intro_segment.source_clips[1].effects
    )
    assert intro_segment.audio_source == "audio2"
    assert len(concatenated_segments) == 1
    assert len(concatenated_segments[0][0]) == 2
    assert final_clip.write_calls


def test_create_clips_with_transitions_keeps_standalone_clip_exports(tmp_path):
    clips_info = [{"filename": "clip-1.mp4", "path": str(tmp_path / "clip-1.mp4")}]

    with (
        patch(
            "src.video_utils.create_clips_from_segments", return_value=clips_info
        ) as mock_create,
        patch(
            "src.video_utils.get_available_transitions",
            side_effect=AssertionError("should not load transitions"),
        ),
    ):
        result = video_utils.create_clips_with_transitions(
            tmp_path / "source.mp4",
            [{"start_time": "00:00", "end_time": "00:10", "text": "hook"}],
            tmp_path,
            font_family="TikTokSans-Regular",
            font_size=24,
            font_color="#FFFFFF",
            caption_template="default",
            output_format="vertical",
            add_subtitles=True,
        )

    assert result == clips_info
    mock_create.assert_called_once()
