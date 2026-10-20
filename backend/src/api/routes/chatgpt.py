"""Session-only controls for local ChatGPT plan usage."""
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth_headers import get_signed_user_id
from ...config import get_config
from ...database import get_db
from ...services.chatgpt_service import ChatGPTError, ChatGPTService, local_urls

router = APIRouter(prefix="/chatgpt", tags=["chatgpt"])


def _require_local():
    if not get_config().self_host:
        raise HTTPException(403, "ChatGPT plan connections are available for local self-hosting only.")
    try:
        local_urls()
    except ChatGPTError as error:
        raise HTTPException(error.status_code, str(error)) from None


def _user(request: Request) -> str:
    _require_local()
    # Account linking must never accept an API key or unsigned user ID.
    return get_signed_user_id(request, get_config())


class AuthorizeRequest(BaseModel):
    account_id: str | None = Field(default=None, max_length=36)


class CompleteRequest(BaseModel):
    state: str = Field(min_length=32, max_length=256)
    code: str | None = Field(default=None, max_length=4096)
    client_id: str | None = Field(default=None, max_length=256)
    error: str | None = Field(default=None, max_length=256)


class ModelRequest(BaseModel):
    model: str = Field(min_length=1, max_length=200)


async def _call(operation):
    try:
        return await operation
    except ChatGPTError as error:
        raise HTTPException(error.status_code, str(error)) from None


@router.get("/status")
async def status(request: Request, db: AsyncSession = Depends(get_db)):
    return await _call(ChatGPTService(db).status(_user(request)))


@router.post("/authorize")
async def authorize(body: AuthorizeRequest, request: Request, db: AsyncSession = Depends(get_db)):
    return await _call(ChatGPTService(db).authorize(_user(request), body.account_id))


@router.get("/callback")
async def callback(request: Request):
    _require_local()
    # The loopback hop exchanges no tokens. The frontend verifies its state
    # cookie and SupoClip session before forwarding the code to /complete.
    query = {key: request.query_params[key] for key in ("state", "code", "client_id", "error")
             if key in request.query_params}
    app_url, _ = local_urls()
    return RedirectResponse(f"{app_url}/api/chatgpt/callback?{urlencode(query)}", status_code=303,
                            headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})


@router.post("/complete")
async def complete(body: CompleteRequest, request: Request, db: AsyncSession = Depends(get_db)):
    return await _call(ChatGPTService(db).complete(_user(request), **body.model_dump()))


@router.get("/accounts/{account_id}/models")
async def models(account_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    return {"models": await _call(ChatGPTService(db).models(_user(request), account_id))}


@router.post("/accounts/{account_id}/activate")
async def activate(account_id: str, body: ModelRequest, request: Request, db: AsyncSession = Depends(get_db)):
    await _call(ChatGPTService(db).activate(_user(request), account_id, body.model))
    return {"enabled": True}


@router.post("/accounts/{account_id}/acknowledge")
async def acknowledge(account_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    await _call(ChatGPTService(db).acknowledge(_user(request), account_id))
    return {"acknowledged": True}


@router.delete("/accounts/{account_id}")
async def disconnect(account_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    revoked = await _call(ChatGPTService(db).disconnect(_user(request), account_id))
    return {"disconnected": True, "revocation_confirmed": revoked}
