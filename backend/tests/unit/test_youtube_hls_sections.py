"""HLS timeline contracts and real native downloads of selected segments."""
import functools
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import shutil
import subprocess
import threading
from unittest.mock import Mock
from urllib.parse import urljoin

import pytest

from src import youtube_proxy as mod
from src import youtube_source as source

BASE = 'https://manifest.googlevideo.com/path/media.m3u8?signature=private'
PLAYLIST = '''#EXTM3U
#EXT-X-VERSION:7
#EXT-X-TARGETDURATION:4
#EXT-X-MEDIA-SEQUENCE:70
#EXT-X-MAP:URI="init.mp4"
#EXTINF:4,
segment0.m4s
#EXTINF:3.5,
segment1.m4s
#EXTINF:4.5,
segment2.m4s
#EXT-X-ENDLIST
'''


def test_playlist_timeline_and_selected_init_sequence():
    fragments = mod.parse_hls_fragments(PLAYLIST, BASE, 12)
    selected, start, end = mod.select_fragments_for_window(fragments, 4.2, 7.2)
    assert (start, end) == (4, 7.5)
    assert [f['url'] for f in selected] == [
        'https://manifest.googlevideo.com/path/init.mp4',
        'https://manifest.googlevideo.com/path/segment1.m4s',
    ]
    clipped = mod._section_hls_playlist(selected)
    assert '#EXT-X-MEDIA-SEQUENCE:71' in clipped
    assert '#EXT-X-MAP:URI=' in clipped
    assert 'segment1.m4s' in clipped and 'segment0.m4s' not in clipped and 'segment2.m4s' not in clipped


@pytest.mark.parametrize('playlist', [
    PLAYLIST.replace('#EXT-X-ENDLIST', ''),
    PLAYLIST.replace('#EXT-X-MAP:URI="init.mp4"', '#EXT-X-KEY:METHOD=AES-128,URI="key"'),
    PLAYLIST.replace('#EXTINF:3.5,', '#EXT-X-DISCONTINUITY\n#EXTINF:3.5,'),
    PLAYLIST.replace('#EXTINF:3.5,', '#EXT-X-GAP\n#EXTINF:3.5,'),
    PLAYLIST.replace('#EXTINF:3.5,', '#EXT-X-BYTERANGE:100@0\n#EXTINF:3.5,'),
    PLAYLIST.replace('#EXTINF:3.5,', '#EXTINF:nan,'),
    PLAYLIST.replace('#EXTINF:3.5,', '#EXTINF:-2,'),
    PLAYLIST.replace('#EXTINF:3.5,', '#EXTINF:unknown,'),
    PLAYLIST.replace('#EXT-X-MEDIA-SEQUENCE:70', '#EXT-X-MEDIA-SEQUENCE:unknown'),
    PLAYLIST.replace('segment1.m4s', 'http://127.0.0.1/private'),
    PLAYLIST.replace('segment1.m4s', 'https://attacker.example/video'),
    PLAYLIST.replace('segment1.m4s', 'https://media.googlevideo.com:invalid/video'),
    PLAYLIST.replace('segment1.m4s', 'https://[invalid/video'),
    '#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1000000\nvariant.m3u8\n#EXT-X-ENDLIST',
])
def test_unsupported_or_invalid_playlists_use_full_fallback(playlist):
    with pytest.raises(mod.PartialDownloadUnavailable):
        mod.parse_hls_fragments(playlist, BASE, 12)


def test_incomplete_source_timeline_is_rejected():
    with pytest.raises(mod.PartialDownloadUnavailable, match='full source timeline'):
        mod.parse_hls_fragments(PLAYLIST, BASE, 30)


def test_playlist_fetch_uses_proxy_client_and_counts_bytes(monkeypatch):
    response = Mock(url=BASE)
    response.read.return_value = PLAYLIST.encode()
    client = Mock()
    client.__enter__ = Mock(return_value=client)
    client.__exit__ = Mock(return_value=False)
    client.urlopen.return_value = response
    factory = Mock(return_value=client)
    monkeypatch.setattr('yt_dlp.YoutubeDL', factory)
    budget = mod.ByteBudget(10000)
    options = {'proxy': 'http://login:password@proxy.example:823'}
    fmt = {'url': BASE, 'protocol': 'm3u8_native', 'http_headers': {'X-Test': 'media'}}
    expanded = mod.expand_hls_format(fmt, options, budget, 12)
    assert len(expanded['fragments']) == 4
    factory.assert_called_once_with(options)
    assert client.urlopen.call_args.args[0].headers['X-Test'] == 'media'
    assert budget.snapshot() == len(PLAYLIST.encode())
    response.close.assert_called_once()
    assert 'fragments' not in fmt


