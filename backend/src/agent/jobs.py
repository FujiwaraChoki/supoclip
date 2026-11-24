"""Background work the agent started: studio edits and project follow-ups.

Jobs live in ``agent_jobs`` and are stepped by whichever API process claims
them (``runtime.poll``). Each step is short and idempotent enough to repeat:
a process that dies mid-step leaves the claim to expire and another process
picks the job up again.

clip_edit: prepare the studio for the clip if needed -> ask the AI editor ->
save the edited draft -> render a download (optional) -> done.
followup: wait for the project to finish -> wake the agent in the chat.
"""

from __future__ import annotations

import logging
from typing import Any

from . import events, store
from .api_client import AgentAPI, AgentAPIError, _detail_text

logger = logging.getLogger(__name__)

EDIT_STEP_DELAY = 3.0
PREPARE_DELAY = 6.0
FOLLOWUP_DELAY = 20.0
MAX_ATTEMPTS = 3


class JobFailed(Exception):
    pass


def job_card(job: dict) -> dict:
    from .tools import _job_card

    return _job_card(job)


async def _publish(job: dict, **changes: Any) -> None:
    await events.publish(job["conversation_id"], "job", {"job": job_card({**job, **changes})})


async def step(app, job: dict) -> None:
    """Advance one job by one step."""
    if job.get("expired"):
        await store.update_job(job["id"], status="failed", error="This took too long and was stopped.")
        await _publish(job, status="failed", error="This took too long and was stopped.")
        if job["kind"] == "followup":
            await _wake(job, outcome="expired")
        return
    api = AgentAPI(app, job["user_id"], job.get("team_id"), trace_id=f"agent-job-{job['id']}")
    try:
        if job["kind"] == "clip_edit":
            await _step_edit(app, api, job)
        elif job["kind"] == "followup":
            await _step_followup(api, job)
        else:
            await store.update_job(job["id"], status="failed", error="Unknown job")
    except JobFailed as exc:
        await store.update_job(job["id"], status="failed", error=str(exc))
        failed = {**job, "status": "failed", "error": str(exc)}
        await _publish(failed)
        await _after_edit(failed)
    except AgentAPIError as exc:
        message = _detail_text(exc.detail)
        if exc.status >= 500 and job.get("attempts", 0) < MAX_ATTEMPTS:
            await store.update_job(job["id"], delay_seconds=15, bump_attempts=True)
            return
        await store.update_job(job["id"], status="failed", error=message)
        failed = {**job, "status": "failed", "error": message}
        await _publish(failed)
        await _after_edit(failed)
    except Exception:  # noqa: BLE001
        logger.exception("Agent job %s failed", job["id"])
        if job.get("attempts", 0) < MAX_ATTEMPTS:
            await store.update_job(job["id"], delay_seconds=15, bump_attempts=True)
            return
        message = "Something went wrong on our side."
        await store.update_job(job["id"], status="failed", error=message)
        await _publish(job, status="failed", error=message)


# ----------------------------------------------------------------------
# Studio edits
# ----------------------------------------------------------------------
def _editor(job: dict) -> str:
    return f"/tasks/{job['task_id']}/clips/{job['clip_id']}/editor"


async def _step_edit(app, api: AgentAPI, job: dict) -> None:
    payload = dict(job.get("payload") or {})
    step_name = job.get("step") or "start"
    base = _editor(job)

    if step_name in {"start", "preparing"}:
        state = await api.get(base)
        status = state.get("status")
        if status == "ready":
            await store.update_job(job["id"], step="edit", delay_seconds=0)
            await _publish(job, step="edit")
            return
        if status == "preparing":
            await store.update_job(job["id"], step="preparing", delay_seconds=PREPARE_DELAY)
            if step_name != "preparing":
                await _publish(job, step="preparing")
            return
        if status == "failed" and step_name == "preparing":
            raise JobFailed(state.get("error") or "The studio couldn't prepare this clip.")
        await api.post(f"{base}/prepare")
        await store.update_job(job["id"], step="preparing", delay_seconds=PREPARE_DELAY)
        await _publish(job, step="preparing")
        return

    if step_name == "edit":
        state = await api.get(base)
        if state.get("status") != "ready":
            await store.update_job(job["id"], step="start", delay_seconds=PREPARE_DELAY)
            return
        draft = state.get("draft") or {}
        started = await api.post(
            f"{base}/ai",
            {"basis": state.get("basis"), "instruction": payload["instruction"], "document": draft.get("document")},
        )
        payload.update({"ai_job": started.get("id"), "revision": draft.get("revision"), "basis": state.get("basis")})
        await store.update_job(job["id"], step="ai", payload=payload, delay_seconds=EDIT_STEP_DELAY)
        await _publish(job, step="ai")
        return

    if step_name == "ai":
        result = await api.get(f"{base}/ai/{payload['ai_job']}")
        status = result.get("status")
        if status in {"queued", "running"}:
            await store.update_job(job["id"], delay_seconds=EDIT_STEP_DELAY)
            return
        if status == "failed":
            raise JobFailed(result.get("error") or "The AI editor couldn't make that change.")
        summary = (result.get("summary") or "").strip()
        notes = result.get("notes") or []
        if result.get("changed"):
            try:
                saved = await api.patch(
                    base,
                    {"basis": payload.get("basis"), "revision": payload.get("revision"), "document": result.get("document")},
                )
            except AgentAPIError as exc:
                if exc.status == 409 and job.get("attempts", 0) < MAX_ATTEMPTS:
                    # The draft changed meanwhile (the person is in the studio): redo it on the new draft.
                    await store.update_job(job["id"], step="edit", delay_seconds=2, bump_attempts=True)
                    return
                raise
            payload["revision"] = saved.get("revision")
        payload.update({"summary": summary, "notes": notes[:5], "changed": bool(result.get("changed"))})
        if payload.get("render") and result.get("changed"):
            export = await api.post(
                f"{base}/exports",
                {"basis": payload.get("basis"), "document": result.get("document"), "preset": "tiktok"},
            )
            payload["export_job"] = export.get("id")
            await store.update_job(job["id"], step="render", payload=payload, delay_seconds=EDIT_STEP_DELAY * 2)
            await _publish(job, step="render", result={"summary": summary})
            return
        await _finish_edit(job, payload, export_url=None)
        return

    if step_name == "render":
        export = await api.get(f"{base}/exports/{payload['export_job']}")
        status = export.get("status")
        if status in {"queued", "rendering", "running"}:
            await store.update_job(job["id"], delay_seconds=EDIT_STEP_DELAY * 2)
            return
        if status == "completed":
            url = f"/api/tasks/{job['task_id']}/clips/{job['clip_id']}/editor/exports/{payload['export_job']}/file"
            await _finish_edit(job, payload, export_url=url)
            return
        payload["render_error"] = export.get("error") or "The render didn't finish."
        await _finish_edit(job, payload, export_url=None)
        return

    raise JobFailed(f"Unknown step {step_name}")


