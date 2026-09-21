# Decisions

Settled choices — don't re-litigate these without a new entry explaining what
changed. Only non-obvious/debated decisions go here; skip anything a reading
of the code would make obvious anyway.

## Ollama-first, Gemini fallback for LLM features
Date: 2026-09-13
Choice: Content-policy detection and metadata generation try a local Ollama model first; Gemini (or other cloud LLM) is an explicit opt-in fallback, never the default.
Why: Keeps those features free, private, and unlimited by default; a hosted-only default would put a metered API key on the critical path for a local-first app.
Reversible? Yes — `LLM_PROVIDER_MODE` setting (`ollama`/`gemini`/`hybrid`) already supports flipping the default per deployment.

## 4-color palette locked
Date: 2026-09-13
Choice: Every core-product screen (Home, Clipping, Settings) uses exactly 4 colors (ink, paper, teal, blue) — no tints, shades, gradients, or opacity tricks beyond one sanctioned modal-scrim exception.
Why: Contrast comes from scale/weight/space instead of color, per the Swiss design language; more colors would erode that discipline screen by screen.
Reversible? With effort — DESIGN.md documents it as the constraint; loosening it means auditing every screen, not just a config flag.

## Swiss / International Typographic Style as design language
Date: 2026-09-13
Choice: All core-product screens follow Swiss/International Typographic Style (grid discipline, flush-left text, no ornament, absolute type-scale hierarchy) — see DESIGN.md.
Why: Gives the app one consistent visual identity instead of ad hoc per-page styling; chosen over a softer/rounded SaaS look deliberately.
Reversible? With effort — would mean rewriting DESIGN.md and re-theming every screen.

## Safe zones in a config file, not code
Date: 2026-09-14
Choice: Per-platform safe-zone insets live in a data table (`frontend/src/lib/safe-zones.ts`), not inline in the overlay component logic.
Why: Platform UI coverage percentages change independently of rendering logic and are the kind of value a non-engineer might need to tweak — one table to edit, not a code review of the SVG component.
Reversible? Yes — trivial to inline if the config indirection ever stops earning its keep.

## Templates folder-based, not hardcoded
Date: 2026-09-14
Choice: Ranking-tool templates are one folder per template (`backend/templates/ranking/<name>/config.json` + preview asset), loaded by scanning the directory — not a hardcoded Python dict like `caption_templates.py`.
Why: Adding/editing a ranking template becomes a folder drop, no code change or redeploy; caption templates predate this pattern and haven't been migrated (still hardcoded, intentionally — no forcing function yet).
Reversible? Yes — could hardcode later, but folder-based costs nothing extra now.

## Ranking templates differentiate via render-order + overlay dispatch, not per-template render code
Date: 2026-09-14
Choice: Countdown and Ranking List reuse the exact same `full_screen` concat pipeline as Rapid Fire; they differ only through two already-plumbed config fields — `render_order` ("descending" plays worst-to-best) and `list_overlay` (an accumulating corner list of ranks revealed so far). Template previews are served through a backend route (`GET /ranking/templates/{id}/preview`) rather than the frontend reading `preview_path` directly, since that field is an absolute filesystem path.
Why: `layout`/`render_order`/`list_overlay` already existed as template-config fields but were previously decorative (nothing in `RankingService` read them) — wiring them up was enough to ship two full templates with zero new ffmpeg filter-graph code, keeping the "additive, not a rework" promise in docs/architecture.md's Deferred note. Head-to-Head and Tier List don't fit this — they need a genuinely different filter graph (split-screen, grid) and new `ranking_inputs` data, so they stay deferred.
Reversible? Yes — a template can still add a new `layout` value later for a structurally different pipeline; `render_order`/`list_overlay` don't block that.

## Ranking transition SFX and background music mix in-graph, not as a post-render pass
Date: 2026-09-14
Choice: Both features extend the compilation's single `filter_complex` (extra `-i` inputs + `adelay`/`sidechaincompress`/`amix`) rather than calling `video_utils.py`'s existing `mix_sfx_into_clip` helper (which re-encodes the whole file per call) once per cut boundary or once for the music bed.
Why: `mix_sfx_into_clip` is the right tool for a single post-render insertion (hook-title SFX, one call); reusing it here would mean N-1 full re-encodes for N-1 transition boundaries alone, on top of the original render. No bundled default background-music track ships (`backend/music/README.md`) for the same licensing reason `backend/sfx/README.md` ships empty — verified via `find_music_path`/`find_sfx_path` both degrading silently to "no effect" rather than erroring on a missing/unset name.
Reversible? Yes — a template simply omits `transition_sfx`/`background_music` to get the old behavior back; nothing about the data model depends on either being set.

## Caching mandatory for all LLM output
Date: 2026-03-02
Choice: Transcript analysis (segment selection, hook titles) is cached in Postgres (`processing_cache`, keyed by source URL + processing mode + cache-version string); nothing regenerates on every page load or re-open.
Why: LLM calls are the slowest and most expensive step in the pipeline; without caching, re-opening a project would re-run analysis for no reason.
Reversible? With effort — cache-version bump already exists as the invalidation path; removing caching entirely would need re-auditing every call site that assumes cached results.

