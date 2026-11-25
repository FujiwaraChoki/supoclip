# Onboarding guided by Agent, and the free preview clip

Every new account is guided by Agent (the Pebble character) to its first clip. That first clip, from
the person's own video, is free, and the plans are shown next to it.

## Who gets it

`backend/src/onboarding/variant.py`. Accounts created since the launch (`GUIDED_SINCE`,
2026-11-24 18:43 UTC) get it the first time home loads. It is stored in `users.onboarding_variant`
(`pebble`) and `onboarding_assigned` is sent once, with `client` (`web` or `ios`) set to the app it was
first opened in. Older accounts get the standard home. `GET /onboarding/status` returns `variant`:
`pebble`, or null for the standard home.

The `pebble_onboarding` feature flag turns it off per platform from /admin. Accounts then see the
standard home, and no new free previews start.

It began as an A/B test against the earlier onboarding (the "How do you want to work?" screen and the
`welcome` tour), which ended on 2026-11-25: far more new accounts got a finished clip with Agent and
the free preview, and more of them chose a plan. The few paying accounts stored as `classic` keep the
standard home.

Admins can see the guided onboarding with `?onboarding=pebble`. This is remembered for the tab and
never stored.

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
Stripe. The server keeps what matters across devices: that the account is guided, the answers, the
preview project and `users.onboarding_completed_at` (finished or skipped). The create form and the task page talk to the
docked guide through window events (`lib/pebble-events.ts`).

The guided onboarding never shows the "How do you want to work?" screen or the `welcome` tour; Agent
asks the home question itself after a Creator checkout.

## The free preview clip

`backend/src/onboarding/preview.py`. `POST /tasks/` with `"preview": true` makes one. It is allowed
when the flag is on, the install is monetized, the account has no paid plan, isn't in a team, has
confirmed its email, hasn't finished a preview (`users.preview_used_at`), has none running, and the
previews started across everyone in the last 24 hours are under `PREVIEW_DAILY_LIMIT` (default 300). The
video (a link or an upload) must be at most `PREVIEW_MAX_MINUTES` long (default 90). A preview is plain
highlights of the whole video. The free preview is used up when its clip is saved (so cancelling afterwards doesn't give another);
a preview that fails before that doesn't use it up.

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
purchase finishes the job even if nobody comes back to the page or the app was closed. It looks only at
requests whose owner now has a plan, oldest first (most requests never pay). If the render can't be
queued, the preview stays locked and the next try reuses the charge.

## iOS

The iOS app (separate repository) shows the same onboarding when `GET /onboarding/status` says
`pebble`, so someone sees the same onboarding on web and iPhone (since 1.0.11).

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
