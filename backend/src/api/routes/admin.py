from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.exceptions import UserError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ...admin_auth import require_admin_user
from ...ai import _build_transcript_model, _get_missing_llm_key_error
from ...config import get_config
from ...database import get_db
from ...llm_fallback import (
    key_fingerprint,
    model_provider,
    run_with_fallback,
    split_budget,
)
from ...runtime_settings import (
    RUNTIME_SETTING_KEYS,
    encrypt_setting_value,
    get_cached_setting,
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
    "LLM_FALLBACKS": {
        "label": "LLM fallback models",
        "description": (
            "Models to try when the main one is busy, separated by commas, "
            "for example google-gla:gemini-2.5-flash,google-gla:gemini-2.5-flash-lite."
        ),
        "input_type": "text",
    },
    "LLM_PROVIDER_KEYS": {
        "label": "Extra provider keys",
        "description": (
            "JSON with the extra keys of the providers, for example "
            '{"google": ["key1", "key2"]}. The keys are encrypted.'
        ),
        "input_type": "text",
    },
    "OPENAI_API_KEY": {
        "label": "OpenAI API key",
        "description": "Required for openai:* models.",
        "input_type": "password",
    },
    "OPENROUTER_API_KEY": {
        "label": "OpenRouter API key",
        "description": "Required for openrouter:* models, including Sonnet with Luna fallback.",
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

    return {
        "key": setting_key,
        "label": metadata["label"],
        "description": metadata["description"],
        "input_type": metadata["input_type"],
        "source": source,
        "configured": has_env or has_admin_value,
        "has_admin_value": has_admin_value,
        "has_env_value": has_env,
        "prefer_admin_value": prefer_admin_value,
        "overridden_by_env": has_env and has_admin_value and not prefer_admin_value,
        "updated_at": row.get("updated_at"),
    }


@router.get("/health")
async def admin_health(
    request: Request, db: AsyncSession = Depends(get_db)
):
    await require_admin_user(request, db, get_config())
    return {"status": "ok"}


LLM_PROVIDERS = ("google", "google-gla", "openai", "openrouter", "anthropic", "ollama")
PROBE_TIMEOUT_SECONDS = 30.0
PROBE_PROMPT = "Answer with the single word: ok"


class LlmChainUpdate(BaseModel):
    fallbacks: list[str] = Field(default_factory=list)


class LlmKeyInput(BaseModel):
    provider: str
    key: str


class LlmKeyDelete(BaseModel):
    provider: str
    fingerprint: str


class LlmModelTest(BaseModel):
    model: str


async def _save_setting(
    db: AsyncSession, setting_key: str, value: str, user_id: str
) -> None:
    """
    Store one encrypted setting and refresh the cache of the process.

    A value that an administrator saves by hand wins over the environment:
    otherwise the chain of the page would be ignored by the deployment.
    """
    await db.execute(
        text(
            """
            INSERT INTO app_settings (setting_key, encrypted_value, prefer_admin_value, updated_by)
            VALUES (:setting_key, :encrypted_value, TRUE, :updated_by)
            ON CONFLICT (setting_key) DO UPDATE
            SET encrypted_value = EXCLUDED.encrypted_value,
                prefer_admin_value = TRUE,
                updated_by = EXCLUDED.updated_by,
                updated_at = CURRENT_TIMESTAMP
            """
        ),
        {
            "setting_key": setting_key,
            "encrypted_value": encrypt_setting_value(value),
            "updated_by": user_id,
        },
    )
    await db.commit()
    await load_runtime_settings_cache(db)


def _validate_model_name(model_name: str) -> str:
    """A model of the chain is a provider and a model name."""
    cleaned = (model_name or "").strip()
    if ":" not in cleaned:
        raise HTTPException(
            status_code=400,
            detail=(
                "A model is written as provider:model, for example "
                "google-gla:gemini-2.5-flash."
            ),
        )
    provider, name = cleaned.split(":", 1)
    provider = provider.strip().lower()
    if provider not in LLM_PROVIDERS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported provider '{provider}'.",
        )
    if not name.strip():
        raise HTTPException(status_code=400, detail="The model name is missing.")
    return f"{provider}:{name.strip()}"


def _current_chain() -> tuple[str, ...]:
    """
    The chain as the settings cache sees it.

    The cache is refreshed on every save, so the page shows what was just
    written and not what a config object remembers from earlier.
    """
    primary = get_config().llm
    models = [primary]
    for name in (get_cached_setting("LLM_FALLBACKS") or "").split(","):
        cleaned = name.strip()
        if cleaned and cleaned not in models:
            models.append(cleaned)
    return tuple(models)


def _stored_keys() -> dict[str, list[str]]:
    """The provider keys that an administrator stored."""
    raw = (get_cached_setting("LLM_PROVIDER_KEYS") or "").strip()
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except ValueError:
        return {}
    if not isinstance(payload, dict):
        return {}
    stored: dict[str, list[str]] = {}
    for provider, value in payload.items():
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, list):
            continue
        keys = [str(item).strip() for item in value if str(item).strip()]
        if keys:
            stored[str(provider).strip().lower()] = keys
    return stored


