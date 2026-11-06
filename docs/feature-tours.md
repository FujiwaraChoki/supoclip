# Feature tours

Short cards that introduce a new feature to people who already use SupoClip.

## Adding a tour

1. Add an entry to `FEATURE_TOURS` in `frontend/src/lib/feature-tours.ts`. Order matters: the first due tour wins.
2. Put `data-tour="<target>"` on what each step points at.
3. Add the title and body to `frontend/messages/<locale>/tours.json` in every language.
4. Mount `<FeatureTourHost>` if the page doesn't have it yet. Only the create screen does today.
5. For a big feature, give the opening announcement `media`: a seamless loop rendered in code with Blender (scripts and spare renders in `assets/blender/announcement/`, see its README), encoded to WebM, MP4 and a WebP poster at 1600×800 in `frontend/public/tours/`. To compare candidates, set `media.variants` to a `variants.json` beside them: previews then show a picker. Remove it once one is chosen.
6. Check it with `?tour=<id>`, which replays it for anyone.

## Rules

- **Explain the feature, don't just point at it.** Say what each thing makes and when you'd use it. A big feature opens with an `intro` step that offers "Show me" or "Not now".
- **Eight steps at most**, as few as the feature allows. A test enforces the limit.
- **One idea per step**, in a sentence or two.
- **Work on the real page.** A step can set `workflow` to select it while it talks about it, and everything is put back at the end. The page stays usable behind the spotlight.
- **Existing users only.** `releasedAt` is the day the feature reached users; newer accounts never see the tour. For a gated feature, set it to the day it opens.

Each account and tour gets a row in `user_feature_tours` when it is shown, with `completed_at` or `dismissed_at` and `last_step` once it ends. That answers whether a tour helps, and deleting a row shows that tour to that person again.

The host handles the rest: once per account, one tour a day at most, never while the user is working, and steps whose target is missing or disabled are skipped. A tour with nothing on the page waits until its feature is open to that account.

Use `What's new` for news that has nothing to point at.
