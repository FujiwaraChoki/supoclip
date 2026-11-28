"""``/live-sessions``: continuous clipping of a live stream (live_sessions.py).

A session is started by ``POST /tasks/`` with ``source.live.follow``. These routes
list sessions, show one with its parts, and stop one.
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ...database import get_db
from ...live_sessions import list_sessions, load_session, session_parts, session_public, stop_session
from .tasks import _get_auth_context

router = APIRouter(prefix="/live-sessions", tags=["live sessions"])


@router.get("/")
async def get_live_sessions(request: Request, db: AsyncSession = Depends(get_db)):
    """The workspace's recent sessions, newest first (active ones carry their parts)."""
    auth = await _get_auth_context(request, db)
    rows = await list_sessions(db, auth.user_id, auth.team_id)
    return {
        "sessions": [
            session_public(row, await session_parts(db, str(row.id)) if row.status == "active" else None)
            for row in rows
        ]
    }


@router.get("/{session_id}")
async def get_live_session(session_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    auth = await _get_auth_context(request, db)
    row = await load_session(db, session_id, auth.user_id, auth.team_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Live session not found")
    return session_public(row, await session_parts(db, session_id))


@router.post("/{session_id}/stop")
async def stop_live_session(session_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    """Stop adding parts. Parts already made keep processing and keep their clips."""
    auth = await _get_auth_context(request, db)
    row = await load_session(db, session_id, auth.user_id, auth.team_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Live session not found")
    if row.status == "active":
        await stop_session(db, session_id, "user")
        row = await load_session(db, session_id, auth.user_id, auth.team_id)
    return session_public(row, await session_parts(db, session_id))
