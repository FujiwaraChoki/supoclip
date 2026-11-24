# Onboarding guided by Agent, and the free preview clip

New accounts are split into two groups (an A/B test):

- **classic:** the "How do you want to work?" screen and the `welcome` feature tour, as before.
- **pebble:** Agent (the Pebble character) walks them to their first clip. Their first clip from
  their own video is free, and the plans are shown next to it.

Both groups are measured in PostHog: every event carries the `onboarding_variant` property
(registered in the browser by `PebbleGuide`), so conversion to paid and first clip can be compared by
group.

## The PostHog experiment

Assignment is ours, not a PostHog flag. The first time an account is put in a group, the backend sends
`onboarding_assigned` with `$feature/pebble-onboarding` set to the group (and `$set: {onboarding_variant}`).
In PostHog the experiment uses that as a **custom exposure event**, with the feature flag key
`pebble-onboarding` and variants `classic` (the baseline) and `pebble`. The flag itself is never evaluated
by our code, so its rollout settings don't matter. Metrics don't need the variant on them: PostHog counts
each person's events after their exposure.

## Who is in which group

`backend/src/onboarding/variant.py`. Accounts created since `PEBBLE_ONBOARDING_SINCE` are split by
`PEBBLE_ONBOARDING_PERCENT` (default 50) the first time home loads, using a stable hash of the user id.
The group is stored in `users.onboarding_variant` and never changes. Older accounts get neither
(`null`). Self-hosted installs (monetization off) are always `pebble`.

The `pebble_onboarding` feature flag (web on, iOS off, admins bypass) turns the Pebble group off from
/admin. Accounts already in it then see the classic onboarding, and no new free previews start.

Admins can see either onboarding with `?onboarding=pebble` or `?onboarding=classic`. This is
remembered for the tab and never stored.

## The flow

| Step | Where | What Agent does |
| --- | --- | --- |
| intro | home, full page (`pebble-intro.tsx`) | Says hello, asks what they make (`users.onboarding_answers`), shows four showcase clips, offers the free clip |
| link | create form, docked (`pebble-dock.tsx`) | Spotlights the link field. On a pasted link it calls `POST /onboarding/link` (title, length, picture, about how many clips) and spotlights Generate. A picked file is described from its length in the browser |
| making | the preview project | Shows progress in Agent's words and offers to email when the clip is ready |
| ready | the preview project | Names the best clip and its score, spotlights it, shows the plans (`plan-card.tsx`) to unlock the rest |
| paid | back from checkout | The project unlocks the rest in place. Agent says so |
| choose | anywhere | Creator: agent or form as home. Pro: done, plus an offer to try Agent |
| done | | Says goodbye. The first visit to each page then gets a one-line hint (`pebble-hints.tsx`) |

The step is kept in the browser (`pebble-flow.ts`, localStorage) so it survives reloads and the trip to
Stripe. The server keeps what matters across devices: the group, the answers, the preview project and
`users.onboarding_completed_at` (finished or skipped). The create form and the task page talk to the
docked guide through window events (`lib/pebble-events.ts`).

The Pebble group never gets the "How do you want to work?" screen or the `welcome` tour; Agent asks
the home question itself after a Creator checkout.

## The free preview clip

`backend/src/onboarding/preview.py`. `POST /tasks/` with `"preview": true` makes one. It is allowed
when the flag is on, the install is monetized, the account has no paid plan, isn't in a team, has
confirmed its email, hasn't finished a preview (`users.preview_used_at`), has none running, and the
previews started across everyone in the last 24 hours are under `PREVIEW_DAILY_LIMIT` (default 300). The
video (a link or an upload) must be at most `PREVIEW_MAX_MINUTES` long (default 90). A preview is plain
highlights of the whole video. A preview that fails doesn't use up the account's free one.

Nothing is charged. The worker analyses the whole video as usual (so the transcript and analysis are
cached), renders only the clip with the highest score, and stores the rest in `tasks.preview` as
locked moments (hook, score, length). Processing is at 720p, like Pro.

Until it is unlocked, a preview's clip can be watched, downloaded and posted. Editing, the studio and
re-rendering answer 402 `PREVIEW_LOCKED` (`_refuse_locked_preview` in `api/routes/tasks.py`).

**Unlocking:** `POST /tasks/{id}/unlock` needs a paid plan. It charges the video's minutes like a new
project and runs the same job again. The checkpointed transcript and candidates are reused, so the
clips made are the moments the preview listed, and the preview's clip keeps its place. On a 1080p plan
the preview's 720p clip is made again with the rest. Checkout started from a preview comes back to the
project (`return_to` on `/api/billing/checkout`, checked by `safeReturnPath`), and the task page
unlocks it as soon as the plan lands (`usePreviewUnlock` retries while Stripe's webhook catches up).

**Closing the tab is fine.** Everything runs on the server: the preview is an ordinary project on the
workers (the completion email says when it's ready), and the guide picks up where it was from the
project's state. Before checkout, the plan card and the Unlock button record
`preview.unlock_requested_at` (`POST /tasks/{id}/unlock` with `{"when_paid": true}`). The cron job
`unlock_paid_previews` (every minute, cron worker) unlocks those previews once the plan is active, so a
purchase finishes the job even if nobody comes back to the page or the app was closed.

## iOS

The iOS app (separate repository) runs the same test: the group is stored per account, so someone sees
the same onboarding on web and iPhone. The `pebble_onboarding` flag is off for iOS until the app version
with it is live; turn it on in /admin then.

The iOS onboarding is one screen per step (`Screens/Pebble/` in the app): Agent says one line, with one
thing to do. Hello, what do you make, the four showcase clips (bundled), paste a link (Agent reads it),
the free clip being made (it can be left; it comes back when the clip is ready), the clip with the plans
(the App Store paywall), and the rest made in place after purchase. The project screen shows the locked
moments and an Unlock button. Debug builds open it without a server with `-PebbleOnboardingDemo
[-PebbleStage makes]`.

## Showcase clips

The intro and the agent welcome show four clips SupoClip made from real podcast episodes, in four
caption styles, two with dynamic edits (`components/onboarding/showcase-examples.tsx`, files in
`frontend/public/onboarding/examples/`).

## Events

`onboarding_step`, `onboarding_makes_chosen`, `onboarding_examples_opened`, `onboarding_link_checked`,
`preview_started`, `preview_ready`, `onboarding_plans_shown`, `onboarding_checkout_clicked`,
`preview_unlocked`, `onboarding_completed` (with `skipped`) and `pebble_hint_shown`, plus the existing
`upgrade_started` and billing events. The backend's `task_created` carries `preview: true` for
previews.
