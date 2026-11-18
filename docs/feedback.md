# Feedback inbox and webhooks

Every message users send from the in-app feedback button (bug, feature, general, sales) and the
"Why are you cancelling?" dialog (cancellation) is stored in PostgreSQL, triaged by admins at
`/admin/feedback`, and posted to webhooks that admins manage at `/admin/feedback/webhooks`.
Code: `backend/src/feedback/`, `backend/src/api/routes/feedback.py` and `admin_feedback.py`.

## Submitting

`POST /feedback` (web via the `/api/feedback` proxy, the iOS app, or signed backend headers) is the
only way a submission is created. Body:

| Field | Notes |
|---|---|
| `category` | `bug`, `feature`, `general`, `sales` or `cancellation` (anything else is 422) |
| `message` | 1–2000 characters |
| `page_url` | optional, the path the user was on (the web app sends `location.pathname`) |
| `locale` | optional, the app language |
| `app_version` | optional; also read from the `x-supoclip-app-version` header |

The backend also records the user id, an email snapshot, the platform (`x-supoclip-client: ios` →
`ios`, else `web`) and the user agent. A user can send 10 messages per 10 minutes (429 after that).

The submission, its `cancellation_notes` row (category `cancellation`, read by
[cancellation reviews](cancellation-reviews.md)) and one outbox delivery per matching webhook are
written in one transaction. Nothing about webhooks can fail or slow the request: it answers once
the row is committed, even when no webhook exists.

Deleting an account keeps its messages: `user_id` is set to NULL by the foreign key and
a PostgreSQL trigger clears the email snapshot in that same transaction, including direct SQL deletions.
Submissions lock their user row, so deletion cannot leave a concurrently inserted snapshot behind.
A signed request for a deleted user returns 401; it cannot submit as an anonymous user.
Messages and notes are retained as written and may themselves contain personal information.

## Tables

Migration `20261117_0001_feedback_inbox.sql`:

- `feedback_submissions`: the message, sender, client metadata and triage state (`open`,
  `in_progress`, `resolved` with `resolved_at`/`resolved_by`).
- `feedback_notes`: admin notes on a submission.
- `feedback_webhooks`: destinations. `url_encrypted` and `secret_encrypted` use the
  `APP_SETTINGS_ENCRYPTION_KEY` AES-GCM key (falls back to `BACKEND_AUTH_SECRET`); `url_hint`
  (host and last four characters) is all the API returns.
- `feedback_webhook_deliveries`: the outbox, one row per (submission, webhook).
- `feedback_webhook_attempts`: every attempt, for the delivery log.
- `feedback_webhook_env_imports`: which environment webhooks were already imported.

The migration copies existing `cancellation_notes` into `feedback_submissions` (same ids, status
`resolved`, no deliveries: they already reached Discord).

## Delivery

1. The API tries a new submission's deliveries right after responding.
2. The cron worker's `deliver_feedback_webhooks` (every minute, `supoclip_cron` queue) sends
   whatever is still due.

A sender claims one delivery immediately before sending, with a two-minute lease
(`FOR UPDATE SKIP LOCKED`). A unique lease token fences late completion after another sender
reclaims it or an admin cancels/retries it. DNS plus HTTP has a 20-second total deadline; unused
response bodies are never buffered. Reads commit before external I/O, releasing the database
connection. The API runs at most four immediate delivery tasks per process; cron handles overflow.
Unexpected sender errors consume the same retry budget. Expired final attempts and interrupted
one-shot tests become failed on the next cron pass. Outcomes:

| Answer | Result |
|---|---|
| 2xx | `delivered` |
| 5xx, 408, 409, 425, 429, timeout, network error | retried after 30 s, 2 min, 10 min, 30 min, 2 h, 6 h, 12 h (or `Retry-After`, up to 1 h if longer); `failed` after 8 attempts |
| other 4xx, 3xx (redirects are not followed) | `failed` at once |

Admins can retry a `failed` or `cancelled` delivery from the submission or the webhook's delivery
log; it gets a fresh set of attempts. Disabling a webhook cancels its pending deliveries, so turning
it back on never sends a backlog. Fan-out locks destinations until commit so disabling cannot
miss an uncommitted delivery. A request already in flight may still reach the receiver; disabling
cannot retract external side effects. A test send is one attempt, logged with `is_test`.

Delivery is at least once: a send that times out after the receiver got it is sent again with the
same delivery id. Deduplicate on it. Lease fencing protects database outcomes, not external
exactly-once delivery (for example after a process pause or a lost acknowledgement).

### URL safety (SSRF)

URLs are checked when saved and again before every send:

- `https` only, no user name or password, at most 2048 characters
- no `localhost`, single-label names or `.local`/`.internal`/`.lan`/… names
- every address the host resolves to must be public: loopback, private (RFC 1918), link-local
  (including `169.254.169.254`), CGNAT, multicast, reserved and IPv4-mapped/6to4/Teredo forms of
  those are refused