async def _finish_edit(job: dict, payload: dict, *, export_url) -> None:
    result = {
        "summary": payload.get("summary") or ("Done." if payload.get("changed") else "Nothing needed to change."),
        "changed": payload.get("changed"),
        "notes": payload.get("notes") or [],
        "export_url": export_url,
        **({"render_error": payload["render_error"]} if payload.get("render_error") else {}),
    }
    await store.update_job(job["id"], status="completed", step="done", payload=payload, result=result)
    done = {**job, "status": "completed", "step": "done", "payload": payload, "result": result}
    await _publish(done)
    await _after_edit(done)


async def _after_edit(job: dict) -> None:
    """An edit asked to do something next: wake the agent with the outcome."""
    if job["kind"] != "clip_edit" or not (job.get("payload") or {}).get("then"):
        return
    await _wake(job, outcome=job.get("status") or "completed")


# ----------------------------------------------------------------------
# Follow-ups
# ----------------------------------------------------------------------
async def _step_followup(api: AgentAPI, job: dict) -> None:
    try:
        task = await api.get(f"/tasks/{job['task_id']}")
    except AgentAPIError as exc:
        if exc.status == 404:
            await store.update_job(job["id"], status="cancelled", error="The project was deleted.")
            return
        raise
    status = task.get("status")
    if status not in {"completed", "error", "cancelled"}:
        await store.update_job(job["id"], delay_seconds=FOLLOWUP_DELAY)
        return
    woke = await _wake({**job, "project_status": status, "clips_count": len(task.get("clips") or [])}, outcome=status)
    if not woke:
        # A turn is open in the chat (or waiting for approval): try again shortly.
        await store.update_job(job["id"], delay_seconds=15)
        return
    await store.update_job(job["id"], status="completed", step="done", result={"project_status": status})
    await _publish(job, status="completed", step="done")


async def _wake(job: dict, *, outcome: str) -> bool:
    from .runtime import start_followup_turn

    payload = job.get("payload") or {}
    if job["kind"] == "followup":
        title = payload.get("project_title") or "The project"
        if outcome == "completed":
            line = f'"{title}" finished processing with {job.get("clips_count", 0)} clips.'
        elif outcome == "expired":
            line = f'"{title}" still hasn\'t finished after 12 hours, so the follow-up was stopped.'
        else:
            line = f'"{title}" ended with status {outcome}.'
        text = (
            f"{line} Project id {job['task_id']}.\n"
            f"You asked to do this when it finished: {payload.get('then')}\n"
            "Do it now if it still makes sense, then tell the person briefly what you did."
        )
        notice = {"kind": "project_finished", "project_id": job["task_id"], "title": title, "status": outcome}
    else:
        result = job.get("result") or {}
        text = (
            f"The studio edit \"{payload.get('instruction')}\" on clip {job['clip_id']} (project {job['task_id']}) "
            f"{'finished: ' + str(result.get('summary')) if outcome == 'completed' else 'failed: ' + str(job.get('error'))}\n"
            f"You planned to do this next: {payload.get('then')}\n"
            "Do it now if it still makes sense, then tell the person briefly what you did."
        )
        notice = {"kind": "edit_finished", "project_id": job["task_id"], "clip_id": job["clip_id"],
                  "title": payload.get("clip_title"), "status": outcome}
    return await start_followup_turn(job, text, notice, timezone_name=payload.get("timezone"))