@pytest.mark.parametrize('size,limit', [(4_000_001, 0), (100, 50)])
def test_playlist_size_and_shared_budget_are_bounded(monkeypatch, size, limit):
    response = Mock(url=BASE)
    response.read.return_value = b'x' * size
    client = Mock()
    client.__enter__ = Mock(return_value=client)
    client.__exit__ = Mock(return_value=False)
    client.urlopen.return_value = response
    monkeypatch.setattr('yt_dlp.YoutubeDL', Mock(return_value=client))
    with pytest.raises((mod.PartialDownloadUnavailable, mod.DownloadBudgetExceeded)):
        mod.expand_hls_format({'url': BASE, 'protocol': 'm3u8_native'}, {}, mod.ByteBudget(limit), 12)
    response.read.assert_called_once_with(4_000_001)
    response.close.assert_called_once()


class Quiet:
    def debug(self, *args): pass
    def warning(self, *args): pass
    def error(self, *args): pass


def test_hls_audio_without_codec_metadata_is_selected_for_sections():
    formats = [
        {'format_id': '140', 'protocol': 'https', 'vcodec': 'none',
         'acodec': 'aac', 'abr': 128, 'ext': 'm4a'},
        {'format_id': '233', 'protocol': 'm3u8_native', 'vcodec': 'none',
         'acodec': None, 'format_note': 'Default, low', 'ext': 'mp4'},
        {'format_id': '234', 'protocol': 'm3u8_native', 'vcodec': 'none',
         'acodec': None, 'format_note': 'Default, high', 'ext': 'mp4'},
    ]
    assert mod.pick_audio_format(formats, modest=False)['format_id'] == '234'
    assert mod.pick_audio_format(formats, modest=True)['format_id'] == '140'


def test_original_audio_wins_over_first_dub_and_analysis_uses_128k_track():
    formats = [
        {'format_id': '233-0', 'protocol': 'm3u8_native', 'vcodec': 'none',
         'acodec': None, 'format_note': 'English - dubbed-auto'},
        {'format_id': '139-2', 'protocol': 'https', 'vcodec': 'none', 'acodec': 'aac',
         'ext': 'm4a', 'abr': 48, 'language_preference': 10},
        {'format_id': '140-2', 'protocol': 'https', 'vcodec': 'none', 'acodec': 'aac',
         'ext': 'm4a', 'abr': 128, 'language_preference': 10},
        {'format_id': '234-2', 'protocol': 'm3u8_native', 'vcodec': 'none',
         'acodec': None, 'format_note': 'Portuguese - original', 'language_preference': 10},
    ]
    assert mod.pick_audio_format(formats, modest=False)['format_id'] == '234-2'
    assert mod.pick_audio_format(formats, modest=True)['format_id'] == '140-2'
    assert mod.pick_audio_format(formats[:1], modest=False) is None


def test_legacy_audio_cache_is_invalidated_without_losing_full_source(tmp_path):
    (tmp_path / source.MANIFEST_NAME).write_text(json.dumps({
        'version': 1, 'audio': 'audio.m4a', 'ranges': [{'file': 'old.mp4'}], 'full': 'full.mp4',
    }))
    manifest = source._load_manifest(tmp_path, 'abcdefghijk')
    assert manifest['audio'] is None and manifest['ranges'] == []
    assert manifest['full'] == 'full.mp4'
    assert manifest['audio_policy'] == source.AUDIO_POLICY


