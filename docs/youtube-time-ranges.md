# YouTube live links and time ranges

SupoClip accepts recorded videos addressed as `youtube.com/live/<video-id>` as well
as watch, short and embed URLs. This does not record an ongoing livestream.

To process part of a video, include a time range in its URL:

- `https://youtu.be/abcdefghijk?t=10m`: start at minute 10.
- `https://www.youtube.com/live/abcdefghijk?start=600&end=900`: process minutes 10–15.
- `https://youtu.be/abcdefghijk#t=1h2m3s`: start at 1:02:03.

Timestamps accept whole seconds or YouTube's `h`, `m`, `s` notation. `start` takes
precedence over `t`. An omitted end means the end of the video; an end beyond the
video's duration is capped to that duration. Empty, repeated, malformed and
reversed ranges are rejected before creating a task or using a generation.
Duration limits apply to the selected interval.

Ranges use the local yt-dlp/FFmpeg downloader even when Apify is configured for
full videos. If yt-dlp fails, SupoClip does not silently download the full video
through another provider. Cutting at the requested boundaries may require
re-encoding. Transcript and clip timestamps are relative to the cropped media.
Cached media, word timings and analysis are isolated from the original video and
other ranges. Existing full-video analysis caches remain compatible.

Whisper and AssemblyAI transcribe the cropped audio. The optional
`youtube_captions` provider retains only complete caption cues inside the range;
speech crossing either boundary may be omitted with that provider.

## Verification

Run the normal backend and frontend test suites. A synthetic end-to-end download
check is also available without YouTube access or provider credentials:

```sh
cd backend
PYTHONPATH=. .venv/bin/python tests/live/verify_youtube_ranges.py
```

This uses real yt-dlp and FFmpeg against localhost media and verifies the output's
duration, frame color and audio frequency. It does not validate YouTube's current
remote extraction behavior or speech recognition quality.
