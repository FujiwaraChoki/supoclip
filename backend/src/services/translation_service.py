"""Translate the spoken phrases of a clip.

The editor aligns a translation on the phrases of the English speech, so the
model is asked for one translated phrase per source phrase, in the same order.
The English timing never leaves the editor: the model only sees the text, so
the translation cannot invent its own timing.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time

from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.exceptions import UserError

from ..ai import _build_transcript_model, _get_missing_llm_key_error
from ..config import get_config
from ..llm_fallback import (
    ErrorKind,
    model_provider,
    run_with_fallback,
    split_budget,
)
from ..runtime_settings import apply_settings_to_process_env

logger = logging.getLogger(__name__)

MAX_PHRASES = 200

# A slow model must not hold the request open: the editor waits for the
# answer and shows nothing but a spinner. The budget grows with the number of
# phrases, because a long transcript is a longer call.
BASE_TIMEOUT_SECONDS = 60.0
SECONDS_PER_PHRASE = 5.0
MAX_TIMEOUT_SECONDS = 180.0
# The whole chain of models has to answer inside this budget.
MAX_TOTAL_TIMEOUT_SECONDS = 180.0


def request_timeout(phrase_count: int) -> float:
    """How long the model may take for this many phrases."""
    seconds = BASE_TIMEOUT_SECONDS + SECONDS_PER_PHRASE * max(0, phrase_count - 1)
    return min(MAX_TIMEOUT_SECONDS, seconds)


class PhraseTranslations(BaseModel):
    """One translated phrase per source phrase, in the same order."""

    translations: list[str] = Field(default_factory=list)


class TranslationResult(BaseModel):
    translations: list[str]
    needsReview: bool = False
    model: str = ""


class TranslationError(Exception):
    """A failure the creator should see as-is."""

    def __init__(self, message: str, code: str = "translation_failed"):
        super().__init__(message)
        self.code = code


SYSTEM_PROMPT = (
    "You translate the spoken subtitles of a video. You are given the phrases of "
    "the speech in order and you return one translated phrase per source phrase, "
    "in the same order. You never merge phrases, never split them, never drop "
    "one and never add commentary. You keep the meaning, the tone and the "
    "register of the speaker, and you keep names, brands and numbers as they "
    "are. The translation has to fit into the time of the phrase it belongs to, "
    "so it stays as short as the source phrase."
)


def split_sentences(text: str) -> list[str]:
    """Split translated text into sentences, keeping the punctuation."""
    flattened = " ".join(text.split())
    return [
        sentence.strip()
        for sentence in re.split(r"(?<=[.!?…])\s+", flattened)
        if sentence.strip()
    ]


def build_prompt(phrases: list[str], target_language: str) -> str:
    numbered = "\n".join(
        f"{index + 1}. {phrase}" for index, phrase in enumerate(phrases)
    )
    return (
        f"Translate these {len(phrases)} spoken phrases into {target_language}.\n"
        f"{numbered}"
    )


async def translate_phrases(
    phrases: list[str], target_language: str = "ru"
) -> TranslationResult:
    """Translate the phrases of the speech with the configured model."""
    texts = [phrase.strip() for phrase in phrases if phrase.strip()]
    if not texts:
        return TranslationResult(translations=[], model="")
    if len(texts) > MAX_PHRASES:
        raise TranslationError("The transcript is too long to translate at once.")

    config = get_config()
    apply_settings_to_process_env(config.as_runtime_settings())
    chain = config.llm_chain()
    timeout = request_timeout(len(texts))
    # The whole chain has to answer inside one budget: the editor waits for it.
    total_timeout = min(MAX_TOTAL_TIMEOUT_SECONDS, timeout * 2)
    per_model_timeout = split_budget(total_timeout, len(chain))
    prompt = build_prompt(texts, target_language)

    def build_agent(model: str, key: str | None) -> Agent[None, PhraseTranslations]:
        config_error = _get_missing_llm_key_error(model, config)
        if config_error:
            # A model without a key is skipped, not a reason to give up.
            raise UserError(config_error)
        return Agent[None, PhraseTranslations](
            model=_build_transcript_model(config, model, key),
            output_type=PhraseTranslations,
            system_prompt=SYSTEM_PROMPT,
        )

    started = time.monotonic()
    try:
        outcome = await run_with_fallback(
            build_agent,
            lambda agent: agent.run(prompt),
            chain=chain,
            keys_for=lambda model: config.provider_keys(model_provider(model)),
            attempt_timeout=per_model_timeout,
            total_timeout=total_timeout,
        )
    except Exception as exc:
        # The chain reports the failure of its last attempt, with the attempts
        # that came before it.
        kinds = {
            attempt.kind
            for attempt in getattr(exc, "attempts", ())
            if attempt.kind is not None
        }
        if isinstance(exc, (asyncio.TimeoutError, TimeoutError)) or (
            kinds and kinds <= {ErrorKind.TRANSIENT}
        ):
            raise TranslationError(
                "The translation took too long. Try again.", code="translation_timeout"
            ) from exc
        logger.exception("Phrase translation failed")
        raise TranslationError(
            "The translation service is unavailable. Try again.",
            code="translation_unavailable",
        ) from exc
    logger.info(
        "Phrase translation answered in %.1fs for %d phrases with %s",
        time.monotonic() - started,
        len(texts),
        outcome.model,
    )

    model_name = outcome.model
    translations = [
        text.strip() for text in outcome.value.output.translations if text.strip()
    ]
    if len(translations) == len(texts):
        return TranslationResult(
            translations=translations, needsReview=False, model=model_name
        )
    # The model answered with a different number of items: keep its sentences
    # and let the editor show the mapping for a check.
    sentences = split_sentences(" ".join(translations))
    logger.info(
        "Translation returned %d items for %d phrases", len(sentences), len(texts)
    )
    return TranslationResult(
        translations=sentences, needsReview=True, model=model_name
    )
