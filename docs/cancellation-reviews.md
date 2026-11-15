# Cancellation reviews

Every Stripe subscription cancellation is investigated and reported to the SupoClip operator chat
(the Hermes `supoclip` Bot Chat). A report says who left and how (end-of-period request, immediate, or
involuntary), what they said (Stripe survey answer, comment, in-app note), how they used SupoClip, and how
good the clips they got were. The clip review is based on still frames, the transcript and measured audio. Each fact
is labelled as measured, judged or unavailable. A clip that can't be read is reported as not reviewed. It is never judged.

Code: `backend/src/cancellation_reviews/`. Tests: `tests/unit/test_cancellation_review_units.py`,
`tests/integration/test_cancellation_reviews.py`.

## Flow

```
Stripe (per account) ──signed──▶ POST /webhooks/stripe/cancellations/{account_id}   (API, intake.py)
                                   stores a trimmed snapshot in stripe_cancellation_events (idempotent)
cron worker, every minute (runner.py, supoclip_cron queue, never the render queue)
  1. fold events into cancellation_reviews, one open episode per subscription (folding.py)
  2. reconcile: scheduled cancellations in `users` that no event announced
  3. investigate reviews past the settle window (evidence.py, judging.py, report.py)
  4. deliver the outbox (cancellation_report_deliveries) to the chat webhook (delivery.py)
Hermes route cancellation-reports-supoclip ──▶ inbox script ──▶ no-agent job ──▶ bot-chat:supoclip
```

The Stripe endpoint is separate from the billing webhook (`/api/billing/webhook`). It has its own signing
secret and stores events without acting on billing, so it can never slow down or fail billing. The API holds only the
signing secrets, not a Stripe API key.

## What counts as a cancellation

| Stripe event | Result |
|---|---|
| `customer.subscription.updated` that sets `cancel_at`/`cancel_at_period_end` | `scheduled` review (end-of-period: access continues) |
| a later update that only adds the portal survey answer (sent seconds later) | folded into the same review |
| `customer.subscription.deleted` with no open review | `immediate` review; `payment_failed`/`payment_disputed` are labelled involuntary |
| deletion at the end of a reported scheduled cancellation | closes the episode, no second investigation |
| schedule removed before the report | review `withdrawn`, no report |
| schedule removed, or new survey feedback, after the report | short update message |
| an in-app "Why are you cancelling?" note (`POST /feedback`, category `cancellation`) | part of the report, or an update if it comes later |
| the account migration stopping a source subscription (`stripe_account_migrations`, no customer request) | `excluded`. Reopened if the migration later records a customer cancellation |
| a subscription id with an open review | the event updates that review |

Reviews wait `CANCELLATION_REVIEW_SETTLE_SECONDS` (10 min) after their first event, so the survey answer
and migration bookkeeping have arrived. Cancellations already scheduled when this shipped got a `baseline`
row and are never reported.

## The investigation

- **Customer**: masked email, user id, tenure, acquisition, plan, this period's minute usage.
- **Usage**: tasks by status and error code, inputs (source types, lengths, formats, workflows, instructions),
  studio sessions, exports, social posts.
- **Clips**: up to 3 representative clips are reviewed: the best clip of the latest task, the weakest of the latest three tasks,
  and the best clip of an earlier task. Each is downloaded from the bucket, measured with ffprobe/ffmpeg
  (resolution against the plan's cap, length against the requested range, loudness/peak/silence, black and
  frozen frames) and sampled as 8 frames. Claude (`CANCELLATION_REVIEW_MODEL`, default `claude-sonnet-5-5`,
  Anthropic key from admin settings) judges hook, framing, captions and story from the frames and transcript.
  The audio itself is not listened to: the model provider we pay for takes images, not sound. A video+audio
  model needs a funded provider (see "Limits").
- **Assessment**: one more call weighs the stated reason, usage and clip reviews: does clip quality explain
  the cancellation, the likely drivers, one next step, and what could not be checked.

If the model is unavailable the review retries (5 min, 20 min, 1 h). The fourth attempt reports anyway with the
measurements and states that the judgment is missing. An unexpected error on the last attempt still reports the
cancellation, with what failed.

