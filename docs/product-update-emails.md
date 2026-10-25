# Product-update emails

Product announcements use `ProductUpdateEmailService.send_product_update(user_id,
content)`, which re-reads the recipient’s current address and opt-out preference
before each SES send. An opted-out user returns
`{"skipped": true, "reason": "unsubscribed"}` without contacting SES. Do not send
product announcements through the transactional `SesEmailService` directly or
upload a static recipient list to SES: those paths do not check this preference.
Rendered product updates carry `category="product_update"`, and `SesEmailService`
refuses that category without the one-click unsubscribe headers.

The service fills `{{first_name}}` in the subject, HTML and plain text, and
`{{unsubscribe_url}}` in HTML and plain text (spaces inside the braces are fine),
or appends an unsubscribe footer when no link is provided. Fields are filled in a
single pass, so a name that itself contains braces is sent as written. The
selected email draft can be supplied as `EmailContent(subject, html, text)` after
filling its sender-address field. Any other template field fails before the
recipient is looked up. No campaign is sent or scheduled by this change.

Each recipient gets a signed link at
`/email/product-updates/unsubscribe?token=...`. The page works without signing in.
GET displays confirmation without changing preferences, so email scanners and
link previews cannot unsubscribe a recipient. POST stores
`users.product_updates_unsubscribed_at`, including repeated POSTs. Both the page
form and mailbox one-click POSTs use the same endpoint, without a redirect,
session, or cookie. Responses are not cached and do not load analytics or third
party resources. Email addresses are not included in the URL or page.

The message also includes `List-Unsubscribe` and `List-Unsubscribe-Post` headers
using [SES v2 custom headers](https://docs.aws.amazon.com/ses/latest/APIReference-V2/API_Message.html)
and [RFC 8058](https://www.rfc-editor.org/rfc/rfc8058.html). Verify DKIM coverage
of these two headers in a delivered test email before relying on an inbox’s
one-click unsubscribe UI. The footer and confirmation page work independently.

Apply the Prisma migration `20261025160000_product_update_preferences` before
deploying the frontend. The backend has the equivalent re-runnable migration
`20261025_0004_product_update_preferences.sql`. Both runners preserve existing
opt-outs; new and existing users have a null timestamp until they unsubscribe.
`init.sql`, Prisma and SQLAlchemy describe the same nullable column.

No new environment variables are required. Sending product emails requires an
HTTPS `NEXT_PUBLIC_APP_URL` (the backend’s `app_base_url`), configured SES
credentials, and the existing `BACKEND_AUTH_SECRET`. Links are purpose-bound and
signed with that secret, are not time-limited, and become invalid if the user’s
email address or signing secret changes. Without the secret the page answers 503
rather than treating the link as invalid. Deleted users cannot be targeted.

Account verification, password resets, security notices, subscription/billing,
and clip-completion email paths are unchanged. This preference does not cancel
a subscription or change clip-completion notification settings.

## Browser verification

The confirmation and success states were checked against a disposable local
PostgreSQL database, using a test recipient and no email delivery.

![Unsubscribe confirmation](images/product-update-unsubscribe-confirm.jpg)
![Unsubscribe success](images/product-update-unsubscribe-success.jpg)