def _key_rows(config) -> dict[str, list[dict[str, str]]]:
    """The keys of every provider, marked but never shown."""
    stored = _stored_keys()
    rows: dict[str, list[dict[str, str]]] = {}
    for provider in LLM_PROVIDERS:
        entries: list[dict[str, str]] = []
        for key in stored.get(provider, []):
            entries.append(
                {"fingerprint": key_fingerprint(key) or "", "source": "admin"}
            )
        env_key = config.provider_env_key(provider)
        if env_key and env_key not in stored.get(provider, []):
            entries.append(
                {"fingerprint": key_fingerprint(env_key) or "", "source": "environment"}
            )
        if entries:
            rows[provider] = entries
    return rows


def _probe_agent(model_name: str, key: str | None) -> Agent:
    config = get_config()
    config_error = _get_missing_llm_key_error(model_name, config)
    if config_error:
        raise UserError(config_error)
    return Agent(
        _build_transcript_model(config, model_name, key),
        output_type=str,
        system_prompt=PROBE_PROMPT,
    )


@router.get("/llm")
async def get_llm_settings(request: Request, db: AsyncSession = Depends(get_db)):
    """The chain of models and the keys of the providers, without the secrets."""
    await require_admin_user(request, db, get_config())
    config = get_config()
    chain = _current_chain()
    return {
        "primary": config.llm,
        "fallbacks": list(chain[1:]),
        "chain": list(chain),
        "providers": list(LLM_PROVIDERS),
        "keys": _key_rows(config),
        "keys_setting": bool(_stored_keys()),
    }