## One LLM call per video, not per clip
Date: 2026-09-13
Choice: Metadata (title/description/tags) is generated in a single batched LLM call covering every clip in a video, mapped back by `clip_index` — not one call per clip.
Why: N clips would mean N LLM calls (cost + latency) for output that's cheap to batch; a lighter single-clip function exists separately only for the explicit per-clip "Regenerate" button.
Reversible? With effort — would need a new per-clip prompt path and lose the batching cost savings.

## Soft-delete only, no hard-delete
Date: 2026-09-13
Choice: `DELETE /tasks/{id}` sets `deleted_at`, doesn't remove the row. A separate explicit `/purge` endpoint does the real, irreversible delete.
Why: Accidental deletes are recoverable via Trash; source videos are never touched by either path, limiting blast radius further.
Reversible? Yes — purge already exists for anyone who wants immediate hard-delete; not the default because recoverability is the point.

## Local-first, no required cloud
Date: 2026-03-02
Choice: The app runs with no login by default (`REQUIRE_AUTH=false`), resolving every request to a single implicit local user. Real multi-tenant auth is opt-in for the hosted deployment only.
Why: Core value proposition vs. OpusClip — self-hostable without standing up auth/billing infrastructure just to process a video.
Reversible? With effort — `REQUIRE_AUTH=true` already exists as the escape hatch; making cloud auth the default would be a bigger reversal of intent, not just a flag flip.

## Batch over folder watcher
Date: 2026-09-13
Choice: Multiple videos are queued via an explicit batch UI (pick videos + a preset, they process one after another) backed by Postgres state (`batch_queues`/`batch_queue_items`) — not a filesystem folder watcher.
Why: DB-backed state survives an app/backend restart and resumes automatically; a folder watcher adds filesystem-watching complexity and doesn't solve resume-after-crash on its own.
Reversible? With effort — a watcher could be added as an alternate trigger into the same queue tables, but isn't planned.

## Emoji reactions manual only
Date: 2026-09-13
Choice: Emoji reactions are placed by the user at a specific timestamp/position in the editor — never auto-suggested or AI-placed.
Why: Placement is subjective and clip-specific; no reliable signal exists yet for "where should a reaction go" that would beat user judgment.
Reversible? Yes — an AI-suggestion feature could be added on top of the same reactions data model without breaking it.

## Metadata auto-generated per video (on by default)
Date: 2026-09-14
Choice: Title/description/tags auto-generate once per video right after clip detection finishes, gated by `AUTO_GENERATE_METADATA_ENABLED` (default **on**); turning it off falls back to manual "Regenerate" only.
Why: Metadata is useful immediately without an extra click for the common case, while still letting anyone who wants zero automatic LLM calls turn it off.
Reversible? Yes — it's a runtime setting, not a hardcoded path.

## Ranking videos use full color (including gold); the 4-color palette is site-only
Date: 2026-09-14
Choice: `ranking_overlay.py::build_ranking_overlay_ass` renders the #1 rank in gold (`#FFD700`), outside the site's locked ink/paper/teal/blue palette. The palette lock (DESIGN.md) governs the SupoClip website's own UI only — never the content of a rendered video.
Why: The palette exists so the product's screens read as one coherent system; a ranking video is user-generated content going out to social platforms, where "#1 in gold" is the near-universal visual convention for a top ranking and constraining it to the site's palette would look wrong and fight the format.
Reversible? With effort — a config-level color override could be added later, but "video output is unconstrained, site chrome is locked" is the intended, durable split, not a temporary gap.

## SFX aligns to video end for a clean loop point
Date: 2026-09-14
Choice: When the Ranking tool's global transition SFX is configured (`use_global_sfx` templates), one extra SFX instance is added beyond the per-cut-boundary ones, timed so its tail lands exactly at the compilation's total duration (`start = total_duration - sfx_duration`), independent of the configurable before-the-cut offset used at actual cut boundaries.
Why: These compilations are meant to loop (social feeds, repeated plays) — ending on silence or a hard stop reads as unfinished, while a sound effect resolving exactly at the last frame reads as a clean, deliberate ending and loop point.
Reversible? Yes — it's one `if use_global_sfx` branch in `RankingService._render_compilation`; dropping the extra instance doesn't touch the boundary SFX logic.

## Blur-fill is the default framing for non-9:16 ranking clips
Date: 2026-09-14
Choice: A ranking input that isn't already 9:16 defaults to `blur_fill` (scaled-to-fit over a blurred, scaled-to-fill copy of itself) rather than `crop_fill` (center-crop), configurable per clip and tool-wide via Settings (`RANKING_DEFAULT_FRAMING`).
Why: Ranking source footage is frequently chaotic/unfocused by nature (fails, chaotic action, funny accidents) — a center-crop has a real chance of cutting the actual subject out of frame, while blur-fill always preserves the whole original frame.
Reversible? Yes — it's a per-clip enum with a tool-wide default setting, not a hardcoded pipeline step; either could be changed without a data migration.
