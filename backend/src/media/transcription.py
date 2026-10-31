"""Transcription helpers for the video pipeline."""

from concurrent.futures import ThreadPoolExecutor
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from pathlib import Path
import assemblyai as aai
from ..config import get_config
import httpx
import json
import re
import threading
import time
from .common import (
    ANALYSIS_LONG_UTTERANCE_MAX_DURATION_MS,
    ANALYSIS_LONG_UTTERANCE_MAX_WORDS,
    ANALYSIS_SEGMENT_MAX_DURATION_MS,
    ANALYSIS_SEGMENT_MAX_WORDS,
    ANALYSIS_SEGMENT_MIN_WORDS,
    ANALYSIS_UTTERANCE_SPLIT_THRESHOLD_MS,
    ANALYSIS_UTTERANCE_SPLIT_THRESHOLD_WORDS,
    TRANSCRIPT_CACHE_SCHEMA_VERSION,
    _WHISPER_AVAILABLE,
    _WHISPER_MODEL_CACHE,
    _whisper,
    logger,
)
from .ffmpeg import (
    run_ffmpeg_command,
    select_audio_stream_index,
)


def _prepare_audio_for_transcription(video_path: Path) -> Path:
    """Extract a compact audio-only file for transcription."""
    audio_path = video_path.with_name(f"{video_path.stem}.transcription.mp3")
    if audio_path.exists() and audio_path.stat().st_size > 0:
        return audio_path

    # Transcribe the same track the renderer cuts, not ffmpeg's implicit pick.
    audio_stream = select_audio_stream_index(video_path)
    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        *(["-map", f"0:a:{audio_stream}"] if audio_stream is not None else []),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-b:a",
        "64k",
        str(audio_path),
    ]
    try:
        result = run_ffmpeg_command(command, timeout=900)
    except FileNotFoundError:
        logger.warning(
            "ffmpeg is not available; falling back to source video for transcription"
        )
        return video_path

    if result.returncode != 0 or not audio_path.exists() or audio_path.stat().st_size == 0:
        logger.warning(
            "Failed to extract transcription audio with ffmpeg; falling back to source video"
        )
        return video_path

    logger.info(
        "Prepared transcription audio: %s (%.2f MB)",
        audio_path,
        audio_path.stat().st_size / (1024 * 1024),
    )
    return audio_path


def _submit_and_wait_for_assemblyai_transcript(
    transcriber,
    media_path: Path,
    config_obj,
    timeout_seconds: int,
):
    """Submit a transcript job and poll with a total timeout."""
    # Uploads are safe to retry: they do not create a paid transcript. Keep the
    # transcript POST outside this loop because a timeout may hide its success.
    for attempt in range(1, 4):
        try:
            audio_url = transcriber.upload_file(str(media_path))
            break
        except (httpx.TransportError, aai.AssemblyAIError) as error:
            status = getattr(error, "status_code", None)
            retryable = isinstance(error, httpx.TransportError) or status in {408, 429, 500, 502, 503, 504}
            if not retryable:
                raise
            if attempt == 3:
                raise RuntimeError(
                    "AssemblyAI audio upload unavailable after 3 attempts"
                    + (f" (HTTP {status})." if status else ".")
                ) from None
            logger.warning("AssemblyAI audio upload failed (HTTP %s); retrying upload (%s/3)", status, attempt)
            time.sleep(2.0 ** attempt)
    submitted = transcriber.submit(audio_url, config=config_obj)
    if not submitted.id:
        raise RuntimeError("AssemblyAI did not return a transcript ID")

    logger.info("AssemblyAI transcript submitted: %s", submitted.id)
    deadline = time.monotonic() + timeout_seconds
    next_log_at = 0.0
    consecutive_failures = 0

    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(
                f"AssemblyAI transcript {submitted.id} did not complete within {timeout_seconds}s"
            )
        # The SDK poller uses the upload timeout (often 900s) for every GET.
        # Bound each poll without changing the shared client's timeout.
        try:
            response = submitted._client.http_client.get(  # noqa: SLF001
                f"{aai.api.ENDPOINT_TRANSCRIPT}/{submitted.id}",
                timeout=min(30.0, remaining),
            )
            response.raise_for_status()
            response = aai.types.TranscriptResponse(**response.json())
            consecutive_failures = 0
        except (httpx.TransportError, httpx.HTTPStatusError) as error:
            if isinstance(error, httpx.HTTPStatusError) and error.response.status_code not in {408, 429, 500, 502, 503, 504}:
                raise
            consecutive_failures += 1
            logger.warning("AssemblyAI status polling unavailable; retrying the existing transcript")
            time.sleep(min(30.0, 2.0 ** min(consecutive_failures, 4),
                           max(0.0, deadline - time.monotonic())))
            continue
        transcript = aai.Transcript.from_response(
            client=submitted._client,  # noqa: SLF001
            response=response,
        )

        if transcript.status in (
            aai.TranscriptStatus.completed,
            aai.TranscriptStatus.error,
        ):
            return transcript

        now = time.monotonic()
        if now >= deadline:
            raise TimeoutError(
                f"AssemblyAI transcript {submitted.id} did not complete within {timeout_seconds}s"
            )

        if now >= next_log_at:
            logger.info(
                "AssemblyAI transcript %s still %s",
                submitted.id,
                transcript.status,
            )
            next_log_at = now + 30

        time.sleep(min(aai.settings.polling_interval, max(0.0, deadline - now)))


