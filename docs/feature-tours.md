# Feature tours

Short cards on the real page. Two kinds:

- **Feature tours** introduce a new feature to people who already use SupoClip.
- **Onboarding** (`audience: "new"`) welcomes new accounts. The `welcome` tour opens on a rendered announcement (`frontend/public/tours/welcome.*`), shows how to make a first clip on the create screen, then goes to each page in turn (Library, Social and Analytics when they're on, Agents & API, Settings) and ends back on the create screen at the link field. Its `releasedAt` is the day it shipped, and only accounts created on or after it see it. It comes first in `FEATURE_TOURS` and its intro badge says "Welcome" instead of "New".

## Adding a tour

1. Add an entry to `FEATURE_TOURS` in `frontend/src/lib/feature-tours.ts`. Order matters: the first due tour wins.
2. Put `data-tour="<target>"` on what each step points at. The first visible match wins, so one target can sit on both the sidebar link and the mobile tab (`nav-<labelKey>`, already on every nav item). Steps beside the sidebar use `side: "right"`; on phones they open above the tab bar instead.
3. Add the title and body to `frontend/messages/<locale>/tours.json` in every language.
4. The app shell mounts `<FeatureTourHost>` on every signed-in page. A tour only starts on the create screen; a step with `path` is on that page, and the host goes there first (see "Across pages").
5. For a big feature, give the opening announcement `media`: a seamless loop rendered in code with Blender (scripts and spare renders in `assets/blender/announcement/`, see its README), encoded to WebM, MP4 and a WebP poster at 1600×800 in `frontend/public/tours/`. To compare candidates, set `media.variants` to a `variants.json` beside them: previews then show a picker. Remove it once one is chosen.
6. Check it with `?tour=<id>`, which replays it for anyone.

## Rules

- **Explain the feature, don't just point at it.** Say what each thing makes and when you'd use it. A big feature opens with an `intro` step that offers "Show me" or "Not now".
- **Eight steps at most** for a feature, twelve for onboarding, as few as it allows. A test enforces the limits.
- **One idea per step**, in a sentence or two.
- **Work on the real page.** A step can set `workflow` to select it while it talks about it, and everything is put back at the end. The page stays usable behind the spotlight.
- **Feature tours are for existing users only.** `releasedAt` is the day the feature reached users; newer accounts never see the tour. For a gated feature, set it to the day it opens. New accounts learn the page from the `welcome` tour, which skips steps whose target isn't there (no workflow picker while workflows are gated, no Social link while publishing is off, no Agents link in the mobile tab bar).

## Across pages

A step with `path` is on that page. When the tour reaches it, the host saves where it is in session storage and goes there; the next page's host carries on (or the same host, inside one layout like `/settings/*`). A page gets four seconds to show the step's target, then the step is passed over in the direction the user was going. `requires` names a target that must be in the document before going at all, so pages an account doesn't have are skipped without a visit: `nav-social`, `nav-analytics`, `usage` (the plan card). If the user goes to another page themselves, the tour ends as dismissed. Back returns to the page before.

Each account and tour gets a row in `user_feature_tours` when it is shown, with `completed_at` or `dismissed_at` and `last_step` once it ends. That answers whether a tour helps, and deleting a row shows that tour to that person again.

The host handles the rest: once per account, one tour a day at most, never while the user is working, and steps whose target is missing or disabled are skipped. A tour with nothing on the page waits until its feature is open to that account.

Use `What's new` for news that has nothing to point at.
