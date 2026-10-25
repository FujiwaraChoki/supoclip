import io
from pathlib import Path

import pytest

from src.clip_editor import ExportCancelled, _ffmpeg_clock_seconds, export_with_preset
from src.services.clip_export_service import (
    CLIP_EXPORT_QUEUE_MAX_SECONDS,
    CLIP_EXPORT_QUEUE_MISSING_GRACE_SECONDS,
    CLIP_EXPORT_STALE_SECONDS,
    export_download_filename,
    export_options_hash,
    queued_export_should_fail,
    serialize_export,
)
from src.workers.tasks import WorkerSettings


def test_export_options_hash_changes_when_the_source_file_changes():
    first = export_options_hash("tiktok", "s3://bucket/media/clips/a.mp4")
    same = export_options_hash("tiktok", "s3://bucket/media/clips/a.mp4")
    regenerated = export_options_hash("tiktok", "s3://bucket/media/clips/b.mp4")
    other_preset = export_options_hash("reels", "s3://bucket/media/clips/a.mp4")

    assert first == same
    assert first != regenerated
    assert first != other_preset


def test_download_filename_matches_the_preset_and_drops_header_breaks():
    assert export_download_filename("My Clip.mp4", "tiktok") == "My Clip_tiktok.mp4"
    assert export_download_filename('bad"name\n.mp4', "reels") == "badname_reels.mp4"


def test_serialize_hides_the_download_url_until_the_job_succeeds():
    queued = serialize_export(
        {"id": "1", "status": "queued", "progress": 0, "preset": "tiktok", "error_message": None},
        "/tasks/t/clips/c/exports/1/file",
    )
    failed = serialize_export(
        {"id": "1", "status": "failed", "progress": 10, "preset": "tiktok", "error_message": "Export failed. Try again."},
        None,
    )

    assert queued["download_url"] is None
    assert queued["error"] is None
    assert failed["error"] == "Export failed. Try again."
    assert failed["download_url"] is None


def test_queued_export_survives_a_long_wait_unless_the_job_is_gone_or_capped():
    waiting = CLIP_EXPORT_STALE_SECONDS + 60
    assert queued_export_should_fail(age_seconds=waiting, job_alive=True) is False
    assert queued_export_should_fail(age_seconds=waiting, job_alive=None) is False
    assert queued_export_should_fail(age_seconds=CLIP_EXPORT_QUEUE_MISSING_GRACE_SECONDS - 1, job_alive=False) is False
    assert queued_export_should_fail(age_seconds=waiting, job_alive=False) is True
    assert queued_export_should_fail(age_seconds=CLIP_EXPORT_QUEUE_MAX_SECONDS, job_alive=True) is True


def test_worker_registers_preset_export_with_a_single_try():
    registered = next(
        function
        for function in WorkerSettings.functions
        if getattr(function, "name", None) == "export_clip_preset"
    )
    assert registered.timeout_s == 30 * 60
    assert registered.max_tries == 1


def test_ffmpeg_clock_parses_progress_timestamps():
    assert _ffmpeg_clock_seconds("00:00:01.500000") == pytest.approx(1.5)
    assert _ffmpeg_clock_seconds("nope") is None


def test_progress_export_reports_percent_without_changing_the_sync_command(tmp_path, monkeypatch):
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"clip")
    percents = []

    class Process:
        def __init__(self, command, **kwargs):
            self.command = command
            self.stdout = iter(["out_time=00:00:00.500000\n", "out_time=00:00:01.000000\n"])
            self.stderr = io.StringIO("")
            self.returncode = 0

        def poll(self):
            return self.returncode

        def wait(self, timeout=None):
            return self.returncode

    monkeypatch.setattr("src.clip_editor.probe_video_geometry", lambda _path: type("Geometry", (), {"fps": 30})())
    monkeypatch.setattr("src.clip_editor._ffprobe_duration", lambda _path: 1.0)
    monkeypatch.setattr("src.clip_editor.subprocess.Popen", lambda command, **kwargs: Process(command, **kwargs))

    destination = tmp_path / "out" / "export.mp4"
    export_with_preset(source, tmp_path, "tiktok", output_path=destination, on_progress=percents.append)

    assert percents == [50, 99]


def test_progress_export_stops_when_cancelled(tmp_path, monkeypatch):
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"clip")
    terminated = []

    class Process:
        def __init__(self, command, **kwargs):
            self.stdout = iter(["out_time=00:00:00.100000\n"])
            self.stderr = io.StringIO("")
            self.returncode = None

        def poll(self):
            return self.returncode

        def terminate(self):
            terminated.append(True)
            self.returncode = -15

        def wait(self, timeout=None):
            self.returncode = -15
            return self.returncode

    monkeypatch.setattr("src.clip_editor.probe_video_geometry", lambda _path: None)
    monkeypatch.setattr("src.clip_editor._ffprobe_duration", lambda _path: 2.0)
    monkeypatch.setattr("src.clip_editor.subprocess.Popen", lambda command, **kwargs: Process(command, **kwargs))

    with pytest.raises(ExportCancelled):
        export_with_preset(
            source,
            tmp_path,
            "shorts",
            output_path=tmp_path / "export.mp4",
            on_progress=lambda _percent: None,
            should_stop=lambda: True,
        )
    assert terminated == [True]
    assert Path(tmp_path / "export.mp4").name == "export.mp4"
