# Creator agent and posting calendar

The agent home screen lets a Creator-plan user (Pro accounts get a few free messages) say what they want ("clip this and post the best three this
week", "tighten clip 2", "how are my clips doing?") and have it done. The posting calendar shows every
scheduled, publishing, published and failed post on one month or week grid.

## Who gets it

- **Hosted, full access:** Creator (`scale`) and Team plans while paid (the plans that may schedule posts), plus
  admins. Checked on every message, not only when home loads (`agent/access.py`).
- **Pro trial:** paid Pro accounts get `AGENT_TRIAL_MESSAGES` (default 5) messages they start, counted over the
  account's lifetime from the durable `agent_turn_events` ledger (deleting chats never resets it). Follow-ups
  never count, and approving an action on the last free turn still works. After that, `reason` is `trial_used`
  and sending answers 402 `AGENT_TRIAL_USED`.
- **Free, or Pro after the trial:** the agent stays visible but locked (`reason: plan`, 402 `AGENT_PLAN`). Every
  place that would start a turn opens the upgrade dialog with the `agent` reason instead (Creator preselected,
  the onboarding film as its picture). Old chats stay readable; the composer is disabled.
- **Self-hosted:** everyone (monetization off).
- **Feature flag `creator_agent`:** seeded on for web, off for iOS. Off shows everyone the create form.
- **Needs** `ANTHROPIC_API_KEY` (env or the admin runtime settings). `AGENT_MODEL` (default `claude-sonnet-5-5`).
- **Rate and concurrency limits (`agent/rate_limit.py`):**
  - `AGENT_DAILY_TURN_LIMIT` (default 150 user-started turns per rolling 24 hours, 429 `AGENT_DAILY_LIMIT`).
  - `AGENT_BURST_TURN_LIMIT` / `AGENT_BURST_WINDOW_SECONDS` (default 10 user-started turns per rolling 60 seconds, 429 `AGENT_RATE_LIMITED`).
  - `AGENT_MAX_CONCURRENT_TURNS` (default 2 active `queued`/`running` turns across all of a user's chats, 429 `AGENT_CONCURRENT_LIMIT`; follow-ups defer until a slot frees up).
  - Turn limits and Pro trial counts are enforced atomically under a per-user PostgreSQL advisory lock against `agent_turn_events`, so deleting conversations or racing requests across chats cannot bypass them.
  - Per-user Redis endpoint limits (`AGENT_REQUEST_RATE_LIMIT`, default 120 reads/60 s; `AGENT_WRITE_RATE_LIMIT`, default 30 writes/60 s) and concurrent SSE stream cap (`AGENT_MAX_SSE_STREAMS`, default 6, 429 `AGENT_TOO_MANY_STREAMS`). All 429 responses include `Retry-After`.

  - Limit 429s carry `detail: {code, message, retry_after}`. The web and iOS composers show one quiet line
    instead of a toast: the daily cap disables the composer with "Resets in ~Xh", a burst counts `retry_after`
    down with Send paused, and the concurrent cap explains itself without blocking. The draft is kept.

`GET /agent/status` returns `available`, `reason`, `access` (`full` | `trial`), `trial` (`{limit, used}`),
`plan`, `home_mode`, `offer_choice`, `daily_limit`, `turns_today` (rolling 24 hours) and `daily_retry_after`
(seconds until a turn frees up once `turns_today` reaches `daily_limit`, else `null`), so a page opened at
the cap shows it before anyone sends.

## Home screen choice

`users.home_mode` is `agent`, `manual` or `NULL`. Settings → Home screen changes it. Under each composer a
muted link opens the other one just once (`/?mode=manual`, `/?mode=agent`). The decision is `homeScreenFor`
in `frontend/src/components/home-router.tsx`.

"How do you want to work?" is asked once (`offer_choice`) to everyone with full access and to accounts
created since `AGENT_ONBOARDING_SINCE` (the launch). Existing Free and Pro accounts aren't interrupted: they
keep the create form with an "Ask the agent instead" link (a Pro trial) or the same link with a Creator tag
(opens the upgrade dialog). Without full access the choice screen reads like a plan comparison: the create
form comes first and is the suggested one ("Included in Pro", or "Pro" for free accounts), and the agent card is
tagged "Creator" with "Try N free messages" (Pro) or "See Creator", which opens the dialog without saving a choice. Someone whose
home was the agent and who lost access sees the form with a note that their chats are kept.

## How a turn runs

```
browser ── POST /agent/conversations/{id}/messages ──▶ API: append user turn, create agent_runs row
                                                         └─ asyncio task in the API process (agent/runtime.py)
                                                              Claude Sonnet 5.5, streamed (agent/loop.py)
                                                              tools call the API in-process as the user
browser ◀── SSE /agent/conversations/{id}/events ──── Redis stream agent:conversation:{id}:events
```

- **Tools are API calls.** `agent/api_client.py` calls SupoClip's own routes through `httpx.ASGITransport`,
  signed like the web app. Billing, plan limits, feature flags, team scopes and validation all apply; the agent
  can't do anything the person couldn't. Only `find_clips`, the create defaults and the memory notes read or
  write SQL directly, scoped the same way as the task list.
- **Transcript:** `agent_messages` is append-only and stores every content block as Claude returned it
  (thinking and fallback blocks included) because the next request must send them back unchanged. `display`
  holds what the page shows (the person's own words, each tool's card). Varying context (time and timezone,
  first name, remembered notes, attachments) goes into a `<context>` block at the start of each user turn so the
  system prompt and tools stay cached.
- **Requests:** adaptive thinking, effort `medium`, top-level `cache_control`, and server-side refusal fallback
  (`server-side-fallback-2026-07-01`, `fallbacks: "default"`). Text Claude writes between tool calls arrives as
  hidden progress notes, so `show_options` doesn't end the turn: the agent writes its reply after it and the
  page puts the options under the reply.
- **Approvals:** `publish_now` and `delete_project` stop the turn (`awaiting_approval`, `agent_runs.pending`)
  until the person approves or declines in the chat. Sending a new message instead declines them.
- **One turn per chat** (`create_run` refuses while one is open). A heartbeat every 15 s keeps a turn alive; a
  turn silent for 90 s (API restart) is marked `interrupted` by the poller. Stop cancels it from any process.

## Closing the tab

A turn runs on the server (an asyncio task in the API, tracked by `agent_runs` with a heartbeat), not in the
browser, so closing the tab changes nothing: tools keep running, background studio edits and follow-ups are
`agent_jobs` stepped by the poller, and everything said is saved to `agent_messages` as it happens.
Reopening a chat loads the transcript and listens to the conversation's Redis stream from where it left off.
A reply being written at that moment is kept in Redis (`agent:conversation:{id}:partial`, with the stream id it
reached) and returned as `partial` by `GET /agent/conversations/{id}`, so the chat shows it and continues live
from exactly that event. If the API process itself stops mid-turn, the run is marked interrupted within 90
seconds and the next message continues with what was saved.

## Attachments

The composer takes files from the attach button, paste (screenshots straight from the clipboard) and
drag-and-drop anywhere on the page (a full-page drop target appears). Up to six per message.

- **Videos** upload to storage first (`upload://…`) and are listed in the context block, so the agent can
  start a project from them.
- **Images** are shrunk in the browser (longest side 1,600 px) and sent as image blocks; Claude sees them.
- **PDFs** (up to 10 MB) and **text files** (`.txt`, `.md`, `.csv`, `.srt`, `.json`…, up to 500 KB) are sent
  as document blocks.

They travel inside the message (at most 18 MB inline) and are stored with it in `agent_messages`, because
the next request must send the transcript back unchanged. The chat shows only names, sizes and a small image
preview. Validation and the block shapes: `agent/attachments.py`; browser side: `lib/agent-attachments.ts`.

## Background work (`agent_jobs`)

Stepped by a poller in every API process, claimed with `FOR UPDATE SKIP LOCKED`:

- **`clip_edit`:** prepare the studio session if needed → `POST .../editor/ai` with the instruction → save the
  edited draft (revision compare-and-swap; a conflict redoes it on the newer draft) → optionally render an
  export → done. The result is a studio draft plus a download. It does **not** replace the generated clip that
  publishing uses; `trim_clip` and `restyle_project` do. AI edits count against the AI edit allowance.
- **`followup`:** waits for a project to finish, then wakes the agent in the chat with a notice
  ("when it's done, schedule the best clip tomorrow at 6pm"). Expires after 12 hours.

## Generative UI

`show_options` renders tappable UI: `chips` (quick replies), `cards` (icon, label, description, recommended;
workflow icons draw the workflow picker's glyphs), `clips` (real thumbnails), `slots` (posting times). A tap
sends the option's `reply` as the person's next message. Other tools return cards too: clip strips, project
progress, calendar agendas, posts, studio edits with live steps, performance. Shapes live in `agent/tools.py`
and `frontend/src/lib/agent.ts`.

## Agent, the character

Agent (just "Agent", no persona name) is a small dark pebble with a face (`components/agent/agent-mark.tsx`).
Its outline slowly shifts as if breathing. It blinks every few seconds and glances around when idle, leans in
and looks down while you type (`listening`), and a thought bubble with three dots pops up above its head while a
turn runs (`thinking`); then an eyebrow comes in, its mouth goes wavy, and it ponders through three poses
(one brow up, a frown, the other brow) every 1.6 s. Every change of mood squashes and settles the body and
squints the eyes into their new shape; mouths are all two-curve paths so they morph into each other. It hops with smiling eyes when a turn finishes (`happy`) and shows flat eyes on an error
(`sad`). The big Agent on the home screen and the onboarding choice follows the pointer. Sizes are in `em`, so
one font size scales the body and its bubble; reduced motion keeps it still.

## Posting calendar

`/calendar` (in the nav when social publishing is on): month and week grids, platform filter, mobile agenda,
drag a scheduled post to another day to move it, a side sheet to change its time or caption, cancel or retry.
Backed by `GET /social/posts?starts_at=&ends_at=` (at most 100 days, calendar order) and
`PATCH /social/posts/{id}` (scheduled posts only; time, title, caption, hashtags; conditional on
`status = 'scheduled'` so the dispatcher's claim wins cleanly). "Plan with the agent" starts a chat that fills
the gaps.

## Data

`agent_conversations`, `agent_messages`, `agent_runs`, `agent_turn_events`, `agent_jobs`, `agent_memories`
(`backend/src/migrations/sql/20261123_0001_creator_agent.sql`,
`backend/src/migrations/sql/20261128_0001_agent_turn_events.sql`). Conversations are private to the person who
started them, also in a team workspace, and listed per workspace. Memories are short notes the agent keeps
only when asked; Settings shows and deletes them.