def _assemblyai_speech_models_value(speech_model: str) -> List[str]:
    """Prefer Universal-3.5 Pro; retain Universal-2 for unsupported languages.

    Explicit low-cost aliases remain available for self-hosted configurations.
    Legacy quality aliases upgrade to the current Pro model.
    """
    normalized = (speech_model or "universal-3-5-pro").strip().lower()
    if normalized in {"nano", "universal-2"}:
        return ["universal-2"]
    return ["universal-3-5-pro", "universal-2"]


def _get_whisper_model(model_name: str = "base"):
    """Load and cache a Whisper model by name."""
    if not _WHISPER_AVAILABLE:
        raise RuntimeError(
            "Whisper is not installed. Install it with: uv add openai-whisper"
        )
    if model_name not in _WHISPER_MODEL_CACHE:
        logger.info("Loading Whisper model: %s", model_name)
        _WHISPER_MODEL_CACHE[model_name] = _whisper.load_model(model_name)
    return _WHISPER_MODEL_CACHE[model_name]


def transcribe_with_whisper(
    video_path: Path, model_name: str = "base", language: Optional[str] = None
) -> Dict[str, Any]:
    """Transcribe video using local Whisper with word-level timestamps.

    ``language`` None lets Whisper detect it; AssemblyAI-style codes such as
    ``en_us`` are reduced to the ISO 639-1 base Whisper expects.
    """
    audio_path = _prepare_audio_for_transcription(video_path)
    model = _get_whisper_model(model_name)
    whisper_language = language.split("_", 1)[0] if language else None
    logger.info(
        "Starting Whisper transcription with model: %s (language: %s)",
        model_name,
        whisper_language or "auto",
    )
    return model.transcribe(str(audio_path), word_timestamps=True, language=whisper_language)


def _whisper_result_to_transcript_data(whisper_result: Dict[str, Any]) -> Dict[str, Any]:
    """Convert a Whisper result dict to the standard transcript-cache format.

    Whisper reports timings in seconds; the cache (and AssemblyAI path) stores
    milliseconds, so each timestamp is multiplied by 1000 here.
    """
    words_data: List[Dict[str, Any]] = []
    utterances_data: List[Dict[str, Any]] = []

    for segment in whisper_result.get("segments") or []:
        seg_words = [
            {
                "text": w.get("word", w.get("text", "")),
                "start": int(w["start"] * 1000) if isinstance(w.get("start"), float) else int(w.get("start", 0)),
                "end": int(w["end"] * 1000) if isinstance(w.get("end"), float) else int(w.get("end", 0)),
                "confidence": w.get("probability", w.get("confidence", 1.0)),
                "speaker": None,
            }
            for w in segment.get("words") or []
        ]
        utterances_data.append(
            {
                "text": segment.get("text", ""),
                "start": int(segment["start"] * 1000) if "start" in segment else 0,
                "end": int(segment["end"] * 1000) if "end" in segment else 0,
                "speaker": None,
                "words": seg_words,
            }
        )
        words_data.extend(seg_words)

    return {
        "version": TRANSCRIPT_CACHE_SCHEMA_VERSION,
        "words": words_data,
        "utterances": utterances_data,
        "text": whisper_result.get("text", ""),
        "language_code": whisper_result.get("language"),
    }


