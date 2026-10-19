"""Exercise production range options with real yt-dlp/ffmpeg and synthetic localhost media.

Run inside the backend container with PYTHONPATH=/app. No provider credentials,
YouTube requests, transcription requests or application database access are used.
"""

from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from threading import Thread
from unittest.mock import patch

import numpy as np
import yt_dlp

from src.config import Config, set_config_override
from src import youtube_utils as youtube


def run(*args):
    return subprocess.run(args, check=True, capture_output=True).stdout


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass


with TemporaryDirectory(prefix="supoclip-range-qa-") as directory:
    root = Path(directory)
    fixture = root / "fixture.mp4"
    run(
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "color=c=red:s=160x120:r=10:d=6",
        "-f", "lavfi", "-i", "color=c=blue:s=160x120:r=10:d=6",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=16000:duration=6",
        "-f", "lavfi", "-i", "sine=frequency=880:sample_rate=16000:duration=6",
        "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v];[2:a][3:a]concat=n=2:v=0:a=1[a]",
        "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "ultrafast",
        "-c:a", "aac", "-movflags", "+faststart", str(fixture),
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(QuietHandler, directory=str(root)))
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    config = Config()
    config.temp_dir = str(root / "downloads")
    set_config_override(config)
    real_download = yt_dlp.YoutubeDL.download
    local_url = f"http://127.0.0.1:{server.server_port}/fixture.mp4"
    try:
        # Substitute only the remote extractor input. Production builds options,
        # chooses the range provider, finds output and probes it without mocks.
        with patch.object(youtube, "get_youtube_video_info", return_value={"duration": 12, "title": "Synthetic fixture"}), patch.object(
            yt_dlp.YoutubeDL, "download", lambda self, urls: real_download(self, [local_url])
        ):
            output = youtube.download_youtube_video("https://youtube.com/live/abcdefghijk?start=7&end=10")
        assert output and output.exists(), "No cropped output"
        duration = float(run("ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(output)))
        assert abs(duration - 3) < 0.3, duration
        pixel = run("ffmpeg", "-v", "error", "-i", str(output), "-frames:v", "1", "-vf", "scale=1:1", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1")
        assert pixel[2] > 200 and pixel[0] < 40, list(pixel)
        pcm = run("ffmpeg", "-v", "error", "-i", str(output), "-vn", "-ar", "16000", "-ac", "1", "-f", "s16le", "pipe:1")
        samples = np.frombuffer(pcm, dtype=np.int16)
        frequencies = np.fft.rfftfreq(len(samples), 1 / 16000)
        peak = float(frequencies[np.argmax(np.abs(np.fft.rfft(samples)))])
        assert abs(peak - 880) < 5, peak
        print(json.dumps({"source_seconds": 12, "requested_start": 7, "requested_end": 10,
                          "output_seconds": duration, "rgb": list(pixel), "audio_peak_hz": peak,
                          "cache_id": output.stem, "external_requests": 0, "result": "passed"}))
    finally:
        set_config_override(None)
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