@router.patch("/llm/chain")
async def update_llm_chain(
    request: Request,
    payload: LlmChainUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Set the models that are tried after the main one."""
    user_id = await require_admin_user(request, db, get_config())
    fallbacks: list[str] = []
    for model_name in payload.fallbacks:
        cleaned = _validate_model_name(model_name)
        if cleaned not in fallbacks and cleaned != get_config().llm:
            fallbacks.append(cleaned)
    await _save_setting(db, "LLM_FALLBACKS", ",".join(fallbacks), user_id)
    config = get_config()
    return {
        "primary": config.llm,
        "fallbacks": list(_current_chain()[1:]),
        "chain": list(_current_chain()),
    }


@router.post("/llm/keys")
async def add_llm_key(
    request: Request,
    payload: LlmKeyInput,
    db: AsyncSession = Depends(get_db),
):
    """Add a key of one provider. The key itself is never returned."""
    user_id = await require_admin_user(request, db, get_config())
    provider = (payload.provider or "").strip().lower()
    if provider not in LLM_PROVIDERS:
        raise HTTPException(status_code=400, detail=f"Unsupported provider '{provider}'.")
    key = (payload.key or "").strip()
    if not key:
        raise HTTPException(status_code=400, detail="The key is empty.")

    config = get_config()
    stored = _stored_keys()
    keys = list(stored.get(provider, []))
    if key not in keys:
        keys.append(key)
    stored[provider] = keys
    await _save_setting(db, "LLM_PROVIDER_KEYS", json.dumps(stored), user_id)
    return {"provider": provider, "fingerprint": key_fingerprint(key) or ""}


@router.delete("/llm/keys")
async def delete_llm_key(
    request: Request,
    payload: LlmKeyDelete,
    db: AsyncSession = Depends(get_db),
):
    """Remove a key of one provider by its mark."""
    user_id = await require_admin_user(request, db, get_config())
    provider = (payload.provider or "").strip().lower()
    config = get_config()
    stored = _stored_keys()
    keys = list(stored.get(provider, []))
    remaining = [
        key for key in keys if key_fingerprint(key) != payload.fingerprint
    ]
    if remaining:
        stored[provider] = remaining
    else:
        stored.pop(provider, None)
    await _save_setting(db, "LLM_PROVIDER_KEYS", json.dumps(stored), user_id)
    return {"provider": provider, "fingerprint": payload.fingerprint}


@router.post("/llm/test/model")
async def test_llm_model(
    request: Request,
    payload: LlmModelTest,
    db: AsyncSession = Depends(get_db),
):
    """Ask one model something small and report what happened."""
    await require_admin_user(request, db, get_config())
    model_name = _validate_model_name(payload.model)
    config = get_config()
    provider = model_provider(model_name)
    keys = config.provider_keys(provider) or [None]
    started = time.monotonic()
    try:
        outcome = await run_with_fallback(
            lambda name, key: _probe_agent(name, key),
            lambda agent: asyncio.wait_for(agent.run("ok"), timeout=PROBE_TIMEOUT_SECONDS),
            chain=[model_name],
            keys_for=lambda name: keys,
            attempt_timeout=PROBE_TIMEOUT_SECONDS,
            total_timeout=PROBE_TIMEOUT_SECONDS * len(keys),
        )
    except Exception as exc:
        attempts = getattr(exc, "attempts", ())
        last = attempts[-1] if attempts else None
        return {
            "model": model_name,
            "ok": False,
            "seconds": round(time.monotonic() - started, 2),
            "error": last.error if last else str(exc),
            "kind": last.kind.value if last and last.kind else None,
        }
    return {
        "model": model_name,
        "ok": True,
        "seconds": round(time.monotonic() - started, 2),
        "answer": str(outcome.value.output)[:120],
        "attempts": [attempt.as_dict() for attempt in outcome.attempts],
    }


@router.post("/llm/test/chain")
async def test_llm_chain(
    request: Request, db: AsyncSession = Depends(get_db)
):
    """Run the whole chain and report every attempt."""
    await require_admin_user(request, db, get_config())
    config = get_config()
    chain = list(_current_chain())
    per_model = split_budget(PROBE_TIMEOUT_SECONDS * len(chain), len(chain))
    started = time.monotonic()
    try:
        outcome = await run_with_fallback(
            lambda name, key: _probe_agent(name, key),
            lambda agent: asyncio.wait_for(agent.run("ok"), timeout=per_model),
            chain=chain,
            keys_for=lambda name: config.provider_keys(model_provider(name)),
            attempt_timeout=per_model,
            total_timeout=PROBE_TIMEOUT_SECONDS * len(chain),
        )
    except Exception as exc:
        attempts = getattr(exc, "attempts", ())
        return {
            "ok": False,
            "seconds": round(time.monotonic() - started, 2),
            "answered_by": None,
            "attempts": [attempt.as_dict() for attempt in attempts],
        }
    return {
        "ok": True,
        "seconds": round(time.monotonic() - started, 2),
        "answered_by": outcome.model,
        "attempts": [attempt.as_dict() for attempt in outcome.attempts],
    }


@router.get("/runtime-settings")
async def get_runtime_settings(request: Request, db: AsyncSession = Depends(get_db)):
    await require_admin_user(request, db, get_config())
    rows = await get_runtime_setting_rows(db)
    return {
        "settings": [
            _setting_status(setting_key, rows) for setting_key in RUNTIME_SETTING_KEYS
        ]
    }


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
    return {
        "settings": [
            _setting_status(setting_key, rows) for setting_key in RUNTIME_SETTING_KEYS
        ]
    }
