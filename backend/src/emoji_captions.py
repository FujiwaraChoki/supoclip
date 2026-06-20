"""
Contextual emoji + keyword-emphasis annotation for burned captions.

OpusClip-style captions sprinkle a few well-chosen emojis next to meaningful
words and visually emphasise "power" words. This module does that purely from
the spoken word list (so it works without any AI changes) and can be augmented
with per-segment cues emitted by the AI layer.

Everything here is deterministic (no randomness) so the same clip always renders
identically, and emoji frequency is rate-limited so captions stay tasteful
rather than spammy.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set, Tuple

# ---------------------------------------------------------------------------
# Curated keyword -> emoji map. Keys are normalized (lowercase, alpha-numeric).
# Lookup also tries a naive singular form, so "ideas"/"dollars" still match.
# Keep this tasteful and broad; the rate limiter keeps density reasonable.
# ---------------------------------------------------------------------------
EMOJI_KEYWORD_MAP: Dict[str, str] = {
    # money / business
    "money": "💰", "cash": "💵", "dollar": "💵", "dollars": "💵", "rich": "🤑",
    "wealth": "💰", "wealthy": "💰", "millionaire": "🤑", "billionaire": "🤑",
    "million": "💰", "billion": "💰", "profit": "📈", "revenue": "📈",
    "income": "💵", "salary": "💸", "price": "🏷️", "cost": "💲", "free": "🆓",
    "invest": "📊", "investment": "📊", "investing": "📊", "stock": "📈",
    "stocks": "📈", "crypto": "🪙", "bitcoin": "₿", "business": "💼",
    "company": "🏢", "startup": "🚀", "deal": "🤝", "sale": "🛒", "buy": "🛒",
    "sell": "💸", "bank": "🏦", "tax": "🧾", "taxes": "🧾", "budget": "📒",
    "save": "🏦", "savings": "🏦", "debt": "💳", "fund": "💰",
    # growth / success / winning
    "growth": "📈", "grow": "📈", "growing": "📈", "scale": "📊", "success": "🏆",
    "successful": "🏆", "win": "🏆", "winning": "🏆", "winner": "🥇", "won": "🏆",
    "goal": "🎯", "goals": "🎯", "target": "🎯", "achieve": "✅", "result": "✅",
    "results": "✅", "best": "🥇", "first": "🥇", "top": "⬆️", "champion": "🏆",
    "level": "🆙", "upgrade": "⬆️", "boost": "🚀", "rocket": "🚀",
    # ideas / mind / learning
    "idea": "💡", "ideas": "💡", "think": "🤔", "thinking": "🤔", "thought": "💭",
    "smart": "🧠", "genius": "🧠", "brain": "🧠", "mind": "🧠", "learn": "📚",
    "learning": "📚", "study": "📚", "school": "🎓", "knowledge": "🧠",
    "lesson": "📖", "book": "📖", "books": "📚", "read": "📖", "question": "❓",
    "answer": "💡", "secret": "🤫", "truth": "💯", "fact": "📌", "facts": "💯",
    "remember": "🧠", "focus": "🎯", "discover": "🔍", "research": "🔬",
    # emotion / hype
    "love": "❤️", "loved": "❤️", "heart": "❤️", "amazing": "🤩", "incredible": "🤯",
    "insane": "🤯", "crazy": "🤯", "wow": "😮", "shocking": "😱", "scary": "😱",
    "fear": "😱", "happy": "😄", "happiness": "😄", "sad": "😢", "angry": "😡",
    "fire": "🔥", "hot": "🔥", "lit": "🔥", "cool": "😎", "perfect": "👌",
    "beautiful": "😍", "favorite": "⭐", "epic": "🤩", "magic": "✨",
    "powerful": "💪", "power": "⚡", "strong": "💪", "energy": "⚡",
    "stop": "✋", "warning": "⚠️", "danger": "⚠️", "boom": "💥", "explode": "💥",
    # time
    "time": "⏰", "today": "📅", "tomorrow": "📅", "now": "⏰", "fast": "⚡",
    "quick": "⚡", "quickly": "⚡", "instantly": "⚡", "minute": "⏱️",
    "minutes": "⏱️", "hour": "⏰", "hours": "⏰", "day": "📅", "days": "📅",
    "year": "📆", "years": "📆", "future": "🔮", "forever": "♾️", "deadline": "⏳",
    # people / social
    "people": "👥", "team": "🤝", "family": "👨‍👩‍👧", "friend": "🫂", "friends": "🫂",
    "everyone": "🙌", "everybody": "🙌", "you": "👉", "audience": "👀",
    "followers": "📲", "subscribe": "🔔", "viral": "📈", "famous": "🌟",
    "customer": "🛍️", "customers": "🛍️", "boss": "💼", "leader": "🫡",
    # work / hustle / health
    "work": "💼", "working": "💼", "hustle": "💪", "grind": "💪", "effort": "💪",
    "hard": "💪", "build": "🛠️", "building": "🏗️", "create": "🎨",
    "creating": "🎨", "health": "🏥", "healthy": "🥗", "food": "🍽️", "eat": "🍴",
    "gym": "🏋️", "workout": "🏋️", "muscle": "💪", "sleep": "😴", "water": "💧",
    "run": "🏃", "running": "🏃",
    # tech / world
    "ai": "🤖", "robot": "🤖", "tech": "💻", "technology": "💻", "computer": "💻",
    "phone": "📱", "internet": "🌐", "online": "🌐", "data": "📊", "code": "👨‍💻",
    "world": "🌍", "earth": "🌍", "global": "🌍", "space": "🚀", "science": "🔬",
    "game": "🎮", "games": "🎮", "music": "🎵", "video": "🎬", "movie": "🎬",
    "car": "🚗", "house": "🏠", "home": "🏠", "travel": "✈️", "light": "💡",
    "key": "🔑",
}

# Words that should be visually emphasised (highlight + pop) even without an
# emoji. Superlatives, absolutes and hype words that anchor a sentence.
POWER_WORDS: Set[str] = {
    "never", "always", "everything", "nothing", "everyone", "nobody", "anyone",
    "best", "worst", "most", "biggest", "huge", "massive", "tiny", "every",
    "only", "first", "last", "free", "now", "today", "instantly", "forever",
    "guaranteed", "proven", "secret", "truth", "fact", "literally", "actually",
    "exactly", "must", "need", "stop", "warning", "danger", "critical", "key",
    "important", "remember", "mistake", "wrong", "right", "perfect", "ultimate",
    "powerful", "insane", "crazy", "incredible", "amazing", "shocking", "viral",
    "million", "billion", "thousand", "percent", "double", "triple", "ten",
}

_NORMALIZE_RE = re.compile(r"[^a-z0-9%]+")
_NUMBER_RE = re.compile(r"\d")


def normalize_token(text: str) -> str:
    """Lowercase a word and strip surrounding punctuation."""
    return _NORMALIZE_RE.sub("", (text or "").lower())


def _singularize(token: str) -> str:
    """Very small naive singular form for plural keyword matching."""
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 4 and token.endswith("es") and not token.endswith("ses"):
        return token[:-2]
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def _lookup_emoji(token: str, lookup: Dict[str, str]) -> Optional[str]:
    if not token:
        return None
    if token in lookup:
        return lookup[token]
    singular = _singularize(token)
    if singular != token and singular in lookup:
        return lookup[singular]
    return None


def build_emoji_lookup(ai_cues: Optional[List[Dict[str, Any]]]) -> Dict[str, str]:
    """Merge the built-in emoji map with optional AI-provided cues.

    ai_cues: list of {"keyword": str, "emoji": str}. AI cues take precedence and
    can introduce contextual emojis the static map doesn't cover.
    """
    lookup = dict(EMOJI_KEYWORD_MAP)
    for cue in ai_cues or []:
        try:
            keyword = normalize_token(str(cue.get("keyword", "")))
            emoji = str(cue.get("emoji", "")).strip()
        except AttributeError:
            continue
        if keyword and emoji:
            lookup[keyword] = emoji
    return lookup


def annotate_caption_words(
    words: List[Dict[str, Any]],
    ai_cues: Optional[List[Dict[str, Any]]] = None,
    *,
    enable_emoji: bool = True,
    enable_emphasis: bool = True,
    max_emojis: int = 8,
    min_word_gap: int = 3,
    repeat_gap: int = 8,
) -> Tuple[Dict[int, str], Set[int]]:
    """Annotate a clip's word list with emojis and emphasis flags.

    Returns (emoji_by_index, emphasis_indices) keyed by index into ``words``.

    - Emoji placement is rate-limited: at most ``max_emojis`` per clip, no two
      emojis within ``min_word_gap`` words, and the same emoji is not reused
      within ``repeat_gap`` words.
    - Emphasis is set for emoji words, recognised power words, and numeric /
      percentage tokens (e.g. "10x", "90%").
    """
    emoji_by_index: Dict[int, str] = {}
    emphasis_indices: Set[int] = set()
    if not words:
        return emoji_by_index, emphasis_indices

    lookup = build_emoji_lookup(ai_cues) if enable_emoji else {}

    last_emoji_word = -(min_word_gap + 1)
    recent_emoji: Dict[str, int] = {}
    emoji_count = 0

    for idx, word in enumerate(words):
        raw = str(word.get("text", ""))
        token = normalize_token(raw)
        if not token:
            continue

        is_number = bool(_NUMBER_RE.search(token))
        emoji = _lookup_emoji(token, lookup) if enable_emoji else None

        # Emphasis: emoji words, power words, and numbers/percentages.
        if enable_emphasis and (emoji or token in POWER_WORDS or is_number):
            emphasis_indices.add(idx)

        if not emoji or emoji_count >= max_emojis:
            continue
        if idx - last_emoji_word < min_word_gap:
            continue
        if idx - recent_emoji.get(emoji, -(repeat_gap + 1)) < repeat_gap:
            continue

        emoji_by_index[idx] = emoji
        emphasis_indices.add(idx)
        last_emoji_word = idx
        recent_emoji[emoji] = idx
        emoji_count += 1

    return emoji_by_index, emphasis_indices