def transcribe_with_youtube_captions(video_url: str) -> Optional[str]:
    """Extract a plain-text transcript from a YouTube video's captions via yt-dlp.

    Only valid for YouTube-sourced videos. Returns plain text without word-level
    timestamps, so subtitle generation is not supported on this path.
    """
    try:
        import yt_dlp
    except ImportError:
        logger.error("yt-dlp is required for YouTube caption extraction")
        return None

    video_id = None
    match = re.search(r"(?:v=|youtu\.be/|shorts/)([A-Za-z0-9_-]{11})", video_url)
    if match:
        video_id = match.group(1)

    if not video_id:
        logger.error("Could not extract YouTube video ID from URL: %s", video_url)
        return None

    temp_dir = Path(get_config().temp_dir)
    temp_dir.mkdir(parents=True, exist_ok=True)
    subs_path = temp_dir / f"{video_id}.en.vtt"

    try:
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "writesubtitles": True,
            "writeautomaticsub": True,
            "subtitleslangs": ["en"],
            "subtitlesformat": "vtt",
            "skip_download": True,
            "outtmpl": str(temp_dir / video_id),
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([video_url])

        if subs_path.exists():
            text = subs_path.read_text(encoding="utf-8")
            lines = []
            for line in text.splitlines():
                stripped = line.strip()
                if (
                    stripped
                    and not stripped.startswith("WEBVTT")
                    and not stripped.startswith("Kind:")
                    and not stripped.startswith("Language:")
                    and "-->" not in line
                    and not stripped.startswith("NOTE")
                    and not re.match(r"^\d+$", stripped)
                ):
                    lines.append(stripped)
            return " ".join(lines)

        logger.warning("No English captions found for video %s", video_id)
        return None

    except Exception as e:
        logger.error("Failed to extract YouTube captions: %s", e)
        return None
    finally:
        for f in temp_dir.glob(f"{video_id}.*"):
            if f.suffix in (".vtt", ".srt", ".ttml", ".json"):
                try:
                    f.unlink()
                except OSError:
                    pass


def get_video_transcript(
    video_path: Path,
    speech_model: str = "universal",
    source_url: Optional[str] = None,
) -> str:
    """Get a video transcript using the configured provider.

    Dispatches to AssemblyAI, local Whisper, Khaya AI, or YouTube captions based on
    ``TRANSCRIPTION_PROVIDER``. ``source_url`` enables the youtube_captions
    provider, which needs the original URL rather than a local file path.
    """
    logger.info(f"Getting transcript for: {video_path}")
    runtime_config = get_config()
    provider = runtime_config.transcription_provider

    if provider == "whisper":
        return _get_transcript_with_whisper(video_path, runtime_config)
    if provider == "khaya":
        return _get_transcript_with_khaya(video_path, runtime_config)
    if provider == "youtube_captions":
        if not source_url:
            raise ValueError(
                "youtube_captions provider requires a YouTube URL. "
                "Pass source_url to get_video_transcript()."
            )
        return _get_transcript_with_youtube_captions(source_url)
    return _get_transcript_with_assemblyai(video_path, speech_model, runtime_config)


def _get_transcript_with_assemblyai(
    video_path: Path, speech_model: str, runtime_config
) -> str:
    """Get transcript using AssemblyAI with word-level timing for precise subtitles."""
    # A job owns its client: concurrent jobs must not mutate shared SDK settings.
    client = aai.Client(settings=aai.Settings(
        api_key=runtime_config.assembly_ai_api_key,
        base_url=aai.settings.base_url,
        http_timeout=getattr(runtime_config, "assembly_ai_request_timeout_seconds", 60),
    ))
    transcriber = aai.Transcriber(client=client)

    # AssemblyAI now requires the plural `speech_models` list; the singular
    # `best`/`nano`/`universal` values were deprecated server-side.
    speech_models_value = _assemblyai_speech_models_value(speech_model)

    # Without a language, AssemblyAI assumes US English and garbles everything
    # else; detection also routes to the first listed model that supports it.
    language = runtime_config.transcription_language
    config_obj = aai.TranscriptionConfig(
        speaker_labels=True,
        punctuate=True,
        format_text=True,
        speech_models=speech_models_value,
        language_code=language or None,
        language_detection=None if language else True,
    )

    try:
        logger.info("Starting AssemblyAI transcription")
        transcription_media_path = _prepare_audio_for_transcription(video_path)
        # Once submitted, a polling timeout must not submit another paid job or
        # restart the full transcription budget. Retry status GETs above instead.
        transcript = _submit_and_wait_for_assemblyai_transcript(
            transcriber,
            transcription_media_path,
            config_obj,
            runtime_config.assembly_ai_http_timeout_seconds,
        )

        if transcript is None:
            raise RuntimeError("AssemblyAI transcription did not return a transcript")

        if transcript.status == aai.TranscriptStatus.error:
            logger.error(f"AssemblyAI transcription failed: {transcript.error}")
            raise Exception(f"Transcription failed: {transcript.error}")

        response = getattr(transcript, "json_response", None) or {}
        logger.info(
            "AssemblyAI transcript language: %s (confidence: %s)",
            response.get("language_code") or language or "unknown",
            response.get("language_confidence"),
        )

        formatted_lines = format_transcript_for_analysis(transcript)
        cache_transcript_data(video_path, transcript)

        result = "\n".join(formatted_lines)
        logger.info(
            f"Transcript formatted: {len(formatted_lines)} segments, {len(result)} chars"
        )
        return result

    except Exception as e:
        logger.error(f"Error in AssemblyAI transcription: {e}")
        raise
    finally:
        client.http_client.close()


