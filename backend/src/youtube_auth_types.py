from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


YouTubeCookieAccountStatus = Literal[
    "pending_login",
    "healthy",
    "cooldown",
    "refresh_required",
    "invalid",
    "disabled",
    "legacy",
]

class YouTubeCookieAccountDTO(BaseModel):
    id: str
    label: str
    email_hint: Optional[str] = None
    status: YouTubeCookieAccountStatus
    priority: int = 100
    playwright_storage_state_path: Optional[str] = None
    yt_dlp_cookiefile_path: Optional[str] = None
    last_used_at: Optional[datetime] = None
    last_verified_at: Optional[datetime] = None
    last_refresh_started_at: Optional[datetime] = None
    last_refresh_completed_at: Optional[datetime] = None
    consecutive_auth_failures: int = 0
    cooldown_until: Optional[datetime] = None
    last_error_code: Optional[str] = None
    last_error_message: Optional[str] = None
    created_by_user_id: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class YouTubeCookieEventDTO(BaseModel):
    id: str
    account_id: str
    event_type: str
    status: str
    task_id: Optional[str] = None
    message: Optional[str] = None
    metadata_json: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class CreateYouTubeCookieAccountRequest(BaseModel):
    label: str = Field(min_length=1, max_length=255)
    email_hint: Optional[str] = Field(default=None, max_length=255)
    priority: int = Field(default=100, ge=0, le=1000)


class UpdateYouTubeCookieAccountRequest(BaseModel):
    label: Optional[str] = Field(default=None, min_length=1, max_length=255)
    email_hint: Optional[str] = Field(default=None, max_length=255)
    priority: Optional[int] = Field(default=None, ge=0, le=1000)
    status: Optional[YouTubeCookieAccountStatus] = None
    action: Optional[
        Literal["promote_primary", "retire", "disable", "enable", "refresh_required"]
    ] = None


class UploadYouTubeCookiesRequest(BaseModel):
    cookies_text: str = Field(min_length=1)


class ResolvedYouTubeCookieContext(BaseModel):
    account_id: Optional[str] = None
    label: Optional[str] = None
    status: Optional[YouTubeCookieAccountStatus] = None
    cookiefile_path: Optional[str] = None
    storage_state_path: Optional[str] = None
    is_legacy_fallback: bool = False


class AuthFailureClassification(BaseModel):
    is_auth_failure: bool
    code: str
    message: str
