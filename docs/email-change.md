# Changing the account email

Users change their sign-in address in **Settings → Account** (`/settings#account`). The change needs two
codes: one sent to the current address, then one sent to the new address. Nothing changes until both are
entered.

## Flow

| Step | Request (`POST /api/account/email-change`) | What happens |
|------|-------------------------------|--------------|
| 1 | `{"action":"start","password":"..."}` | Re-authenticates (password, or a session younger than 24 h for accounts without one) and emails a code to the current address. |
| 1 | `{"action":"verify_current","code":"123456"}` | Proves the current address. An address that was never verified becomes verified here. |
| 2 | `{"action":"new_email","email":"..."}` | Rejects an address another account uses (sign-up already reveals this; each lookup counts as a send) or one Better Auth's sign-in wouldn't accept, and emails a code to the new address. |
| 2 | `{"action":"verify_new","code":"123456"}` | Proves the new address, then finishes (below). |
| | `{"action":"resend"}` | New code for the current step, after a 60 s cooldown. |
| | `{"action":"finish"}` | Retries finishing after a billing failure, without new codes. One finish runs at a time (`in_progress` otherwise). |

`GET` returns the current state (so the dialog resumes after the user checks their mail), `DELETE` cancels.
Errors come back as `{"error": "<code>"}` (`wrong_code` adds `attemptsLeft`, limits add `retryAfterSeconds` and
`Retry-After`). The Settings dialog shows a translated message for each code.

## Finishing

1. Every Stripe customer of the user gets the new email: the one on the user row (on its account, or the legacy
   source account) and, during a Stripe account migration, both the source and destination customers. If one
   fails, the ones already changed get back the address Stripe had before, the account email stays as it was,
   and the dialog offers **Try again**.
2. One SQL statement switches `users.email` (verified), deletes the user's other sessions, removes pending
   password reset links and adds a row to `email_change_log`. If it fails (for example, another account took
   the address a moment earlier), the Stripe change is undone.

A finish first claims the request (`finish_claimed_at`, 5-minute lease). Without it, a second "Try again" could
fail on the already-switched account and undo the first one's Stripe change.
3. The old address gets a notice with the new address and a support reply-to. A failed notice is logged and
   does not undo the change.

Other copies of the email follow on their own: the backend reads `users.email` when it sends, PostHog's person
property is updated on the next session load, and the frontend reads the session from the database (no cookie
cache), so the current tab sees the new address immediately.

API keys and MCP connections keep working: they are bound to the account, not the address, and changing the
email needed the password and both mailboxes.

## Safety

- Codes are 6 digits, stored only as an HMAC (`BETTER_AUTH_SECRET`) bound to the user, step and address, valid
  10 minutes, used once, and dead after 5 wrong guesses. A whole request expires after 30 minutes.
- A request belongs to the session that started it. Starting again from another session replaces it.
- Limits per user and per client IP (`X-Real-IP`, else the last `X-Forwarded-For` hop, stored as an HMAC):
  5 sends / 15 sends, 20 / 60 code guesses and 5 / 20 wrong passwords per hour. Codes to one new address are
  also limited to 3 an hour whichever accounts ask (HMAC of the address), so the flow can't be used to flood an
  inbox. An attempt is recorded before it is counted, so parallel requests can't slip past a limit; a correct
  password gives its slot back. Rows in `email_change_events` older than a day are pruned.
- Code emails keep the code out of the subject (`email_deliveries` stores subjects), and the one to the new
  address leaves out the account name, which whoever typed the address chose.
- Only session cookies can use the route (not API keys), and it refuses cross-origin and non-JSON writes.
- The backend's `POST /account/email-change-email` only mails what an open request expects: the current-address
  code to the address on file, the new-address code to the request's `new_email`, and the notice only for an
  unsent `email_change_log` row from the last hour whose new address the account now holds (once per change).

## Why not Better Auth's email OTP plugin

Better Auth 1.7 has `emailOTP({ changeEmail: { enabled, verifyCurrentEmail } })`. It wasn't used because adding
the plugin also turns on OTP sign-in and OTP password reset endpoints; its current-address code is the public
`send-verification-otp` code; its default hashing is plain SHA-256 of six digits; its limits are per IP only;
and it has no step between proving the new address and switching it, which the Stripe update and its rollback
need. The flow above keeps its design where it fits (atomic consume, attempt counter).

## iOS

The iOS app (source outside this repo) can link its account screen to `https://www.supoclip.com/settings#account`.

## Tables

`email_change_requests` (one open request per user), `email_change_events` (rate limit log) and
`email_change_log` (every completed change: old and new address, session, HMAC'd IP, when the notice went out;
support's record when someone replies "this wasn't me"). All are created by the Prisma migration
`20261119120000_email_change_requests` and the backend migration `20261119_0001_email_change_requests.sql`
(same idempotent DDL) and cascade away with the user.
