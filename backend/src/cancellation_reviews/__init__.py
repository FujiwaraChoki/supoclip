"""Investigate every Stripe subscription cancellation and report it to the operator chat.

Flow (docs/cancellation-reviews.md):

1. ``api/routes/stripe_cancellations.py`` verifies a Stripe webhook against the
   account's own signing secret and stores a trimmed snapshot (``intake``).
2. The cron worker folds stored events into one review per cancellation episode
   (``folding``), waits a settle window, then investigates (``evidence``,
   ``judging``), writes the report (``report``) and delivers it through a
   signed outbox (``delivery``). ``runner`` ties the steps together.
"""
