"""AI helpers for localization and social-ready copy."""

from __future__ import annotations

from pydantic import BaseModel, Field
from pydantic_ai import Agent

from .ai import _build_transcript_model, _get_missing_llm_key_error, _split_llm_name
from .config import get_config
from .runtime_settings import apply_settings_to_process_env


class LocalizedTranscript(BaseModel):
    language: str
    translated_text: str = Field(min_length=1)


class SocialCopy(BaseModel):
    title: str = Field(min_length=1, max_length=140)
    description: str = Field(min_length=1, max_length=2200)
    hashtags: list[str] = Field(default_factory=list, max_length=12)


def _agent(output_type: type[BaseModel], system_prompt: str) -> Agent:
    runtime_config = get_config()
    apply_settings_to_process_env(runtime_config.as_runtime_settings())
    error = _get_missing_llm_key_error(runtime_config.llm, runtime_config)
    if error:
        raise RuntimeError(error)
    provider, _ = _split_llm_name(runtime_config.llm)
    return Agent(
        model=_build_transcript_model(runtime_config),
        output_type=output_type,
        system_prompt=system_prompt,
        output_retries=2 if provider == "ollama" else 1,
    )


async def translate_transcript(text: str, target_language: str) -> LocalizedTranscript:
    """Translate spoken copy while preserving meaning and natural short-form pacing."""
    agent = _agent(
        LocalizedTranscript,
        "You localize short-form video transcripts. Preserve meaning, names, numbers, tone, "
        "and paragraph order. Return only the requested language; do not add facts or commentary.",
    )
    result = await agent.run(
        f"Translate the following clip transcript into {target_language}.\n\n{text.strip()}"
    )
    return result.output


async def generate_social_copy(text: str, platform: str) -> SocialCopy:
    """Generate grounded post copy tailored to one social platform."""
    agent = _agent(
        SocialCopy,
        "You write concise social post copy for an existing video clip. Stay grounded in the "
        "transcript, avoid clickbait that the clip cannot support, and return clean hashtags "
        "without spaces or a leading #.",
    )
    result = await agent.run(
        f"Create title, description, and hashtags for {platform}.\n\nClip transcript:\n{text.strip()}"
    )
    output = result.output
    output.hashtags = [
        tag.strip().lstrip("#").replace(" ", "")
        for tag in output.hashtags
        if tag.strip().lstrip("#")
    ][:12]
    return output
