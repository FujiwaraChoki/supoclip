"""The cron job: fold new Stripe events, investigate due reviews, deliver reports.

Runs every minute on the cron worker (``supoclip_cron`` queue, one copy at a
time), never on the render queue. Each step is idempotent and resumable:

- events are folded once (``folded_at``); a fold error is stored and retried
- a review is claimed with a lease; a worker that dies mid-investigation leaves
  an expired lease, and the next run claims it again
- the model being unavailable retries the investigation with backoff; the last
  attempt reports with what could be checked, saying what could not
- reports go to an outbox; delivery retries until the chat side confirms
"""

from __future__ import annotations

import logging
import shutil
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import text

from ..config import get_config
from . import store
from .delivery import deliver
from .evidence import (
    candidate_clips,
    fetch_and_measure,
    mask_email,
    migration_for_subscription,
    resolve_user,
    select_representative,
    user_context,
)
from .folding import feedback_of, fold_event
from .judging import ClipJudgment, JudgeUnavailable, assess_case, judge_clip, model_name
from .report import customer_text, plan_hint, render_report, render_update
from .schema import account_column, has_table

logger = logging.getLogger(__name__)

LEASE = timedelta(minutes=25)
REVIEWS_PER_RUN = 3
# The cron job times out after 30 minutes; a review can take up to ~10 with a slow model.
RUN_BUDGET_SECONDS = 15 * 60
MAX_ATTEMPTS = 4
RETRY_DELAYS = (timedelta(minutes=5), timedelta(minutes=20), timedelta(hours=1))
DELIVERY_DELAYS = (60, 300, 900, 3600, 3 * 3600, 6 * 3600)
NOTE_WINDOW = timedelta(days=45)
RECONCILE_GRACE = timedelta(hours=2)
TRANSFER_STAGES = ("source_stopped", "destination_staged", "destination_active")


class RetryLater(Exception):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _context_label(db, user_id: str | None) -> str:
    if not user_id:
        return "an unlinked Stripe customer"
    row = (await db.execute(text("SELECT email FROM users WHERE id = :id"), {"id": user_id})).first()
    return f"{mask_email(row.email) if row else 'deleted account'} (user {user_id})"


async def fold_pending_events(db, settle: timedelta, source_account: str | None) -> int:
    folded = 0
    for row in await store.unfolded_events(db):
        try:
            review = await store.lock_open_review(db, row["subscription_id"])
            outcome = fold_event(review, store.stored_event(row), settle)
            if outcome.create is not None:
                user_id = await resolve_user(db, row["account_id"], row["customer_id"], source_account)
                await store.create_review(
                    db, account_id=row["account_id"], livemode=row["livemode"], subscription_id=row["subscription_id"],
                    customer_id=row["customer_id"], user_id=user_id, **outcome.create,
                )
            elif review is not None and outcome.update:
                if review.status == "baseline" and outcome.update.get("status") == "pending":
                    # Baselines were taken from `users`, without the event's account and mode.
                    outcome.update.update(account_id=row["account_id"], customer_id=row["customer_id"],
                                          livemode=row["livemode"])
                await store.update_review(db, review.id, **outcome.update)
            if review is not None and outcome.notices:
                current = await store.get_review(db, review.id)
                label = await _context_label(db, current.get("user_id"))
                for kind in outcome.notices:
                    await store.enqueue_delivery(db, review.id, "update", render_update(kind, current, label))
            await store.mark_event_folded(db, row["id"])
            await db.commit()
            folded += 1
        except Exception as exc:
            await db.rollback()
            last = row["fold_attempts"] + 1 >= store.MAX_FOLD_ATTEMPTS
            (logger.exception if last else logger.warning)(
                "Could not fold Stripe cancellation event %s (attempt %d)", row["id"], row["fold_attempts"] + 1,
                exc_info=True)
            await store.mark_event_folded(db, row["id"], error=f"{type(exc).__name__}: {exc}"[:500])
            await db.commit()
    return folded