def _get_transcript_with_whisper(video_path: Path, runtime_config) -> str:
    """Get transcript using local Whisper with word-level timestamps."""
    model_name = runtime_config.whisper_model
    logger.info("Starting Whisper transcription with model: %s", model_name)
    whisper_result = transcribe_with_whisper(
        video_path, model_name, runtime_config.transcription_language or None
    )

    formatted_lines = format_transcript_for_analysis(whisper_result)
    cache_transcript_data(video_path, whisper_result)

    result = "\n".join(formatted_lines)
    logger.info(
        "Whisper transcript formatted: %d segments, %d chars",
        len(formatted_lines),
        len(result),
    )
    return result


KHAYA_SEGMENT_MAX_SECONDS = 12.0
KHAYA_SEGMENT_MAX_WORDS = 30
KHAYA_SEGMENT_MAX_GAP_SECONDS = 1.5
KHAYA_MAX_ATTEMPTS = 4
# Each attempt likely counts against the Khaya quota, so a chunk the server
# keeps failing is split smaller instead of being retried at full length.
KHAYA_SERVER_ERROR_ATTEMPTS = 2
KHAYA_FALLBACK_PIECE_SECONDS = 120
KHAYA_MAX_SKIPPED_FRACTION = 0.25
# ffmpeg's segment muxer can leave a few-millisecond tail that Khaya cannot decode.
KHAYA_MIN_PIECE_SECONDS = 1.0


class KhayaTimestampsUnsupported(Exception):
    """Khaya rejected the requested timestamp granularity for this language."""


class KhayaServerError(RuntimeError):
    """Khaya kept failing a chunk with 5xx responses or dropped connections."""


class KhayaPieceRejected(RuntimeError):
    """Khaya could not decode one piece's audio; other pieces are unaffected."""


class _KhayaRateLimiter:
    """Spaces request starts so all worker threads stay under a per-minute cap."""

    def __init__(self, requests_per_minute: int):
        self.interval = 60.0 / max(1, requests_per_minute)
        self._lock = threading.Lock()
        self._next_start = 0.0
        self.calls = 0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            start = max(now, self._next_start)
            self._next_start = start + self.interval
            self.calls += 1
        if start > now:
            time.sleep(start - now)


def _split_audio_for_khaya(
    audio_path: Path, chunk_seconds: int, chunk_dir: Optional[Path] = None
) -> List[tuple]:
    """Cut audio into MP3 chunks; returns [(chunk_path, offset_seconds), ...]."""
    chunk_dir = chunk_dir or audio_path.with_name(f"{audio_path.stem}.khaya_chunks")
    chunk_dir.mkdir(parents=True, exist_ok=True)
    for stale in chunk_dir.glob("*"):
        stale.unlink()
    segment_list = chunk_dir / "segments.csv"
    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(audio_path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-b:a",
        "64k",
        "-f",
        "segment",
        "-segment_time",
        str(chunk_seconds),
        "-segment_list",
        str(segment_list),
        "-segment_list_type",
        "csv",
        str(chunk_dir / "chunk_%05d.mp3"),
    ]
    result = run_ffmpeg_command(command, timeout=900)
    if result.returncode != 0 or not segment_list.exists():
        raise RuntimeError("Failed to split audio into chunks for Khaya transcription")

    # Each CSV row is "filename,start,end"; the start is the chunk's true offset.
    chunks = []
    for line in segment_list.read_text().splitlines():
        parts = line.strip().split(",")
        if len(parts) < 3 or not parts[0]:
            continue
        start, end = float(parts[1]), float(parts[2])
        if end - start < KHAYA_MIN_PIECE_SECONDS:
            (chunk_dir / parts[0]).unlink(missing_ok=True)
            continue
        chunks.append((chunk_dir / parts[0], start))
    return chunks


