from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ...admin_auth import require_admin_user
from ...ai import _get_missing_llm_key_error
from ...config import get_config
from ...database import get_db
from ...runtime_settings import (
    RUNTIME_SETTING_KEYS,
    decrypt_setting_value,
    encrypt_setting_value,
    get_runtime_setting_rows,
    load_runtime_settings_cache,
)

router = APIRouter(prefix="/admin", tags=["admin"])


SETTING_METADATA = {
    "ASSEMBLY_AI_API_KEY": {
        "label": "AssemblyAI API key",
        "description": "Used for video transcription.",
        "input_type": "password",
    },
    "LLM": {
        "label": "LLM model",
        "description": "Provider and model, for example openai:gpt-5.2.",
        "input_type": "text",
    },
    "OPENAI_API_KEY": {
        "label": "OpenAI API key",
        "description": "Required for openai:* models.",
        "input_type": "password",
    },
    "GOOGLE_API_KEY": {
        "label": "Google API key",
        "description": "Required for google-gla:* models and fallback YouTube metadata.",
        "input_type": "password",
    },
    "ANTHROPIC_API_KEY": {
        "label": "Anthropic API key",
        "description": "Required for anthropic:* models.",
        "input_type": "password",
    },
    "OLLAMA_BASE_URL": {
        "label": "Ollama base URL",
        "description": "Optional URL for local or hosted Ollama-compatible endpoints.",
        "input_type": "text",
    },
    "OLLAMA_API_KEY": {
        "label": "Ollama API key",
        "description": "Optional, used by hosted Ollama-compatible providers.",
        "input_type": "password",
    },
    "YOUTUBE_DATA_API_KEY": {
        "label": "YouTube Data API key",
        "description": "Optional metadata provider key.",
        "input_type": "password",
    },
    "APIFY_API_TOKEN": {
        "label": "Apify API token",
        "description": "Optional YouTube download fallback provider token.",
        "input_type": "password",
    },
    "PEXELS_API_KEY": {
        "label": "Pexels API key",
        "description": "Optional B-roll stock footage provider key.",
        "input_type": "password",
    },
    "TRANSCRIPTION_PROVIDER": {
        "label": "Transcription provider",
        "description": "Which service transcribes source video into word-level timestamps.",
        "input_type": "select",
        "options": ["assemblyai", "whisper", "youtube_captions"],
    },
    "WHISPER_MODEL": {
        "label": "Whisper model",
        "description": "Model size used when the transcription provider is whisper.",
        "input_type": "select",
        "options": ["tiny", "base", "small", "medium", "large"],
    },
    "WHISPER_LANGUAGE": {
        "label": "Whisper language",
        "description": "Optional ISO 639-1 language code (e.g. en, es). Leave blank to auto-detect.",
        "input_type": "text",
    },
    "MAX_CLIPS": {
        "label": "Max clips per task",
        "description": "Upper bound on how many clips the AI selects from a source video.",
        "input_type": "text",
    },
    "CLIP_DURATION": {
        "label": "Default clip duration (seconds)",
        "description": "Target clip length used as a hint during AI segment selection.",
        "input_type": "text",
    },
    "DEFAULT_PROCESSING_MODE": {
        "label": "Default processing mode",
        "description": "Processing mode used when a task doesn't specify one.",
        "input_type": "select",
        "options": ["fast", "balanced", "quality"],
    },
    "FAST_MODE_MAX_CLIPS": {
        "label": "Fast mode max clips",
        "description": "Clip cap applied specifically to fast processing mode.",
        "input_type": "text",
    },
    "GPU_ACCELERATION_ENABLED": {
        "label": "GPU acceleration",
        "description": "Use hardware-accelerated video encoding (NVENC) for rendering when available.",
        "input_type": "select",
        "options": ["true", "false"],
    },
    "AUTO_GENERATE_METADATA_ENABLED": {
        "label": "Auto-generate metadata",
        "description": "Automatically generate title/description/tags for every clip right after "
        "clip detection finishes. When off, metadata is only generated via the manual "
        "Regenerate buttons.",
        "input_type": "select",
        "options": ["true", "false"],
    },
    "RANKING_SFX_FILENAME": {
        "label": "Ranking transition SFX",
        "description": "Filename of the sound effect played at every cut in a ranking compilation. "
        "Set by uploading a file in Settings -> Ranking; leave blank for silent transitions.",
        "input_type": "text",
    },
    "RANKING_SFX_OFFSET_PCT": {
        "label": "Ranking SFX offset (%)",
        "description": "How far before each cut the transition SFX starts, as a percentage of the "
        "SFX's own length.",
        "input_type": "text",
    },
    "RANKING_DEFAULT_FRAMING": {
        "label": "Ranking default framing",
        "description": "How a ranking clip that isn't already 9:16 fills the frame by default "
        "(overridable per clip).",
        "input_type": "select",
        "options": ["blur_fill", "crop_fill", "letterbox"],
    },
    "LLM_PROVIDER_MODE": {
        "label": "Local LLM provider mode",
        "description": "Ollama (local) is always tried first for content-policy/metadata features. "
        "Gemini fallback is used only if Ollama is unavailable.",
        "input_type": "select",
        "options": ["ollama", "gemini", "hybrid"],
    },
    "OLLAMA_MODEL": {
        "label": "Ollama model",
        "description": "Model used for content-policy and metadata generation calls. "
        "qwen2.5:7b-instruct gives noticeably better structured-output quality if it fits "
        "in VRAM (~4.5GB at Q4); qwen2.5:3b-instruct/llama3.2:3b are lighter fallbacks.",
        "input_type": "select",
        "options": [
            "qwen2.5:7b-instruct",
            "qwen2.5:3b-instruct",
            "llama3.2:3b",
            "gemma2:2b",
            "qwen2.5:3b",
        ],
    },
    "GEMINI_MODEL": {
        "label": "Gemini model",
        "description": "Fallback model used when Ollama is unavailable and LLM_PROVIDER_MODE allows it. "
        "Uses the same Google API key as the general LLM setting. Flash-lite variants are the "
        "cheapest/fastest and are plenty for hook and metadata generation.",
        "input_type": "select",
        "options": [
            "gemini-3.5-flash-lite",
            "gemini-3-flash-lite",
            "gemini-3.5-flash",
            "gemini-3-flash-preview",
        ],
    },
}