async def reconcile(db, now: datetime, source_account: str | None) -> int:
    """Catch cancellations no webhook told us about (missed delivery, an account without an endpoint)."""
    created = 0
    account = await account_column(db)
    rows = await db.execute(
        text(
            f"""
            SELECT u.id, u.stripe_subscription_id, u.stripe_customer_id, {account} AS stripe_account_id,
                   u.subscription_cancel_at
            FROM users u
            WHERE u.subscription_provider = 'stripe' AND u.subscription_cancel_at IS NOT NULL
              AND u.stripe_subscription_id IS NOT NULL AND u."updatedAt" < :settled
              AND NOT EXISTS (SELECT 1 FROM cancellation_reviews r
                              WHERE r.subscription_id = u.stripe_subscription_id AND r.status <> 'withdrawn')
              AND NOT EXISTS (SELECT 1 FROM stripe_cancellation_events e
                              WHERE e.subscription_id = u.stripe_subscription_id AND e.folded_at IS NULL
                                AND e.fold_attempts < :max_fold_attempts)
            LIMIT 20
            """
        ),
        {"settled": now - RECONCILE_GRACE, "max_fold_attempts": store.MAX_FOLD_ATTEMPTS},
    )
    for row in rows.all():
        await store.create_review(
            db, account_id=row.stripe_account_id or source_account or "", livemode=True,
            subscription_id=row.stripe_subscription_id, customer_id=row.stripe_customer_id, user_id=row.id,
            origin="reconciliation", kind="scheduled", not_before=now,
            snapshot={"status": "active", "cancel_at": int(row.subscription_cancel_at.timestamp()), "reconciled": True},
            snapshot_at=now,
        )
        created += 1
    if not await has_table(db, "stripe_account_migrations"):
        await db.commit()
        return created
    # A customer who cancels mid-transfer: their source subscription was stopped by the transfer
    # (excluded), and the cancellation itself only shows up in the migration row.
    reopened = await db.execute(
        text(
            """
            UPDATE cancellation_reviews r SET status = 'pending', exclusion_reason = NULL, not_before = :now,
                   attempts = 0, updated_at = NOW()
            FROM stripe_account_migrations m
            WHERE r.status = 'excluded' AND r.exclusion_reason = 'account_migration_transfer'
              AND m.cancellation_requested AND m.source_subscription = r.subscription_id
            """
        ),
        {"now": now},
    )
    await db.commit()
    return created + (reopened.rowcount or 0)


async def attach_late_notes(db, now: datetime) -> int:
    count = 0
    for note in await store.late_notes(db, NOTE_WINDOW, now):
        if note["review_status"] == "reported":
            review = await store.get_review(db, note["review_id"])
            label = await _context_label(db, review.get("user_id"))
            await store.enqueue_delivery(db, review["id"], "update", render_update("note", review, label, note["message"]))
            await store.attach_notes(db, [note["id"]], review["id"])
            count += 1
        # Pending or processing reviews pick the note up when they are investigated.
    await db.commit()
    return count


async def classify(db, review: dict[str, Any]) -> tuple[str, str | None, list[str]]:
    """("exclude", reason) for transfers, ("merge", other id) for duplicates, else ("report", None, flags)."""
    snapshot = review.get("snapshot") or {}
    flags: list[str] = []
    migration = await migration_for_subscription(db, review["account_id"], review["subscription_id"])
    if migration and not migration["cancellation_requested"]:
        if migration["is_source"] and (snapshot.get("migration_operation") or migration["stage"] in TRANSFER_STAGES):
            return "exclude", "account_migration_transfer", flags
        if not migration["is_source"] and migration["review_reason"]:
            return "exclude", "account_migration_rollback", flags
    if migration and migration["cancellation_requested"]:
        flags.append("cancelled during the move between Stripe accounts")
        other = (
            await db.execute(
                text(
                    """
                    SELECT id FROM cancellation_reviews
                    WHERE user_id = :user_id AND id <> :id AND status IN ('reported', 'processing')
                      AND created_at >= NOW() - INTERVAL '14 days'
                    LIMIT 1
                    """
                ),
                {"user_id": migration["user_id"], "id": review["id"]},
            )
        ).first()
        if other:
            return "merge", other.id, flags
    if snapshot.get("reconciled"):
        flags.append("found from SupoClip's account state, not a Stripe event: the survey answer and comment "
                     "are not available")
    return "report", None, flags


def _case(review, context, usage, notes, evidence, judgments) -> dict[str, Any]:
    snapshot = review.get("snapshot") or {}
    return {
        "cancellation": {
            "kind": review["kind"], "stripe_reason": snapshot.get("cancellation_reason"),
            "requested_at": review.get("requested_at"), "ended_at": review.get("ended_at"),
            "interval": snapshot.get("interval"), "price_cents": snapshot.get("amount"),
            "subscription_started": snapshot.get("start_date"),
        },
        "customer_words": {
            "survey_answer": snapshot.get("cancellation_feedback"),
            "comment": customer_text(snapshot.get("cancellation_comment")),
            "in_app_notes": [customer_text(n["message"]) for n in notes],
        },
        "account": None if context is None else {
            "signed_up": context["user"]["signed_up"], "plan_now": context["user"]["plan_now"],
            "acquisition": context["user"]["acquisition"], "tasks": context["tasks"], "activity": context["activity"],
            "usage_this_period": None if not usage else {
                k: usage.get(k) for k in ("usage_minutes", "minute_limit", "output_short_side", "period_start", "period_end")
            },
        },
        "clips_reviewed": [
            {
                "picked_because": item.role, "virality_score": item.clip.get("virality_score"),
                "available": item.available, "unavailable_reason": item.unavailable_reason,
                "measurements": item.metrics, "automatic_checks": item.checks,
                "judgment": (judgments[item.clip["id"]].model_dump()
                             if isinstance(judgments.get(item.clip["id"]), ClipJudgment)
                             else judgments.get(item.clip["id"])),
            }
            for item in evidence
        ],
    }