def _khaya_transcribe_chunk(
    client: httpx.Client,
    runtime_config,
    chunk_path: Path,
    timestamps: str,
    limiter: Optional[_KhayaRateLimiter] = None,
) -> Dict[str, Any]:
    """POST one audio chunk to Khaya, retrying on rate limits and server errors."""
    url = f"{runtime_config.khaya_base_url}/transcribe"
    params = {"language": runtime_config.khaya_language, "timestamps": timestamps}
    audio_bytes = chunk_path.read_bytes()
    server_failures = 0
    for attempt in range(1, KHAYA_MAX_ATTEMPTS + 1):
        if limiter:
            limiter.wait()
        try:
            response = client.post(
                url,
                params=params,
                content=audio_bytes,
                headers={"Content-Type": "audio/mpeg"},
            )
        except httpx.TransportError as e:
            server_failures += 1
            if server_failures >= KHAYA_SERVER_ERROR_ATTEMPTS:
                raise KhayaServerError(f"Khaya request failed: {e}") from e
            time.sleep(2**attempt)
            continue

        if response.status_code == 200:
            return response.json()
        if response.status_code == 400:
            body = response.text
            if "UNSUPPORTED_TIMESTAMPS" in body:
                raise KhayaTimestampsUnsupported(body)
            if "INVALID_AUDIO_FORMAT" in body or "EMPTY_AUDIO" in body:
                raise KhayaPieceRejected(f"Khaya could not decode {chunk_path.name}: {body[:300]}")
            raise RuntimeError(f"Khaya rejected {chunk_path.name}: {body[:500]}")
        if response.status_code in (401, 403):
            raise RuntimeError(
                "Khaya rejected the API key. Check KHAYA_API_KEY and your subscription."
            )
        if response.status_code >= 500:
            server_failures += 1
            if server_failures >= KHAYA_SERVER_ERROR_ATTEMPTS:
                raise KhayaServerError(
                    f"Khaya transcription failed ({response.status_code}): {response.text[:500]}"
                )
            time.sleep(2**attempt)
            continue
        if response.status_code == 429 and attempt < KHAYA_MAX_ATTEMPTS:
            retry_after = response.headers.get("Retry-After", "")
            time.sleep(int(retry_after) if retry_after.isdigit() else 2**attempt)
            continue
        raise RuntimeError(
            f"Khaya transcription failed ({response.status_code}): {response.text[:500]}"
        )
    raise RuntimeError("Khaya transcription failed after retries")


def _khaya_words_from_response(payload: Dict[str, Any], offset: float) -> List[Dict[str, Any]]:
    """Return words with absolute float-second timings from one chunk response.

    Segment-only responses get their duration spread evenly over their words.
    """
    timings = payload.get("timings") or {}
    words = []
    for word in timings.get("words") or []:
        text = str(word.get("word", "")).strip()
        if text:
            words.append(
                {
                    "word": f" {text}",
                    "start": float(word.get("start", 0)) + offset,
                    "end": float(word.get("end", 0)) + offset,
                    "probability": 1.0,
                }
            )
    if words:
        return words

    for segment in timings.get("segments") or []:
        tokens = str(segment.get("text", "")).split()
        if not tokens:
            continue
        start = float(segment.get("start", 0)) + offset
        end = max(start, float(segment.get("end", 0)) + offset)
        step = (end - start) / len(tokens)
        for index, token in enumerate(tokens):
            words.append(
                {
                    "word": f" {token}",
                    "start": start + index * step,
                    "end": start + (index + 1) * step,
                    "probability": 1.0,
                }
            )
    return words


def _transcribe_khaya_piece(
    client: httpx.Client,
    runtime_config,
    limiter: _KhayaRateLimiter,
    cache_dir: Path,
    piece_path: Path,
    offset: float,
    piece_seconds: int,
    timestamps: str,
) -> tuple:
    """Transcribe one piece; returns (words, skipped_ranges).

    Successful responses are saved under ``cache_dir`` so a resumed task never
    pays for the same audio twice. A piece Khaya keeps failing is re-split into
    shorter pieces; a short piece that still fails is skipped and reported.
    """
    cache_path = cache_dir / (
        f"{runtime_config.khaya_language}_{timestamps}_{round(offset * 1000)}_{piece_seconds}.json"
    )
    if cache_path.exists():
        payload = json.loads(cache_path.read_text())
        # A piece Khaya already failed is not retried (and re-billed) on resume;
        # delete its cache file to try it again.
        if payload.get("skipped"):
            return [], [(offset, offset + piece_seconds)]
        if not payload.get("split"):
            return _khaya_words_from_response(payload, offset), []

    try:
        if cache_path.exists():
            # Already split on an earlier run: go straight to its saved sub-pieces.
            raise KhayaServerError("split on an earlier run")
        payload = _khaya_transcribe_chunk(client, runtime_config, piece_path, timestamps, limiter)
    except KhayaPieceRejected as e:
        logger.warning("Skipping %.0fs-%.0fs: %s", offset, offset + piece_seconds, e)
        cache_path.write_text(json.dumps({"skipped": True, "reason": str(e)[:300]}))
        return [], [(offset, offset + piece_seconds)]
    except KhayaServerError as e:
        if piece_seconds > KHAYA_FALLBACK_PIECE_SECONDS:
            logger.warning(
                "Khaya kept failing the %ss piece at %.0fs (%s); retrying it in %ss pieces",
                piece_seconds,
                offset,
                e,
                KHAYA_FALLBACK_PIECE_SECONDS,
            )
            cache_path.write_text(json.dumps({"split": True}))
            sub_pieces = _split_audio_for_khaya(
                piece_path,
                KHAYA_FALLBACK_PIECE_SECONDS,
                cache_dir / f"split_{round(offset * 1000)}",
            )
            words: List[Dict[str, Any]] = []
            skipped: List[tuple] = []
            for sub_path, sub_offset in sub_pieces:
                sub_words, sub_skipped = _transcribe_khaya_piece(
                    client,
                    runtime_config,
                    limiter,
                    cache_dir,
                    sub_path,
                    offset + sub_offset,
                    KHAYA_FALLBACK_PIECE_SECONDS,
                    timestamps,
                )
                words.extend(sub_words)
                skipped.extend(sub_skipped)
                sub_path.unlink(missing_ok=True)
            return words, skipped
        logger.warning(
            "Skipping %.0fs-%.0fs: Khaya could not transcribe it (%s)",
            offset,
            offset + piece_seconds,
            e,
        )
        cache_path.write_text(json.dumps({"skipped": True, "reason": str(e)[:300]}))
        return [], [(offset, offset + piece_seconds)]

    cache_path.write_text(json.dumps(payload))
    return _khaya_words_from_response(payload, offset), []


