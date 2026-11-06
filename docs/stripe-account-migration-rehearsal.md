# Stripe account routing and migration rehearsal

Account-aware runtime wiring is implemented locally behind `STRIPE_ACCOUNT_MIGRATION_ENABLED=true`. That flag has **not** been enabled in production. Nothing was pushed or deployed, and no live customer copy, billing or customer-email change was made.

## Runtime behavior

Request-scoped SDKs verify their account identity before use. Authenticated checkout, portal, cancellation, resume and payment recovery select the user's stored account. New checkout stays on the configured source unless `STRIPE_CHECKOUT_ACCOUNT_ID` is deliberately changed later. Missing configuration/context fails closed. Matching email never merges customers in the shared US account.

The database stores account ownership and a revisioned migration ledger. Entitlement updates check expected ownership transactionally. Activation requires a paid invoice, the original source paid-through time, and fresh proof that the source cannot renew. Current-account events use the existing billing policy. Old source events cannot overwrite a migrated subscription. Cancellation intent is durable across API outages; the replacement must be stopped and read back before a staging hold is released. Failed payment/SCA permits payment-method recovery without activating or creating another checkout. Refund/dispute events hold the operation and stop a staged replacement for review. Migration-owned events bypass ordinary notifications.

Source root webhook and explicit account endpoints use separate signing secrets, with account/mode checks before database writes. Event receipts include account + mode + event ID, a retryable lease and fenced completion. Existing successful live source receipts are recognized during rollout. Alternate entitlement grants and already-paid access are preserved. Account deletion selects the correct account and holds while a replacement could still bill.

## Future release configuration

Keep `STRIPE_SECRET_KEY` and `STRIPE_WEBHOOK_SECRET` for the Swiss source. Configure `STRIPE_SOURCE_ACCOUNT_ID`, `STRIPE_DESTINATION_ACCOUNT_ID`, `STRIPE_DESTINATION_SECRET_KEY` and `STRIPE_DESTINATION_WEBHOOK_SECRET` through the secrets system. The approved destination is `acct_1TcXIA6djMaoJqpX`. Customers, catalog aliases, signing secrets and portal configurations remain app-specific even though the US account is shared. Do not globally replace the source key.

SupoClip source: `acct_1T70qFPB539K703a`. The SDK API version is `2026-02-25.clover`; future webhook delivery must explicitly use the reviewed version.

The source price variables keep their names; destination equivalents use `STRIPE_DESTINATION_` with `PRO_PRICE_ID`, `PRO_ANNUAL_PRICE_ID`, `SCALE_PRICE_ID`, `SCALE_ANNUAL_PRICE_ID`, `PRO_LEGACY_PRICE_IDS` and `SCALE_LEGACY_PRICE_IDS`. Preserve the destination counterpart of legacy HD price `price_1T70xUPB539K703aa7eVBiuG`; do not collapse the two $9.99 contracts. Aliases must come from verified Stripe prices.

US portals require `STRIPE_DESTINATION_CUSTOMER_PORTAL_CONFIGURATION_ID`, separate from Shiori. Creator checkout requires `STRIPE_DESTINATION_CREATOR_COUPON_ID` and JSON `STRIPE_DESTINATION_PROMOTION_CODE_MAP` mapping source promotion IDs to verified destination equivalents. Missing mappings hold checkout instead of dropping a discount. Existing affiliate administration remains on the source account.

Root source webhook is `/api/billing/webhook`; the explicit endpoint is `/api/billing/webhook/[accountId]`. `/api/billing/cancel` allows authenticated cancellation during staging without also running the legacy cancellation. Settings exposes this action when a transfer holds the portal. Current-account paid upgrades keep the prior tier until payment succeeds. HD and App Store/affiliate grants remain intact; RevenueCat stays independent.

## Verification

Signed SDK webhooks run against a disposable application database: real Postgres transactions for SupoClip and real in-memory Convex transactions for Shiori. Stripe transport resources are synthetic and unexpected network requests fail. Tests cover activation, unsafe source renewal, failed payment/SCA, old events, wrong account/signature, duplicate delivery, stale revisions/leases, cancellation failure and retry, refund holds, and alternate grants. SDK tests cover credential identity and concurrent request isolation. Existing billing and account-deletion regressions also run. Production TypeScript and scoped ESLint pass. Evidence is in the private local migration review pack outside the repositories.

Use pnpm in `frontend`. `vitest.billing-migration.config.ts` requires the dedicated localhost test database. The SQL upgrade was applied over the previous schema and `prisma migrate diff --exit-code` reports no difference. Existing checkout/portal/resume/webhook/subscription-sync, cancellation/settings, deletion and translation tests pass.

## Still separate from engineering preparation

Before live rollout, review destination prices/discounts/tax/wallet/portal settings, deploy the additive schema and gated code, configure exact-version webhook delivery, refresh source contracts, and inspect the actual returned customer/payment-method mappings after a separately authorized copy. Nothing here authorizes a live copy or cutover.

Keep the account-aware integration enabled once account bindings or ledgers are in use. Turning its deployment flag off does not roll back Stripe or database ownership; rollback requires verified stop/resume proofs and a reviewed data transition.

No automated bulk live cutover executor is introduced. Populate the ledger only from verified account-qualified records. Freeze billing changes while arming an operation; re-read the contract immediately before stopping renewal or staging a replacement. Verify source stop before a replacement may charge; retain the original paid-through time. Reconcile timeouts by read-back rather than creating another replacement. Operator proof flags cannot replace those reads.

Keep ambiguous users, unsupported wallets, complimentary/retention contracts and already-ending subscriptions in their reviewed hold cohorts. Refund/dispute review and rollback require fresh Stripe read-back and explicit proofs. Preserve source invoice records: a destination portal does not merge historic invoices from another account.
