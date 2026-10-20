"""Local, self-hosted Sign in with ChatGPT and renewable plan credentials.

The loopback callback hands the code back to the frontend, which checks its
HttpOnly state cookie and SupoClip session before requesting token exchange.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import os
import secrets
import time
from urllib.parse import urlencode, urlsplit

import httpx
import jwt

from ..database import AsyncSessionLocal
from ..repositories.chatgpt_repository import ChatGPTRepository as Repo

ISSUER = "https://auth.openai.com"
AUTHORIZE_URL = f"{ISSUER}/api/accounts/authorize"
TOKEN_URL = f"{ISSUER}/api/accounts/oauth/token"
RESOURCE = "https://api.openai.com/v1"
SCOPES = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"
PLAN_SCOPE = "chatgpt.tokens.use.direct"
USAGE_URL = "https://chatgpt.com/settings/usage"
_jwks = jwt.PyJWKClient(f"{ISSUER}/.well-known/jwks.json", timeout=15)


class ChatGPTError(RuntimeError):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def local_urls() -> tuple[str, str]:
    app_url = os.getenv("NEXT_PUBLIC_APP_URL", "http://localhost:3107").rstrip("/")
    callback = os.getenv("CHATGPT_REDIRECT_URI", "http://127.0.0.1:8000/chatgpt/callback")
    app, redirect = urlsplit(app_url), urlsplit(callback)
    if (app.scheme != "http" or app.hostname not in {"localhost", "127.0.0.1"}
            or app.path or app.query or app.fragment or app.username or app.password
            or redirect.scheme != "http" or redirect.hostname != "127.0.0.1"
            or redirect.path != "/chatgpt/callback" or redirect.query or redirect.fragment
            or redirect.username or redirect.password):
        raise ChatGPTError("ChatGPT sign-in requires SupoClip running locally on this computer.", 403)
    return app_url, callback


def token_record(payload: dict, previous: dict | None = None) -> dict:
    previous = previous or {}
    if not isinstance(payload.get("access_token"), str) or not payload["access_token"]:
        raise ChatGPTError("OpenAI returned an incomplete sign-in response. Try again.", 502)
    result = {**previous, **{key: payload[key] for key in (
        "access_token", "refresh_token", "id_token", "scope", "earliest_refresh_at"
    ) if key in payload}}
    result["expires_at"] = time.time() + float(payload.get("expires_in", 3600))
    return result


def validate_identity(id_token: str, client_id: str, nonce: str) -> dict:
    try:
        key = _jwks.get_signing_key_from_jwt(id_token)
        claims = jwt.decode(id_token, key.key, algorithms=["RS256"], audience=client_id,
                            issuer=ISSUER, options={"require": ["exp", "iss", "aud", "sub", "nonce"]})
        if not secrets.compare_digest(str(claims["nonce"]), nonce):
            raise ValueError("nonce")
        if not isinstance(claims["sub"], str) or not claims["sub"]:
            raise ValueError("subject")
        return claims
    except Exception:
        # JWT/provider exceptions can contain credentials; never propagate them.
        raise ChatGPTError("Unable to verify the ChatGPT account. Please sign in again.", 400) from None


async def token_request(data: dict) -> dict:
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(TOKEN_URL, data={**data, "resource": RESOURCE})
        if response.status_code >= 500:
            raise ChatGPTError("ChatGPT sign-in is temporarily unavailable. Please try again.", 502)
        if response.status_code == 429:
            raise ChatGPTError("ChatGPT sign-in is rate limited. Please try again later.", 429)
        if response.status_code != 200:
            raise ChatGPTError("ChatGPT authorization expired or was declined. Please reconnect.", 401)
        payload = response.json()
        if not isinstance(payload, dict):
            raise ChatGPTError("OpenAI returned an incomplete sign-in response. Try again.", 502)
        return payload
    except (httpx.HTTPError, ValueError):
        raise ChatGPTError("Unable to contact ChatGPT. Please try again.", 502) from None


class ChatGPTService:
    def __init__(self, db):
        self.db = db

    async def status(self, user_id: str) -> dict:
        accounts = []
        for row in await Repo.accounts(self.db, user_id):
            tokens = Repo.tokens(row)
            accounts.append({key: row[key] for key in ("id", "email", "model", "enabled", "welcome_seen")}
                            | {"connected": bool(tokens), "plan_usage": PLAN_SCOPE in tokens.get("scope", "").split()})
        return {"accounts": accounts, "usage_url": USAGE_URL}

    async def authorize(self, user_id: str, account_id: str | None = None) -> dict:
        _, callback = local_urls()
        account = await Repo.account(self.db, user_id, account_id) if account_id else None
        if account_id and not account:
            raise ChatGPTError("ChatGPT account not found.", 404)
        host_id = await Repo.host_id(self.db)
        state, nonce, verifier = (secrets.token_urlsafe(32) for _ in range(3))
        client_id = account["client_id"] if account else "dynamic_agent_client"
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        attempt = {"nonce": nonce, "verifier": verifier, "client_id": client_id,
                   "redirect_uri": callback, "subject": account["subject"] if account else None}
        await Repo.save_attempt(self.db, user_id, state, attempt)
        await self.db.commit()
        params = {"client_id": client_id, "ext_agent_host_id": host_id, "response_type": "code",
                  "redirect_uri": callback, "scope": SCOPES, "resource": RESOURCE,
                  "state": state, "nonce": nonce, "code_challenge_method": "S256", "code_challenge": challenge}
        if not account:
            params["agent_name_hint"] = "SupoClip"
        elif account["subject"]:
            params["login_hint"] = account["email"]
            if account["encrypted_tokens"] and PLAN_SCOPE not in Repo.tokens(account).get("scope", "").split():
                # Re-request permission only after an explicit prior decline.
                params["prompt"] = "consent"
        return {"authorization_url": f"{AUTHORIZE_URL}?{urlencode(params)}", "state": state}

    async def complete(self, user_id: str, state: str, code: str | None,
                       client_id: str | None, error: str | None = None) -> dict:
        attempt = await Repo.consume_attempt(self.db, user_id, state)
        if not attempt:
            raise ChatGPTError("This sign-in attempt expired or was already used. Please try again.")
        if error:
            raise ChatGPTError("ChatGPT sign-in was cancelled. You can try again in settings.")
        expected = attempt["client_id"]
        if expected == "dynamic_agent_client":
            if not client_id or not client_id.startswith("oaiapp_"):
                raise ChatGPTError("ChatGPT registration did not return a client ID. Please try again.")
            await Repo.register_client(self.db, user_id, client_id)
            await self.db.commit()
        elif client_id and client_id != expected:
            raise ChatGPTError("ChatGPT returned a different account registration. Please try again.")
        else:
            client_id = expected
        if not code:
            raise ChatGPTError("ChatGPT did not return an authorization code.")
        payload = await token_request({"grant_type": "authorization_code", "client_id": client_id,
                                       "code": code, "code_verifier": attempt["verifier"],
                                       "redirect_uri": attempt["redirect_uri"]})
        identity = await asyncio.to_thread(validate_identity, payload.get("id_token", ""), client_id, attempt["nonce"])
        if attempt["subject"] and identity["sub"] != attempt["subject"]:
            raise ChatGPTError("The ChatGPT account changed. Add it as a separate account instead.")
        tokens = token_record(payload)
        account_id = await Repo.save_account(self.db, user_id, client_id, identity, tokens)
        # Connecting is separate from opting a selected model into clip analysis.
        if PLAN_SCOPE not in tokens.get("scope", "").split():
            await Repo.disable(self.db, user_id, account_id)
        await self.db.commit()
        return {"connected": True}

    async def access_token(self, user_id: str, account_id: str, *, require_enabled: bool = False) -> str:
        # Row locking serializes rotating refresh tokens across API/worker processes.
        account = await Repo.account(self.db, user_id, account_id, lock=True)
        if not account or not account["encrypted_tokens"] or (require_enabled and not account["enabled"]):
            raise ChatGPTError("Reconnect your ChatGPT account in settings before clipping.", 401)
        tokens = Repo.tokens(account)
        if PLAN_SCOPE not in tokens.get("scope", "").split():
            raise ChatGPTError("Enable ChatGPT plan usage by reconnecting your account in settings.", 403)
        if tokens["expires_at"] <= time.time() + 60:
            if not tokens.get("refresh_token"):
                raise ChatGPTError("Your ChatGPT session expired. Reconnect in settings.", 401)
            tokens = token_record(await token_request({"grant_type": "refresh_token", "client_id": account["client_id"],
                                                      "refresh_token": tokens["refresh_token"]}), tokens)
            await Repo.save_tokens(self.db, user_id, account_id, tokens)
        await self.db.commit()
        if PLAN_SCOPE not in tokens.get("scope", "").split():
            raise ChatGPTError("ChatGPT plan permission was revoked. Reconnect in settings.", 403)
        return tokens["access_token"]

    async def models(self, user_id: str, account_id: str) -> list[dict]:
        token = await self.access_token(user_id, account_id)
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.get(f"{RESOURCE}/models", headers={"Authorization": f"Bearer {token}"})
            if response.status_code != 200:
                raise ChatGPTError("Unable to load your ChatGPT models. Check your plan or reconnect.", 502)
            return [{"id": model["slug"], "name": model.get("display_name") or model["slug"]}
                    for model in response.json().get("models", [])
                    if model.get("visibility") == "list" and isinstance(model.get("slug"), str)]
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            raise ChatGPTError("Unable to load your ChatGPT models. Please try again.", 502) from None

    async def activate(self, user_id: str, account_id: str, model: str):
        models = await self.models(user_id, account_id)
        if model not in {item["id"] for item in models}:
            raise ChatGPTError("Select a model available to your ChatGPT account.")
        await Repo.lock_user(self.db, user_id)
        account = await Repo.account(self.db, user_id, account_id, lock=True)
        if not account or not account["encrypted_tokens"]:
            raise ChatGPTError("Reconnect this ChatGPT account first.", 401)
        await Repo.select_model(self.db, user_id, account_id, model)
        await self.db.commit()

    async def acknowledge(self, user_id: str, account_id: str):
        await Repo.acknowledge(self.db, user_id, account_id)
        await self.db.commit()

    async def disconnect(self, user_id: str, account_id: str) -> bool:
        account = await Repo.account(self.db, user_id, account_id, lock=True)
        if not account:
            raise ChatGPTError("ChatGPT account not found.", 404)
        tokens = Repo.tokens(account)
        revoked = not bool(tokens.get("refresh_token"))
        if tokens.get("refresh_token"):
            try:
                async with httpx.AsyncClient(timeout=15) as client:
                    metadata = await client.get(f"{ISSUER}/.well-known/openid-configuration")
                    metadata.raise_for_status()
                    endpoint = metadata.json()["revocation_endpoint"]
                    if urlsplit(endpoint).scheme != "https" or urlsplit(endpoint).hostname != "auth.openai.com":
                        raise ValueError("revocation endpoint")
                    for delay in (0, 0.25, 0.75):
                        if delay:
                            await asyncio.sleep(delay)
                        response = await client.post(endpoint, data={"token": tokens["refresh_token"],
                            "token_type_hint": "refresh_token", "client_id": account["client_id"]})
                        if response.status_code == 200:
                            revoked = True
                            break
                        if response.status_code < 500:
                            break
            except (httpx.HTTPError, ValueError, KeyError):
                revoked = False
        await Repo.disable(self.db, user_id, account_id, clear_tokens=True)
        await self.db.commit()
        return revoked


async def active_chatgpt_account(user_id: str):
    async with AsyncSessionLocal() as db:
        row = await Repo.active(db, user_id)
        return (user_id, row["id"], row["model"]) if row and row["model"] else None