class RuntimeSettingsUpdate(BaseModel):
    updates: dict[str, str] = Field(default_factory=dict)
    delete_keys: list[str] = Field(default_factory=list)
    prefer_admin_values: dict[str, bool] = Field(default_factory=dict)


def _setting_status(
    setting_key: str, rows: dict[str, dict[str, object]]
):
    env_value = get_config()._get_optional_env(setting_key)
    has_env = bool(env_value)
    row = rows.get(setting_key, {})
    has_admin_value = bool(row.get("encrypted_value"))
    prefer_admin_value = bool(row.get("prefer_admin_value"))
    metadata = SETTING_METADATA[setting_key]

    if has_admin_value and (prefer_admin_value or not has_env):
        source = "admin"
    elif has_env:
        source = "environment"
    else:
        source = "unset"

    # Never expose secret values back to the client, but every other setting
    # must show its live effective value so users aren't left guessing what
    # is actually configured (only the raw input for typing a new value is
    # blank).
    current_value: str | None = None
    if metadata["input_type"] != "password":
        if source == "admin":
            encrypted_value = row.get("encrypted_value")
            if encrypted_value:
                try:
                    current_value = decrypt_setting_value(str(encrypted_value))
                except Exception:
                    current_value = None
        elif source == "environment":
            current_value = env_value

    disabled_reason: str | None = None
    if setting_key == "GPU_ACCELERATION_ENABLED":
        from ...video_utils import detect_gpu_encoder

        if not detect_gpu_encoder():
            disabled_reason = (
                "No supported GPU encoder (NVENC) detected on this machine — "
                "rendering will keep using CPU encoding regardless of this setting."
            )

    return {
        "key": setting_key,
        "label": metadata["label"],
        "description": metadata["description"],
        "input_type": metadata["input_type"],
        "options": metadata.get("options"),
        "source": source,
        "configured": has_env or has_admin_value,
        "has_admin_value": has_admin_value,
        "has_env_value": has_env,
        "prefer_admin_value": prefer_admin_value,
        "overridden_by_env": has_env and has_admin_value and not prefer_admin_value,
        "updated_at": row.get("updated_at"),
        "current_value": current_value,
        "disabled_reason": disabled_reason,
    }


@router.get("/health")
async def admin_health(
    request: Request, db: AsyncSession = Depends(get_db)
):
    await require_admin_user(request, db, get_config())
    return {"status": "ok"}



# Every settings page load/save triggers this check, so it must fail fast
# when Ollama isn't reachable rather than stalling the whole page — the
# explicit "Test connection" button (test_ollama_connection below) is the
# place for a more patient, user-initiated check.
_BACKGROUND_OLLAMA_CHECK_TIMEOUT_SECONDS = 1.5


async def _check_ollama_for_settings_page():
    """One background Ollama probe per settings request, reused for both the
    OLLAMA_MODEL live options and the llm_status indicator, so a page
    load/save never pays for two separate (redundant) round trips."""
    from ...ollama_status import check_ollama_status

    config = get_config()
    return await check_ollama_status(
        config.resolve_ollama_base_url(), timeout=_BACKGROUND_OLLAMA_CHECK_TIMEOUT_SECONDS
    )


