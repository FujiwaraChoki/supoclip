"""Per-user ChatGPT registrations; credentials are encrypted before persistence."""
from __future__ import annotations

import hashlib
import json
import uuid

from sqlalchemy import text

from ..runtime_settings import decrypt_setting_value, encrypt_setting_value


class ChatGPTRepository:
    @staticmethod
    async def host_id(db) -> str:
        await db.execute(text("""
            INSERT INTO chatgpt_host (singleton, host_id) VALUES (true, :host_id)
            ON CONFLICT (singleton) DO NOTHING
        """), {"host_id": f"urn:uuid:{uuid.uuid4()}"})
        return (await db.execute(text("SELECT host_id FROM chatgpt_host"))).scalar_one()

    @staticmethod
    async def accounts(db, user_id: str):
        result = await db.execute(text("""
            SELECT * FROM chatgpt_accounts WHERE user_id = :user_id ORDER BY id
        """), {"user_id": user_id})
        return [dict(row) for row in result.mappings()]

    @staticmethod
    async def account(db, user_id: str, account_id: str, *, lock: bool = False):
        result = await db.execute(text("""
            SELECT * FROM chatgpt_accounts WHERE user_id = :user_id AND id = :id
        """ + (" FOR UPDATE" if lock else "")), {"user_id": user_id, "id": account_id})
        row = result.mappings().first()
        return dict(row) if row else None

    @staticmethod
    async def active(db, user_id: str):
        result = await db.execute(text("""
            SELECT * FROM chatgpt_accounts WHERE user_id = :user_id AND enabled
        """), {"user_id": user_id})
        row = result.mappings().first()
        return dict(row) if row else None

    @staticmethod
    def tokens(account) -> dict:
        return json.loads(decrypt_setting_value(account["encrypted_tokens"])) if account["encrypted_tokens"] else {}

    @staticmethod
    async def save_tokens(db, user_id: str, account_id: str, tokens: dict):
        await db.execute(text("""
            UPDATE chatgpt_accounts SET encrypted_tokens = :tokens
            WHERE user_id = :user_id AND id = :id
        """), {"tokens": encrypt_setting_value(json.dumps(tokens)), "user_id": user_id, "id": account_id})

    @staticmethod
    async def save_account(db, user_id: str, client_id: str, identity: dict, tokens: dict):
        result = await db.execute(text("""
            INSERT INTO chatgpt_accounts (id, user_id, client_id, subject, email, encrypted_tokens)
            VALUES (:id, :user_id, :client_id, :subject, :email, :tokens)
            ON CONFLICT (user_id, client_id) DO UPDATE
            SET email = EXCLUDED.email, subject = EXCLUDED.subject,
                encrypted_tokens = EXCLUDED.encrypted_tokens
            RETURNING id
        """), {"id": str(uuid.uuid4()), "user_id": user_id, "client_id": client_id,
                "subject": identity["sub"], "email": identity.get("email", "ChatGPT account"),
                "tokens": encrypt_setting_value(json.dumps(tokens))})
        return result.scalar_one()

    @staticmethod
    async def register_client(db, user_id: str, client_id: str):
        # Retain an issued registration even if its first code exchange fails.
        # No identity, tokens or plan access are assigned until OIDC validation.
        await db.execute(text("""
            INSERT INTO chatgpt_accounts (id, user_id, client_id, email)
            VALUES (:id, :user_id, :client_id, 'Pending ChatGPT sign-in')
            ON CONFLICT (user_id, client_id) DO NOTHING
        """), {"id": str(uuid.uuid4()), "user_id": user_id, "client_id": client_id})

    @staticmethod
    async def lock_user(db, user_id: str):
        await db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:user_id, 0))"), {"user_id": user_id})

    @staticmethod
    async def acknowledge(db, user_id: str, account_id: str):
        await db.execute(text("""UPDATE chatgpt_accounts SET welcome_seen = true
            WHERE user_id = :user_id AND id = :id"""), {"user_id": user_id, "id": account_id})

    @staticmethod
    async def disable(db, user_id: str, account_id: str, *, clear_tokens: bool = False):
        await db.execute(text("UPDATE chatgpt_accounts SET enabled = false" +
            (", encrypted_tokens = NULL" if clear_tokens else "") +
            " WHERE user_id = :user_id AND id = :id"), {"user_id": user_id, "id": account_id})

    @staticmethod
    async def save_attempt(db, user_id: str, state: str, attempt: dict):
        await db.execute(text("DELETE FROM chatgpt_oauth_attempts WHERE expires_at < CURRENT_TIMESTAMP"))
        await db.execute(text("""
            INSERT INTO chatgpt_oauth_attempts (state_hash, user_id, encrypted_attempt, expires_at)
            VALUES (:state, :user_id, :attempt, CURRENT_TIMESTAMP + INTERVAL '10 minutes')
        """), {"state": hashlib.sha256(state.encode()).hexdigest(), "user_id": user_id,
                "attempt": encrypt_setting_value(json.dumps(attempt))})

    @staticmethod
    async def consume_attempt(db, user_id: str, state: str):
        result = await db.execute(text("""
            DELETE FROM chatgpt_oauth_attempts
            WHERE state_hash = :state AND user_id = :user_id AND expires_at > CURRENT_TIMESTAMP
            RETURNING encrypted_attempt
        """), {"state": hashlib.sha256(state.encode()).hexdigest(), "user_id": user_id})
        value = result.scalar_one_or_none()
        # Commit consumption before the network exchange: callbacks cannot be replayed.
        await db.commit()
        return json.loads(decrypt_setting_value(value)) if value else None

    @staticmethod
    async def select_model(db, user_id: str, account_id: str, model: str):
        # Serialize account switches for a user, including across API processes.
        await ChatGPTRepository.lock_user(db, user_id)
        await db.execute(text("UPDATE chatgpt_accounts SET enabled = false WHERE user_id = :user_id"), {"user_id": user_id})
        await db.execute(text("""
            UPDATE chatgpt_accounts SET enabled = true, model = :model
            WHERE user_id = :user_id AND id = :id
        """), {"model": model, "user_id": user_id, "id": account_id})
