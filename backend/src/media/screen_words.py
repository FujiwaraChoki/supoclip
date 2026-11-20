"""Caption words the picture already shows, and words that are not speech.

A game with subtitles on writes its characters' lines on the screen. Captions
for the same words say everything twice, one above the other. Music and sound
effects also leave a few "words" in a transcript that nobody said.

``spoken_caption_words`` takes the transcript words of a clip and returns the
ones worth a caption: it reads the lower part of the picture (tesseract, the
same local OCR the interruption-notice check uses) at the moment each phrase
is spoken, and drops a phrase whose words are written there. It also drops a
lone low-confidence word with silence around it. Anything it cannot check is
kept: a missing binary, a failed decode or a timeout never costs a caption.
"""

from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple
import logging
import os
import re
import shutil
import subprocess

logger = logging.getLogger(__name__)

PHRASE_GAP_SECONDS = 0.8        # a longer silence starts a new phrase
PHRASE_MAX_WORDS = 12
SCREEN_BAND_TOP = 0.6           # games write subtitles in the lower part of the picture
SCREEN_FRAME_WIDTH = 2560       # enlarged: subtitle text is small, and the OCR wants tall letters
SCREEN_TEXT_LEVEL = 175         # subtitles are near white; everything darker becomes background
SCREEN_MATCH_SHARE = 0.6        # of a phrase's content words found on screen
CONTENT_WORD_LETTERS = 5        # shorter words (the, you, over) are on any screen with text: they prove nothing
UNLABELLED_OWN_RUN = 8          # this many words in a row not on screen, without speaker labels: somebody else talking
PHRASE_MIN_CONTENT = 3          # content words a phrase needs before it can count as read from the screen
MAX_SCREEN_READS = 30           # per clip
READ_EVERY_SECONDS = 1.25       # how often the picture is read while somebody speaks
READ_REACH_SECONDS = 1.5        # a subtitle may come up this long before its word and stay as long after
SUBTITLED_VOICE_SHARE = 0.4     # of a voice's content words found on screen: the game subtitles that voice ...
SUBTITLED_VOICE_WORDS = 8       # ... judged on this many content words at least; then none of its words get a caption
STRAY_MAX_WORDS = 2             # a "phrase" this short ...
STRAY_SILENCE_SECONDS = 4.0     # ... with this much silence either side ...
STRAY_MAX_CONFIDENCE = 0.5      # ... and this little confidence is a sound, not speech

_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)


def _tokens(text: str) -> List[str]:
    return [token.lower() for token in _TOKEN.findall(text or "")]


def phrases(words: Sequence[Dict[str, Any]]) -> List[List[int]]:
    """Indices of ``words`` grouped into phrases: runs without a long pause, of a readable length."""
    groups: List[List[int]] = []
    for index, word in enumerate(words):
        if (groups and len(groups[-1]) < PHRASE_MAX_WORDS
                and word["start"] - words[groups[-1][-1]]["end"] < PHRASE_GAP_SECONDS):
            groups[-1].append(index)
        else:
            groups.append([index])
    return groups


def read_screen(video_path: Path, seconds: float) -> List[str]:
    """Words written in the lower part of the picture at ``seconds``. Raises when it cannot be read."""
    binary = shutil.which("tesseract")
    if binary is None:
        raise RuntimeError("tesseract is not installed")
    frame = subprocess.run(
        ["ffmpeg", "-v", "error", "-nostdin", "-ss", f"{max(0.0, seconds):.3f}", "-i", str(video_path),
         "-frames:v", "1", "-an", "-sn",
         "-vf", f"crop=iw:ih*{1 - SCREEN_BAND_TOP:.2f}:0:ih*{SCREEN_BAND_TOP:.2f},"
                f"scale={SCREEN_FRAME_WIDTH}:-2:flags=lanczos,format=gray,"
                f"lut='if(gte(val,{SCREEN_TEXT_LEVEL}),0,255)'",
         "-f", "image2pipe", "-c:v", "png", "-"],
        capture_output=True, timeout=30,
    )
    if frame.returncode != 0 or not frame.stdout:
        raise RuntimeError(frame.stderr.decode("utf-8", "replace")[-200:] or "no frame")
    read = subprocess.run(
        [binary, "stdin", "stdout", "--psm", "11"], input=frame.stdout, capture_output=True, timeout=15,
        env={**os.environ, "OMP_THREAD_LIMIT": "1"},
    )
    if read.returncode != 0:
        raise RuntimeError(read.stderr.decode("utf-8", "replace")[-200:])
    return _tokens(read.stdout.decode("utf-8", "replace"))


def _written(token: str, shown: set, by_length: Dict[int, List[str]]) -> bool:
    if token in shown:
        return True
    # The OCR gets a letter wrong now and then: a long word may differ in one.
    return len(token) >= 5 and any(
        sum(a != b for a, b in zip(token, other)) <= 1 for other in by_length.get(len(token), ())
    )


