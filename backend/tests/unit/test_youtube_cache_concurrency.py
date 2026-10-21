"""Concurrent jobs share one immutable download per video and cached range."""
from concurrent.futures import ThreadPoolExecutor
import threading

import pytest

from src.config import Config, set_config_override
from src import youtube_source as source


@pytest.mark.parametrize('kind', ['audio', 'section'])
def test_same_video_jobs_wait_for_and_reuse_inflight_download(kind, tmp_path, monkeypatch):
    config = Config()
    config.temp_dir = str(tmp_path)
    config.youtube_proxy_url = 'http://user:password@proxy.example:823'
    config.youtube_section_padding_seconds = 0
    monkeypatch.setattr(source, '_storage', lambda: None)
    started, release, duplicate, second_started = (threading.Event() for _ in range(4))
    calls = []

    def download(*args, **kwargs):
        calls.append(kwargs['task_id'])
        if len(calls) > 1:
            duplicate.set()
        started.set()
        assert release.wait(5), 'Test did not release the download'
        path = args[2].with_suffix('.m4a') if kind == 'audio' else args[4]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'immutable media')
        return path if kind == 'audio' else (path, args[2], args[3])

    monkeypatch.setattr(source, 'download_audio' if kind == 'audio' else 'download_section_file', download)

    def job(task_id):
        if task_id == 'second-job':
            second_started.set()
        if kind == 'audio':
            return source.download_audio_for_job('https://youtube.com/watch?v=abcdefghijk',
                                                 'abcdefghijk', task_id=task_id)
        return source.materialize_clip_source('https://youtube.com/watch?v=abcdefghijk',
            'abcdefghijk', [{'start_time': 10, 'end_time': 20}], task_id=task_id, duration=100)

    set_config_override(config)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(job, 'first-job')
            assert started.wait(2)
            second = pool.submit(job, 'second-job')
            assert second_started.wait(2)
            try:
                assert not duplicate.wait(.15), 'Concurrent jobs started duplicate writes to the cache'
            finally:
                release.set()
            assert first.result(timeout=3).read_bytes() == b'immutable media'
            assert second.result(timeout=3).read_bytes() == b'immutable media'
        assert calls == ['first-job']
    finally:
        release.set()
        set_config_override(None)
