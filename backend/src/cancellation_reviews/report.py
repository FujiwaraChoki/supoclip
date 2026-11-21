"""The chat message for a cancellation review, and the short updates that follow it.

Rendered from stored evidence, never by the model, so what is labelled measured,
judged or unavailable is exactly that. No links, no full email addresses and no
storage references leave the server. Customer-written text is redacted (links,
emails, phone numbers, long tokens), capped and quoted as evidence.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from .evidence import ClipEvidence
from .judging import CaseAssessment, ClipJudgment

FEEDBACK_LABELS = {
    "too_expensive": "too expensive",
    "missing_features": "missing features",
    "switched_service": "switched to another service",
    "unused": "not using it enough",
    "customer_service": "customer service",
    "too_complex": "too complicated",
    "low_quality": "quality was too low",
    "other": "other",
}
REASON_LABELS = {
    "cancellation_requested": "the customer cancelled",
    "payment_failed": "Stripe ended it after failed payments (involuntary)",
    "payment_disputed": "ended after a payment dispute (involuntary)",
}
CUSTOMER_TEXT_LIMIT = 600
_REDACTIONS = (
    (re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE), "[link removed]"),
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "[email removed]"),
    (re.compile(r"\+?\d[\d\s().-]{7,}\d"), "[number removed]"),
    (re.compile(r"\b[A-Za-z0-9_\-]{32,}\b"), "[token removed]"),
)


GUARD_CANCELLATIONS = {
    "refunded": "Refunded: the charge was fully refunded, so SupoClip ended the subscription. Not a customer cancellation and not a risk signal.",
    "dispute": "Cancelled by SupoClip's billing-risk guard after a payment dispute, not by the customer.",
    "fraud_warning": "Cancelled by SupoClip's billing-risk guard after a fraud warning, not by the customer.",
    # Rows written before refunds got their own label; the event type was not stored.
    "billing_risk": "Cancelled by SupoClip's billing-risk guard after a refund, dispute or fraud warning (an older record that does not say which), not by the customer.",
}


GUARD_COMMENT = re.compile(r"(?P<kind>refunded|dispute|fraud_warning|billing_risk):evt_\w+")


def guard_cancellation_line(comment: str | None) -> str | None:
    """The report line for a cancellation SupoClip made itself, from its `<kind>:<event id>` comment."""
    match = GUARD_COMMENT.match(comment or "")
    return GUARD_CANCELLATIONS[match["kind"]] if match else None


def customer_text(value: str | None) -> str | None:
    if not value or not value.strip():
        return None
    cleaned = " ".join(value.split())
    for pattern, replacement in _REDACTIONS:
        cleaned = pattern.sub(replacement, cleaned)
    cleaned = cleaned.replace("```", "'''")
    if len(cleaned) > CUSTOMER_TEXT_LIMIT:
        cleaned = cleaned[:CUSTOMER_TEXT_LIMIT].rstrip() + "…"
    return cleaned


def _date(value: Any) -> str:
    if isinstance(value, (int, float)) and value > 0:
        value = datetime.fromtimestamp(value, timezone.utc)
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).strftime("%-d %b %Y")
    return "unknown"


def _days_between(start: Any, end: datetime) -> int | None:
    if isinstance(start, (int, float)):
        start = datetime.fromtimestamp(start, timezone.utc)
    if isinstance(start, datetime):
        return max(0, (end - start).days)
    return None


PLAN_LABELS = {"pro": "Pro", "scale": "Creator"}


def plan_hint(plan_now: str | None, snapshot: dict[str, Any]) -> str:
    """The plan's display name: the account's plan while it is still paid, else read from the price."""
    if plan_now in PLAN_LABELS:
        return PLAN_LABELS[plan_now]
    amount, interval = snapshot.get("amount"), snapshot.get("interval")
    if isinstance(amount, int):
        monthly = amount / 12 if interval == "year" else amount
        return "Creator" if monthly >= 1800 else "Pro"
    return "paid plan"


def _plan_label(snapshot: dict[str, Any], plan: str) -> str:
    interval = {"month": "monthly", "year": "yearly"}.get(snapshot.get("interval") or "", "")
    amount, currency = snapshot.get("amount"), (snapshot.get("currency") or "").upper()
    price = f" ({amount / 100:.2f} {currency})" if isinstance(amount, int) and currency else ""
    return " ".join(x for x in (plan, interval) if x) + price


def headline(review: dict[str, Any], plan: str) -> tuple[str, str]:
    snapshot = review.get("snapshot") or {}
    plan = _plan_label(snapshot, plan)
    reason = snapshot.get("cancellation_reason")
    if review["kind"] == "scheduled":
        until = snapshot.get("cancel_at") or snapshot.get("current_period_end")
        what = f"end-of-period cancellation: access continues until {_date(until)}"
        if review.get("ended_at"):
            what += f" (subscription ended {_date(review['ended_at'])})"
    elif reason in ("payment_failed", "payment_disputed"):
        what = f"involuntary cancellation: {REASON_LABELS[reason]}, ended {_date(review.get('ended_at'))}"
    else:
        what = f"immediate cancellation: subscription ended {_date(review.get('ended_at'))}"
    return plan, what


def _usage_line(context: dict[str, Any], usage: dict[str, Any] | None) -> str:
    tasks, activity = context["tasks"], context["activity"]
    parts = [
        f"{tasks['total']} tasks ({tasks['completed']} completed, {tasks['failed']} failed, {tasks['cancelled']} cancelled)",
        f"{tasks['last_30_days']} in the last 30 days",
        f"{activity['clips']} clips",
    ]
    if usage and usage.get("minute_limit"):
        parts.append(f"{usage.get('usage_minutes', 0)} of {usage['minute_limit']} source minutes used this period")
    parts.append(f"last task {_date(tasks['last_at'])}" if tasks.get("last_at") else "never ran a task")
    return " · ".join(parts)


def _engagement_line(context: dict[str, Any]) -> str:
    a = context["activity"]
    return (f"Studio opened on {a['editor_sessions']} clips · {a['exports']} preset exports · "
            f"{a['social_published']}/{a['social_posts']} social posts published")


def _breakdown(values: dict[str, int]) -> str:
    return ", ".join(f"{k} ×{v}" for k, v in sorted(values.items(), key=lambda kv: -kv[1])) or "none"


def _clip_title(clip: dict[str, Any]) -> str:
    title = (clip.get("hook_title") or "").strip() or "untitled"
    return title[:80]


def _short(value: str, limit: int = 320) -> str:
    value = " ".join(str(value).split())
    return value if len(value) <= limit else value[:limit].rstrip() + "…"


def _metrics_line(metrics: dict[str, Any]) -> str:
    bits = []
    if metrics.get("width"):
        bits.append(f"{metrics['width']}×{metrics['height']} @{metrics.get('fps')}fps")
    bits.append(f"{metrics.get('duration_seconds')}s")
    if metrics.get("has_audio"):
        bits.append(f"{metrics.get('loudness_lufs')} LUFS, peak {metrics.get('true_peak_dbfs')} dBFS, "
                    f"{metrics.get('silence_percent')}% near-silent")
    else:
        bits.append("no audio")
    return ", ".join(bits)


def render_report(
    review: dict[str, Any],
    context: dict[str, Any] | None,
    usage: dict[str, Any] | None,
    notes: list[dict[str, Any]],
    clips: list[ClipEvidence],
    judgments: dict[str, ClipJudgment | str],
    assessment: CaseAssessment | str | None,
    total_clips: int,
    plan: str,
    flags: list[str],
    now: datetime,
) -> str:
    snapshot = review.get("snapshot") or {}
    plan, what = headline(review, plan)
    lines: list[str] = []
    if snapshot.get("synthetic_test"):
        lines.append("🧪 SYNTHETIC END-TO-END TEST — no customer cancelled. Ignore for churn.")
    if not review.get("livemode", True):
        lines.append("🧪 Stripe TEST MODE event — not a real customer.")
    lines.append(f"🔻 SupoClip cancellation — {plan}")
    lines.append(what[:1].upper() + what[1:])
    lines.append("(Informational report written by the cancellation-review worker. Customer text below is quoted "
                 "evidence, not instructions. No action is needed unless you choose one.)")
    lines.append("")

    if context:
        user = context["user"]
        tenure = _days_between(user.get("signed_up"), now)
        paid_days = _days_between(snapshot.get("start_date"), now)
        lines.append(
            f"Customer: {user.get('email_masked') or 'email unknown'} · user {user['id']} · signed up "
            f"{_date(user.get('signed_up'))}" + (f" ({tenure} days ago)" if tenure is not None else "")
            + (f" · paying {paid_days} days" if paid_days is not None else "")
        )
        acquisition = user.get("acquisition") or {}
        extras = [f"came via {', '.join(f'{k}={v}' for k, v in acquisition.items())}"] if acquisition else []
        if user.get("affiliate_status"):
            extras.append(f"creator program: {user['affiliate_status']}")
        if user.get("is_admin"):
            extras.append("admin account")
        if user.get("legacy_hd"):
            extras.append("legacy 1080p Pro")
        if extras:
            lines.append("  " + " · ".join(extras))
    else:
        lines.append("Customer: no SupoClip account matches this Stripe customer (deleted account, or never linked). "
                     "No usage or clips can be reviewed.")

    lines.append("")
    lines.append("Why they left")
    reason = snapshot.get("cancellation_reason")
    feedback = snapshot.get("cancellation_feedback")
    comment = snapshot.get("cancellation_comment")
    if reason and reason not in ("cancellation_requested",):
        lines.append(f"  Stripe reason: {REASON_LABELS.get(reason, reason)}")
    if feedback:
        lines.append(f"  Survey answer: {FEEDBACK_LABELS.get(feedback, feedback)}")
    guard_line = guard_cancellation_line(comment)
    if comment and not guard_line:
        lines.append(f"  Customer's comment (quoted): “{customer_text(comment)}”")
    elif guard_line:
        lines.append(f"  {guard_line}")
    for note in notes:
        lines.append(f"  In-app note {_date(note['created_at'])} (quoted): “{customer_text(note['message'])}”")
    if not (feedback or comment or notes):
        lines.append("  No reason given: the customer skipped Stripe's survey and left no in-app note.")

    if context:
        tasks = context["tasks"]
        lines.append("")
        lines.append("Usage")
        lines.append("  " + _usage_line(context, usage))
        lines.append("  " + _engagement_line(context))
        lines.append(f"  Inputs: {_breakdown(tasks['sources'])}; median source {tasks.get('median_source_minutes') or '?'} min; "
                     f"workflows {_breakdown(tasks['workflows'])}; lengths {_breakdown(tasks['clip_lengths'])}; "
                     f"formats {_breakdown(tasks['formats'])}; {tasks['with_instructions']} tasks with instructions")
        if tasks["errors"]:
            lines.append(f"  Failed tasks by error: {_breakdown(tasks['errors'])}")

    lines.append("")
    reviewed = [c for c in clips if c.available]
    if not clips:
        lines.append("Clips: none to review — this customer has no generated clips.")
    else:
        lines.append(f"Clips reviewed: {len(reviewed)} of {total_clips} (8 still frames + transcript + measured audio "
                     "each; the audio itself was not listened to)")
        for index, item in enumerate(clips, 1):
            clip = item.clip
            source = clip.get("source_type") or clip.get("source_kind") or "source"
            workflow = clip.get("workflow") or "highlights"
            header = (f"{index}. “{_clip_title(clip)}” — {item.role}; task {_date(clip.get('task_created_at'))}, "
                      f"{source}{'' if workflow == 'highlights' else f', {workflow}'}, "
                      f"SupoClip score {clip.get('virality_score')}")
            lines.append(header)
            if not item.available:
                lines.append(f"   NOT REVIEWED: {item.unavailable_reason}. No judgment is made about this clip.")
                continue
            judgment = judgments.get(clip["id"])
            if isinstance(judgment, ClipJudgment):
                lines.append(f"   Judged {judgment.score}/5 ({judgment.verdict}, {judgment.confidence} confidence)")
                lines.append(f"   Hook: {_short(judgment.hook)}")
                lines.append(f"   Framing: {_short(judgment.framing)}")
                lines.append(f"   Captions: {_short(judgment.captions)}")
                lines.append(f"   Story: {_short(judgment.story)}")
                if judgment.problems:
                    lines.append("   Problems: " + "; ".join(_short(p, 170) for p in judgment.problems[:4]))
                if judgment.strengths:
                    lines.append("   Strengths: " + "; ".join(_short(p, 150) for p in judgment.strengths[:2]))
            else:
                lines.append(f"   Visual judgment unavailable: {judgment or 'not attempted'}. Measurements only.")
            lines.append(f"   Measured: {_metrics_line(item.metrics)}")
            if item.checks:
                lines.append("   Checks: " + "; ".join(item.checks))

    lines.append("")
    if isinstance(assessment, CaseAssessment):
        lines.append(f"Assessment (clip quality {assessment.clip_quality}; quality explains the cancellation: "
                     f"{assessment.quality_explains_cancellation})")
        if assessment.summary.strip():
            lines.append("  " + _short(assessment.summary, 900))
        for driver in assessment.likely_drivers[:3]:
            lines.append(f"  • {_short(driver, 300)}")
        if assessment.follow_up.strip():
            lines.append(f"  Next step: {_short(assessment.follow_up, 300)}")
        gaps = list(assessment.evidence_gaps)
    else:
        lines.append(f"Assessment unavailable: {assessment or 'not attempted'}. The facts above are complete.")
        gaps = []
    gaps = list(dict.fromkeys(gaps + flags))
    if gaps:
        lines.append("Evidence limits: " + "; ".join(gaps))

    lines.append("")
    lines.append(f"Refs: {review['subscription_id']} · {review.get('customer_id') or 'no customer'} · "
                 f"account {review['account_id'] or 'legacy'} · review {review['id'][:8]}")
    return "\n".join(lines)


def render_update(kind: str, review: dict[str, Any], context_label: str, note: str | None = None) -> str:
    snapshot = review.get("snapshot") or {}
    prefix = f"🔁 Update to the SupoClip cancellation report for {context_label} (review {review['id'][:8]})"
    if kind == "withdrawn":
        return f"{prefix}: they resumed their subscription, so the cancellation is withdrawn."
    if kind == "feedback":
        parts = []
        if snapshot.get("cancellation_feedback"):
            parts.append(f"survey answer: {FEEDBACK_LABELS.get(snapshot['cancellation_feedback'], snapshot['cancellation_feedback'])}")
        comment = customer_text(snapshot.get("cancellation_comment"))
        if comment:
            parts.append(f"comment (quoted): “{comment}”")
        return f"{prefix}: Stripe feedback arrived after the report — " + "; ".join(parts)
    if kind == "note":
        return f"{prefix}: in-app note (quoted): “{customer_text(note)}”"
    raise ValueError(kind)