- Discord webhooks must be `https://discord.com/api/webhooks/…` (or `discordapp.com`, `ptb.`, `canary.`)

The sender connects to the address it checked (no second DNS lookup, so rebinding can't swap it)
with the real host in `Host` and TLS SNI; the certificate is still verified against the host name.
Proxy environment variables are ignored. Errors stored and logged name the failure (`HTTP 404`,
`timed out`, `blocked: url_private_address`), never the URL.

## Webhook types

### Discord

An embed like the one before the inbox: the category label as title (linking to the submission in
`/admin/feedback`), the message, the category color, and Category, User ID, Email, Platform and
Page fields. `allowed_mentions` is empty, so `@everyone` in a message pings nobody.

### Generic JSON

`POST` with `Content-Type: application/json` and these headers:

| Header | Value |
|---|---|
| `X-SupoClip-Event` | `feedback.created` (or `feedback.test` from the admin's test button) |
| `X-SupoClip-Delivery` | delivery id, the same on every retry |
| `X-SupoClip-Timestamp` | Unix seconds when this attempt was signed |
| `X-SupoClip-Signature` | `t=<timestamp>,v1=<hex HMAC-SHA256 of "<timestamp>." + raw body>` |
| `User-Agent` | `SupoClip-Webhooks/1` |

Payload (version 1):

```json
{
  "type": "feedback.created",
  "version": 1,
  "delivery_id": "6f0c…",
  "sent_at": "2026-11-17T09:30:02Z",
  "submission": {
    "id": "0b1e…",
    "category": "bug",
    "category_label": "Bug Report",
    "message": "Captions drift after 10 minutes",
    "status": "open",
    "created_at": "2026-11-17T09:30:00Z",
    "page_url": "/tasks/abc",
    "platform": "web",
    "app_version": null,
    "locale": "en",
    "user": { "id": "user-id", "email": "user@example.com" },
    "admin_url": "https://www.supoclip.com/admin/feedback/0b1e…"
  }
}
```

`user` is `null` for anonymous senders. New fields may be added within version 1.

Verifying (Python):

```python
import hashlib, hmac, time

def verify(secret: str, raw_body: bytes, header: str, tolerance: int = 300) -> bool:
    parts = dict(item.split("=", 1) for item in header.split(","))
    timestamp = int(parts["t"])
    if abs(time.time() - timestamp) > tolerance:
        return False
    expected = hmac.new(secret.encode(), f"{timestamp}.".encode() + raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, parts["v1"])
```

The secret (`whsec_…`) is shown once, when the webhook is created or its secret is rotated
("New secret"). Rotating takes effect when the next attempt loads its destination. An already in-flight attempt
may still use the old signing secret; receivers should allow both secrets during a short transition.
Verify the signed timestamp with a bounded tolerance and durably deduplicate delivery ids before
applying side effects. Each retry is newly timestamped/signed; its submission reflects current data.

### Hermes assistant

The operator chat's Hermes gateway route, configured on the server rather than by an admin. When
both `HERMES_FEEDBACK_WEBHOOK_URL` and `HERMES_FEEDBACK_WEBHOOK_SECRET` are set, each API start
keeps one `hermes` row ("Hermes assistant") that every submission is queued for. If either is
unset, the API removes the row and its delivery log at start, so nothing is queued for it.
Admins see it in `/admin/feedback/webhooks` as "Configured on the server", with no URL: they can
switch it off (kept across restarts), send a test and read its log, but not edit or delete it.

The row stores neither value. Every sender (the API and the cron worker) reads them from its own
environment, so the cron worker needs both variables too; a sender without them leaves the
delivery for a retry ("not configured on this server"). The row also stores no categories:
submissions are queued for it by kind. Both keep an older release that runs alongside during a
release switch from queueing for it, sending to it, or redirecting it with an admin edit.

The URL is the operator's, so it skips the public-https rule above (it may name the gateway on an
internal address); it must be `http` or `https`, and redirects and proxy variables are still ignored.
Admin-entered URLs are checked exactly as before.

Each request is signed with Hermes' generic V2 scheme (shared with
[cancellation reviews](cancellation-reviews.md), `backend/src/hermes_webhook.py`):

| Header | Value |
|---|---|
| `X-Webhook-Timestamp` | Unix seconds when this attempt was signed |
| `X-Webhook-Signature-V2` | hex HMAC-SHA256 of `"<timestamp>." + raw body` with the route secret |
| `X-Request-ID` | delivery id, the same on every retry (the gateway deduplicates on it) |

```json
{
  "type": "supoclip.feedback",
  "version": 1,
  "delivery_id": "6f0c…",
  "submission": {
    "id": "0b1e…", "category": "bug", "message": "Captions drift after 10 minutes",
    "user_id": "user-id", "email": "user@example.com", "page_url": "/tasks/abc",
    "platform": "web", "app_version": null, "created_at": "2026-11-17T09:30:00Z"
  }
}
```

