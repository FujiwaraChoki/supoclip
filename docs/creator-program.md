# Creator program: commission and payouts

Approved creators get a code (`MAYA`) and a link (`/?ref=maya`). Their audience gets 20% off its
first 3 months, the creator gets Pro for free while in the program, and the creator earns
commission on what that audience pays. Applications, approval and the code itself are described
in `frontend/src/server/affiliates.ts`; this page covers commission and payouts.

## Terms

The numbers live in `frontend/src/lib/creator-commission.ts`. The public page (`/affiliate`), the
creator page (`/settings/creator-program`), the application terms and the ledger all read them. The
approval email (`backend/src/services/affiliate_email_service.py`) repeats them in prose, and a
test fails if the two disagree.

| Term | Value |
|---|---|
| Commission | 30% of what the customer paid, after the creator discount, without tax |
| Duration | Invoices paid in the 12 months after the subscription's first payment (a yearly plan counts once) |
| Hold | 30 days after each payment before it can be paid out |
| Minimum payout | 25 in the payout currency; smaller balances roll over |
| Payout countries | US, UK, Canada, Switzerland and the EEA (Stripe Connect cross-border payouts; others need Stripe sales) |
| Currency | USD |

Commission is earned only by creators who live in a payout country, and only while they are
approved. The application asks where the creator lives (`affiliates.country`: a payout country
code, or `ZZ` for elsewhere). Creators elsewhere are still welcome: they get free Pro and a code
for their audience, and their creator page explains that commission isn't available there.
Creators who applied before the question existed have no country; their creator page asks once
(`POST /api/affiliates/country`, which only sets an empty country), and until then they earn
nothing. The admin panel shows each applicant's country, and the approval email only mentions
commission when the creator can earn it. A revoked creator earns nothing new and
is skipped by payouts. Purchases made with the creator's own code earn nothing, and so do house
codes (`PRODUCTHUNT`), which have no creator. App Store purchases don't go through Stripe and don't
earn commission; the public page says so.

## How a payment becomes commission

1. **Checkout** stores the code on the subscription (`subscription_data.metadata.creator_code`)
   along with the discount, so the creator is known after the 3-month discount ends.
2. **`invoice.paid`** (and `invoice.payment_succeeded`, which arrives for the same payment) reaches
   `handleAffiliateCommissionEvent` in `frontend/src/server/affiliate-commissions.ts` after the
   billing handlers. The first paid invoice of a subscription creates its `affiliate_referrals` row
   from that metadata. Subscriptions from before this release have no metadata; their creator is
   found through the invoice's creator promotion code, which is still active on every one of them
   because the program started on 9 Oct 2026.
3. Each charge that paid the invoice adds one `earned` row to `affiliate_commissions`. The base is
   the charge's balance transaction amount: what reached our Stripe balance, before Stripe fees,
   in the settlement currency. The invoice's tax is removed in proportion. A Brazilian customer
   paying in BRL therefore earns commission in the balance's currency, at the rate Stripe used.
4. **`charge.refunded`** adds a negative `refund` row for the refunded share of the commission
   (partial refunds add up). **`charge.dispute.created`** reverses whatever is left on the charge;
   **`charge.dispute.closed`** with status `won` gives it back. Inquiries (`warning_*`) are ignored.
   Negative rows are payable immediately, so a refund of commission already paid comes off the
   creator's next payout.

Every row has an idempotency key built from Stripe IDs (`earned:<charge>`, `refund:<charge>:<amount>`,
`dispute:<dispute>`), so redelivered and duplicate events never count twice. If the ledger throws,
the webhook fails and Stripe redelivers the event; the billing handlers that ran first are
idempotent.

## Payouts

Creators set up payouts on their creator page. That creates a Stripe Connect **Express** account
in the country they pick (transfers only in the US; transfers and card payments elsewhere, which
Stripe requires of a US platform there), and sends them to Stripe's hosted
onboarding. Stripe collects identity, address, tax and bank details. The page reads the account's
status from Stripe each time it loads; payouts are on once `payouts_enabled` is true and the
`transfers` capability is active. "Open Stripe dashboard" opens their Express dashboard, where they
see transfers and change bank details.

An admin pays everyone from **/admin → Creator Program → Commission payouts** once a month:

1. Payouts left `pending` by an interrupted run are finished first.
2. For each approved creator whose available balance in a currency is at least the minimum, the
   account is checked with Stripe again, then the available rows are moved into a new
   `affiliate_payouts` row in one transaction.
