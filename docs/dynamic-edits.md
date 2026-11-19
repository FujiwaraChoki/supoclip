# Dynamic edits

Dynamic edits make a clip look cut by a short-form editor: animated captions, hard reframes and short pushes, sound
effects on cuts and graphics, a steady music bed, the odd flash at a cut, colour grades and retention overlays, each
placed on a moment that matters. An LLM finds the moments ("beats"); deterministic code decides what happens on them;
one TypeScript renderer draws every frame, in the browser and on the worker. Every timing, size and level below comes
from a frame-level study of reference edits ([Reference edits](#reference-edits)).

The feature is behind the `dynamic_edits` feature flag. With intensity **Off** and a classic caption template,
nothing new runs: prompts, cache keys, job arguments and renders are byte-identical to before (pinned by
`backend/tests/unit/test_dynamic_edits_legacy_pins.py` and the existing legacy pins).

Contents: [For users](#for-users) · [Caption styles](#animated-caption-styles) · [Hook title](#hook-title) ·
[Reference edits](#reference-edits) · [Effects](#effects) ·
[Intensity budgets](#intensity-and-budgets) · [Configuration](#configuration) · [Architecture](#architecture) ·
[Assets and drop-ins](#assets-and-self-hoster-drop-ins) · [Rollout](#rollout) · [Fallbacks](#fallbacks) ·
[Known gaps](#known-gaps) · [Troubleshooting](#troubleshooting)

## For users

### On the create screen

Open **Settings** in the composer:

- **Edits** tab: **Edit intensity** is Off, Subtle, Bold or Max.
  - **Subtle** (podcast clippers): outlined subtitles a phrase at a time, a reframe now and then, a constant music
    bed 11 dB under the voice, almost no sound effects.
  - **Bold** (Hormozi): captions on a dark pill, revealed word by word, the key word in a box or colour; punch-ins
    that hold until the next one (about 2.5 framing changes per 10 s), short 11-frame pushes on emphasis words, a
    short hit on half of the reframes, a quiet bed (−19 dB).
  - **Max** (MrBeast and clipper channels): heavy outlined caps, word by word; reframes on almost every line (about
    5.5 per 10 s, 20–35 %), louder hits, a loud bed (−6 dB) that rises in pauses, the odd 3-frame flash or 6-frame
    light leak at a real cut.
  - At every intensity zooms are reframes that hold, transitions happen only at real cuts, sound effects land only
    on visual events, the music is a steady bed and the clip ends at −14 LUFS ([Effects](#effects)).
  - Once it's on, **Fine-tune** (closed until you open it; a number on it counts what you changed) holds the
    switches that turn off what you don't want: sound effects, music (and its mood: Auto lets the AI pick
    one of seven, or pick aggressive, hype, chill, dramatic, inspiring, funny or dark yourself), zooms, transitions,
    the swearing treatment (leave, bleep, bleep and cover the mouth with a bar, bleep and pixelate the mouth; the
    captions show a bleeped word as `f***`), a progress bar, a loop ending, and GIF reactions where the server
    offers them. Camera shake, speed ramps and
    freezes and the "Wait for it" sticker are never placed automatically (no reference edit uses them); their stored
    settings stay valid and hand-made cues of those kinds still render.
  - B-roll, where the server offers it, is a switch under the intensity cards and works at any intensity, Off included.
- **Captions** tab: one grid of preview tiles, the classic styles first and then the
  [three animated styles](#animated-caption-styles). An animated tile is the real renderer drawing the Bold look
  over a demo scene; a classic tile is a still in the style's own font and colours. Font, size and colour sit
  behind **Customize**. An animated style works with intensity Off too ("style
  only"): the captions are drawn with the Bold look and nothing else changes.

The create screen remembers the choice on your account. If dynamic edits aren't available to you (flag off,
older backend), the request drops `edit_style` and swaps an animated caption style for its closest classic one.

### On the results page

The task's settings sheet has the same intensity and switches. Applying them to existing clips re-renders them
in the background: the clips show "Rendering edits…" until they're done. Trims, splits, merges and caption edits
of clips with dynamic edits also re-render in the background. A clip may show:

- "This video had no word timing, so captions and effects follow approximate timing." (`fx_meta.timing != "word"`)
- "Some effects were simplified for this clip." (`fx_meta.fx_level != "full"`, see [Fallbacks](#fallbacks))

### In the clip studio

The **Edits** tab in the inspector changes the clip's intensity and switches, lists the beats (click to seek,
delete one, change its kind), and edits the selected effect. "Plan edits" asks the AI for beats when a clip has
none; "Re-plan with AI" asks again. "Remove all edits" clears everything. **Grade** and **grain** live in the
**Effects** tab. The timeline gains an **FX lane** (drag to move, double-click to edit, Delete to remove,
double-click an empty spot to add a sound) and a **Music lane** showing how the bed ducks under the voice. Every
change is one undoable step. See [editor.md](editor.md#dynamic-edits).

The studio's **AI edit** field can change the same settings ("make it calmer", "no music"): it sets the `fx.*`
settings (intensity, switches, mood, censoring, grade) and can switch the captions to one of the three listed styles.

### Through the API, MCP and iOS

`POST /tasks/` takes `edit_style` ([API reference](api-reference.md#dynamic-edits)). The MCP tool
`supoclip_create_clip_task` takes `intensity` and an `edit_style` passthrough; `supoclip_list_edit_styles` lists
the choices, and `supoclip_list_caption_templates` includes the animated styles when they are enabled. The iOS app
offers the same choices once `ios_enabled` is on for the `dynamic_edits` flag; clips rendered on the server carry
their effects for every client.

### On the homepage

The landing page has a **Dynamic edits** section (`frontend/src/components/landing/dynamic-edits-section.tsx`) that
plays one real demo clip at Off, Subtle, Bold and Max (switch while it plays), plus a Max clip with b-roll.

## Animated caption styles

Template ids live in `FX_CAPTION_TEMPLATES_DICT` (`backend/src/caption_templates.py`), separate from the seven
classic templates. Each names the classic template whose libass captions replace it when a clip renders without
the drawing stage (`ass_fallback`). Three are offered (`LISTED_FX_CAPTION_TEMPLATES`):

| Id | Name | What it does | Classic fallback | Needs |
|---|---|---|---|---|
| `highlighter_swipe` | Highlighter | The key word of a caption group in CAPS, black on an opaque rounded box (green at Bold, yellow at Subtle and Max), there on the word's first frame and gone with its group. | `minimal` | – |
| `emoji_drop` | Emoji Drop | An emoji (only one the model picked because it depicts a thing the words name: 💰, 🚗, 📈) under the caption, 0.26 W, 2 frames in, held until the caption group changes. Never a face emoji, never on a sad moment. | `tiktok` | – |
| `behind_head` | Behind Head | One giant lowercase word (Archivo Black, 0.70 W, at most 0.16 H) behind the speaker: the person is cut out (matte) and drawn over it. On a wide shot it sits at chin/neck height; on a close-up (face at least 12 % of the height) it goes across the top of the head, so the head covers only the foot of its middle letters. It hard-cuts in on a reframe and out on the next; the captions step aside while it shows. Without a person in frame the word goes in front; on an interview's stacked two-shots it is dropped. | `minimal` | face, matte |

**Unlisted styles.** `marker_circle`, `slam`, `stat_counter`, `kinetic_stack`, `decode_glitch` and
`face_doodles` (`hidden: True`) are no longer offered anywhere: not in `GET /caption-templates?include=fx`, the
`templates` of `GET /tasks/edit-styles`, the create screen, the studio picker, the AI editor's choices or MCP. They stay
valid ids, so a stored task, a draft or an API caller that uses one keeps rendering with that template's own drawers
and motion (`look: "template"`), with the fx-v2 plans for everything else. This is the safest fallback: nothing
re-renders differently from what the user picked, and no stored id becomes an error. The AI editor never switches a
draft to one.

### Intensity looks

The listed styles draw captions with the look of the clip's intensity (`FxCaptionStyle.look`, chosen by
`contracts.caption_look_for`: Off ("style only") draws Bold, a serious clip draws Subtle). Python
(`edit_fx/caption_style.py`, `LOOKS`) resolves font, size, case, outline, pill and keyword colour; fx-core
(`captions/looks.ts`, `captions/groups.ts`) groups, times and draws:

| | Subtle | Bold | Max |
|---|---|---|---|
| Font, size at 1080 wide | TikTok Sans Bold, 52 px (4.8 % W) | Poppins ExtraBold, 72 px (6.7 % W) | THE BOLD FONT, 84 px (7.8 % W), ALL CAPS |
| Group | phrase, up to 4 words, shown whole | phrase, up to 3 words, word by word | phrase, up to 3 words, word by word |
| Display time | first word − 2 frames | each word − 1 frame | each word − 1 frame |
| Entrance | none | none (the line re-centres) | 2 frames: 0.85 scale at 60 %, then 1.0 |
| Edge | black outline 0.07 em, soft shadow | dark pill (`#170904`, 85 %) behind the line, every listed style | black outline 0.09 em, hard shadow |
| Keyword | box (highlighter) or gold | CAPS, green box (highlighter) or yellow | box (highlighter) or yellow |
| Keyword rate (per 10 s, min gap) | 1.25, 4 s | 4, 1.2 s | 5.5, 0.8 s |
| Row (no layout preference) | 0.68 H | 0.55 H | 0.56 H |

Phrase groups break at sentence or clause punctuation, at a pause of 0.25 s, before "and/but/so/because…" once a group
holds two words, at a segment boundary and when full; a group stays up until the next (gaps under 0.3 s bridged), at
least 0.4 s. Bold and Max strip edge punctuation. A layout's caption row (a stacked layout's seam, the band under a
letterboxed picture) wins over the look's row. The display time is calibrated against the references with the study's
own scripts: transcript word starts sit one to three frames after the heard onset, so −1 frame shows a word about one
frame after it is heard, like Hormozi.

**Interview and mixed clips get a row per shot** (`edit_fx/caption_rows.py`, `FxCaptionStyle.rows`). The
`interview` layout composes each camera shot on its own, and one caption height can't suit them all. A `compositor`
layout that switches between frame-filling crops (or covers) and fitted graphics reports its shots the same way
(`describe_vertical_layout`, `interview_segments`: a crop or cover is `single`, a fitted shot `fit`), so its captions
sit at mid-chest on the speaker and below the picture of a graph instead of across it:

- **Two-shot (`pair`):** 0.50, on the seam between the stacked panels.
- **Close-up (`single`):** 0.65, mid-chest under the chin.
- **Graphic or b-roll (`fit`):** 0.76, on the blurred fill under the picture. It goes lower when the picture is taller
  than 16:9, so it always clears the picture by 1.5 % H.

The rows follow these rules:

- **Short shots:** a shot under 1 s (a flash or a quick reaction) keeps the previous shot's row. A clip that opens on
  one takes the next shot's row.
- **A camera cut ends the caption group on screen** (a row change; `phrase_groups(cuts=...)` /
  `phraseGroups(..., cuts)`). Words starting before the cut − 0.15 s (`CUT_LEAD`) stay in the old group, which leaves
  exactly at the cut. The rest start the next shot's groups, grouped as usual from there, and show from the cut on the
  new row. A word starting within 0.15 s before the cut goes with the next shot, so no stub flashes up for a few frames.
  Captions therefore switch exactly at cuts, never move inside a group, and never sit on another shot's row.
- **The look emoji** leaves with its group at a cut.
- **Burned-in source captions:** they push each row clear.
- **Hook title:** rows never move for it, and it never moves for them: it sits at the top, or beneath a head it would
  cover ([Hook title](#hook-title)).
- **Overlays:** the look emoji, standalone keywords, the decode accent and sticker obstacles read the row in effect
  (`PreparedScene.captionYAt`).
- **The user's position wins:** a caption edit's position pins the captions with no rows.
- **Where rows apply:** only the intensity looks, on interview layouts and on compositor layouts that mix crops and
  fitted shots. Other layouts, the hidden templates and legacy ASS captions keep one row.
- **Stacked two-shots:** automatic reframes and behind-the-head words are dropped while an interview shows its two
  speakers stacked (`panel_windows` in `video_utils._fx_ladder_setup`); a zoom would cross the seam and the panel
  edge would cut the word. Single-person shots keep theirs.

In the studio, the stored `vertical_layout` keeps the shots (`segments`) and the burned-in `caption_band`. Preparing a
session then seeds `EditDocument.captionRows` on the source clock: `{y, rows}`, where `y` is the automatic caption
position the rows replace. Server export and browser draw both map the rows through the draft's segments
(`studio_caption_rows` / `studioCaptionRows`, pinned by `shared/fx/fixtures/{rows,scene}`).

Rows stop applying as soon as the user places the captions: `captions.y` changes, the caption layer moves vertically,
or the frame is no longer the full vertical picture. A caption's own transform pins that caption, and dragging starts
from where its row drew it. Combining drafts rebases each part's rows. Sessions prepared before rows existed have no
`captionRows` and keep one row.

Studio: the draft seeds font, size, case, words per line, accent and the pill switch from the look
(`editor_service._look_caption_seed`), and the user can change them; the motion and outline follow the look of the
draft's intensity. The studio's caption timeline still lists fixed word groups; the preview and export draw phrase
groups (a per-caption move made from the timeline applies when its first word starts a phrase group).

Sizes are in units of `min(W, H) / 1080`, so styles survive the 720p cap. Non-Latin text falls back to the system
Noto fonts on both renderers.

## Hook title

The hook title is the AI-written headline shown for the first seconds of a clip (`hook_title`, 0.12 s to about 4 s). On
a Dynamic edits clip it is drawn by fx-core (`frontend/src/lib/fx/core/hook.ts`, scene field `FxScene.hook`, an
`FxHookTitle`), in the browser and on the worker from the same code. Python resolves the text, font, accent, look,
caption template and window (`backend/src/edit_fx/hook_title.py`); fx-core picks the style from the template, lays the
title out, sizes and places it. Every clip that goes through the Dynamic edits render gets this title, also one whose plan
only adds sound. Classic clips (intensity Off with a classic template) keep the libass title exactly as before.

The style follows the caption template (`HOOK_VARIANTS`), so the title belongs to the captions under it:

| Style | Templates | Look |
|---|---|---|
| Stack | TikTok, Minimal, Podcast, Behind Head, Emoji Drop, Highlighter, Stat Counter, and any template not listed | a white label that hugs every line, drawn as one shape (corner radius 0.34 em, also where two lines differ in width; lines closer than 0.5 em in half-width share an edge); near-black type `#0B0B0F`, 80u (7.4 % W), one label 1.4 em tall per line, 0.42 em of label beside the text; soft shadow. Key words get a marker box in the accent |
| Outline | Default, Marker Circle, Face Doodles, Slam, Kinetic Stack, Hormozi, MrBeast, Neon, Decode Glitch | type straight on the picture, 92u (8.5 % W; Slam and Kinetic Stack 98u), lines 1.14 em apart, outline 0.1 em with round joins, soft shadow; every outline is drawn before any fill. Key words take the accent as their colour (Hormozi: a green box). Own colours: MrBeast yellow with red key words, Neon cyan with a magenta glow, Decode Glitch a magenta offset shadow |

| | |
|---|---|
| Type | Poppins ExtraBold for the listed styles (a font the user picked instead wins), other templates their caption font, never a regular weight (TikTok Sans Regular becomes Bold); sentence case as written (an unlisted all-caps template keeps capitals) |
| Lines | balanced; one line under 62 % of the text width, else two or three (see Size); at most 0.88 W; never under the style's floor (58u stack, 64u outline) unless one word is wider than the frame. Text without spaces (Chinese, Japanese, Korean) breaks between characters |
| Key words | numbers first, then power words (never, secret, million, …), at most two; a marker box covers the word, not its comma. The accent is the captions' keyword colour; one too pale or too dark becomes `#FFE45C`, and a template with its own accent keeps it |
| Motion | in over 260 ms (0.86 → 1.035 → 1, fade over the first 120 ms), marker boxes wipe in left to right from 200 ms (180 ms each, the second 80 ms later), out over the last 200 ms (fade, 1 → 0.96). Subtle is 10 % smaller with no overshoot; Max tilts the title −2° |
| Clock | edit time, so a speed ramp or freeze moves it like the captions |

**Size** follows the title (`hookBlock`): a few words that fit one line grow up to 1.2 times the style's size; most
titles take two lines, a little larger when they are short and down to 85 % for a long one; only then a third line,
which shrinks to the style's floor.

**Placement** (`placeHook`): the title's home is the top of the frame, top edge at 11 % of the height, centred, on
every clip. It leaves home in one case only: it would cover a head there (more than 18 % of a face box at least 5 % H
tall, in at least a quarter of the window's face samples). Then it sits right beneath that head, 2 % H under the
lowest chin, if there is room: clear of the captions fx-core draws, of any other head, and ending no lower than
64 % H. When that only holds until a camera cut (a caption row change, or the main head jumping in the face track),
the title is judged on its opening shot and leaves at the cut, as long as it showed for 2.2 s. Without room it stays
at home. Captions, letterbox bands and stickers never move it (a placed sticker keeps off it), and the studio's
offset adds to wherever it sits. A title needs the face track, so a render with a title runs the face analysis even
when no cue needs it.

**Without the drawing stage** (`FX_RUNTIME=off`, the safe and none levels) libass burns
the title (`build_hook_title_ass(heavy=True)`): Poppins ExtraBold in white, about 6 % W, black outline and a soft dark
halo, numbers and power words in the template's accent, no boxes; top of the frame, or on the seam of a stacked
opening.

**Studio.** The hook title is its own layer over the preview: `drawEditorTextLayer` draws the same title with
`hookOps`, and the selection box is the title (`hookBounds`). The draft's hook transform is an offset from the title's
position and a scale; its font and accent are the draft's caption font and accent (`studioHook` ==
`editor_fx_render.studio_hook`, pinned by `shared/fx/fixtures/scene`).

**Title text.** `ai.sanitize_hook_title(raw, source_text=…)` gives a bare number back the sign the speaker used
("90 of guests" → "90% of guests", only when every mention in the clip's transcript carries it) and brings a Title
Case headline to sentence case (names the transcript capitalises in mid-sentence keep their capital), so one task's
titles read alike. The prompt asks for both.

## Reference edits

The behaviour above and below follows a frame-level study of 26 reference clips (Alex Hormozi current and classic,
MrBeast, Diary of a CEO, Modern Wisdom, Flagrant clips, Iman Gadzhi; 30 fps frame strips, cut and zoom tracking,
caption events against energy onsets, demucs-separated sound). The rules that matter most:

1. Nothing animates slowly: captions, boxes and emoji land in 0–2 frames.
2. A zoom is a reframe that holds until the next one, alternating in and out; it never bounces back on a timer.
3. Transitions are rare; 98–100 % of cuts are hard.
4. Sounds follow visual events (cuts, graphics), not words; mostly short low hits, booms and pops.
5. Music is a steady bed 6–19 dB under the voice, not a −30 dB whisper that pumps.

The sample renders and their measurements against the references are kept outside the repository (see the PR).

## Effects

What the resolver can place (cue kinds in `backend/src/edit_fx/contracts.py`, drawers in
`frontend/src/lib/fx/core/`):

- **Accents**: the caption style's mark: a keyword box or colour (at most one per caption group), an emoji, a
  behind-the-head word. Unlisted styles keep their own (circle, slam, counter, sticker, …).
- **Reframes** (`zoom` mode `punch`): a hard step to 110–115 % (Subtle), 112–120 % (Bold) or 120–135 % (Max) about the
  face, held until the next reframe ("out", back to the base framing) or the next real cut. Placed on beat key words
  and sentence starts one frame early, at least 1.2 s (Bold) / 0.8 s (Max) apart; never across a stacked layout's
  panels (`reaction`, `stack`, `overlap` layouts get none, and an interview's stacked two-shots drop theirs).
- **Nudges** (`zoom` mode `nudge`): +6–10 % over 11 frames, nearly linear with a soft landing, held; between two
  reframes they alternate with an 11-frame ease back out (`pull`), so they never stack. Slow baseline pushes
  (`kb<n>`) only at Subtle and style-only.
- **Transitions** at real cuts only: a 3-frame white flash (0.6, 0.95, 0.3) or a 6-frame warm light leak. Bold
  places at most one per 40 s and only on passage breaks; Max one per 20 s. Whip, spin, zoom-through and glitch
  transitions stay available by hand in the studio, never automatically.
- **Not automatic**: camera shake, flash/glitch/desaturate hits, speed ramps, slow-motion tails, freezes, emoji
  bursts, risers and the "Wait for it" sticker. Hand-made cues of those kinds still render (time warp needs
  `FX_WARP=on`).
- **Sound effects** on visual events only: a short low hit or boom one frame before half of the Bold reframes and
  60 % of the Max ones, a pop with every third emoji, a boom with a behind-the-head word, a short whoosh two frames
  before a flash. Plain talking gets none. The sound ids (whoosh, impact, pop, riser, record scratch, boom, cash
  register, ticker, slap, stamp, glitch, tape stop) are unchanged; unlisted styles keep their own sounds.
- **Bleeps**: a 1 kHz tone over every swear, optionally with a black bar or pixelation over the mouth (from face
  landmarks). Bleeps ignore budgets. No bleeps with approximate timing, and none with intensity Off. The captions
  mask every word a bleep covers by at least 30 ms: the first letter stays and every later letter becomes a star
  (`fuck` → `f***`, `edit_fx/censor.py`, mirrored by fx-core `censor.ts`). Bold and Max strip edge punctuation but
  keep the stars.
- **Music bed**: one of 53 curated CC0 beds in seven moods (hype, chill, dramatic, funny, inspiring, dark,
  aggressive; Auto follows the beat model, which keeps aggressive for heated rants, call-outs and roasts and never gives it to a sincere moment),
  looped as a steady bed: about 11 dB under the voice at Subtle and 19 dB at Bold, constant; 6 dB under it at Max,
  rising about 7 dB in pauses (slow 0.25/0.6 s ramps, joined across short gaps, so it never pumps). Each mood has
  7–8 curated beds and the plan picks one by seed. Aggressive beds sit 4 dB and hype beds 2 dB above those levels
  (`MOOD_MUSIC_TRIM_DB` in `resolver.py`): a drill beat under a rant should be felt, not whispered.
- **Grades**: punchy, warm, teal & orange, noir, VHS, dream; Auto picks punchy at Max and none otherwise. **Grain**
  is optional.
- **Retention**: a progress bar along the bottom, a loop ending (the last 0.4 s crossfades into the first frame,
  music seam included), and a "Wait for it…" sticker before a payoff.
- **Reactions**: an optional GIF picture-in-picture on the funniest moments, with "via GIPHY" attribution. Off
  unless the server has `REACTIONS_PROVIDER=giphy` and a key, and the user switches it on.

**Serious mode**: when the beat model marks a clip as serious (grief, illness, violence, tragedy), it gets Subtle
budgets and the Subtle caption look whatever the intensity, no comedic sounds, emoji, doodles or reactions, and an
"inspiring" bed. Bleeps stay. Independently, a clip whose words mark a vulnerable moment (crying, died, funeral, …)
never gets a punchline or a comic tone from the beat model (`beats._sincere_guard`), and no emoji lands on a sad beat.

## Intensity and budgets

Rates per 10 s of edited duration (`per10_cap`: round half up, at least one from 6 s), with the minimum gap. "Style
only" is a listed caption style with intensity Off. The source of truth is the tables at the top of
`backend/src/edit_fx/resolver.py`.

| Family | Style only | Subtle | Bold | Max |
|---|---|---|---|---|
| Reframe events (in + out) | – | 1.2 / 3.0 s | 2.6 / 1.2 s | 5.5 / 0.8 s |
| Reframe size | – | 110–115 % | 112–120 % | 120–135 % |
| Nudges (11 frames) | – | 0.5, +5–6 % | 2.5, +6–8 % | 4.5, +8–10 % |
| Keywords (one per caption group) | 4 / 1.2 s | 1.25 / 4 s | 4 / 1.2 s | 5.5 / 0.8 s |
| Emoji (emoji drop) | 1 / 3 s | 0.5 / 6 s | 1 / 3 s | 2.5 / 1.5 s |
| Behind-the-head words | 1 per 20 s | 1 per 30 s | 1 per 20 s | 1 per 12 s |
| Hits on reframes and cuts | – | – | 50 % | 60 % |
| Sound effects (cap / gap) | style sounds, 10 per min / 1.0 s | 6 per min / 2.5 s | 18 per min / 0.8 s | 36 per min / 0.35 s |
| Transitions at real cuts | – | – | 1 per 40 s, passage breaks | 1 per 20 s |
| Music bed under speech / duck (aggressive +4 dB, hype +2 dB) | – | about −11 dB / 0 | about −19 dB / 0 | about −6 dB / 7 dB |
| SFX bus gain | −16 dB | −20 dB | −16 dB | −10 dB |
| Grain (when on) | – | 0.15 | 0.20 | 0.30 |

Levels are calibrated on rendered clips measured with the study's own scripts: hits sit about 16 / 12 / 5 dB under the
separated voice peak and the bed about 11 / 19 / 6 dB under the voice during speech. Hidden templates' own accents
keep their old family budget (accents 10 / 20 / 40 per minute).

Global rules (in order): serious mode; bleeps; quiet zones (nothing starts in the first 0.25 s except caption marks,
nothing in the last 0.3 s unless the loop ending is on; a held reframe may run to the end); no zoom or transition
inside B-roll or reaction windows, no zoom starting within 0.4 s of a transition or holding across one; nothing in
the top 20 % while the hook title shows. Effects you add or delete in the studio survive a re-resolve: reframe,
nudge and cut-hit ids are frame numbers (`z<frame>.zoom`, `n<frame>.zoom`, `e<frame>.sfx`) and keyword ids word
numbers (`w<n>.kw`), so a deleted one stays deleted while its moment stays.

**Loudness.** Generation normalises the voice to −14 LUFS, then measures voice and bus together and moves the whole
mix back to −14 LUFS (`fx_audio.measure_mix_lufs`, ±12/+6 dB), before a −1.7 dBFS limiter, so the encoded clip reads
−14 LUFS ± 0.5 with a true peak at or under −1 dBTP.

## Configuration

`backend/src/edit_fx/env.py` is the only reader. Unknown values fall back to the default. Set them on the API, the
video worker, the AI worker and every [remote render worker](render-worker.md#dynamic-edits-renderer-deno);
`docker-compose.yml` passes them through the `&worker-environment` anchor.

| Variable | Default | Purpose |
|---|---|---|
| `EDIT_PLAN_LLM` | `LLM` | Model for beat tagging (`provider:model`) |
| `EDIT_PLAN_TIMEOUT_SECONDS` | `45` | One beat-tagging call; past it the clip uses heuristic beats |
| `EDIT_PLAN_DAILY_LIMIT` | `100` | Studio "Plan edits" requests per user per day (refunded on failure) |
| `FX_RUNTIME` | `deno` | `off` is the kill switch: no drawing stage, clips render at level safe |
| `FX_DENO_BIN` | `deno` | The deno binary |
| `FX_WARP` | `on` | `off` disables speed ramps and freezes everywhere |
| `FX_RENDER_TIMEOUT_SECONDS` | `max(900, 6 × clip seconds)` | Wall-clock limit for one FX render |
| `FX_LIBRARY_DIR` | `/app/fx_library` | Self-hoster drop-in sounds and music |
| `FX_LOUDNORM` | `static` | `static` (measured voice gain) or `dynamic` (the old one-pass loudnorm) |
| `FX_CHAIN_MODE` | `pipe` | `file` makes the drawing stage write a file instead of piping, if pipes prove flaky |
| `REACTIONS_PROVIDER` | `off` | `giphy` enables GIF reactions (hosted stays `off` until legal review) |
| `GIPHY_API_KEY` | empty | Needed with `REACTIONS_PROVIDER=giphy` |

## Architecture

Clocks: **S** is source-clip time (words, beats, stored cues), **E** is edit time after cuts, **F** is final output
time after the time warp. Generation is S == E.

1. **Beats** (`backend/src/edit_fx/beats_llm.py`): one pydantic-ai call per clip returns word ids, never
   timestamps: hook, emphasis, number, punchline, swear, emotional turn, payoff, build, with strength, tone, an
   emoji, a sticker label, the clip's mood and sensitivity. Code validates them (`beats.validate_llm_plan`), adds
   every listed swear (`edit_fx/swears.txt`, several languages), and caches them in Redis for 14 days and on the
   clip's sidecar. Beats don't depend on intensity, so changing intensity never calls the model again. Without a
   model, on timeout or error, `beats.heuristic_beats` runs and PostHog gets `edit_beats_fallback {reason}`.
2. **Resolver** (`edit_fx/resolver.py`): pure and deterministic (seeded by task and clip). Beats + intensity +
   style → a typed `FxPlan` of cues, with the budgets above. Reads only the committed asset manifest.
3. **Renderer** (`frontend/src/lib/fx/core`): one TypeScript library draws every cue on a canvas. The studio
   preview and browser export use it directly; the worker runs the same code as an esbuild bundle
   (`backend/src/fx_runtime/fx-core.js`, regenerate with `cd frontend && pnpm run build:fx-core`; CI checks it for
   drift) under deno with `@napi-rs/canvas`.
4. **The render chain** (`backend/src/media/fx_chain.py`), three processes joined by pipes:
   - **P1** ffmpeg: the framing head and B-roll (and a reaction PiP and matte) → raw RGBA frames.
   - **P2** deno (`src/fx_runtime/main.ts`): draws each output frame through the warp, the hook title included.
   - **P3** ffmpeg: the ASS layer (classic captions, recap point labels), the audio mix (`media/fx_audio.py`: voice
     through the warp, bleeps, SFX and music bus, ducking, static loudness) and the encode.
   - Face tracking (YuNet with landmarks) and the person matte (PP-HumanSeg) run before P1 only when the plan
     or the hook title needs them (`media/fx_analysis.py`), from one decode at 6 fps.
   - The 720p cap still runs last.
5. **Storage**: `tasks.edit_style` holds the choice; `generated_clips.fx_meta`
   (`{fx_level, timing, intensity, template}`) marks FX clips. The clip's `.source_map.json` sidecar holds
   `edit_style`, `edit_beats`, `edit_fx` (the plan, S time), `face_track`, `fx_level`, `edit_fx_warp` (the warp
   actually rendered) and `vertical_layout`. Re-renders reuse the plan when nothing changed, re-resolve on a
   style change, and re-anchor beats after a cleanup change without calling the model.
6. **Jobs**: generation renders FX clips in the video worker. Settings re-renders and FX clip edits run as
   `fx_clip_job` (the API answers 202 and the client polls `GET /tasks/{id}/clip-jobs/{job_id}`). The studio's
   "Plan edits" runs `edit_fx_plan_job` on the AI worker, its face/matte analysis `editor_fx_analysis` on the
   video worker, and its server export renders the document's `fx` through the same chain.

### The deno and napi canvas requirement

The worker image pins deno 2.9.8 (`ARG DENO_VERSION` in `backend/Dockerfile`) and bakes the `@napi-rs/canvas`
0.1.80 cache into `DENO_DIR` at build time; renders run `--cached-only --frozen` with narrow permissions and need
no network. A source checkout runs `cd backend/src/fx_runtime && deno install --frozen` once. Check a machine
with `cd backend/src/fx_runtime && deno task selftest` (one JSON line ending in `"ok":true`). Details:
[render-worker.md](render-worker.md#dynamic-edits-renderer-deno).

### Observability

- PostHog `fx_render {intensity, template, fx_level, cues, warp, duration_ms, p1_ms, p2_ms, p3_ms, analysis_ms,
  library_fingerprint}` for every FX render.
- PostHog `fx_render_failed {stage, level, error, template, intensity}` on each downgrade; `stage` is one of
  `p1`, `p2`, `p3`, `q`, `output`, `timeout`, `setup`, `analysis`, `av_check`.
- PostHog `edit_beats_fallback {reason}` when heuristic beats replace the model. No transcript text is sent.
- Logs (Axiom) carry `fx_render_failed level=… error=…` (generation) and `fx_render_failed surface=studio
  stage=…` (studio export), plus warnings that degrade one part of a render: `fx_bus_failed` (no SFX or music),
  `fx_analysis_failed` (no face or matte), `fx_atlas_unavailable` (no emoji). Render timings are in the
  `fx_render` event.

## Assets and self-hoster drop-ins

Built-in assets are committed in `backend/src/fx_assets/`, so nothing is downloaded at run time and everything is
licence-clean:

- `sfx/`: 74 sound effects, 4–8 takes per sound id, CC0 1.0. They are edited from Freesound.org uploads whose
  authors released them under CC0; each source's licence was checked on its Freesound page.
  `SOURCES-CC0.txt` lists every file with its Freesound URL, author, original title and the processing.
- `music/`: 53 curated, seamlessly looping beds, 7–8 per mood, CC0 1.0 (from Free Music Archive, Freesound and
  OpenGameArt tracks whose authors released them under CC0). `music/SOURCES-CC0.txt` lists every bed with its source
  page, author and original title; `music/beds.json` adds tempo, key and the date each licence was checked.
- `emoji/`: an atlas of Noto Color Emoji glyphs (`build_emoji_atlas.ts`, OFL-1.1).
- `manifest.json`: hashes, durations, levels and each sound's source (`write_manifest.py`).

The bleep is not a file; the mixer generates its 1 kHz tone.

**Sound files.** 48 kHz 16-bit PCM WAV, mono or stereo as recorded, sample peak −6 dBFS, at most 2.5 s, named
`sfx/<sound>-<name>.wav`. The mixer reads that format directly, without ffmpeg, and every browser decodes it. The
mix levels (`SFX_TRIM_DB`, the bus gain) assume the −6 dBFS peak, and each sound's takes are loudness-matched to each
other. All 74 files take 10.8 MB.

**Which take plays.** A cue's `variant` names one take: variant *v* is the *v*-th entry for that sound in
`write_manifest.py` `SFX_FILES`. The resolver sets it to `seed % takes` from the built-in manifest, and sounds added
in the studio or by the AI edit use variant 0, the most neutral take. The file therefore depends only on the
document, and the server mix, the studio preview and the browser export play the same one. Riser takes alternate
1.5 s and 2.5 s, so an even variant is a 1.5 s riser and an odd variant a 2.5 s one. A variant past the end falls back
to variant 0.

**Adding a sound.** Add a CC0 (or self-made) file under `sfx/` at the format above, with a row in `SOURCES-CC0.txt`.
Append its name to the sound's list in `SFX_FILES`; appending keeps every existing variant number. Then run
`cd backend && .venv/bin/python scripts/fx/write_manifest.py`. The tests check the hashes and levels, that every file
has a CC0 provenance row, and that the resolver fixtures use the manifest's take counts. When a count changes, also
update `SFX_VARIANTS` in `scripts/fx/gen_resolver_goldens.py` and `tests/fixtures/fx_resolver/builders.py`, then
regenerate the goldens with `gen_resolver_goldens.py --accept`; only the variant numbers in them change.
`scripts/fx/synth_sfx.py` keeps the older synthesised set as a code-only alternative that writes drop-ins.

**Music beds.** `music/<mood>-<name>.mp3`, ids like `aggressive-tlc` (the file name without `.mp3`): aggressive, chill,
funny and hype have 8 beds, dark, dramatic and inspiring 7. Each is one whole-bar loop cut from its track, its seam
crossfaded, set to −20 LUFS integrated (the level the mixer assumes for built-ins, `assets.BED_MEAN_DB`) and encoded as
MP3 128 kbit/s CBR, 48 kHz stereo, with its gapless header; the 53 files take 61 MB. An MP3 smears its first and last
milliseconds, so each file holds 100 ms of the loop's own audio before and after the loop: the manifest's `loop_start`
(0.1 s) says where the loop begins and `loop_seconds` how long it is (to the sample), and the server mix, the studio
preview and the browser export all tile `[loop_start, loop_start + loop_seconds)` and never play the padding. Plans
and documents saved before these beds name the old one-per-mood ids (`hype`, `chill`, …); those play the first
built-in bed of the bed's mood (`mix_bed_file`, `bedAsset`).

**Adding or changing a bed.** The beds are imported from a curated library kept outside the repo (`catalog.json` plus
48 kHz 16-bit stereo `masters/<id>.wav`): `cd backend && .venv/bin/python scripts/fx/import_music.py
/path/to/music-library` rewrites `music/` (the MP3s, `beds.json`, `SOURCES-CC0.txt`), then `write_manifest.py`
measures and hashes them (`make fx-assets MUSIC_LIBRARY=/path/to/music-library` runs both). Only CC0 tracks are
accepted. The resolver fixtures read the beds from the manifest, so regenerate the goldens with
`gen_resolver_goldens.py --accept`; only the music lines (bed id, level) and plan hashes may change.

`GET /fx/assets` serves the manifest with content-addressed URLs so the studio preview plays the same files.

To use your own sounds or music, drop files into `backend/fx_library/` (mounted at `FX_LIBRARY_DIR`), like fonts:

- `sfx/<sound>[-<name>].{wav,mp3,m4a,ogg}`, where `<sound>` is one of `whoosh impact pop riser record_scratch boom
  cash_register ticker slap stamp glitch tape_stop`.
- `music/<mood>-<name>.{m4a,mp3,wav}`, where `<mood>` is one of `hype chill dramatic funny inspiring dark aggressive`.
  A mood's drop-ins replace all of its built-in beds; a drop-in plays from its first sample (it has no `loop_start`)
  and loops with a short crossfade.

Drop-ins never change the plan. At mix time they replace the built-in files for that sound or mood (picked by
`seed % count` in file-name order) and are gain-normalised. Risers are first narrowed to the drop-ins closest in
length to the cue's built-in variant (1.5 s or 2.5 s), so give risers of both lengths. **Every render machine must mount the same library**, or previews and renders differ; each
render logs a `library_fingerprint`. Only add files you have the rights to publish.

## Performance

Budget (60 s clip at 1080×1920): stage-2 wall time with effects, **including face/matte analysis**, at most
**2.0×** the classic render; over **3.0×** blocks the release. Intensity off with a classic template runs no new code
(+0). The deno compositor's target is a mean of 35 ms per frame or less (p95 80 ms or less).

`backend/scripts/fx/bench_fx.py` renders a synthetic 60 s 1080×1920 clip (152 transcript words) through
`VideoService.create_single_clip` for each mode and reads the chain's own timings (`FxResult.timings`, the same
numbers the worker logs as `fx_chain … timings=`). Beats come from the heuristic, so the beat model's time (≤6 s,
concurrent with B-roll planning) is not included. The process is pinned to 4 cores, the per-chain budget.

```bash
cd backend && .venv/bin/python scripts/fx/bench_fx.py --runs 2 --json bench.json   # ffmpeg + deno 2.9.8 on PATH
```

Measured 2026-11-14 on the development laptop (x86_64, 32 cores, pinned to 4; ffmpeg n9.0.2, deno 2.9.8), median
of 2 runs:

| Mode | Template | Cues | Stage 2 (s) | × classic | FX chain (s) | Analysis (s) | P1‖P2‖P3 (s) | P2 draw (s) | P2 ms/frame |
|---|---|---|---|---|---|---|---|---|---|
| Classic (off) | default | – | 44.0 | 1.00 | – | – | – | – | – |
| Style only | marker_circle | 9 | 62.2 | 1.41 | 45.4 | 4.6 | 40.6 | 30.2 | 16.8 |
| Subtle | slam | 17 | 60.5 | 1.37 | 44.9 | 4.6 | 40.0 | 29.1 | 16.2 |
| Bold | slam | 44 | 77.4 | 1.76 | 61.5 | 4.6 | 56.6 | 45.5 | 25.3 |
| Max | slam | 74 | 70.0 | 1.59 | 55.2 | 4.5 | 50.4 | 40.5 | 22.5 |
| Bold, behind head | behind_head | 41 | 69.3 | 1.57 | 54.1 | 4.7 | 49.1 | 40.8 | 22.7 |

P1, P2 and P3 run as one pipe, so their times overlap; "P2 draw" is the compositor's own drawing time, which
dominates the pipeline. The audio bus and voice loudness pass take 0.1–0.2 s together. Every mode is within the
2.0× budget and none is near the 3.0× blocker. The compositor averages 16–25 ms per frame, inside the 35 ms target.

Caveats:
- These numbers predate the cut to three listed styles: the bench still renders the unlisted `marker_circle` and
  `slam` templates (`bench_fx.py`). Re-run it with the listed styles before quoting it for the rollout gate.
- The laptop was busy during the run: a Docker image build, a sample-render job and a pytest run shared the
  machine (load average 6–15). The first run was 15–35 % slower than the second (bold 93.8 s against 61.1 s,
  classic 47.1 s against 40.9 s); the second, quieter run gives 1.22–1.55×.
- The source is a lavfi test pattern, so there is no real face: analysis decodes and runs detection but finds
  nothing, and behind-head text has no matte windows. With a real speaker, analysis also builds the matte (budget
  ≤3 s).
- **Still owed for the rollout perf gate, before the flag opens beyond admins:** the same bench on sora (render worker) and on the
  prod host, a 10-clip FX settings re-render job, and the arm64 napi canvas load.

## Rollout

The migration `backend/src/migrations/sql/20261114_0001_dynamic_edits.sql` adds `tasks.edit_style`,
`generated_clips.fx_meta` and seeds the `dynamic_edits` flag off on web and iOS with admins bypassing.

1. Deploy order: migration → video workers, render workers and the AI worker (new image) → API → frontend.
   Roll back the other way, flag first.
2. Dark: admins only. Watch `fx_render`, `fx_render_failed`, `edit_beats_fallback`. If more than 1 % of renders
   fall back, set `FX_CHAIN_MODE=file`.
3. Open on web in `/admin` → Feature Flags (`web_enabled`). Set the tours' `releasedAt` in
   `frontend/src/lib/feature-tours.ts` to that day.
4. iOS: `ios_enabled` once an iOS build sends `edit_style`. Server-rendered clips already carry the effects for
   every client.

With the flag off, a request that turns effects on (an active intensity, or an animated template) returns 403
`DYNAMIC_EDITS_NOT_AVAILABLE` before any charge. Clips that already have effects keep working: re-renders, font
changes and studio exports. Feature tours: `dynamic-edits` (create screen) and `dynamic-edits-studio`, both
`requiresFlag: "dynamic_edits"`; the announcement loop is rendered by
`backend/scripts/fx/render_tour_media.py` through the real chain.

## Release checklist

Tick these in order before opening the flag on web (Phase 2). Items marked *done on the branch* were checked
when the feature branch was finished; repeat them if the code changes.

**Before merging**

- [ ] Full backend suite with PostgreSQL and Redis, `FX_CHAIN_REAL_DEPS=1 pytest tests/unit/fx_media` with
      deno 2.9.8 on the path, and the drift checks `scripts/fx/gen_fixtures.py --check`,
      `gen_resolver_goldens.py --check` and `gen_scene_fixtures.py --check` (*done on the branch*).
- [ ] Frontend `pnpm run lint`, `pnpm exec tsc --noEmit`, `FX_REQUIRE_FIXTURES=1 pnpm run test`, and the
      Playwright polish config including `e2e/editor-effects.spec.ts` and `e2e/fx-parity.spec.ts`
      (*done on the branch*).
- [ ] `pnpm run build:fx-core` leaves `backend/src/fx_runtime/fx-core.js` unchanged (`git diff --exit-code`).
- [ ] `deno task test` and `deno task selftest` in `backend/src/fx_runtime`; MCP tests.
- [ ] Off stays legacy: `test_dynamic_edits_legacy_pins.py` and `test_dynamic_edits_off_is_legacy.py` green.
- [ ] The `fx-media-ffmpeg61` CI job green on GitHub (only emulated locally so far).

**Image and machines**

- [ ] The Docker image builds with deno 2.9.8 and the baked npm cache; `docker run --rm --network none <image>
      .venv/bin/python -m src.media.fx_selftest` passes on amd64 (and arm64 if a render host uses it).
- [ ] Bench on sora and on the prod host: `backend/scripts/fx/bench_fx.py` (see Performance). Stage 2 with
      effects over 3× the classic render blocks the release; over 2× needs a decision.
- [ ] One real generation at each intensity (Subtle, Bold, Max) plus a style-only clip, watched and listened to
      by a person: sound effects, music beds, ducking and bleeps.
- [ ] A real clip's colours compared with its classic render (no shift, BT.709 tags kept).

**Rollout**

- [ ] Deploy in the order under Rollout: migration, workers (video, render on sora, AI), API, frontend.
- [ ] Phase 0 (dark, admins): `fx_render_failed` and `edit_beats_fallback` under 1 % of `fx_render` for a few
      days; Axiom `fx_chain` timings within budget.
- [ ] Phase 1: studio and clip edits with effects, admins only. Clip jobs finish (no `WORKER_STOPPED` or
      `WORKER_UNAVAILABLE` in normal operation).
- [ ] Phase 2: `web_enabled` on in `/admin`, the tours' `releasedAt` set to that day, the CHANGELOG **New**
      entry. Reactions stay self-host only until the GIPHY legal review.
- [ ] iOS: `ios_enabled` only once an iOS build that sends `edit_style` is approved.
- [ ] Rollback drill known: flag off first, then the API, then workers; `FX_RUNTIME=off` is the kill switch
      (clips render at level safe).

## Fallbacks

Each FX render steps down a ladder and records `fx_level` (`create_optimized_clip`, `editor_fx_render`):

1. **full**: the three-process chain. If P1 failed with B-roll, retry once without B-roll. If the audio and video
   lengths disagree with a warp, retry without the warp.
2. **safe**: no drawing stage, no warp, no picture effects; animated captions are burned with their classic
   fallback and the hook title as heavy outlined type ([Hook title](#hook-title)); sound effects, music and bleeps
   stay. Also what `FX_RUNTIME=off` gives.
3. **none**: the classic render, exactly as before.

A planning error renders the clip without effects rather than losing it. Transcripts without word timing get
`timing="approx"` (no bleeps, fixed highlighter speed) or `"none"` (only music, grade, grain, progress bar, loop
ending and transitions).

## Known gaps

- Safari and Firefox measure text slightly differently from Skia, so the studio preview is approximate there.
- The studio's browser export refuses documents with a time warp, reactions or behind-the-head accents; use the
  background export.
- The browser export has no loudness stage or 720p cap.
- Settings re-renders and FX clip edits are asynchronous (202 and poll), where classic ones answer directly.
- Combined studio clips need "Analyse face" again before face-anchored effects are exact.
- Bleeps for non-English swearing are best effort (the word list plus the model; an English word the list does not
  confirm is never bleeped). Behind-the-head edges are soft.
- Drop-in libraries are not mirrored through the bucket yet.
- The studio's caption timeline lists fixed word groups while look captions draw phrase groups (see Intensity looks).
- The keyword per caption group and the emoji's end are planned on the resolver's copy of the phrase groups; a
  caption edit in the studio that changes the words is re-planned on the next re-resolve.
- Generation drops automatic zooms on panel layouts (`reaction`, `stack`, `overlap`); the studio does not know the
  layout kind yet, so a studio re-resolve of such a clip can add reframes across the panels (delete them, or turn
  Zooms off for that clip).
- The hook title card's Chinese, Japanese and Korean text uses the system Noto fonts at regular weight, lighter than
  the Latin type.
- A title with no room beneath a head (a close-up that reaches the captions) stays at the top and covers the forehead.
- Classic (libass) captions are not known to fx-core: a title beneath a head only stays above 64 % H to clear them.
- There is no "editorial" flavour yet (the study's Iman Gadzhi look: text 5 frames early, blur-in, film-burn flashes).
- Caption rows follow the vertical layout when it has one: a clip that letterboxes throughout (mostly fitted
  content) puts captions under the picture, about 0.8 H, lower than the references' 0.50–0.57 H. B-roll, POV and
  handheld footage fill the frame instead (a full-height crop), so most clips no longer letterbox.

## Troubleshooting

| Symptom | Check |
|---|---|
| No Edits tab or animated caption styles | `GET /tasks/edit-styles` → `enabled`. The flag must be on for the platform (or the user an admin with bypass) |
| 403 `DYNAMIC_EDITS_NOT_AVAILABLE` | The flag is off for this platform and the request turns effects on |
| Clips render but say "simplified" | `fx_render_failed` in PostHog: its `stage` says where. `p2`/`setup` usually means deno or the napi cache: run `deno task selftest` on that machine |
| Every render falls to safe | `FX_RUNTIME=off`, deno missing, or the image was built without the baked `DENO_DIR` cache |
| Renders time out | `FX_RENDER_TIMEOUT_SECONDS`, CPU contention (each chain is three processes; `max_jobs` bounds them) |
| Effects ignore the moments you'd expect | `edit_beats_fallback`: the beat model failed or has no key, so heuristic beats ran |
| No speed ramps or freezes | Expected: they are never placed automatically. Hand-made ones need `FX_WARP=on` |
| No bleeps | Approximate timing, intensity Off, or censor set to "Leave as is" |
| Preview sounds different from the render | Drop-in libraries differ between machines: compare `library_fingerprint` in `fx_render` |
| A clip shows an old animated style (slam, counter, …) | It was made with an unlisted style; it keeps rendering, but the style can't be picked anew |
| Behind-the-head text in front of the speaker | No person found in that window (the matte model found too little), or a studio clip without "Analyse face" |