A 2xx counts as delivered only when its JSON `status` shows the route filed it (`accepted`,
`duplicate`, …). `ignored` (the route's script refused it), `error` or no status is retried like a
5xx. The test button sends category `test` with the message "Test from SupoClip admin".

## Admin API

All routes require an admin (`require_admin_user`); the web proxies at `/api/admin/feedback/*` and
`/api/admin/feedback-webhooks/*` also check the session and refuse cross-origin writes.

| Route | |
|---|---|
| `GET /admin/feedback?category&status&q&from&to&page` | list (50 per page) and counts per status; `q` matches email, name, user id or submission id |
| `GET /admin/feedback/{id}` | submission, notes, deliveries, sender summary |
| `PATCH /admin/feedback/{id}` | `{"status": "open" \| "in_progress" \| "resolved"}` |
| `POST /admin/feedback/{id}/notes` | `{"body": "…"}` |
| `POST /admin/feedback/deliveries/{id}/retry` | retry a failed or cancelled delivery |
| `GET /admin/feedback-webhooks` | webhooks (hints only) and the environment import status |
| `POST /admin/feedback-webhooks` | `{name, kind: "discord" \| "json", url, categories, enabled}`; returns the secret once for `json` |
| `PATCH /admin/feedback-webhooks/{id}` | any of `name`, `url` (empty keeps it), `categories`, `enabled`; only `enabled` for `hermes` (else 422 `webhook_managed`) |
| `POST /admin/feedback-webhooks/{id}/test` | send a sample now |
| `POST /admin/feedback-webhooks/{id}/rotate-secret` | new signing secret (`json` only) |
| `GET /admin/feedback-webhooks/{id}/deliveries` | last 50 deliveries with their attempts |
| `DELETE /admin/feedback-webhooks/{id}` | delete it and its delivery log (409 for `hermes`) |

## Rollout from the Discord environment variables

Before the inbox, `DISCORD_FEEDBACK_WEBHOOK_URL` received every category but `sales`, and
`DISCORD_SALES_WEBHOOK_URL` received `sales`. On the first API start after the migration, each
variable that is set becomes a webhook row ("Feedback (Discord)": bug, feature, general,
cancellation; "Sales (Discord)": sales), so Discord keeps getting the same messages without any
admin action. The import happens once per variable (`feedback_webhook_env_imports`): after that
the variables are no longer read, and editing or deleting the rows is final. A variable with an
invalid URL is skipped, logged, and tried again on the next start.

Once the rows show up in `/admin/feedback/webhooks`, the variables can be removed from the
environment. Nothing depends on the worker having them.


## Encryption keys and migration operations

Use a dedicated, high-entropy `APP_SETTINGS_ENCRYPTION_KEY`, shared by both API slots and the cron
worker. Keep it in the secret manager and backups. The existing encryption helper falls back to
`BACKEND_AUTH_SECRET` or `BETTER_AUTH_SECRET`; changing that fallback also changes the encryption
key. AES-GCM ciphertext is authenticated and randomly nonced, but there is no online encryption-key
ring or automatic re-encryption. Do not rotate the encryption key independently on one process.
Coordinate re-encryption of existing runtime settings and webhook URL/secret rows under the new
key before switching all readers, retaining the old key until verification completes. If a key was
lost, restore it from backup or re-enter destination URLs and rotate signing secrets. Decryption
failures are visible as bounded failed deliveries and can be retried after the key is repaired.
Signing-secret rotation in the UI is separate from encryption-key rotation.

The schema is additive. The original migration creates indexes only on new tables, but its foreign
keys briefly lock `users`, and the cancellation backfill runs in the migration transaction. Test a
production-size copy before rollout: the migration bounds lock waits to 5 seconds and statements
to 30 seconds, so an oversized backfill fails/rolls back instead of indefinitely blocking writes.
If that bound is exceeded, plan a separate batched backfill before deploying; do not raise it blindly.
The safety migration adds a lease token and an anonymization trigger. Both migrations are
idempotent. The migration runner now preserves dollar-quoted function bodies when splitting SQL.

For application rollback, leave these additive tables and the privacy trigger in place. Do not drop
them: that destroys submissions, outbox state, and env-import markers. The previous app can still
use `cancellation_notes`, but it does not persist new general feedback; reconcile any messages sent
during a rollback before re-enabling inbox delivery. Rollbacks do not undo webhooks already sent.

Compatibility: successful feedback requests still return HTTP 200 with `status: "ok"` plus an
additive `id`. Invalid categories retain main's existing 422 status and string `detail`; whitespace-only messages
are now rejected with 422. The old 400 "Feedback is not configured" becomes a successful stored
submission even without webhooks. Clients should treat all non-2xx responses as failure. Existing signed iOS
requests are covered by integration tests. API-key feedback was not supported before this PR and
remains unsupported. The native iOS source is not in this repository, so its error-message rendering
needs a separate client smoke test if it distinguishes 400 from 422.
