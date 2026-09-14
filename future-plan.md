# SupoClip — Future Development Plan

> **Status:** Planning document. This describes work that has NOT been done yet.
> Tier 1 (bug fixes) and Tier 2 (tabs refactor) are complete — see below for what's already shipped.

---

## Context

SupoClip started as a single-purpose clipping tool. It is now on the path to becoming a **multi-tool platform for short-form content creation**.

Long-term vision:
- **Clipping** (active) — long-form video → short clips
- **Ranking/Compilation tool** (planned) — short clips → visually ranked compilation videos
- **Voiceover/Animation tool** (planned) — script → AI voiceover + stock images/animations
- Possibly more tools later (stock video editor, etc.)

The platform is **local-first**, with a **retro-future Swiss/industrial** visual language (monochrome base + teal accent + amber warning). It should feel like an operations dashboard, not a marketing site.

---

## Already Complete (for reference only)

### ✅ Tier 1 — Bug Fixes (done)
- Yellow keyword highlighting on hooks
- `/list` select-all delete
- Emoji rendering (color, not monochrome)
- Improved progress bar
- GPU acceleration toggle (config flag)

### ✅ Tier 2 — Tabs Refactor (done)
- Tab/router shell with "Clipping" and a generic "Placeholder" tab
- Clipping code moved into its own module
- Minimal shared tool interface defined (`id`, `name`, `icon`, `mount()`, optional `cleanup()`)
- Shared infrastructure separated from tool-specific code
- Documentation for adding a new tool

---

## Remaining Work — By Tier

### Tier 3 — Automation & Intelligence
**Size:** Large — split into three sub-prompts
**Blocked by:** Nothing

#### 3a — Content Policy Detection
- Detect topics an algorithm may not favor (sex talk, drugs, etc.)
- Do NOT beep audio — asterisk sensitive words in captions instead
- User-configurable sensitivity and word list
- Swearing is fine by default

#### 3b — Titles, Descriptions, Tags Generation
- One LLM call **per video** (not per clip) to minimize cost
- Output per clip:
  - Title (short-form platform-optimized)
  - Description (50–100 chars, near-perfect SEO)
  - Tag set for sorting
- Suggested tag dimensions:
  - Video type (e.g., funny, educational, ranking)
  - Theme (e.g., football, sleep, memory)
  - A few other organizational tags — Claude to propose
- Cache generated metadata in the project (do not regenerate on render)

#### 3c — Batch Processing + Preset Flow
- Batch queue: drop multiple videos, process sequentially
- Rate limiting required (avoid API throttling, resource exhaustion)
- Preset → one-click export flow:
  - Before processing, prompt for preset (or use last-used)
  - One button → processes + exports
- Progress per item + overall batch progress

---

### Tier 4 — External Integrations & Automation
**Size:** Large — split per feature
**Blocked by:** Decisions below (see "Open Decisions")

#### 4a — Folder Watcher
- Auto-process videos dropped into a watched directory
- Export back into the **same directory** as the source long-form video
- Choose automation level (fully auto vs. confirm-before-export)
- Decision needed: single drop folder, or per-project watched folders?

#### 4b — Safe Zone Overlay
- Visual overlay showing where TikTok / IG / YouTube Shorts UI elements will cover the video
- Toggle in editor; respects per-platform safe margins
- Small feature, pairs with export presets

#### 4c — YouTube Upload + Multi-Channel + Scheduling
- Auto-schedule clips for upload
- Assign clips to specific channels
- **HARD CONSTRAINT:** YouTube Data API default quota = 10,000 units/day; one upload ≈ 1,600 units → ~6 uploads/day without a quota increase
- Decision needed: accept the limit, or pursue quota increase?
- Requires OAuth per channel

---

### Tier 5 — New Tools
**Size:** Very large — each is its own multi-phase plan
**Blocked by:** Tier 3 and 4 (foundation + automation for the clipping tool first)

#### 5a — Ranking / Compilation Tool
- Input: multiple short videos
- UI: reorder, rank, assign positions
- Render: ranking-specific templates, transitions, number overlays
- Different pipeline than clipping — validates the tabs architecture

#### 5b — Voiceover / Animation / Stock Tool
- Input: script (or text)
- Output: AI voiceover + stock images/animations
- Fully different pipeline again — further validates the architecture

---

## Open Decisions (resolve before starting blocked work)

| # | Decision | Blocks | Notes |
|---|----------|--------|-------|
| 1 | YouTube upload: accept ~6/day limit or pursue quota increase? | 4c | Quota increases take weeks and may be denied |
| 2 | Folder watcher: single drop folder or per-project folders? | 4a | Affects UI and file-watching architecture |
| 3 | Content policy: block, flag, or auto-asterisk? | 3a | Asterisk = auto-fix; also decide if warning shown pre-export |
| 4 | Tag system: auto-assigned by LLM, manual, or both? | 3b | Affects UI and generation prompt |
| 5 | YouTube multi-channel: how many channels realistically? | 4c | 1–3 vs 10+ changes OAuth design |
| 6 | Titles/descriptions: LLM-best-guess, or real keyword research? | 3b | Real SEO requires paid keyword API (Ahrefs, etc.) |

---

## Suggested Prompt Split

| Prompt | Scope | Estimated Size | Notes |
|--------|-------|----------------|-------|
| **3a** | Content policy detection | Medium | Independent |
| **3b** | Titles / descriptions / tags | Medium | Independent; biggest unlock for scaling |
| **3c** | Batch + preset flow | Medium–Large | Builds on existing presets |
| **4a** | Folder watcher | Medium | After decision #2 |
| **4b** | Safe zone overlay | Small | Can be folded into 4a or standalone |
| **4c** | YouTube upload + multi-channel | Large | After decisions #1 and #5 |
| **5a** | Ranking / compilation tool | Very Large | Own multi-phase plan |
| **5b** | Voiceover / stock tool | Very Large | Own multi-phase plan |

**Rule:** `/clear` between every prompt. Do not chain them in a single session — context cost and quality both degrade.

---

## Priority Order & Rationale

1. **Tier 3** first — independent features, no external blockers, highest user value. Titles/tags directly unlocks the "scale my channel" goal.
2. **Tier 4** second — but only after resolving the open decisions. Don't build half a feature and stall.
3. **Tier 5** last — new tools are the *reason* for the platform, but the foundation and automation for the clipping tool must be solid first.

**Principle:** decisions before dependencies. Anything with an external blocker (YouTube quota, design decisions) waits until the blocker is resolved.

---

## Design & Engineering Principles

- **Local-first.** No required cloud calls except opted-in transcription APIs.
- **Minimal new dependencies.** Justify each addition.
- **Backwards compatibility.** `.env` remains a fallback; settings persist to config.
- **Scope discipline.** Don't refactor unrelated code. Note out-of-scope changes in summaries instead of making them.
- **Commit per phase.** No broken intermediate states.
- **Documentation lives with the code.** Update `CLAUDE.md` / `README.md` as part of each prompt, not at the end.

---

## How This Document Should Be Used

- Treat each row in "Suggested Prompt Split" as a **separate coding session**.
- Before starting a blocked item, confirm the relevant decision in "Open Decisions" is resolved.
- When a tier completes, update this document — strike through or move items to "Already Complete."
- This is a **living plan**. Reorder based on what actually matters when you get there.
