# SupoClip Design System

International Typographic (Swiss) Style. This document is the source of truth for
every UI decision in the app. If code and this document disagree, fix the code —
unless the change is intentional, in which case update this document in the same
commit.

## 1. Principles

- **Grid discipline.** Every element aligns to the 12-column grid and the 4px
  spacing scale below. Nothing floats free of it.
- **Sans-serif only.** One text typeface, used at every size. See §3.
- **Flush-left, ragged-right.** No centered text unless the element is
  structurally centered (e.g. a single icon in a fixed-size tile) — never
  centered body copy or headings.
- **Asymmetry over balance.** Layouts are built from unequal column spans, not
  mirrored/centered compositions.
- **Whitespace is structural.** Space is used to group and separate content,
  not left over. Don't fill space just because it's empty.
- **Absolute hierarchy.** Contrast comes from a small number of very different
  sizes/weights, not many similar ones. See the type scale.
- **Rules serve structure.** A hairline exists to separate or group content —
  never as decoration.
- **No ornament.** No drop shadows, no gradients, no soft rounded corners
  (radius is `0` everywhere — see §6.1). Any exception must be documented here
  first.
- **Color is functional.** Color communicates state or interactivity. It is
  never chosen for decoration.

## 2. Color

Four colors, full stop. No tints, no new hues, no opacity tricks on these four
— contrast comes from scale, weight, and space instead.

| Token | Hex | Role |
|---|---|---|
| `--color-ink` | `#0f0a01` | Near-black. Text, hairlines, structure. |
| `--color-paper` | `#fbfbfb` | Off-white. Ground/background. |
| `--color-teal` | `#558a86` | Primary accent — interactive, success. |
| `--color-blue` | `#08327d` | Secondary accent — informational. |

### Functional states

- **Success** → teal.
- **Info** → blue.
- **Warning / Error** → **not a color.** Rendered as ink at heavier weight
  plus a rule, or an inverted block (paper text on an ink fill). Never red or
  yellow. See `task-status-dot.tsx` for the reference implementation of an
  inverted-block error state.

### Semantic tokens (`frontend/src/app/globals.css`)

Every ShadCN/Radix primitive already consumes these semantic variables, so
retheming happens here, not in the components:

| Variable | Light | Dark | Notes |
|---|---|---|---|
| `--background` | paper | ink | |
| `--foreground` | ink | paper | |
| `--card`, `--popover`, `--muted` | = background | = background | flat — no elevation tiers |
| `--card-foreground`, `--popover-foreground`, `--muted-foreground` | = foreground | = foreground | "muted" means smaller/lighter type, never a dimmed color |
| `--primary` | teal | teal | `--primary-foreground` is paper (light) / ink (dark) for contrast |
| `--secondary`, `--accent` | blue | blue | `-foreground` is paper |
| `--destructive` | ink | paper | pair with weight/rule, not a hue |
| `--border`, `--input` | ink | paper | solid 1px hairline, never alpha |
| `--ring` | teal | teal | solid 2px focus ring, no blur |

One sanctioned exception: modal/sheet scrims use `bg-ink/50` — a functional
dimming layer, not a decorative tint. Don't add any other alpha usage of the
four colors.

## 3. Typography

**Font stack:** `Geist Sans` (primary, already loaded via `next/font/google`
in `frontend/src/app/layout.tsx`) — a grotesque sans, the Inter/Helvetica
equivalent for this codebase. `Geist Mono` for numerals, timestamps, and
status/system text (e.g. `status-strip.tsx`). No third family in core-product
screens. (`Syne` remains loaded only for the marketing pages that are being
phased out — do not use it in any new core-product UI.)