def _group_words_into_segments(words: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Group words into short Whisper-style segments for analysis and captions."""
    segments: List[Dict[str, Any]] = []
    current: List[Dict[str, Any]] = []
    for word in words:
        # A long pause (or a chunk boundary in silence) starts a new segment.
        if current and word["start"] - current[-1]["end"] > KHAYA_SEGMENT_MAX_GAP_SECONDS:
            segments.append(_words_to_segment(current))
            current = []
        current.append(word)
        duration = current[-1]["end"] - current[0]["start"]
        sentence_end = word["word"].rstrip().endswith((".", "?", "!"))
        if sentence_end or duration >= KHAYA_SEGMENT_MAX_SECONDS or len(current) >= KHAYA_SEGMENT_MAX_WORDS:
            segments.append(_words_to_segment(current))
            current = []
    if current:
        segments.append(_words_to_segment(current))
    return segments


def _words_to_segment(words: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "text": "".join(w["word"] for w in words).strip(),
        "start": words[0]["start"],
        "end": words[-1]["end"],
        "words": words,
    }


def transcribe_with_khaya(video_path: Path, runtime_config) -> Dict[str, Any]:
    """Transcribe with Khaya AI ASR into a Whisper-shaped result dict."""
    if not runtime_config.khaya_api_key:
        raise RuntimeError("TRANSCRIPTION_PROVIDER=khaya requires KHAYA_API_KEY")

    audio_path = _prepare_audio_for_transcription(video_path)
    chunks = _split_audio_for_khaya(audio_path, runtime_config.khaya_chunk_seconds)
    logger.info(
        "Starting Khaya transcription: %d chunks of %ss (language: %s)",
        len(chunks),
        runtime_config.khaya_chunk_seconds,
        runtime_config.khaya_language,
    )

    chunk_seconds = runtime_config.khaya_chunk_seconds
    cache_dir = audio_path.with_name(f"{audio_path.stem}.khaya_cache")
    cache_dir.mkdir(exist_ok=True)
    limiter = _KhayaRateLimiter(runtime_config.khaya_requests_per_minute)
    with httpx.Client(
        timeout=runtime_config.khaya_request_timeout_seconds,
        headers={"Ocp-Apim-Subscription-Key": runtime_config.khaya_api_key},
    ) as client:

        def transcribe(chunk: tuple, timestamps: str) -> tuple:
            chunk_path, offset = chunk
            return _transcribe_khaya_piece(
                client, runtime_config, limiter, cache_dir, chunk_path, offset, chunk_seconds, timestamps
            )

        timestamps = "word"
        try:
            first = transcribe(chunks[0], timestamps)
        except KhayaTimestampsUnsupported:
            logger.warning(
                "Khaya has no word timestamps for %s; using segment timings",
                runtime_config.khaya_language,
            )
            timestamps = "segment"
            first = transcribe(chunks[0], timestamps)

        with ThreadPoolExecutor(max_workers=runtime_config.khaya_concurrency) as pool:
            rest = list(pool.map(lambda chunk: transcribe(chunk, timestamps), chunks[1:]))
    logger.info("Khaya API calls made: %d", limiter.calls)

    words: List[Dict[str, Any]] = []
    skipped: List[tuple] = []
    for piece_words, piece_skipped in [first, *rest]:
        words.extend(piece_words)
        skipped.extend(piece_skipped)

    for chunk_path, _ in chunks:
        chunk_path.unlink(missing_ok=True)

    total_seconds = chunks[-1][1] + chunk_seconds
    skipped_seconds = sum(end - start for start, end in skipped)
    if skipped_seconds > total_seconds * KHAYA_MAX_SKIPPED_FRACTION:
        raise RuntimeError(
            f"Khaya could not transcribe {skipped_seconds:.0f}s of audio; "
            "finished pieces are saved and a resume will only retry the rest"
        )
    if skipped:
        logger.warning(
            "Khaya transcript has gaps where audio could not be transcribed: %s",
            ", ".join(f"{start:.0f}s-{end:.0f}s" for start, end in skipped),
        )

    segments = _group_words_into_segments(words)
    logger.info(
        "Khaya transcription complete: %d words, %d segments (%s timings)",
        len(words),
        len(segments),
        timestamps,
    )
    return {
        "text": " ".join(segment["text"] for segment in segments),
        "segments": segments,
        "language": runtime_config.khaya_language,
    }


def _get_transcript_with_khaya(video_path: Path, runtime_config) -> str:
    """Get transcript using Khaya AI ASR with word-level timestamps."""
    khaya_result = transcribe_with_khaya(video_path, runtime_config)
    formatted_lines = format_transcript_for_analysis(khaya_result)
    cache_transcript_data(video_path, khaya_result)
    result = "\n".join(formatted_lines)
    logger.info(
        "Khaya transcript formatted: %d segments, %d chars",
        len(formatted_lines),
        len(result),
    )
    return result


def _get_transcript_with_youtube_captions(source_url: str) -> str:
    """Get transcript from YouTube captions (plain text, no word timings)."""
    logger.info("Extracting YouTube captions for: %s", source_url)
    transcript = transcribe_with_youtube_captions(source_url)
    if not transcript:
        raise RuntimeError(
            "YouTube caption extraction failed or returned no captions. "
            "Falling back requires a different TRANSCRIPTION_PROVIDER."
        )
    logger.info("YouTube caption transcript: %d chars", len(transcript))
    return transcript


def cache_transcript_data(video_path: Path, transcript) -> None:
    """Cache transcript data for subtitle generation.

    Handles both AssemblyAI transcript objects and Whisper result dicts.
    """
    cache_path = video_path.with_suffix(".transcript_cache.json")

    if isinstance(transcript, dict):
        cache_data = _whisper_result_to_transcript_data(transcript)
        with open(cache_path, "w") as f:
            json.dump(cache_data, f)
        logger.info("Cached %d words to %s", len(cache_data["words"]), cache_path)
        return

    words_data = []
    if transcript.words:
        words_data = [_serialize_transcript_word(word) for word in transcript.words]

    utterances_data = []
    if getattr(transcript, "utterances", None):
        utterances_data = [
            {
                "text": utterance.text,
                "start": utterance.start,
                "end": utterance.end,
                "speaker": getattr(utterance, "speaker", None),
                "words": [
                    _serialize_transcript_word(word)
                    for word in getattr(utterance, "words", []) or []
                ],
            }
            for utterance in transcript.utterances
        ]

    response = getattr(transcript, "json_response", None)
    language_code = response.get("language_code") if isinstance(response, dict) else None
    if not language_code:
        language_code = getattr(getattr(transcript, "config", None), "language_code", None)

    cache_data = {
        "version": TRANSCRIPT_CACHE_SCHEMA_VERSION,
        "words": words_data,
        "utterances": utterances_data,
        "text": transcript.text,
        # Lets language-specific cleanup (e.g. default filler words) avoid
        # cutting ordinary words in other languages.
        "language_code": language_code if isinstance(language_code, str) else None,
    }

    with open(cache_path, "w") as f:
        json.dump(cache_data, f)

    logger.info(f"Cached {len(words_data)} words to {cache_path}")


def load_cached_transcript_data(video_path: Path) -> Optional[Dict]:
    """Load cached AssemblyAI transcript data."""
    cache_path = video_path.with_suffix(".transcript_cache.json")

    if not cache_path.exists():
        return None

    try:
        with open(cache_path, "r") as f:
            payload = json.load(f)
            if "version" not in payload:
                payload["version"] = TRANSCRIPT_CACHE_SCHEMA_VERSION
                payload.setdefault("utterances", [])
            return payload
    except Exception as e:
        logger.warning(f"Failed to load transcript cache: {e}")
        return None


def _serialize_transcript_word(word) -> Dict[str, Any]:
    if isinstance(word, dict):
        return {
            "text": word.get("word", word.get("text", "")),
            "start": int(word["start"] * 1000) if isinstance(word.get("start"), float) else int(word.get("start", 0)),
            "end": int(word["end"] * 1000) if isinstance(word.get("end"), float) else int(word.get("end", 0)),
            "confidence": word.get("probability", word.get("confidence", 1.0)),
            "speaker": word.get("speaker"),
        }
    return {
        "text": word.text,
        "start": word.start,
        "end": word.end,
        "confidence": word.confidence if hasattr(word, "confidence") else 1.0,
        "speaker": getattr(word, "speaker", None),
    }


def _join_transcript_tokens(tokens: List[str]) -> str:
    text = " ".join(token.strip() for token in tokens if token and token.strip())
    for before, after in (
        (" ,", ","),
        (" .", "."),
        (" !", "!"),
        (" ?", "?"),
        (" ;", ";"),
        (" :", ":"),
        (" n't", "n't"),
        (" 're", "'re"),
        (" 've", "'ve"),
        (" 'll", "'ll"),
        (" 'd", "'d"),
        (" 'm", "'m"),
        (" 's", "'s"),
    ):
        text = text.replace(before, after)
    return text.strip()


def _format_words_for_analysis(
    words: List[Any],
    speaker: Optional[str] = None,
    *,
    min_words_per_segment: int = ANALYSIS_SEGMENT_MIN_WORDS,
    max_words_per_segment: int = ANALYSIS_SEGMENT_MAX_WORDS,
    max_duration_ms: int = ANALYSIS_SEGMENT_MAX_DURATION_MS,
) -> List[str]:
    if not words:
        return []

    formatted_lines: List[str] = []
    current_words: List[Any] = []
    current_start: Optional[int] = None

    def flush_segment() -> None:
        nonlocal current_words, current_start
        if not current_words:
            return
        start_time = format_ms_to_timestamp(current_words[0].start)
        end_time = format_ms_to_timestamp(current_words[-1].end)
        text = _join_transcript_tokens([word.text for word in current_words])
        if text:
            speaker_prefix = f"Speaker {speaker}: " if speaker else ""
            formatted_lines.append(f"[{start_time} - {end_time}] {speaker_prefix}{text}")
        current_words = []
        current_start = None

    for word in words:
        if current_start is None:
            current_start = word.start

        current_words.append(word)
        duration_ms = word.end - current_start
        segment_word_count = len(current_words)
        ends_sentence = str(word.text).endswith((".", "!", "?"))

        should_flush = False
        if segment_word_count >= max_words_per_segment:
            should_flush = True
        elif (
            ends_sentence
            and segment_word_count >= min_words_per_segment
        ):
            should_flush = True
        elif (
            duration_ms >= max_duration_ms
            and segment_word_count >= min_words_per_segment
        ):
            should_flush = True

        if should_flush:
            flush_segment()

    flush_segment()
    return formatted_lines


def format_transcript_for_analysis(transcript) -> List[str]:
    """Format transcripts into readable timestamped segments for AI analysis.

    Handles both AssemblyAI transcript objects (utterances/words with ms
    timings) and Whisper result dicts (segments with second-based timings).
    """
    # Whisper result dict: treat each segment as an utterance, converting the
    # second-based timings to milliseconds to match the timestamp formatter.
    if isinstance(transcript, dict):
        formatted_lines = []
        for segment in transcript.get("segments") or []:
            start_ms = int(segment.get("start", 0) * 1000)
            end_ms = int(segment.get("end", 0) * 1000)
            formatted_lines.append(
                f"[{format_ms_to_timestamp(start_ms)} - {format_ms_to_timestamp(end_ms)}] "
                f"{segment.get('text', '').strip()}"
            )
        return formatted_lines

    utterances = getattr(transcript, "utterances", None) or []
    if utterances:
        formatted_lines = []
        for utterance in utterances:
            utterance_words = list(getattr(utterance, "words", []) or [])
            utterance_duration = max(0, int(utterance.end) - int(utterance.start))
            if utterance_words and (
                utterance_duration > ANALYSIS_UTTERANCE_SPLIT_THRESHOLD_MS
                or len(utterance_words) > ANALYSIS_UTTERANCE_SPLIT_THRESHOLD_WORDS
            ):
                formatted_lines.extend(
                    _format_words_for_analysis(
                        utterance_words,
                        getattr(utterance, "speaker", None),
                        max_words_per_segment=ANALYSIS_LONG_UTTERANCE_MAX_WORDS,
                        max_duration_ms=ANALYSIS_LONG_UTTERANCE_MAX_DURATION_MS,
                    )
                )
                continue

            start_time = format_ms_to_timestamp(utterance.start)
            end_time = format_ms_to_timestamp(utterance.end)
            speaker = getattr(utterance, "speaker", None)
            speaker_prefix = f"Speaker {speaker}: " if speaker else ""
            formatted_lines.append(
                f"[{start_time} - {end_time}] {speaker_prefix}{utterance.text}"
            )
        return formatted_lines

    formatted_lines = []
    words = getattr(transcript, "words", None) or []
    if not words:
        return formatted_lines

    logger.info(f"Processing {len(words)} words with precise timing")
    return _format_words_for_analysis(words)


def format_ms_to_timestamp(ms: int) -> str:
    """Format milliseconds to MM:SS format."""
    seconds = ms // 1000
    minutes = seconds // 60
    seconds = seconds % 60
    return f"{minutes:02d}:{seconds:02d}"
