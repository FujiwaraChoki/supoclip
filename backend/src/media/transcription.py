"""Transcription helpers for the video pipeline."""

from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from pathlib import Path
import assemblyai as aai
from ..config import get_config
import httpx
import json
import threading
import re
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
from ..workflows.language import is_no_space

SONIOX_POLLING_INTERVAL_SECONDS = 2.0
SONIOX_MIN_WORD_DURATION_MS = 180
SONIOX_TOKEN_WORD_GAP_MS = 1000
SONIOX_UTTERANCE_PAUSE_SPLIT_MS = 1500
_SONIOX_RETRYABLE_STATUS_CODES = frozenset({408, 429, 500, 502, 503, 504})
_SONIOX_TRAILING_PUNCT_RE = re.compile(
    r"^[.,?!;:…—–\-)\]}'\"»”。，！？、；：）」』】]+$"
)
# Only unambiguous closing/French spaced marks attach when preceded by a space;
# quotes, apostrophes, dashes, and opening brackets start a new word instead.
_SONIOX_SPACED_CLOSING_PUNCT_RE = re.compile(
    r"^[.,?!;:…»”。，！？、；：）」』】]+$"
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


def _soniox_delete_file_best_effort(client: httpx.Client, file_id: str) -> None:
    """Remove the temporary uploaded audio from Soniox to avoid storage limits."""
    try:
        response = client.delete(f"/v1/files/{file_id}", timeout=15.0)
        if response.status_code >= 400 and response.status_code != 404:
            logger.warning(
                "Soniox file cleanup returned HTTP %s for %s",
                response.status_code,
                file_id,
            )
    except Exception as error:
        logger.warning("Soniox file cleanup failed for %s: %s", file_id, error)


def _soniox_delete_transcription_best_effort(
    client: httpx.Client, transcription_id: str
) -> None:
    """Delete a finished or errored Soniox transcription to stay under account limits."""
    try:
        response = client.delete(f"/v1/transcriptions/{transcription_id}", timeout=15.0)
        if response.status_code >= 400 and response.status_code != 404:
            logger.warning(
                "Soniox transcription cleanup returned HTTP %s for %s",
                response.status_code,
                transcription_id,
            )
    except Exception as error:
        logger.warning(
            "Soniox transcription cleanup failed for %s: %s",
            transcription_id,
            error,
        )


def _submit_and_wait_for_soniox_transcript(
    client: httpx.Client,
    media_path: Path,
    model: str,
    language: str,
    timeout_seconds: int,
    request_timeout_seconds: int = 60,
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    """Upload audio, submit a Soniox async transcription job, and poll until done."""
    file_id: Optional[str] = None
    content_type = "audio/mpeg" if media_path.suffix.lower() == ".mp3" else "application/octet-stream"

    # Uploads are safe to retry: they do not create a paid transcription job.
    for attempt in range(1, 4):
        try:
            with media_path.open("rb") as audio_file:
                upload_response = client.post(
                    "/v1/files",
                    files={"file": (media_path.name, audio_file, content_type)},
                    timeout=float(request_timeout_seconds),
                )
            upload_response.raise_for_status()
            file_id = upload_response.json().get("id")
            if not file_id:
                raise RuntimeError("Soniox audio upload did not return a file ID")
            break
        except (httpx.TransportError, httpx.HTTPStatusError) as error:
            status = error.response.status_code if isinstance(error, httpx.HTTPStatusError) else None
            retryable = isinstance(error, httpx.TransportError) or status in _SONIOX_RETRYABLE_STATUS_CODES
            if not retryable:
                raise
            if attempt == 3:
                raise RuntimeError(
                    "Soniox audio upload unavailable after 3 attempts"
                    + (f" (HTTP {status})." if status else ".")
                ) from None
            if isinstance(error, httpx.ReadTimeout):
                logger.warning(
                    "Soniox audio upload read timed out (an earlier upload may have succeeded server-side); "
                    "retrying upload (%s/3)",
                    attempt,
                )
            else:
                logger.warning("Soniox audio upload failed (HTTP %s); retrying upload (%s/3)", status, attempt)
            time.sleep(2.0 ** attempt)

    assert file_id is not None
    transcription_id: Optional[str] = None
    can_delete_transcription = False
    try:
        create_payload: Dict[str, Any] = {
            "model": model,
            "file_id": file_id,
            "enable_speaker_diarization": True,
            "enable_language_identification": not bool(language),
        }
        if language:
            create_payload["language_hints"] = [language.split("_", 1)[0]]
            create_payload["language_hints_strict"] = True

        # Keep the paid job submission outside any retry loop.
        submit_response = client.post(
            "/v1/transcriptions",
            json=create_payload,
            timeout=float(request_timeout_seconds),
        )
        submit_response.raise_for_status()
        job = submit_response.json()
        transcription_id = job.get("id")
        if not transcription_id:
            raise RuntimeError("Soniox did not return a transcription ID")

        logger.info("Soniox transcript submitted: %s (model: %s)", transcription_id, model)
        deadline = time.monotonic() + timeout_seconds
        next_log_at = 0.0
        consecutive_failures = 0

        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                logger.warning(
                    "Soniox transcript %s timed out while still active; cannot delete until finished",
                    transcription_id,
                )
                raise TimeoutError(
                    f"Soniox transcript {transcription_id} did not complete within {timeout_seconds}s"
                )
            try:
                poll_response = client.get(
                    f"/v1/transcriptions/{transcription_id}",
                    timeout=min(30.0, remaining),
                )
                poll_response.raise_for_status()
                job = poll_response.json()
                consecutive_failures = 0
            except (httpx.TransportError, httpx.HTTPStatusError) as error:
                if (
                    isinstance(error, httpx.HTTPStatusError)
                    and error.response.status_code not in _SONIOX_RETRYABLE_STATUS_CODES
                ):
                    raise
                consecutive_failures += 1
                logger.warning("Soniox status polling unavailable; retrying the existing transcript")
                time.sleep(
                    min(
                        30.0,
                        2.0 ** min(consecutive_failures, 4),
                        max(0.0, deadline - time.monotonic()),
                    )
                )
                continue

            status = job.get("status")
            if status == "completed":
                can_delete_transcription = True
                break
            if status == "error":
                can_delete_transcription = True
                detail = job.get("error_message") or job.get("error_type") or "unknown error"
                raise RuntimeError(f"Soniox transcription failed: {detail}")

            now = time.monotonic()
            if now >= deadline:
                logger.warning(
                    "Soniox transcript %s timed out while still %s; cannot delete until finished",
                    transcription_id,
                    status,
                )
                raise TimeoutError(
                    f"Soniox transcript {transcription_id} did not complete within {timeout_seconds}s"
                )
            if now >= next_log_at:
                logger.info("Soniox transcript %s still %s", transcription_id, status)
                next_log_at = now + 30
            time.sleep(min(SONIOX_POLLING_INTERVAL_SECONDS, max(0.0, deadline - now)))

        # Fetching a completed transcript is idempotent and gets its own bounded
        # budget so slow polling near the deadline does not starve the GET.
        fetch_budget = max(60.0, float(request_timeout_seconds) * 2.0)
        fetch_deadline = time.monotonic() + fetch_budget
        consecutive_failures = 0
        while True:
            fetch_remaining = fetch_deadline - time.monotonic()
            if fetch_remaining <= 0:
                raise TimeoutError(
                    f"Soniox transcript {transcription_id} fetch did not complete within {int(fetch_budget)}s"
                )
            try:
                tx_response = client.get(
                    f"/v1/transcriptions/{transcription_id}/transcript",
                    timeout=min(float(request_timeout_seconds), fetch_remaining),
                )
                tx_response.raise_for_status()
                return tx_response.json(), job
            except (httpx.TransportError, httpx.HTTPStatusError) as error:
                if (
                    isinstance(error, httpx.HTTPStatusError)
                    and error.response.status_code not in _SONIOX_RETRYABLE_STATUS_CODES
                ):
                    raise
                consecutive_failures += 1
                if consecutive_failures >= 4:
                    raise
                logger.warning("Soniox transcript fetch unavailable; retrying (%s/3)", consecutive_failures)
                time.sleep(
                    min(
                        16.0,
                        2.0 ** consecutive_failures,
                        max(0.0, fetch_deadline - time.monotonic()),
                    )
                )
    finally:
        # Delete the finished/errored transcription before the file (per Soniox SDK
        # guidance) so account-wide transcription and file limits are never exhausted.
        if transcription_id and can_delete_transcription:
            _soniox_delete_transcription_best_effort(client, transcription_id)
        _soniox_delete_file_best_effort(client, file_id)


def _soniox_result_to_transcript_data(
    transcript_payload: Dict[str, Any],
    *,
    audio_duration_ms: Optional[int] = None,
    fallback_language: Optional[str] = None,
) -> Dict[str, Any]:
    """Convert a Soniox async transcript payload into the standard cache format.

    Merges BPE subword tokens into whole words (flushing on standalone whitespace
    tokens, no-space script boundaries, speaker changes, or token gaps), clamps
    short or overlapping word durations without overlapping the next word, and
    groups consecutive same-speaker runs into utterances.
    """
    tokens = transcript_payload.get("tokens") or []
    words_data: List[Dict[str, Any]] = []
    lang_counts: Dict[str, int] = {}
    current: Optional[Dict[str, Any]] = None

    def flush_word() -> None:
        nonlocal current
        if current is None:
            return
        confs = current["confs"]
        words_data.append(
            {
                "text": current["text"],
                "start": int(current["start"]),
                "end": int(current["end"]),
                "confidence": round(sum(confs) / len(confs), 4) if confs else 1.0,
                "speaker": current["speaker"],
            }
        )
        current = None

    for tok in tokens:
        if not isinstance(tok, dict):
            continue
        raw_text = str(tok.get("text") or "")
        if not raw_text:
            continue

        tok_lang = tok.get("language")
        lang_code: Optional[str] = None
        if isinstance(tok_lang, str) and tok_lang.strip():
            lang_code = tok_lang.strip().lower()
            lang_counts[lang_code] = lang_counts.get(lang_code, 0) + 1

        starts_with_space = raw_text[0].isspace()
        stripped = raw_text.strip()
        if not stripped:
            # Standalone whitespace tokens mark an explicit word boundary.
            flush_word()
            continue

        start_ms = max(0, int(tok.get("start_ms") or 0))
        end_ms = max(start_ms, int(tok.get("end_ms") or start_ms))
        raw_conf = tok.get("confidence")
        conf = float(raw_conf) if raw_conf is not None else 1.0
        raw_speaker = tok.get("speaker")
        speaker_str = str(raw_speaker) if raw_speaker is not None else None
        is_trailing_punct = (
            bool(_SONIOX_SPACED_CLOSING_PUNCT_RE.match(stripped))
            if starts_with_space
            else bool(_SONIOX_TRAILING_PUNCT_RE.match(stripped))
        )
        token_no_space = (not is_trailing_punct) and is_no_space(lang_code, stripped)

        if current is None:
            current = {
                "text": stripped,
                "start": start_ms,
                "end": end_ms,
                "confs": [conf],
                "speaker": speaker_str,
                "no_space": token_no_space,
            }
        elif is_trailing_punct and (speaker_str == current["speaker"] or speaker_str is None):
            # Attach trailing punctuation (including French spaced " ?" / " !" and CJK "。"/"，")
            # to the current word instead of creating an orphan caption word.
            current["text"] += stripped
        elif (
            not starts_with_space
            and not token_no_space
            and not current["no_space"]
            and (speaker_str == current["speaker"] or speaker_str is None)
            and (start_ms - current["end"]) < SONIOX_TOKEN_WORD_GAP_MS
        ):
            current["text"] += stripped
            current["end"] = max(current["end"], end_ms)
            current["confs"].append(conf)
        else:
            flush_word()
            current = {
                "text": stripped,
                "start": start_ms,
                "end": end_ms,
                "confs": [conf],
                "speaker": speaker_str,
                "no_space": token_no_space,
            }

    flush_word()

    # Soniox emits tight 60ms frame spans on single-token words (and occasional
    # start == end stamps or overlapping ends). Pad short endings toward a natural
    # duration and clamp every word's end so words never overlap the next start.
    for index, word in enumerate(words_data):
        start_ms = word["start"]
        end_ms = word["end"]
        next_start = words_data[index + 1]["start"] if index + 1 < len(words_data) else None

        if next_start is not None and end_ms > next_start:
            target_end = max(start_ms, next_start)
        else:
            target_end = max(end_ms, start_ms + SONIOX_MIN_WORD_DURATION_MS)
            if next_start is not None:
                # Leave a 10ms cushion before the next word when room allows.
                gap_cap = next_start - 10 if next_start - start_ms > 20 else next_start
                target_end = min(target_end, max(end_ms, gap_cap))
        if audio_duration_ms is not None and audio_duration_ms > 0:
            target_end = min(target_end, max(start_ms, int(audio_duration_ms)))
        word["end"] = max(start_ms, target_end)

    # Group words into speaker-consistent utterances for LLM analysis & caching.
    utterances_data: List[Dict[str, Any]] = []
    utt_words: List[Dict[str, Any]] = []

    def flush_utterance() -> None:
        nonlocal utt_words
        if not utt_words:
            return
        utterances_data.append(
            {
                "text": _join_transcript_tokens([w["text"] for w in utt_words]),
                "start": utt_words[0]["start"],
                "end": utt_words[-1]["end"],
                "speaker": utt_words[0].get("speaker"),
                "words": utt_words,
            }
        )
        utt_words = []

    for word in words_data:
        if utt_words:
            prev = utt_words[-1]
            speaker_changed = word.get("speaker") != prev.get("speaker")
            long_pause = (word["start"] - prev["end"]) >= SONIOX_UTTERANCE_PAUSE_SPLIT_MS
            if speaker_changed or long_pause:
                flush_utterance()
        utt_words.append(word)
    flush_utterance()

    language_code: Optional[str] = max(lang_counts, key=lang_counts.get) if lang_counts else None
    if not language_code and fallback_language:
        language_code = fallback_language.split("_", 1)[0]

    text = transcript_payload.get("text")
    if not isinstance(text, str) or not text.strip():
        text = _join_transcript_tokens([w["text"] for w in words_data])

    return {
        "version": TRANSCRIPT_CACHE_SCHEMA_VERSION,
        "words": words_data,
        "utterances": utterances_data,
        "text": text,
        "language_code": language_code,
    }


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

    Dispatches to AssemblyAI, Soniox, local Whisper, or YouTube captions based on
    ``TRANSCRIPTION_PROVIDER``. ``source_url`` enables the youtube_captions
    provider, which needs the original URL rather than a local file path.
    """
    logger.info(f"Getting transcript for: {video_path}")
    runtime_config = get_config()
    provider = runtime_config.transcription_provider

    if provider == "soniox":
        return _get_transcript_with_soniox(video_path, runtime_config)
    if provider == "whisper":
        return _get_transcript_with_whisper(video_path, runtime_config)
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
    selected_model = getattr(runtime_config, "assembly_ai_speech_model", "") or speech_model
    speech_models_value = _assemblyai_speech_models_value(selected_model)

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


def _get_transcript_with_soniox(video_path: Path, runtime_config) -> str:
    """Get transcript using Soniox Async API with word-level timing and diarization."""
    api_key = getattr(runtime_config, "soniox_api_key", None)
    if not api_key:
        raise RuntimeError(
            "SONIOX_API_KEY is not set. Configure SONIOX_API_KEY or switch TRANSCRIPTION_PROVIDER."
        )

    model = getattr(runtime_config, "soniox_model", "stt-async-v6") or "stt-async-v6"
    base_url = (getattr(runtime_config, "soniox_base_url", "https://api.soniox.com") or "https://api.soniox.com").rstrip("/")
    timeout_seconds = int(getattr(runtime_config, "soniox_http_timeout_seconds", 900))
    request_timeout_seconds = int(getattr(runtime_config, "soniox_request_timeout_seconds", 60))
    language = getattr(runtime_config, "transcription_language", "") or ""

    client = httpx.Client(
        base_url=base_url,
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=float(request_timeout_seconds),
    )
    try:
        logger.info("Starting Soniox transcription (model: %s)", model)
        transcription_media_path = _prepare_audio_for_transcription(video_path)
        transcript_payload, job = _submit_and_wait_for_soniox_transcript(
            client,
            transcription_media_path,
            model,
            language,
            timeout_seconds,
            request_timeout_seconds,
        )

        transcript_data = _soniox_result_to_transcript_data(
            transcript_payload,
            audio_duration_ms=job.get("audio_duration_ms"),
            fallback_language=language or None,
        )
        logger.info(
            "Soniox transcript language: %s (%d words, %d utterances)",
            transcript_data.get("language_code") or "unknown",
            len(transcript_data["words"]),
            len(transcript_data["utterances"]),
        )

        formatted_lines = format_transcript_for_analysis(transcript_data)
        cache_transcript_data(video_path, transcript_data)

        result = "\n".join(formatted_lines)
        logger.info(
            "Soniox transcript formatted: %d segments, %d chars",
            len(formatted_lines),
            len(result),
        )
        return result
    except Exception as error:
        logger.error("Error in Soniox transcription: %s", error)
        raise
    finally:
        client.close()


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

    Handles AssemblyAI transcript objects, pre-normalized transcript dicts
    (e.g. Soniox), and Whisper result dicts.
    """
    cache_path = video_path.with_suffix(".transcript_cache.json")

    if isinstance(transcript, dict):
        if "words" in transcript and "utterances" in transcript:
            cache_data = {
                "version": transcript.get("version", TRANSCRIPT_CACHE_SCHEMA_VERSION),
                "words": transcript.get("words") or [],
                "utterances": transcript.get("utterances") or [],
                "text": transcript.get("text") or "",
                "language_code": transcript.get("language_code"),
            }
        else:
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


# Corrections to cached transcripts, held in this process: {sidecar path: {a word as cached: what changes}}.
# A word is named by (start ms, end ms, text) as the file has it. ``media.edge_listen`` finds them by
# listening to the audio around a clip's cuts; the file itself is shared and is never rewritten. Renders run
# in worker threads, so every access is under one lock, and a worker that lives for weeks keeps the
# corrections of the last WORD_FIX_VIDEOS videos only (another process, or this one later, finds them again
# the same way).
WORD_FIX_VIDEOS = 32
_WORD_FIX_LOCK = threading.RLock()
_WORD_FIXES: Dict[str, Dict[tuple, Dict[str, Any]]] = {}
_WORD_FIX_ORIGINS: Dict[str, Dict[tuple, tuple]] = {}
_WORD_FIX_GENERATION: Dict[str, int] = {}


def _word_fix_key(video_path: Path) -> str:
    return str(Path(video_path).with_suffix(".transcript_cache.json"))


def register_word_fixes(video_path: Path, fixes: Dict[tuple, Dict[str, Any]]) -> bool:
    """Record corrections for words of ``video_path``'s transcript: {(start ms, end ms, text) as now loaded:
    {"start": s, "end": s, "text": str}} with seconds and only the fields that change.

    True when anything new was recorded. Every later ``load_cached_transcript_data`` returns the corrected
    words; ``word_fix_generation`` changes so caches built on the words can tell.
    """
    key = _word_fix_key(video_path)
    with _WORD_FIX_LOCK:
        if key not in _WORD_FIXES and len(_WORD_FIXES) >= WORD_FIX_VIDEOS:
            oldest = next(iter(_WORD_FIXES))
            _WORD_FIXES.pop(oldest, None)
            _WORD_FIX_ORIGINS.pop(oldest, None)      # its generation stays: caches must not match again
        known, origins = _WORD_FIXES.setdefault(key, {}), _WORD_FIX_ORIGINS.setdefault(key, {})
        changed = False
        for current, change in fixes.items():
            current = (int(current[0]), int(current[1]), str(current[2]))
            origin = origins.get(current, current)
            before = {name: value for name, value in known.get(origin, {}).items() if name != "applied"}
            merged = {**before, **{name: value for name, value in change.items() if value is not None and name != "applied"}}
            if merged == before:
                continue
            known[origin] = merged
            changed = True
        if changed:
            _WORD_FIX_GENERATION[key] = _WORD_FIX_GENERATION.get(key, 0) + 1
        return changed


def word_fix_generation(video_path: Path) -> int:
    """How many times corrections were recorded for ``video_path``'s transcript in this process."""
    with _WORD_FIX_LOCK:
        return _WORD_FIX_GENERATION.get(_word_fix_key(video_path), 0)


def corrected_word_time(video_path: Path, seconds: float, edge: str) -> float:
    """Where a word time read from the cached transcript is now: ``seconds`` is a word's ``edge`` ("start" or
    "end") as the file has it; the corrected time when one was recorded for that word, else ``seconds``."""
    wanted = int(round(float(seconds) * 1000))
    with _WORD_FIX_LOCK:
        for origin, change in (_WORD_FIXES.get(_word_fix_key(video_path)) or {}).items():
            if abs(origin[0 if edge == "start" else 1] - wanted) <= 2:
                # As the words were last loaded: a correction gives way to a neighbour it would overlap.
                applied = change.get("applied") or change
                if edge in applied:
                    return float(applied[edge])
    return seconds


def _apply_word_fixes(cache_path: Path, payload: Dict[str, Any]) -> None:
    words = payload.get("words")
    with _WORD_FIX_LOCK:
        fixes = _WORD_FIXES.get(str(cache_path))
        if not fixes or not isinstance(words, list):
            return
        origins = _WORD_FIX_ORIGINS.setdefault(str(cache_path), {})
        for position, word in enumerate(words):
            try:
                origin = (int(word["start"]), int(word["end"]), str(word["text"]))
            except (KeyError, TypeError, ValueError):
                continue
            change = fixes.get(origin)
            if not change:
                continue
            fixed = dict(word)
            if "start" in change:
                fixed["start"] = int(round(float(change["start"]) * 1000))
            if "end" in change:
                fixed["end"] = int(round(float(change["end"]) * 1000))
            if "text" in change:
                fixed["text"] = str(change["text"])
            # Words stay in order and apart: a corrected word gives way to a neighbour that was not corrected.
            if position > 0:
                fixed["start"] = max(int(fixed["start"]), int(words[position - 1]["end"]))
            if position + 1 < len(words):
                following = words[position + 1]
                later = fixes.get((int(following["start"]), int(following["end"]), str(following["text"])), {})
                limit = int(round(float(later["start"]) * 1000)) if "start" in later else int(following["start"])
                fixed["end"] = min(int(fixed["end"]), max(limit, int(fixed["start"]) + 1))
            fixed["end"] = max(int(fixed["end"]), int(fixed["start"]) + 1)
            change["applied"] = {"start": fixed["start"] / 1000.0, "end": fixed["end"] / 1000.0}
            # The word as it is loaded from now on is the same word: a correction of it lands on this one.
            origins[(int(fixed["start"]), int(fixed["end"]), str(fixed["text"]))] = origin
            words[position] = fixed


def load_cached_transcript_data(video_path: Path) -> Optional[Dict]:
    """Load cached transcript data, with the corrections this process has recorded for it."""
    cache_path = video_path.with_suffix(".transcript_cache.json")

    if not cache_path.exists():
        return None

    try:
        with open(cache_path, "r") as f:
            payload = json.load(f)
            if "version" not in payload:
                payload["version"] = TRANSCRIPT_CACHE_SCHEMA_VERSION
                payload.setdefault("utterances", [])
            _apply_word_fixes(cache_path, payload)
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


def _word_field(word: Any, field: str, default: Any = None) -> Any:
    if isinstance(word, dict):
        return word.get(field, default)
    return getattr(word, field, default)


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
        start_time = format_ms_to_timestamp(int(_word_field(current_words[0], "start", 0)))
        end_time = format_ms_to_timestamp(int(_word_field(current_words[-1], "end", 0)))
        text = _join_transcript_tokens([str(_word_field(word, "text", "")) for word in current_words])
        if text:
            speaker_prefix = f"Speaker {speaker}: " if speaker else ""
            formatted_lines.append(f"[{start_time} - {end_time}] {speaker_prefix}{text}")
        current_words = []
        current_start = None

    for word in words:
        word_start = int(_word_field(word, "start", 0))
        word_end = int(_word_field(word, "end", word_start))
        word_text = str(_word_field(word, "text", ""))
        if current_start is None:
            current_start = word_start

        current_words.append(word)
        duration_ms = word_end - current_start
        segment_word_count = len(current_words)
        ends_sentence = word_text.endswith((".", "!", "?"))

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

    Handles AssemblyAI transcript objects (utterances/words with ms timings),
    pre-normalized transcript dicts (e.g. Soniox), and Whisper result dicts
    (segments with second-based timings).
    """
    if isinstance(transcript, dict) and "segments" in transcript and "utterances" not in transcript:
        formatted_lines = []
        for segment in transcript.get("segments") or []:
            start_ms = int(segment.get("start", 0) * 1000)
            end_ms = int(segment.get("end", 0) * 1000)
            formatted_lines.append(
                f"[{format_ms_to_timestamp(start_ms)} - {format_ms_to_timestamp(end_ms)}] "
                f"{segment.get('text', '').strip()}"
            )
        return formatted_lines

    utterances = (
        transcript.get("utterances")
        if isinstance(transcript, dict)
        else getattr(transcript, "utterances", None)
    ) or []
    if utterances:
        formatted_lines = []
        for utterance in utterances:
            utterance_words = list(_word_field(utterance, "words", []) or [])
            utt_start = int(_word_field(utterance, "start", 0))
            utt_end = int(_word_field(utterance, "end", utt_start))
            utterance_duration = max(0, utt_end - utt_start)
            speaker = _word_field(utterance, "speaker", None)
            if utterance_words and (
                utterance_duration > ANALYSIS_UTTERANCE_SPLIT_THRESHOLD_MS
                or len(utterance_words) > ANALYSIS_UTTERANCE_SPLIT_THRESHOLD_WORDS
            ):
                formatted_lines.extend(
                    _format_words_for_analysis(
                        utterance_words,
                        speaker,
                        max_words_per_segment=ANALYSIS_LONG_UTTERANCE_MAX_WORDS,
                        max_duration_ms=ANALYSIS_LONG_UTTERANCE_MAX_DURATION_MS,
                    )
                )
                continue

            start_time = format_ms_to_timestamp(utt_start)
            end_time = format_ms_to_timestamp(utt_end)
            speaker_prefix = f"Speaker {speaker}: " if speaker else ""
            utt_text = str(_word_field(utterance, "text", ""))
            formatted_lines.append(
                f"[{start_time} - {end_time}] {speaker_prefix}{utt_text}"
            )
        return formatted_lines

    formatted_lines = []
    words = (
        transcript.get("words")
        if isinstance(transcript, dict)
        else getattr(transcript, "words", None)
    ) or []
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
