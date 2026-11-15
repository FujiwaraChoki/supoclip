"""How one stored Stripe event changes a subscription's cancellation episode.

Pure: the runner loads the open review (if any), calls :func:`fold_event` and
writes the result. Events for one subscription are folded in ``event.created``
order. An event older than the review's snapshot cannot move it backwards.

- A *scheduled* cancellation (``cancel_at``/``cancel_at_period_end`` on a live
  subscription) opens a ``scheduled`` review. Stripe's customer portal sends the
  survey answer as a second update seconds later, which only updates the snapshot.
- A deletion without an open review opens an ``immediate`` review (cancelled now,
  or ended by Stripe after failed payments). A deletion that ends a scheduled
  episode only records ``ended_at``: the request was already investigated.
- Removing the schedule withdraws the episode. Before its report that means no
  report at all, after it a short update.
- New survey feedback after the report becomes a short update.
- A ``baseline`` row (scheduled before this shipped) is reopened only by a fresh
  transition into a schedule.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from .intake import LIVE_STATUSES

ENDED_STATUSES = frozenset({"canceled", "incomplete_expired"})


@dataclass
class OpenReview:
    id: str
    status: str
    kind: str
    snapshot: dict[str, Any]
    snapshot_at: datetime | None
    reported_feedback: dict[str, Any] | None


@dataclass
class StoredEvent:
    event_type: str
    created: datetime
    snapshot: dict[str, Any]


@dataclass
class FoldOutcome:
    create: dict[str, Any] | None = None
    update: dict[str, Any] = field(default_factory=dict)
    # ("feedback" | "withdrawn") updates to an already delivered report.
    notices: list[str] = field(default_factory=list)


def is_scheduled(snapshot: dict[str, Any]) -> bool:
    return snapshot.get("status") in LIVE_STATUSES and (
        bool(snapshot.get("cancel_at_period_end")) or snapshot.get("cancel_at") is not None
    )


def is_ended(event: StoredEvent) -> bool:
    return event.event_type == "customer.subscription.deleted" or event.snapshot.get("status") in ENDED_STATUSES


def feedback_of(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        "reason": snapshot.get("cancellation_reason"),
        "feedback": snapshot.get("cancellation_feedback"),
        "comment": snapshot.get("cancellation_comment"),
    }


def _timestamp(value: Any, fallback: datetime) -> datetime:
    if isinstance(value, (int, float)) and value > 0:
        return datetime.fromtimestamp(value, fallback.tzinfo)
    return fallback


def fold_event(review: OpenReview | None, event: StoredEvent, settle: timedelta) -> FoldOutcome:
    snapshot = event.snapshot
    if review is not None and review.snapshot_at is not None and event.created < review.snapshot_at:
        return FoldOutcome()  # stale: a newer event already described this subscription
    fresh = {"snapshot": snapshot, "snapshot_at": event.created} if review is not None else {}

    if is_ended(event):
        ended_at = _timestamp(snapshot.get("ended_at") or snapshot.get("canceled_at"), event.created)
        if review is None:
            return FoldOutcome(create={
                "kind": "immediate", "snapshot": snapshot, "snapshot_at": event.created,
                "requested_at": _timestamp(snapshot.get("canceled_at"), event.created),
                "ended_at": ended_at, "not_before": event.created + settle,
            })
        return FoldOutcome(update={**fresh, "ended_at": ended_at})

    if is_scheduled(snapshot):
        if review is None:
            return FoldOutcome(create={
                "kind": "scheduled", "snapshot": snapshot, "snapshot_at": event.created,
                "requested_at": _timestamp(snapshot.get("canceled_at"), event.created),
                "not_before": event.created + settle,
            })
        outcome = FoldOutcome(update=fresh)
        previous = snapshot.get("previous") or {}
        if review.status == "baseline" and previous.get("scheduled") is False:
            # Baselines are cancellations scheduled before this feature shipped. A fresh
            # transition into a schedule is a new request that was baselined only because
            # its event arrived (Stripe retry) after the baseline was taken.
            outcome.update.update({
                "status": "pending", "requested_at": _timestamp(snapshot.get("canceled_at"), event.created),
                "not_before": event.created + settle,
            })
            return outcome
        if review.status == "reported" and review.reported_feedback is not None:
            reported, now = review.reported_feedback, feedback_of(snapshot)
            if (now["feedback"], now["comment"]) != (reported.get("feedback"), reported.get("comment")) and (
                now["feedback"] or now["comment"]
            ):
                outcome.notices.append("feedback")
                outcome.update["reported_feedback"] = now
        return outcome

    previous = snapshot.get("previous") or {}
    if previous.get("scheduled") and snapshot.get("status") in LIVE_STATUSES:
        if review is None:
            return FoldOutcome()
        outcome = FoldOutcome(update={**fresh, "status": "withdrawn", "withdrawn_at": event.created})
        if review.status == "reported":
            outcome.notices.append("withdrawn")
        return outcome

    return FoldOutcome(update=fresh)
