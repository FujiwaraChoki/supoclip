"""Each render of a task assembles its YouTube timeline in its own directory.

Regression: overlapping edits of one task (a retried settings request) shared
``youtube-work/<video>-<task>``; the second render deleted it, and the first
lost its source mid-render.
"""

import os
import time

import pytest

from src import youtube_source as source
from src.config import Config, set_config_override
from src.youtube_utils import cleanup_downloaded_files


@pytest.fixture
def work_config(tmp_path):
    config = Config()
    config.temp_dir = str(tmp_path)
    set_config_override(config)
    yield config
    set_config_override(None)


def test_overlapping_renders_of_one_task_do_not_delete_each_others_source(work_config):
    first = source._work_dir("abcdefghijk", "task-1234567890")
    (first / "render.mp4").write_bytes(b"first render source")

    second = source._work_dir("abcdefghijk", "task-1234567890")
    (second / "render.mp4").write_bytes(b"second render source")

    assert first != second
    assert (first / "render.mp4").read_bytes() == b"first render source"

    # The second render finishing removes only its own timeline.
    cleanup_downloaded_files("abcdefghijk", source_path=second / "render.mp4")
    assert not second.exists()
    assert (first / "render.mp4").read_bytes() == b"first render source"


def test_abandoned_work_directories_are_swept(work_config):
    stale = source._work_dir("abcdefghijk", "task-crashed-render")
    old = time.time() - source.WORK_DIR_MAX_AGE_SECONDS - 60
    os.utime(stale, (old, old))
    live = source._work_dir("abcdefghijk", "task-live-render")

    source._work_dir("zyxwvutsrqp", "task-other")

    assert not stale.exists()
    assert live.exists()
