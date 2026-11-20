"""Low-speech notice: SupoClip picks clips from what people say.

A video with little or no talking (gameplay, music, B-roll) still gets
clips, but weak ones. The worker stops after transcription with the
``low_speech`` failure (refunded) so the user can decide. Resuming that
task, or creating the same video again, makes the clips anyway.
"""

from __future__ import annotations

import re
from typing import Any, Mapping, Optional

LOW_SPEECH_CODE = "low_speech"
# Set in ``tasks.source_metadata`` when the ``low_speech_notice`` flag was on
# for the platform that created the task. Without it the worker never asks.
LOW_SPEECH_CHECK_KEY = "low_speech_check"
# Set in ``tasks.source_metadata`` once the user asked for clips anyway.
LOW_SPEECH_CONFIRMED_KEY = "low_speech_confirmed"

LOW_SPEECH_MESSAGE = (
    "SupoClip works best on videos where people talk, and this one has very little "
    "speech, so the clips may be weak. Your minutes were refunded. To make clips "
    "anyway, resume this task or create it again."
)

# AssemblyAI's error for audio without speech: a video with no transcript at all.
_NO_SPOKEN_AUDIO = "no spoken audio"
# "[00:06 - 00:15] Speaker A: " in front of every transcript line.
_LINE_PREFIX = re.compile(r"\[\d[\d:]* - \d[\d:]*\](?: Speaker [^:\s]+:)?")


def is_no_speech_error(error: BaseException) -> bool:
    """True when transcription failed only because nobody speaks."""
    return _NO_SPOKEN_AUDIO in str(error).lower()


def count_spoken_words(transcript_data: Any, transcript: Optional[str]) -> int:
    """Words in the transcript: word timings when present, else the text."""
    if isinstance(transcript_data, Mapping):
        words = transcript_data.get("words")
        if isinstance(words, list) and words:
            return sum(
                1
                for word in words
                if isinstance(word, Mapping) and str(word.get("text") or "").strip()
            )
    return len(_LINE_PREFIX.sub(" ", transcript or "").split())


def low_speech_details(
    words: int, source_seconds: Optional[float], min_words_per_minute: float
) -> Optional[dict[str, Any]]:
    """The notice's details when this video has too little speech, else None.

    ``min_words_per_minute`` 0 turns the notice off. Without a known length
    only a video with no words at all is flagged.
    """
    if min_words_per_minute <= 0:
        return None
    seconds = float(source_seconds or 0)
    if seconds <= 0:
        return {"words": 0, "words_per_minute": 0} if words <= 0 else None
    rate = words * 60.0 / seconds
    if rate >= min_words_per_minute:
        return None
    return {"words": words, "words_per_minute": round(rate)}