def _read_moments(words: Sequence[Dict[str, Any]], groups: Sequence[Sequence[int]]) -> List[float]:
    """When to look at the picture: every ``READ_EVERY_SECONDS`` while somebody speaks, thinned to the budget."""
    moments: List[float] = []
    for group in groups:
        start, end = words[group[0]]["start"], words[group[-1]]["end"]
        count = max(1, int(round((end - start) / READ_EVERY_SECONDS)))
        moments += [start + (end - start) * (k + 0.5) / count for k in range(count)]
    if len(moments) > MAX_SCREEN_READS:
        step = len(moments) / MAX_SCREEN_READS
        moments = [moments[int(k * step)] for k in range(MAX_SCREEN_READS)]
    return moments


def spoken_caption_words(video_path: Path, words: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """``words`` (source seconds: text, start, end, confidence, speaker) without the ones that need no caption."""
    words = list(words)
    if not words:
        return words
    groups = phrases(words)
    drop: set = set()
    for position, group in enumerate(groups):
        if len(group) > STRAY_MAX_WORDS:
            continue
        before = words[group[0]]["start"] - (words[groups[position - 1][-1]]["end"] if position else -1e9)
        after = (words[groups[position + 1][0]]["start"] if position + 1 < len(groups) else 1e9) - words[group[-1]]["end"]
        confidence = sum(float(words[i].get("confidence", 1.0) or 0.0) for i in group) / len(group)
        if min(before, after) >= STRAY_SILENCE_SECONDS and confidence < STRAY_MAX_CONFIDENCE:
            drop.update(group)
    try:
        reads = [(moment, read_screen(video_path, moment))
                 for moment in _read_moments(words, [g for g in groups if not drop.issuperset(g)])]
    except Exception as exc:  # noqa: BLE001 - captions for everything when the picture cannot be read
        logger.warning("Screen words unavailable for %s (%s); keeping the captions", video_path, exc)
        reads = []
    # A word is written when a look at the picture around the time it is spoken shows it.
    written: Dict[int, bool] = {}
    for index, word in enumerate(words):
        tokens = [t for t in _tokens(str(word.get("text", ""))) if len(t) >= CONTENT_WORD_LETTERS]
        if not tokens or index in drop:
            continue
        shown: set = set()
        for moment, screen in reads:
            if word["start"] - READ_REACH_SECONDS <= moment <= word["end"] + READ_REACH_SECONDS:
                shown.update(screen)
        by_length: Dict[int, List[str]] = {}
        for token in shown:
            by_length.setdefault(len(token), []).append(token)
        written[index] = all(_written(token, shown, by_length) for token in tokens)
    # A voice whose words keep turning up on screen is the game's, subtitled: none of it needs a caption, also
    # the lines the OCR missed (half-captioned dialogue is worse than either). With speaker labels that is one
    # character or the narrator, and the streamer keeps their captions; without them it is the whole clip.
    voices: Dict[Any, List[int]] = {}
    for index in written:
        voices.setdefault(words[index].get("speaker"), []).append(index)
    for speaker, indices in voices.items():
        if (len(indices) >= SUBTITLED_VOICE_WORDS
                and sum(written[i] for i in indices) >= SUBTITLED_VOICE_SHARE * len(indices)):
            gone = {index for index, word in enumerate(words) if word.get("speaker") == speaker}
            if speaker is None:
                # No labels to tell the voices apart: a long stretch with nothing of it on screen is the
                # streamer between two subtitled lines, and keeps its captions.
                run: List[int] = []
                for index in sorted(indices) + [None]:
                    if index is not None and not written[index]:
                        run.append(index)
                        continue
                    if len(run) >= UNLABELLED_OWN_RUN:
                        gone -= set(range(run[0], run[-1] + 1))
                    run = []
            drop.update(gone)
    # Otherwise only the phrases that are written out: a line read from the screen between the streamer's own.
    for group in groups:
        content = [i for i in group if i in written]
        if len(content) >= PHRASE_MIN_CONTENT and sum(written[i] for i in content) >= SCREEN_MATCH_SHARE * len(content):
            drop.update(group)
    if drop:
        logger.info("Captions: %d of %d words left out (shown on screen or not speech)", len(drop), len(words))
    return [word for index, word in enumerate(words) if index not in drop]


def without_screen_words(
    video_path: Path, transcript_data: Dict[str, Any], ranges: Sequence[Tuple[float, float]]
) -> Dict[str, Any]:
    """``transcript_data`` without the words of ``ranges`` (source seconds) that need no caption.

    The transcript itself is not changed: the clip's text, its length and the clip studio still see every word.
    """
    from .timeline import get_absolute_words_in_range

    source: List[Dict[str, Any]] = []
    for start, end in ranges:
        source += get_absolute_words_in_range(transcript_data, start, end)
    kept = {id(word) for word in spoken_caption_words(video_path, source)}
    gone = [(word["start"], word["end"]) for word in source if id(word) not in kept]
    if not gone:
        return transcript_data

    def dropped(word: Dict[str, Any]) -> bool:
        middle = (float(word["start"]) + float(word["end"])) / 2000.0
        return any(start - 0.01 <= middle <= end + 0.01 for start, end in gone)

    return {**transcript_data, "words": [word for word in transcript_data["words"] if not dropped(word)]}
