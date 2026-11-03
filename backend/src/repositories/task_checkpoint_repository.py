"""Durable, task-private results from completed processing stages."""

import hashlib
import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .edit_transaction import TaskCancelled, commit_unless_editing


def checkpoint_input_key(url: str, source_type: str, processing_mode: str,
                         clip_length: str, config: Any) -> str:
    # Provider/language changes invalidate word timings. Rendering preferences
    # don't: retries keep completed clips and apply current styling to new ones.
    inputs = {
        "version": 1, "url": url.strip(), "source_type": source_type,
        "processing_mode": processing_mode, "clip_length": clip_length,
        "transcription_provider": getattr(config, "transcription_provider", "assemblyai"),
        "transcription_language": getattr(config, "transcription_language", ""),
        "whisper_model": getattr(config, "whisper_model", ""),
    }
    return hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()


class TaskCheckpointRepository:
    @staticmethod
    async def get(db: AsyncSession, task_id: str, input_key: str) -> dict[str, Any]:
        result = await db.execute(text("""
            SELECT payload FROM task_processing_checkpoints
            WHERE task_id = :task_id AND input_key = :input_key
        """), {"task_id": task_id, "input_key": input_key})
        return result.scalar_one_or_none() or {}

    @staticmethod
    async def save(db: AsyncSession, task_id: str, input_key: str,
                   patch: dict[str, Any]) -> None:
        result = await db.execute(text("""
            INSERT INTO task_processing_checkpoints (task_id, input_key, payload)
            SELECT id, :input_key, CAST(:payload AS jsonb) FROM tasks WHERE id = :task_id
            ON CONFLICT (task_id) DO UPDATE SET
                input_key = EXCLUDED.input_key,
                payload = CASE WHEN task_processing_checkpoints.input_key = EXCLUDED.input_key
                    THEN task_processing_checkpoints.payload || EXCLUDED.payload
                    ELSE EXCLUDED.payload END,
                updated_at = CURRENT_TIMESTAMP
            RETURNING task_id
        """), {"task_id": task_id, "input_key": input_key, "payload": json.dumps(patch)})
        if result.scalar_one_or_none() is None:
            raise TaskCancelled()
        await commit_unless_editing(db)