| Level | Size / line-height | Weight | Tracking | Usage |
|---|---|---|---|---|
| `display` | 56px / 1.05 | 700 | -0.02em | One per screen, max. Page-defining headline only. |
| `h1` | 32px / 1.15 | 700 | -0.01em | Screen/section title (e.g. project name, "Settings"). |
| `h2` | 22px / 1.2 | 600 | -0.005em | Sub-section title within a screen. |
| `h3` | 17px / 1.3 | 600 | 0 | Card/panel title, table group header. |
| `body` | 15px / 1.5 | 400 | 0 | Default reading/UI text. |
| `small` | 13px / 1.45 | 400 | 0 | Secondary text, form hints, list metadata. |
| `caption` | 12px / 1.4 | 500 | 0.01em | Captions under media, inline field labels. |
| `micro` | 11px / 1.3 | 600 | 0.08em, uppercase | Status labels, tab labels, table column headers. |

Hierarchy comes from jumping between these levels, not from intermediate
sizes. If a design needs something between two levels, it's using the wrong
level — pick the one that reads correctly at a glance.

## 4. Grid & Spacing

**Base unit: 4px.** Every margin, padding, and gap is a multiple of it:
`4, 8, 12, 16, 24, 32, 48, 64, 96` (Tailwind's `1, 2, 3, 4, 6, 8, 12, 16, 24`
spacing steps — don't use the in-between steps like `5`/`7`/`10`).

**Grid:** 12 columns. Use Tailwind's existing breakpoints (`sm`/`md`/`lg`/
`xl`/`2xl` — no new breakpoints). Standard page margin is `24px` (`px-6`) on
mobile, `48px` (`px-12`) from `lg` up. Gutters are `24px` (`gap-6`).

**Breaking the grid:** rare, and only for full-bleed media that has no
meaningful column boundary — a video preview frame, a clip thumbnail grid.
Even then, the container around the media stays on-grid.

## 5. Components

Each rule below states geometry/states and points at the file that owns it.

### Buttons — `frontend/src/components/ui/button.tsx`
CVA variants: `default` (primary/teal fill, paper text), `secondary` (blue
fill), `outline`/`ghost` (ink border or transparent, ink text), `destructive`
(ink fill, paper text — weight carries the warning, not a hue). Square
corners (radius token is 0). Focus state is the solid 2px teal ring. Disabled
state is reduced to `opacity-50` — the one exception to "no opacity tricks",
since it communicates non-interactivity rather than color meaning.

### Inputs — `frontend/src/components/ui/{input,select,switch,slider,textarea,checkbox}.tsx`
1px ink hairline border, square corners, no inner shadow. Focus = solid teal
ring, no border-color change (avoids introducing a fifth color state).
Disabled = `opacity-50`. Select/switch/slider handles are ink or teal fills,
never new colors.

### Cards — `frontend/src/components/ui/card.tsx`, `home/tool-card.tsx`
Flat: same background as the page, separated by a 1px ink hairline border,
never a shadow. Internal padding is `24px` (`p-6`). A card's own internal
layout still follows the 4px scale and flush-left text.

### Tables / lists
Row height is a multiple of the base unit — `48px` (`h-12`) for dense project
rows, `64px` for cards-as-rows. Rows are separated by a single 1px hairline
(`divide-y`), not alternating background stripes. Hover = swap
`background`↔`foreground` is too strong; use a 1px ink border appearing on the
row instead. Selection = ink background fill with paper text (inverted
block), consistent with the warning/error convention.

### Tabs / navigation — `frontend/src/components/tool-tabs.tsx`
Active tab: 2px ink bottom border + full-weight text. Inactive tab: no
border, `muted-foreground` weight (same color, lighter/smaller type per §2).
No pill backgrounds, no rounded tab shape.

### Modals / dialogs — `frontend/src/components/ui/{dialog,alert-dialog,sheet}.tsx`
Square corners, 1px ink border, `bg-ink/50` scrim (the one sanctioned
alpha use). Width: `max-w-2xl` for standard dialogs, full-width `Sheet` for
editor side panels. Actions are flush-right in the footer, primary action
last (rightmost).

### Toasts — `frontend/src/lib/toast.ts`, `components/ui/sonner.tsx`
Bottom-positioned (Sonner default), square corners, ink border. Success/info/
warning auto-dismiss at 4s; errors persist until dismissed
(`duration: Infinity`) — this behavior already exists in `lib/toast.ts` and
is unchanged by this refactor. Always import `toast` from `@/lib/toast`, never
`sonner` directly.