async def investigate(review: dict[str, Any], storage_factory, temp_root: str, source_account: str | None) -> str:
    """Investigate one claimed review and file its report. Returns the outcome."""
    from ..database import AsyncSessionLocal
    from ..runtime_settings import load_runtime_settings_cache
    from ..services.billing_service import BillingService

    final_attempt = review["attempts"] >= MAX_ATTEMPTS
    Path(temp_root).mkdir(parents=True, exist_ok=True)
    work_dir = Path(tempfile.mkdtemp(prefix="cancellation-review-", dir=temp_root))
    try:
        async with AsyncSessionLocal() as db:
            await load_runtime_settings_cache(db)  # the Anthropic key is an admin setting
            decision, detail, flags = await classify(db, review)
            if decision == "exclude":
                await store.update_review(db, review["id"], status="excluded", exclusion_reason=detail,
                                          lease_expires_at=None)
                await db.commit()
                return f"excluded ({detail})"
            if decision == "merge":
                await store.update_review(db, review["id"], status="merged", exclusion_reason=f"merged into {detail}",
                                          lease_expires_at=None)
                await db.commit()
                return "merged"

            user_id = review.get("user_id") or await resolve_user(
                db, review["account_id"], review.get("customer_id"), source_account)
            context = await user_context(db, user_id) if user_id else None
            usage = None
            if context:
                try:
                    usage = await BillingService(db).get_usage_summary(user_id)
                except Exception:
                    logger.warning("Cancellation review %s: usage summary failed", review["id"], exc_info=True)
                    flags.append("this period's minute usage could not be computed")
            since = (review.get("requested_at") or review["created_at"]) - timedelta(days=2)
            notes = await store.notes_for_user(db, user_id, since) if user_id else []
            all_clips = await candidate_clips(db, user_id) if user_id else []
            await db.commit()

        selected = select_representative(all_clips)
        storage = storage_factory()
        expected = (usage or {}).get("output_short_side")
        import asyncio

        evidence = [
            await asyncio.to_thread(fetch_and_measure, storage, role, clip, work_dir, expected)
            for role, clip in selected
        ]
        judgments: dict[str, ClipJudgment | str] = {}
        failures: list[str] = []
        for item in evidence:
            if not item.available:
                continue
            try:
                judgments[item.clip["id"]] = await judge_clip(item)
            except JudgeUnavailable as exc:
                judgments[item.clip["id"]] = str(exc)
                failures.append(str(exc))
        case = _case(review, context, usage, notes, evidence, judgments)
        try:
            assessment: Any = await assess_case(case)
        except JudgeUnavailable as exc:
            assessment = str(exc)
            failures.append(str(exc))
        if failures and not final_attempt:
            raise RetryLater("; ".join(dict.fromkeys(failures)))
        if failures:
            flags.append(f"the review model ({model_name()}) failed after {review['attempts']} attempts")

        message = render_report(
            review, context, usage, notes, evidence, judgments, assessment, len(all_clips),
            plan_hint((context or {}).get("user", {}).get("plan_now"), review.get("snapshot") or {}), flags, _now(),
        )
        stored_evidence = {
            "case": case,
            "assessment": assessment.model_dump() if hasattr(assessment, "model_dump") else assessment,
            "model": model_name(), "flags": flags,
        }
        async with AsyncSessionLocal() as db:
            current = await store.get_review(db, review["id"])
            if current is None or current["status"] != "processing":
                return f"skipped (now {current and current['status']})"
            await store.update_review(
                db, review["id"], status="reported", report_text=message, reported_at=_now(), user_id=user_id,
                reported_feedback=feedback_of(review.get("snapshot") or {}), evidence=stored_evidence,
                lease_expires_at=None, last_error=None,
            )
            await store.attach_notes(db, [n["id"] for n in notes], review["id"])
            await store.enqueue_delivery(db, review["id"], "report", message)
            await db.commit()
        return "reported"
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


