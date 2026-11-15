"""Cancellation reviews: Stripe verification, event folding, clip selection, report wording, delivery."""

import hashlib
import hmac
import json
import time
from datetime import datetime, timedelta, timezone

import pytest

from src.cancellation_reviews.delivery import interpret, signed_request
from src.cancellation_reviews.evidence import (
    ClipEvidence,
    mask_email,
    objective_checks,
    select_representative,
)
from src.cancellation_reviews.folding import OpenReview, StoredEvent, fold_event
from src.cancellation_reviews.intake import (
    SignatureError,
    parse_cancellation_event,
    parse_webhook_secrets,
    subscription_snapshot,
    verify_stripe_signature,
)
from src.cancellation_reviews.judging import CaseAssessment, ClipJudgment
from src.cancellation_reviews.report import (
    customer_text,
    plan_hint,
    render_report,
    render_update,
)

SECRET = "whsec_test_secret"
T0 = datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc)
SETTLE = timedelta(minutes=10)


def stripe_header(payload: bytes, secret: str = SECRET, timestamp: int | None = None) -> str:
    timestamp = timestamp or int(time.time())
    signature = hmac.new(secret.encode(), f"{timestamp}.".encode() + payload, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={signature}"


def subscription(**overrides):
    base = {
        "id": "sub_1", "object": "subscription", "customer": "cus_1", "status": "active",
        "cancel_at": None, "cancel_at_period_end": False, "canceled_at": None, "ended_at": None,
        "start_date": 1760000000, "created": 1760000000,
        "cancellation_details": {"reason": None, "feedback": None, "comment": None},
        "metadata": {},
        "items": {"data": [{"current_period_start": 1762000000, "current_period_end": 1764600000,
                            "price": {"id": "price_pro", "unit_amount": 999, "currency": "usd",
                                      "recurring": {"interval": "month"}}}]},
    }
    base.update(overrides)
    return base


def event(event_type="customer.subscription.updated", previous=None, created=None, **sub):
    data = {"object": subscription(**sub)}
    if previous is not None:
        data["previous_attributes"] = previous
    return json.dumps({
        "id": f"evt_{time.time_ns()}", "object": "event", "type": event_type, "livemode": True,
        "created": int((created or T0).timestamp()), "data": data,
    }).encode()


def stored(payload: bytes) -> StoredEvent:
    parsed = parse_cancellation_event(payload)
    assert parsed is not None
    return StoredEvent(parsed.event_type, parsed.created, parsed.snapshot)


# --- verification -------------------------------------------------------------------------------

def test_valid_signature_passes_and_tampering_fails():
    payload = b'{"id":"evt_1"}'
    verify_stripe_signature(payload, stripe_header(payload), [SECRET])
    with pytest.raises(SignatureError):
        verify_stripe_signature(payload + b" ", stripe_header(payload), [SECRET])
    with pytest.raises(SignatureError):
        verify_stripe_signature(payload, stripe_header(payload, "whsec_other"), [SECRET])
    with pytest.raises(SignatureError):
        verify_stripe_signature(payload, None, [SECRET])


def test_stale_signature_is_rejected_and_secret_roll_is_accepted():
    payload = b"{}"
    old = int(time.time()) - 3600
    with pytest.raises(SignatureError):
        verify_stripe_signature(payload, stripe_header(payload, timestamp=old), [SECRET])
    verify_stripe_signature(payload, stripe_header(payload, "whsec_new"), [SECRET, "whsec_new"])


def test_secrets_are_parsed_per_account_and_junk_is_ignored():
    parsed = parse_webhook_secrets("acct_A:whsec_1, acct_A:whsec_2,acct_B:whsec_3,bad:whsec_x,acct_C:nope,")
    assert parsed == {"acct_A": ["whsec_1", "whsec_2"], "acct_B": ["whsec_3"]}


# --- which events matter -------------------------------------------------------------------------

def test_unrelated_updates_are_ignored():
    assert parse_cancellation_event(event(previous={"metadata": {}})) is None
    assert parse_cancellation_event(event(previous={"status": "trialing"})) is None  # trial converted
    payload = json.dumps({"id": "evt", "object": "event", "type": "invoice.paid", "created": 1, "data": {}}).encode()
    assert parse_cancellation_event(payload) is None


def test_scheduled_cancellation_is_captured_with_survey_answer():
    parsed = parse_cancellation_event(event(
        previous={"cancel_at": None, "canceled_at": None, "cancellation_details": {"reason": None}},
        cancel_at=1764600000, canceled_at=1763200000,
        cancellation_details={"reason": "cancellation_requested", "feedback": "too_expensive", "comment": "pricey"},
    ))
    assert parsed.snapshot["cancel_at"] == 1764600000
    assert parsed.snapshot["cancellation_feedback"] == "too_expensive"
    assert parsed.snapshot["previous"]["scheduled"] is False
    assert parsed.snapshot["amount"] == 999 and parsed.snapshot["interval"] == "month"


def test_snapshot_marks_migration_and_synthetic_metadata():
    snap = subscription_snapshot(
        subscription(metadata={"billing_migration_operation": "op1", "supoclip_synthetic_test": "true"}), None)
    assert snap["migration_operation"] == "op1" and snap["synthetic_test"] is True


# --- folding -------------------------------------------------------------------------------------

def review_from(outcome, status="pending"):
    return OpenReview(id="r1", status=status, kind=outcome.create["kind"], snapshot=outcome.create["snapshot"],
                      snapshot_at=outcome.create["snapshot_at"], reported_feedback=None)


def test_portal_cancellation_and_its_later_survey_answer_are_one_review():
    first = fold_event(None, stored(event(previous={"cancel_at": None}, cancel_at=1764600000)), SETTLE)
    assert first.create["kind"] == "scheduled" and first.create["not_before"] == T0 + SETTLE
    review = review_from(first)
    second = fold_event(review, stored(event(
        previous={"cancellation_details": {"feedback": None}}, created=T0 + timedelta(seconds=4), cancel_at=1764600000,
        cancellation_details={"reason": "cancellation_requested", "feedback": "missing_features", "comment": None},
    )), SETTLE)
    assert second.create is None and not second.notices
    assert second.update["snapshot"]["cancellation_feedback"] == "missing_features"


def test_withdrawal_before_the_report_means_no_report():
    review = review_from(fold_event(None, stored(event(previous={"cancel_at": None}, cancel_at=1764600000)), SETTLE))
    outcome = fold_event(review, stored(event(previous={"cancel_at": 1764600000}, created=T0 + timedelta(minutes=3))), SETTLE)
    assert outcome.update["status"] == "withdrawn" and outcome.notices == []


def test_withdrawal_after_the_report_sends_an_update():
    review = review_from(fold_event(None, stored(event(previous={"cancel_at": None}, cancel_at=1764600000)), SETTLE),
                         status="reported")
    outcome = fold_event(review, stored(event(previous={"cancel_at": 1764600000}, created=T0 + timedelta(days=1))), SETTLE)
    assert outcome.update["status"] == "withdrawn" and outcome.notices == ["withdrawn"]


def test_period_end_deletion_closes_the_episode_without_a_new_investigation():
    review = review_from(fold_event(None, stored(event(previous={"cancel_at": None}, cancel_at=1764600000)), SETTLE),
                         status="reported")
    outcome = fold_event(review, stored(event("customer.subscription.deleted", created=T0 + timedelta(days=20),
                                              status="canceled", ended_at=1764600000)), SETTLE)
    assert outcome.create is None and outcome.notices == [] and outcome.update["ended_at"]


def test_deletion_without_a_request_is_an_immediate_review():
    outcome = fold_event(None, stored(event(
        "customer.subscription.deleted", status="canceled", canceled_at=int(T0.timestamp()),
        cancellation_details={"reason": "payment_failed", "feedback": None, "comment": None})), SETTLE)
    assert outcome.create["kind"] == "immediate"
    assert outcome.create["snapshot"]["cancellation_reason"] == "payment_failed"


def test_feedback_after_the_report_is_an_update_once():
    review = review_from(fold_event(None, stored(event(previous={"cancel_at": None}, cancel_at=1764600000)), SETTLE),
                         status="reported")
    review.reported_feedback = {"reason": "cancellation_requested", "feedback": None, "comment": None}
    late = stored(event(previous={"cancellation_details": {}}, created=T0 + timedelta(hours=1), cancel_at=1764600000,
                        cancellation_details={"reason": "cancellation_requested", "feedback": "other", "comment": "x"}))
    outcome = fold_event(review, late, SETTLE)
    assert outcome.notices == ["feedback"]
    review.reported_feedback = outcome.update["reported_feedback"]
    assert fold_event(review, late, SETTLE).notices == []


def test_an_older_event_cannot_move_a_review_backwards():
    review = review_from(fold_event(None, stored(event(previous={"cancel_at": None}, cancel_at=1764600000,
                                                        created=T0 + timedelta(minutes=5))), SETTLE))
    stale = stored(event(previous={"cancel_at": 1764600000}, created=T0))
    assert fold_event(review, stale, SETTLE).update == {}


# --- evidence ------------------------------------------------------------------------------------

def clip(clip_id, task, score, created_days_ago=0):
    return {"id": clip_id, "task_id": task, "virality_score": score, "text": "Hello there. This works.",
            "task_created_at": T0 - timedelta(days=created_days_ago)}


def test_representative_clips_cover_best_weakest_and_an_earlier_task():
    clips = [clip("a1", "A", 90), clip("a2", "A", 40), clip("b1", "B", 80, 3), clip("b2", "B", 20, 3)]
    picked = [c["id"] for _, c in select_representative(clips)]
    assert picked == ["a1", "b2", "b1"]
    assert select_representative([]) == []
    assert [c["id"] for _, c in select_representative([clip("x", "X", 50)])] == ["x"]


def test_objective_checks_compare_against_the_plan_and_settings():
    checks = objective_checks(
        {"clip_length": "short", "output_format": "vertical", "text": "and so we went"},
        {"width": 720, "height": 1280, "duration_seconds": 48.0, "has_audio": True, "loudness_lufs": -28.0,
         "true_peak_dbfs": -0.1, "silence_percent": 35.0, "black_seconds": 0, "frozen_seconds": 0},
        1080,
    )
    joined = " | ".join(checks)
    assert "below the plan's 1080px" in joined
    assert "outside the 'short' range" in joined
    assert "-28.0 LUFS" in joined and "clipping" in joined and "near-silent" in joined
    assert "opens mid-thought" in joined and "ends without sentence punctuation" in joined


def test_masked_email_hides_the_local_part():
    assert mask_email("jane.doe@example.com") == "j***@example.com"
    assert mask_email(None) is None


# --- report --------------------------------------------------------------------------------------

def review_row(**overrides):
    row = {
        "id": "11111111-2222-3333-4444-555555555555", "account_id": "acct_src", "livemode": True,
        "subscription_id": "sub_1", "customer_id": "cus_1", "kind": "scheduled", "ended_at": None,
        "requested_at": T0, "created_at": T0,
        "snapshot": {"status": "active", "cancel_at": 1764600000, "interval": "month", "amount": 999, "currency": "usd",
                     "cancellation_reason": "cancellation_requested", "cancellation_feedback": None,
                     "cancellation_comment": None, "start_date": 1760000000},
    }
    row.update(overrides)
    return row


def context():
    return {
        "user": {"id": "user-1", "email_masked": "j***@example.com", "signed_up": T0 - timedelta(days=40),
                 "plan_now": "pro", "acquisition": {}, "affiliate_status": None, "is_admin": False, "legacy_hd": False},
        "tasks": {"total": 2, "completed": 1, "failed": 1, "cancelled": 0, "last_30_days": 2, "with_instructions": 0,
                  "first_at": T0, "last_at": T0, "median_source_minutes": 12.0, "errors": {"download_error": 1},
                  "sources": {"youtube": 2}, "workflows": {"highlights": 2}, "clip_lengths": {"auto": 2},
                  "formats": {"vertical": 2}, "caption_templates": {"default": 2}},
        "activity": {"clips": 0, "mean_virality": None, "editor_sessions": 0, "exports": 0, "social_posts": 0,
                     "social_published": 0},
    }


def render(**kwargs):
    defaults = dict(review=review_row(), context=context(), usage=None, notes=[], clips=[], judgments={},
                    assessment="not attempted", total_clips=0, plan="Pro", flags=[], now=T0)
    defaults.update(kwargs)
    return render_report(**defaults)


def test_report_is_honest_about_no_clips_and_no_reason():
    text = render()
    assert "End-of-period cancellation" in text
    assert "none to review" in text
    assert "No reason given" in text
    assert "Pro monthly" in text


def test_unavailable_clips_are_never_judged():
    missing = ClipEvidence("best clip of the latest task", {"id": "c1", "hook_title": "Hook", "virality_score": 70},
                           False, "the file is no longer in storage (deleted or expired)")
    text = render(clips=[missing], total_clips=1)
    assert "NOT REVIEWED: the file is no longer in storage" in text
    assert "Judged" not in text


def test_judged_clip_and_assessment_are_rendered_with_measurements():
    item = ClipEvidence("best clip of the latest task", {"id": "c1", "hook_title": "Hook", "virality_score": 70,
                                                         "source_type": "youtube", "task_created_at": T0},
                        True, metrics={"width": 1080, "height": 1920, "fps": 30.0, "duration_seconds": 31.0,
                                       "has_audio": True, "loudness_lufs": -14.0, "true_peak_dbfs": -1.0,
                                       "silence_percent": 2.0},
                        checks=["31s fits the 'auto' range 15–60s"])
    judgment = ClipJudgment(hook="Strong question", framing="Centered", captions="Readable", story="Complete",
                            problems=[], strengths=["clear speaker"], score=4, verdict="good", confidence="high")
    assessment = CaseAssessment(clip_quality="good", quality_explains_cancellation="unlikely",
                                likely_drivers=["price — survey answer"], summary="Fine clips.", follow_up="None.",
                                evidence_gaps=[])
    text = render(clips=[item], judgments={"c1": judgment}, assessment=assessment, total_clips=5)
    assert "Clips reviewed: 1 of 5" in text and "Judged 4/5" in text
    assert "1080×1920" in text and "-14.0 LUFS" in text
    assert "quality explains the cancellation: unlikely" in text


def test_customer_text_is_redacted_capped_and_quoted():
    text = render(review=review_row(snapshot={**review_row()["snapshot"], "cancellation_feedback": "other",
                                              "cancellation_comment": "see https://evil.example/x or mail me@x.com " + "a" * 900}))
    assert "https://" not in text and "me@x.com" not in text
    assert "[link removed]" in text and "[email removed]" in text
    assert "Customer's comment (quoted)" in text
    assert len(customer_text("b" * 2000)) <= 601


def test_synthetic_and_billing_risk_cases_are_labelled():
    synthetic = render(review=review_row(snapshot={**review_row()["snapshot"], "synthetic_test": True}))
    assert synthetic.startswith("🧪 SYNTHETIC END-TO-END TEST")
    risk = render(review=review_row(kind="immediate", ended_at=T0,
                                    snapshot={**review_row()["snapshot"], "cancellation_comment": "billing_risk:evt_9"}))
    assert "billing-risk guard" in risk and "evt_9" not in risk


def test_involuntary_and_unlinked_customer():
    text = render(review=review_row(kind="immediate", ended_at=T0, snapshot={
        **review_row()["snapshot"], "cancellation_reason": "payment_failed"}), context=None)
    assert "Involuntary cancellation" in text and "no SupoClip account matches" in text


def test_updates_and_plan_names():
    assert "withdrawn" in render_update("withdrawn", review_row(), "j***@example.com")
    assert plan_hint("scale", {}) == "Creator"
    assert plan_hint("free", {"amount": 2499, "interval": "month"}) == "Creator"
    assert plan_hint("free", {"amount": 9990, "interval": "year"}) == "Pro"


# --- delivery ------------------------------------------------------------------------------------

def test_delivery_is_signed_like_the_gateway_expects():
    body, headers = signed_request("route-secret", "d1", "hello", "report", now=1700000000)
    expected = hmac.new(b"route-secret", b"1700000000." + body, hashlib.sha256).hexdigest()
    assert headers["X-Webhook-Signature-V2"] == expected
    assert headers["X-Request-ID"] == "d1"
    assert json.loads(body)["text"] == "hello"


def test_only_a_proven_handoff_counts_as_delivered():
    assert interpret(202, {"status": "accepted"}).delivered
    assert interpret(200, {"status": "duplicate"}).delivered
    assert not interpret(200, {"status": "ignored", "reason": "script"}).delivered
    assert not interpret(401, {"error": "Invalid signature"}).delivered
    assert not interpret(502, None).delivered


def test_a_baseline_is_reopened_only_by_a_fresh_schedule():
    baseline = OpenReview(id="b", status="baseline", kind="scheduled", snapshot={}, snapshot_at=None,
                          reported_feedback=None)
    survey_only = fold_event(baseline, stored(event(previous={"cancellation_details": {}}, cancel_at=1764600000)), SETTLE)
    assert "status" not in survey_only.update
    period_end = fold_event(baseline, stored(event("customer.subscription.deleted", status="canceled")), SETTLE)
    assert "status" not in period_end.update and period_end.create is None
    fresh = fold_event(baseline, stored(event(previous={"cancel_at": None}, cancel_at=1764600000)), SETTLE)
    assert fresh.update["status"] == "pending" and fresh.update["not_before"] == T0 + SETTLE


def test_empty_assessment_fields_are_not_rendered_as_blank_lines():
    assessment = CaseAssessment(clip_quality="not_assessed", quality_explains_cancellation="unknown",
                                likely_drivers=["never ran a task"], summary="", follow_up=" ", evidence_gaps=[])
    text = render(assessment=assessment)
    assert "Next step:" not in text and "\n  \n" not in text
    assert "• never ran a task" in text


async def test_an_empty_assessment_is_asked_for_again(monkeypatch):
    from src.cancellation_reviews import judging

    answers = [
        CaseAssessment(clip_quality="not_assessed", quality_explains_cancellation="unknown", likely_drivers=[],
                       summary="", follow_up="", evidence_gaps=[]),
        CaseAssessment(clip_quality="not_assessed", quality_explains_cancellation="unknown", likely_drivers=[],
                       summary="Never used it.", follow_up="Ask why.", evidence_gaps=[]),
    ]
    prompts = []

    async def parse(system, content, output_format):
        prompts.append(content[0]["text"])
        return answers.pop(0)

    monkeypatch.setattr(judging, "_parse", parse)
    result = await judging.assess_case({"x": 1})
    assert result.summary == "Never used it." and len(prompts) == 2 and "non-empty summary" in prompts[1]