### Progress indicators — `frontend/src/components/ui/progress.tsx`
A single 4px-tall ink track with a teal fill bar. No rounded ends. An
indeterminate state uses the existing `command-bar-pulse` keyframe idiom
(border-weight pulse) rather than a shimmering gradient.

### Empty states
Icon (ink, no color fill) + one line of `body` copy + one primary button.
Existing pattern in `EmptyState`/`recent-projects.tsx` — unchanged by this
refactor beyond token/type-scale swaps.

### Loading states
Skeleton blocks (`components/ui/skeleton.tsx`) at `background`/`foreground`
contrast, 300ms delay before showing (existing decision, unchanged).

### Rank number tile — `backend/src/ranking_overlay.py` (burned-in), Ranking
tool's ordering list (on-screen)
A fixed square tile with a single centered digit — the sanctioned
"structurally centered" exception from §1/§7. Tile fill is ink or teal
(never blue/paper, so it always reads against either theme and against
arbitrary video content once burned in); digit is paper on a teal/ink
tile, ink on the (rare) paper-tile override. No new colors: this is the
same ink/paper/teal vocabulary as everywhere else, just applied to a
one-character label instead of a button or badge. Applies to the three
tile-based templates (Rapid Fire, Countdown, Ranking List).

### Gold #1 — Ranking tool's `ranking_classic` template only
`build_ranking_overlay_ass`'s #1 rank tile/text render in gold (`#FFD700`),
and the rank-select UI (`(rank)/rank/create/page.tsx`) previews that same
gold on the #1 slot. This is the **one deliberate exception** to "no new
colors" — but it's scoped to Ranking *video output* and the UI previewing
that output, never to site chrome (buttons, nav, cards, status badges
elsewhere stay strictly ink/paper/teal/blue). See DECISIONS.md's "Ranking
videos use full color" entry for the reasoning; don't extend gold (or any
other new color) to a site-UI element on the strength of this precedent.

### Folder-based clip picker — `(rank)/rank/create/page.tsx`
Three-step flow (folder → select → text), built from existing primitives
only: a bordered drop zone (`border-border`, matches the editor's upload
zones) with two `Button variant="outline"` triggers for the OS folder
picker (`<input webkitdirectory>`) and a plain multi-file picker: a
`grid grid-cols-2 sm:grid-cols-4` thumbnail grid (matches the template
picker's grid pattern elsewhere in this tool); a `Badge variant="outline"`
for "used N×" usage-count hints, reusing the same badge component and
outline style as status badges on `/list`. No new component was added —
this screen composes existing `Button`/`Input`/`Textarea`/`Badge`/
`EmptyState` the same way `(clipping)/create` does.

## 6. Tokens Reference

### 6.1 Radius
`--radius-sm/md/lg/xl` are all pinned to `0px` in the `@theme inline` block of
`globals.css`. There is no rounded-corner scale in this system. If a future
screen genuinely needs a radius (e.g. an avatar), it must be proposed and
documented here first — do not add ad-hoc `rounded-*` classes.

### 6.2 Full variable table
See `frontend/src/app/globals.css` `:root`/`.dark` blocks — they are the
literal source; this document's §2 table mirrors them by role.

## 7. How to add a new screen

1. Pick every type size from the §3 table — no arbitrary `text-[Npx]`.
2. Every spacing value is a multiple of 4px from the Tailwind steps listed in
   §4 — no `p-5`/`gap-7`/etc.
3. Build interactive elements from `components/ui/*` primitives only — don't
   hand-roll a button/input/dialog.
4. No new colors, shadows, gradients, or radii. If the palette genuinely
   can't express something, that's a signal to rethink the design, not to add
   a color.
5. Default to flush-left, ragged-right text. Centered text needs a structural
   reason (e.g. a number inside a fixed square tile).
6. Lay the screen out on the 12-column grid from §4; note in your PR
   description if and why you broke it.
7. If your screen needs a rule not covered here, add it to this document in
   the same change, not after the fact.
