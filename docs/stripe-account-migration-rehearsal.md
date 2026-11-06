# Stripe account migration preparation

This branch adds a pure billing migration policy and tests. It is **not imported by production routes**, does not change Prisma or the backend, and has no Stripe write or email capability. Do not deploy it as a completed account migration.

The source is `acct_1T70qFPB539K703a`; the approved destination is `acct_1TcXIA6djMaoJqpX`. Existing subscribers must keep their prices, paid-through dates, cancellation intent, free/affiliate/App Store access, and grandfathered HD status.

`frontend/src/lib/billing-migration.ts` validates account-qualified bindings and transition proofs. It separates test/live events, routes portals to the current owner, holds resume/upgrade during transition, limits prepaid access to the original paid-through date, and requires destination-stop/source-resume proof for rollback. It does not execute the transition.

## Integration gate before cutover

1. Persist an authenticated migration ledger keyed by app user and operation. One already-ending source subscription does not match both IDs in the app's current billing record; let it end rather than recreating it or matching by email.
2. Configure separate source/destination Stripe clients through the existing secrets mechanism. Keep source routing for legacy accounts; a global `STRIPE_SECRET_KEY` replacement is insufficient.
3. Wire account-aware routing into checkout, portal, resume, subscription sync and past-due recovery. Block a second checkout while source renewal or a replacement schedule remains live. Keep RevenueCat and affiliate grants outside the migration.
4. Verify each account's webhook with its own secret/API version and scope dedupe by account/mode. Before `upsertSubscriptionState`, `syncSubscriptionUpdate`, invoice handling or `handleSubscriptionDeleted`, validate the current account/customer/subscription owner. Use an expected-owner database update so concurrent events cannot clobber a replacement.
5. Suppress migration-owned `sendCancellationScheduledEmail`/`unsubscribed` notifications. Carry a genuine user cancellation to the staged destination. Do not revive the 36 already-ending subscriptions or the two past-due subscriptions.
6. Create separate destination price definitions for the three prices in use. The two $9.99 prices must not be collapsed: `price_1T70xUPB539K703aa7eVBiuG` carries grandfathered HD. Add its destination counterpart to `STRIPE_PRO_LEGACY_PRICE_IDS` and preserve `legacy_hd` during the transition. Keep public signup prices separate from import-only aliases.
7. Preserve the temporary retention discount's actual end date; do not restart its three-month duration. Keep that subscriber on legacy billing until the remaining-duration import is verified or the discount expires.
8. Rehearse integrated routes, signed webhooks and Prisma writes against a disposable app database, including first payment, failure/SCA, old cancellation, a real user cancellation mid-transfer, duplicate delivery, grandfathered HD, App Store/affiliate ownership, and rollback. This integrated app/database rehearsal has not yet been run.

## Verification performed

Policy and existing subscription-sync/webhook tests run with Vitest; scoped TypeScript/ESLint cover the new files. The operator pack contains GET-only source inventory, a read-only SQL billing snapshot, mapping validation and real Stripe sandbox renewals. No production frontend/backend/worker was changed or restarted. No live billing or customer emails were changed.

The installed Stripe SDK defaults to API `2026-02-25.clover`; the exact source price/Billing-mode probes use this version. The US account inspector default is `2026-04-22.dahlia`, so future webhook versions require explicit configuration and testing.
