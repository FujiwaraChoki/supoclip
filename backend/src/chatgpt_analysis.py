"""Task-local ChatGPT plan analysis, with no API-key or provider fallback."""
from __future__ import annotations

from contextvars import ContextVar
from types import SimpleNamespace

import httpx
from openai import APIError, AsyncOpenAI
from pydantic import ValidationError

from .database import AsyncSessionLocal
from .services.chatgpt_service import ChatGPTError, ChatGPTService, RESOURCE

# Bind identities, never tokens. Concurrent jobs/windows inherit their own context.
chatgpt_account: ContextVar[tuple[str, str, str] | None] = ContextVar("chatgpt_account", default=None)


def response_error(code: str | None, status_code: int = 502) -> ChatGPTError:
    if code in {"subscription_sharing_usage_limit_exceeded", "subscription_sharing_usage_unavailable"} or status_code == 429:
        return ChatGPTError("ChatGPT plan usage is unavailable or its limit was reached. Manage usage in ChatGPT settings before retrying.", 429)
    if status_code in {401, 403}:
        return ChatGPTError("ChatGPT access was declined or revoked. Check your plan and reconnect in settings.", status_code)
    return ChatGPTError("ChatGPT could not complete clip analysis. Please try again; no other AI provider was charged.", status_code)


class ChatGPTAnalysisAgent:
    """Small adapter preserving the existing analysis validation/retry contract."""
    def __init__(self, binding: tuple[str, str, str], output_type, instructions: str):
        self.binding = binding
        self.output_type = output_type
        self.instructions = instructions

    async def run(self, prompt: str):
        user_id, account_id, model = self.binding
        messages = [{"role": "user", "content": prompt}]
        for attempt in range(3):
            async with AsyncSessionLocal() as db:
                # The selected account/model is a job snapshot. Switching models
                # affects new jobs; signing out clears tokens and stops new calls.
                token = await ChatGPTService(db).access_token(user_id, account_id)
            try:
                async with AsyncOpenAI(api_key=token, base_url=RESOURCE, max_retries=0, timeout=120) as client:
                    stream = await client.responses.create(
                        model=model, instructions=self.instructions, input=messages,
                        store=False, stream=True,
                        text={"format": {"type": "json_schema", "name": self.output_type.__name__,
                                         "schema": self.output_type.model_json_schema()}},
                    )
                    completed, output = False, ""
                    async with stream:
                        async for event in stream:
                            if event.type == "response.output_text.delta":
                                output += event.delta
                                if len(output) > 2_000_000:
                                    raise response_error(None)
                            elif event.type == "response.completed":
                                completed = event.response.status == "completed"
                            elif event.type in {"response.failed", "response.incomplete", "error", "response.refusal.delta"}:
                                error = getattr(getattr(event, "response", None), "error", None)
                                raise response_error(getattr(error, "code", None) or getattr(event, "code", None))
                    if not completed:
                        raise response_error(None)
            except APIError as error:
                raise response_error(getattr(error, "code", None), getattr(error, "status_code", None) or 502) from None
            except httpx.HTTPError:
                raise response_error(None) from None
            try:
                return SimpleNamespace(output=self.output_type.model_validate_json(output),
                                       response=SimpleNamespace(model_name=model))
            except ValidationError:
                if attempt == 2:
                    raise ChatGPTError("ChatGPT returned invalid clip data after three attempts. Try another model in settings.", 502) from None
                messages.extend([{"role": "assistant", "content": output},
                                 {"role": "user", "content": "Return valid JSON matching the requested schema. Check every required field and timestamp."}])