@pytest.fixture(params=['mpegts', 'fmp4'])
def hls_server(tmp_path, request):
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        pytest.skip('ffmpeg and ffprobe required')
    segment_type = request.param
    suffix = 'ts' if segment_type == 'mpegts' else 'm4s'
    subprocess.run([
        'ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=160x90:rate=10',
        '-t', '20', '-an', '-c:v', 'libx264', '-preset', 'ultrafast', '-g', '20',
        '-sc_threshold', '0', '-hls_time', '2', '-hls_playlist_type', 'vod',
        '-hls_segment_type', segment_type, '-hls_fmp4_init_filename', 'v-init.mp4',
        '-hls_segment_filename', str(tmp_path / f'v%03d.{suffix}'), str(tmp_path / 'video.m3u8'),
    ], check=True, capture_output=True)
    subprocess.run([
        'ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
        'aevalsrc=sin(2*PI*(200*t+8*t*t)):s=16000:d=20', '-c:a', 'aac',
        '-hls_time', '2', '-hls_playlist_type', 'vod',
        '-hls_segment_type', segment_type, '-hls_fmp4_init_filename', 'a-init.mp4',
        '-hls_segment_filename', str(tmp_path / f'a%03d.{suffix}'), str(tmp_path / 'audio.m3u8'),
    ], check=True, capture_output=True)
    requests = []
    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_GET(self):
            requests.append(self.path)
            super().do_GET()
    server = ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(Handler, directory=str(tmp_path)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}/', tmp_path, requests
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def test_real_native_section_download_skips_unselected_segments_and_preserves_alignment(hls_server, monkeypatch):
    import numpy as np
    base, directory, requests = hls_server
    # The production validator allows only HTTPS Google media; this fixture is local.
    monkeypatch.setattr(mod, '_hls_url', urljoin)
    options = {'quiet': True, 'logger': Quiet(), 'proxy': '', 'socket_timeout': 5}
    budget = mod.ByteBudget(2_000_000)
    video = mod.expand_hls_format({'url': base + 'video.m3u8', 'protocol': 'm3u8_native',
        'ext': 'mp4', 'vcodec': 'avc1', 'acodec': 'none'}, options, budget, 20)
    audio = mod.expand_hls_format({'url': base + 'audio.m3u8', 'protocol': 'm3u8_native',
        'ext': 'm4a', 'vcodec': 'none', 'acodec': 'aac'}, options, budget, 20)
    vf, vs, ve = mod.select_window(video, 6.1, 10.3)
    af, audio_start, audio_end = mod.select_window(audio, 6.1, 10.3)
    assert len(vf) < len(video['fragments']) and len(af) < len(audio['fragments'])
    vp = mod.native_download_format(options, 'https://youtube.com/watch?v=abcdefghijk',
                                    'abcdefghijk', video, directory / 'out-video', vf)
    ap = mod.native_download_format(options, 'https://youtube.com/watch?v=abcdefghijk',
                                    'abcdefghijk', audio, directory / 'out-audio', af)
    selected_paths = { '/' + Path(f['url']).name for f in vf + af }
    assert {p for p in requests if not p.endswith('.m3u8')} == selected_paths
    assert requests.count('/video.m3u8') == 1 and requests.count('/audio.m3u8') == 1
    start, end = max(vs, audio_start), min(ve, audio_end)
    dest = directory / 'section.mp4'
    mod._mux_local_window(vp, ap, dest, video_offset=start-vs,
                          audio_offset=start-audio_start, duration=end-start)
    probe = json.loads(subprocess.run(['ffprobe', '-v', 'error', '-show_entries',
        'format=duration:stream=codec_type,start_time', '-of', 'json', str(dest)],
        capture_output=True, text=True, check=True).stdout)
    assert abs(float(probe['format']['duration']) - (end-start)) < .15
    assert {'audio', 'video'} == {s['codec_type'] for s in probe['streams']}
    assert max(abs(float(s['start_time'])) for s in probe['streams']) < .15
    def pcm(path):
        return np.frombuffer(subprocess.run(['ffmpeg', '-v', 'error', '-i', str(path),
            '-map', '0:a:0', '-ar', '8000', '-f', 'f32le', '-'],
            capture_output=True, check=True).stdout, dtype=np.float32)
    reference = pcm(directory / 'audio.m3u8')
    actual = pcm(dest)[:8000]
    lo = int((start-.5)*8000)
    comparison = reference[lo:int((start+.5)*8000)+len(actual)]
    best = np.argmax(np.correlate(comparison, actual, mode='valid'))
    assert abs((lo + best)/8000 - start) < .15


def test_non_keyframe_cuts_keep_later_spans_on_the_source_timeline(hls_server):
    import numpy as np
    _, directory, _ = hls_server
    full = directory / 'full.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-i', str(directory / 'video.m3u8'),
        '-i', str(directory / 'audio.m3u8'), '-map', '0:v:0', '-map', '1:a:0',
        '-c', 'copy', '-shortest', str(full)], capture_output=True, check=True)
    piece = source.CachedPiece(0, 20, full)
    dest = directory / 'assembled.mp4'
    timeline = source.assemble_timeline([(piece, 2.3, 5.3), (piece, 9.7, 12.7)], dest)
    def pcm(path):
        return np.frombuffer(subprocess.run(['ffmpeg', '-v', 'error', '-i', str(path),
            '-map', '0:a:0', '-ac', '1', '-ar', '8000', '-f', 'f32le', '-'],
            capture_output=True, check=True).stdout, dtype=np.float32)
    reference, actual = pcm(full), pcm(dest)
    # The final AAC frame can extend decoded samples by a few milliseconds.
    assert abs(len(actual)/8000 - 6) < .1
    for span in timeline.spans:
        original, local = span.original_start + 1, span.local_start + 1
        needle = actual[int(local*8000):int((local+1)*8000)]
        lo = int((original-.4)*8000)
        search = reference[lo:int((original+.4)*8000)+len(needle)]
        best = np.argmax(np.correlate(search, needle, mode='valid'))
        assert abs((lo+best)/8000 - original) < .1