## Privacy

Reports carry ids and a masked email. They never contain full emails, names, links or storage references. Customer-written text
is redacted (links, emails, phone numbers, long tokens), capped at 600 characters and quoted as evidence. The
report says it is not an instruction. The model gets frames, transcripts and aggregate usage: no email, no
name. Events from customers who are not SupoClip's are dropped before they are stored. This matters for an
account shared with another product.

## Configuration

API (`backend`):

```bash
STRIPE_CANCELLATION_WEBHOOK_SECRETS=acct_A:whsec_...,acct_B:whsec_...   # one entry per endpoint; two during a secret roll
STRIPE_SOURCE_ACCOUNT_ID=acct_...                                        # legacy account (users without stripe_account_id)
```

Cron worker (`worker`):

```bash
CANCELLATION_REVIEWS_ENABLED=true
CANCELLATION_REVIEW_SETTLE_SECONDS=600
CANCELLATION_REVIEW_MODEL=claude-sonnet-5-5
CANCELLATION_REPORT_WEBHOOK_URL=https://<hermes host>/p/supoclip/webhooks/cancellation-reports-supoclip
CANCELLATION_REPORT_WEBHOOK_SECRET=...                                   # the Hermes route's HMAC secret
STRIPE_SOURCE_ACCOUNT_ID=acct_...
```

### Stripe endpoint (per account)

Events: `customer.subscription.updated`, `customer.subscription.deleted`. URL:
`https://api.supoclip.com/webhooks/stripe/cancellations/<account id>`. The signing secret goes into
`STRIPE_CANCELLATION_WEBHOOK_SECRETS`. Creating it needs a key with webhook write access on that account.

### Hermes (operator chat)

Scripts: `deploy/hermes/`. As the `hermes` user:

```bash
install -m 700 deploy/hermes/cancellation-report-*.py ~/.hermes/profiles/supoclip/scripts/
hermes -p supoclip cron create "every 15m" --name "SupoClip cancellation reports" --no-agent \
  --script cancellation-report-outbox.py --deliver bot-chat:supoclip
hermes webhook subscribe cancellation-reports-supoclip --route-profile supoclip --cron-job <job id> \
  --script ~/.hermes/profiles/supoclip/scripts/cancellation-report-inbox.py \
  --description "SupoClip cancellation review reports"
```

nginx exposes only `location = /p/supoclip/webhooks/cancellation-reports-supoclip` to the gateway's
loopback listener. The route checks HMAC V2 (timestamped). The inbox script accepts only well-formed
reports and files one per delivery id. The no-agent job posts them to `bot-chat:supoclip` verbatim. The
worker counts a report as delivered only when the route says it was filed (`202 accepted`) or already was
(`200 duplicate`).

## Operating it

```sql
-- what happened to recent cancellations
SELECT created_at, kind, status, origin, exclusion_reason, attempts, last_error, user_id
FROM cancellation_reviews ORDER BY created_at DESC LIMIT 20;
-- undelivered messages
SELECT id, kind, attempts, last_error, next_attempt_at FROM cancellation_report_deliveries WHERE status = 'pending';
-- events that could not be folded
SELECT id, event_type, fold_error FROM stripe_cancellation_events WHERE fold_error IS NOT NULL;
```

Re-run an investigation: `UPDATE cancellation_reviews SET status = 'pending', attempts = 0, not_before = NOW()
WHERE id = '...'` (it reports again). Turn everything off: `CANCELLATION_REVIEWS_ENABLED=false` (events are
still stored, nothing is investigated or sent). Disable the Stripe endpoint to stop intake.

## Limits

- Apple (RevenueCat) cancellations are not covered: they don't go through Stripe.
- An account needs its own Stripe endpoint. Without one, the reconciliation sweep still finds scheduled
  cancellations from `users`, but has no survey answer or comment.
- Audio is measured, not listened to. Listening needs a video+audio model on a funded provider (Gemini via a
  paid key or OpenRouter credit); the free-tier Google key must not be used for customer media.