async def _postpone(review: dict[str, Any], error: str) -> None:
    from ..database import AsyncSessionLocal

    delay = RETRY_DELAYS[min(review["attempts"], len(RETRY_DELAYS)) - 1]
    async with AsyncSessionLocal() as db:
        await store.update_review(db, review["id"], status="pending", not_before=_now() + delay,
                                  last_error=error[:500], lease_expires_at=None)
        await db.commit()


async def _report_failure(review: dict[str, Any], error: str) -> None:
    """Last resort after every attempt failed: the cancellation is still reported, with why it wasn't investigated."""
    from ..database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        label = await _context_label(db, review.get("user_id"))
        snapshot = review.get("snapshot") or {}
        feedback = snapshot.get("cancellation_feedback")
        comment = customer_text(snapshot.get("cancellation_comment"))
        message = "\n".join(x for x in (
            f"🔻 SupoClip cancellation ({review['kind']}) — {label}",
            f"The investigation failed {review['attempts']} times ({error[:200]}), so no clips were judged.",
            f"Survey answer: {feedback}" if feedback else "No survey answer.",
            f"Customer's comment (quoted): “{comment}”" if comment else "No comment.",
            f"Refs: {review['subscription_id']} · review {review['id'][:8]}",
        ) if x)
        await store.update_review(db, review["id"], status="reported", report_text=message, reported_at=_now(),
                                  reported_feedback=feedback_of(snapshot), lease_expires_at=None, last_error=error[:500])
        await store.enqueue_delivery(db, review["id"], "report", message)
        await db.commit()


async def deliver_due(db, url: str, secret: str, now: datetime) -> int:
    sent = 0
    for item in await store.due_deliveries(db, now):
        result = await deliver(url, secret, item["id"], item["message"], item["kind"])
        if result.delivered:
            await store.mark_delivery(db, item["id"], sent=True)
            sent += 1
            continue
        attempts = item["attempts"] + 1
        delay = DELIVERY_DELAYS[min(attempts, len(DELIVERY_DELAYS)) - 1]
        await store.mark_delivery(db, item["id"], sent=False, error=result.detail, retry_at=now + timedelta(seconds=delay))
        log = logger.error if attempts >= 3 else logger.warning
        log("Cancellation report %s not delivered (attempt %d): %s", item["id"], attempts, result.detail)
    return sent


async def process_cancellation_reviews(ctx: dict[str, Any] | None = None) -> dict[str, Any]:
    from ..database import AsyncSessionLocal
    from ..media_storage import MediaStorage

    config = get_config()
    if not config.cancellation_reviews_enabled:
        return {"enabled": False}
    settle = timedelta(seconds=config.cancellation_review_settle_seconds)
    source_account = config.stripe_source_account_id
    now = _now()
    async with AsyncSessionLocal() as db:
        if not await has_table(db, "cancellation_reviews"):
            return {"enabled": True, "waiting_for": "the API to apply the migration"}
        folded = await fold_pending_events(db, settle, source_account)
        reconciled = await reconcile(db, now, source_account)
        notes = await attach_late_notes(db, now)
    outcomes = []
    started = time.monotonic()
    # One review at a time, so a slow model never holds claimed reviews past the job timeout.
    while len(outcomes) < REVIEWS_PER_RUN and time.monotonic() - started < RUN_BUDGET_SECONDS:
        async with AsyncSessionLocal() as db:
            claimed = await store.claim_due_reviews(db, _now(), LEASE, 1)
        if not claimed:
            break
        review = claimed[0]
        try:
            outcomes.append(await investigate(review, MediaStorage, config.temp_dir, source_account))
        except RetryLater as exc:
            await _postpone(review, str(exc))
            outcomes.append("retry later")
        except Exception as exc:
            logger.exception("Cancellation review %s failed (attempt %d)", review["id"], review["attempts"])
            error = f"{type(exc).__name__}: {exc}"
            if review["attempts"] >= MAX_ATTEMPTS:
                await _report_failure(review, error)
                outcomes.append("reported failure")
            else:
                await _postpone(review, error)
                outcomes.append("retry later")
    sent = 0
    if config.cancellation_report_webhook_url and config.cancellation_report_webhook_secret:
        async with AsyncSessionLocal() as db:
            sent = await deliver_due(db, config.cancellation_report_webhook_url,
                                     config.cancellation_report_webhook_secret, _now())
    return {"folded": folded, "reconciled": reconciled, "late_notes": notes, "reviews": outcomes, "delivered": sent}