3. One Transfer is sent from the platform balance, with the payout ID as idempotency key and
   transfer group. Before sending, the run looks for an existing transfer in that group, so a
   payout whose response was lost is never sent twice, even after Stripe's 24-hour idempotency
   window.
4. If Stripe refuses the transfer (for example, the platform balance is too low), the payout is
   marked `failed` and its commission returns to the balance for the next run. If the outcome is
   unknown (network error, Stripe 5xx), the payout stays `pending` and the next run finishes it.

Transfers are paid from the platform account's **available balance** in USD. Keep enough there
before running payouts; a refused transfer returns the commission to the creator's balance.

## Setup

Production bills new subscribers on the US account (`STRIPE_CHECKOUT_ACCOUNT_ID`, shared with
Shiori), and older subscriptions still renew on the Swiss source account. Commission is earned on
both. Creators are paid from the US account: by default the platform is the account new checkouts
open in.

1. **Turn on Connect** on the US account (Dashboard → Connect → Get started). Choose a platform
   that pays out to others, with Express accounts, and fill in the platform profile. Without this,
   "Set up payouts" answers "Payouts can't be set up right now". Try one onboarding and one
   transfer in test mode first.
2. **Branding:** Connect → Settings → Branding (name, icon, color) is what creators see during
   onboarding and in their Express dashboard. The account is shared with Shiori, so set the name
   creators should recognize.
3. **Key permissions:** the US account is reached with the restricted key
   `STRIPE_DESTINATION_SECRET_KEY`. It needs write access to Connect accounts, account links and
   login links, and to Transfers; read access to Invoices, Invoice Payments, Payment Intents,
   Charges, Balance transactions and Promotion codes. The Swiss key needs the same read access.
   A refused read is logged and that commission is skipped (redelivery wouldn't help, and days of
   failed deliveries would get the webhook disabled), so check the first referred payment's
   commission appears on the creator's page.
4. **Webhook events:** add `charge.refunded`, `charge.dispute.created` and `charge.dispute.closed`
   to both billing endpoints: `/api/billing/webhook` (Swiss) and `/api/billing/webhook/<US account
   id>`. `invoice.paid` and `invoice.payment_succeeded` are already required. On the account
   endpoints commission runs after the migration engine, also for events it handles itself, so
   customers in the Swiss-to-US move are counted and refunded too.
5. **Verified on live Stripe (24 Nov 2026):** throwaway Express accounts with exactly these settings
   were created, given onboarding links and deleted for US, DE, GB, CA and CH. The recipient
   agreement is refused for a US platform in DE, GB, CA, AU, IN and BR, so Australia, India and
   Brazil need Stripe sales (cross-border approval) before they can be added.
6. **Platform override:** `STRIPE_CONNECT_PLATFORM_ACCOUNT_ID` pays creators from another
   configured account. Express accounts belong to the platform that created them: after changing
   it, creators set up payouts again and their earlier accounts are ignored.
7. **Database:** the migration `20261124120000_creator_commissions` (Prisma) /
   `20261124_0001_creator_commissions.sql` (backend) adds the three tables and the payout columns
   on `affiliates`. Self-hosters get them from `init.sql` and `scripts/selfhost-schema-repair.sql`.

## Currency

Commission is kept and paid in USD (`PAYOUT_CURRENCY`), from the US account's USD balance. A charge
that settled in USD counts what settled (so a Pix payment in BRL counts at Stripe's rate). A charge
that settled in another currency, like a USD price paid into the Swiss account's CHF balance,
counts the invoice's own USD amount.

## One window per customer

A customer has one 12-month window per creator, starting at their first referred payment. A
subscription moved from the Swiss account to the US account, or a new subscription after
cancelling, continues that window instead of starting another.

## Taxes

The platform is Shiori LLC, a US company. Stripe collects tax details from creators during
onboarding; check with an accountant which information returns (1099-NEC for US creators) are due
and turn on Stripe's 1099 filing in Connect if so.

## Tables

- `affiliate_referrals`: one row per referred Stripe subscription (creator, customer, code, first
  paid time).
- `affiliate_commissions`: the ledger. `amount` and `base_amount` are minor units of `currency`;
  reversal rows are negative. `payout_id` is set once the row is in a payout.
- `affiliate_payouts`: one Transfer per creator per run and currency (`pending`, `paid`, `failed`).