def _build_llm_status(ollama_status, config: Config) -> dict[str, bool]:
    return {
        "ollama_connected": ollama_status.reachable,
        "gemini_key_set": bool(config.google_api_key),
    }


def _settings_with_live_ollama_models(
    rows: dict[str, dict[str, object]], ollama_status
) -> list[dict]:
    """Same as [_setting_status(k, rows) for k in RUNTIME_SETTING_KEYS], except
    OLLAMA_MODEL's `options` are refreshed from the live daemon (installed
    models) when reachable, instead of the static recommended-models list."""
    settings = [_setting_status(setting_key, rows) for setting_key in RUNTIME_SETTING_KEYS]
    if ollama_status.reachable and ollama_status.models:
        for setting in settings:
            if setting["key"] == "OLLAMA_MODEL":
                setting["options"] = ollama_status.models
    return settings


@router.get("/runtime-settings")
async def get_runtime_settings(request: Request, db: AsyncSession = Depends(get_db)):
    config = get_config()
    await require_admin_user(request, db, config)
    rows = await get_runtime_setting_rows(db)
    ollama_status = await _check_ollama_for_settings_page()
    return {
        "settings": _settings_with_live_ollama_models(rows, ollama_status),
        "llm_status": _build_llm_status(ollama_status, config),
    }


@router.post("/test-ollama-connection")
async def test_ollama_connection(request: Request, db: AsyncSession = Depends(get_db)):
    await require_admin_user(request, db, get_config())
    from ...ollama_status import check_ollama_status

    status = await check_ollama_status(get_config().resolve_ollama_base_url())
    return status.model_dump()


@router.post("/test-gemini-connection")
async def test_gemini_connection(request: Request, db: AsyncSession = Depends(get_db)):
    await require_admin_user(request, db, get_config())
    config = get_config()
    if not config.google_api_key:
        return {"ok": False, "error": "GOOGLE_API_KEY is not set"}

    from pydantic_ai import Agent

    try:
        agent = Agent[None, str](model=f"google-gla:{config.gemini_model}")
        result = await agent.run('Respond with exactly the word "ok".')
        return {"ok": True, "response": result.output}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@router.patch("/runtime-settings")
async def update_runtime_settings(
    request: Request,
    payload: RuntimeSettingsUpdate,
    db: AsyncSession = Depends(get_db),
):
    user_id = await require_admin_user(request, db, get_config())
    allowed_keys = set(RUNTIME_SETTING_KEYS)
    invalid_keys = [
        key
        for key in [
            *payload.updates.keys(),
            *payload.delete_keys,
            *payload.prefer_admin_values.keys(),
        ]
        if key not in allowed_keys
    ]
    if invalid_keys:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported setting key(s): {', '.join(sorted(set(invalid_keys)))}",
        )

    if "LLM" in payload.updates:
        config_error = _get_missing_llm_key_error(
            payload.updates["LLM"].strip(), get_config()
        )
        if config_error and "API_KEY is not set" not in config_error:
            raise HTTPException(status_code=400, detail=config_error)

    for setting_key, raw_value in payload.updates.items():
        value = raw_value.strip()
        if not value:
            continue
        encrypted_value = encrypt_setting_value(value)
        await db.execute(
            text(
                """
                INSERT INTO app_settings (setting_key, encrypted_value, updated_by)
                VALUES (:setting_key, :encrypted_value, :updated_by)
                ON CONFLICT (setting_key) DO UPDATE
                SET encrypted_value = EXCLUDED.encrypted_value,
                    updated_by = EXCLUDED.updated_by,
                    updated_at = CURRENT_TIMESTAMP
                """
            ),
            {
                "setting_key": setting_key,
                "encrypted_value": encrypted_value,
                "updated_by": user_id,
            },
        )

    for setting_key, prefer_admin_value in payload.prefer_admin_values.items():
        await db.execute(
            text(
                """
                UPDATE app_settings
                SET prefer_admin_value = :prefer_admin_value,
                    updated_by = :updated_by,
                    updated_at = CURRENT_TIMESTAMP
                WHERE setting_key = :setting_key
                """
            ),
            {
                "setting_key": setting_key,
                "prefer_admin_value": prefer_admin_value,
                "updated_by": user_id,
            },
        )

    if payload.delete_keys:
        await db.execute(
            text(
                """
                DELETE FROM app_settings
                WHERE setting_key = ANY(CAST(:setting_keys AS text[]))
                """
            ),
            {"setting_keys": payload.delete_keys},
        )

    await db.commit()
    await load_runtime_settings_cache(db)

    rows = await get_runtime_setting_rows(db)
    ollama_status = await _check_ollama_for_settings_page()
    return {
        "settings": _settings_with_live_ollama_models(rows, ollama_status),
        "llm_status": _build_llm_status(ollama_status, get_config()),
    }
