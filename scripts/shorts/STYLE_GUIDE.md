# THE TAKE BUREAU — Editing Style V4

Preset: **TAKE_BUREAU_CLEAN_KINETIC_V1** (`styles/take_bureau_clean_kinetic_v1.json`)
Engine: `edit_short.py` + `kinetic_captions.py` (enabled by `"style"` in the EDL).
Productions 001/002 have no `"style"` key and render exactly as before.

Clean, premium, energetic business-podcast editing. Precision over effects.

## 1. Reference analysis

Reference: Dan Martell podcast Short (TikTok 7690709321864301845), local copy `Download (1).mp4`.
Style reference only: none of its footage, watermark, branding or audio is reused.

Measured with ffprobe/ffmpeg on the file (2026-11-02):

| Property | Measured |
|---|---|
| Duration / size / fps | 32.0 s, 576×1024, 29.97 fps, H.264 + AAC 44.1 kHz stereo |
| Loudness | −13.7 LUFS integrated, LRA 4.5 LU |
| Shot changes (scene > 0.2) | 19: 0.87, 3.34, 4.27, 7.34, 9.78, 11.04, 12.51, 13.75, 15.28, 16.48, 18.65, 20.15, 22.26, 23.12, 24.69, 26.59, 27.43, 29.36, 30.46 s |
| Shot length | mean ≈ 1.6 s, range ≈ 0.8–3.1 s |
| Pauses | no silence ≥ 0.2 s at −40 dB anywhere: dead air is cut out |
| Caption changes | ≈ 35 distinct chunks in 32 s (≈ 0.9 s each), 1–4 words |

Seen by inspecting frames (20 mid-shot frames + frame strips around cuts):

- **Camera:** one speaker, one set. Alternates medium close-up (head fills the top third, eyes ≈ 25 % down)
  and wider shots (hands and mic visible, eyes ≈ 30–35 % down). Many changes are punch-ins on the same
  angle. The mic sits in the lower half of almost every shot.
- **Captions:** heavy condensed uppercase sans, single line, centred. White base with one coloured keyword
  per chunk at most (seen: yellow, cyan, red, magenta, green, orange). Soft dark shadow, no box.
  Centre at ≈ 55–58 % of frame height (upper chest, below the chin). Text is ≈ 4.3 % of frame height,
  longest chunks ≈ 73 % of the width.
- **Highlights carry meaning:** "UNDERSTAND", "UNDER 25", "PAID", "MONEY", "FUTURE", "DEFENSIBLE".
- **Animation:** chunks appear at full size; no scale pop is visible at 30 fps sampling. The energy comes
  from fast chunk swaps, not from motion graphics.
- **Transitions:** hard cuts. One cut (4.27 s) has ~2 frames of motion blur; sharpness at five other cuts
  shows no blur, so it is not a pattern and is not copied.
- **Structure:** no title card or intro. The first word is on screen at 0.1 s and the first cut at
  0.87 s. Ends right after a complete sentence ("…JUST HAVE TO START"). No outro.
- **Not verified:** exact font, animation curves, any music bed or sound effects. Absence of silence could
  be tight cutting or a low bed; it was not separated or identified. Nothing here depends on it.

## 2. Typography and colour

- Font: **Anton** (OFL, `backend/fonts/Anton-Regular.ttf`), the closest licensed match in the repo to the
  reference's condensed weight. Not a claim that the reference uses Anton.
- Uppercase, 150 px at 1080×1920 (≈ 4.3–4.6 % of height), never below 96 px. Letter spacing 1.
- Chunks: at most 3 words / 18 characters. Break at `. , ? ! ; :` and at every cut.
- One line by default. Two lines only when one line would need < 96 px. Line split and size are fixed
  before the chunk appears (measured from the TTF), so text never reflows.
- White fill, 5 px black outline at ~38 % opacity, blurred (`\blur2`), 2 px shadow. No opaque boxes.
- Entry: 105 % → 100 % in 70 ms. Subtle on purpose; set `pop_from_pct: 100` for a dead-still entry.
- Hold: a chunk stays until the next starts, at most 0.6 s past its last word.

### Highlight colours (by meaning, never random)

| Colour | Hex | Use for |
|---|---|---|
| yellow | `#FFE600` | the core idea, the key noun (default) |
| green | `#3DFF5A` | money, growth, wins, "yes" |
| cyan | `#2EE6FF` | numbers, data, time, ages |
| pink | `#FF3DD8` | surprise, emotion, provocation ("SPICY") |
| red | `#FF3030` | warnings, problems, negatives ("PROCRASTINATION") |

Max one highlighted word or phrase per chunk (adjacent highlighted words count as one). Extra ones are
rendered white and logged in `qc.json`. Aim for roughly one highlight every 2–3 chunks; if everything is
coloured, nothing is.

## 3. Face-safe layout

All in output pixels (1080×1920).

- **Target:** caption centre at 57 % height (reference upper-chest band).
- **Face:** each piece is sampled at 20/50/80 % through its real output framing; faces are detected with
  the project's YuNet model (Haar fallback). The box is widened for the slow push-in.
- **Rule:** caption top ≥ face bottom (chin) + 36 px. The caption moves down from the target when needed,
  then shrinks in 6 px steps down to 96 px, staying inside the safe band.
- **Platform safe band:** captions between 10 % and 73 % of height; column centred and kept clear of the
  right-hand action buttons (6 % left / 14 % right margins → ≈ 770 px max width).
- **Unresolved:** anything still colliding is rendered at the closest legal spot and flagged
  (`face_collision_unresolved`, `no_face_detected`, `too_wide`, `below_min_size`,
  `attribution_collision`, `attribution_over_face`) in the console and `qc.json`. Flags need a human look.
- The microphone is not treated as an obstacle (the reference sets captions over it too).

## 4. Campaign attribution

Campaign: The King's Court — The Gap King. Text `YOUTUBE.COM/@THEGAPKING`, whole video.

- Barlow Condensed Bold 44 px, letter spacing 3, white at 78 % opacity, thin outline.
- Top centre, 8.5 % height: above the head in every framing we use, below the platform's top icons,
  and far from the caption band so it does not read as part of a caption.
- Checked against faces every render (`attribution_over_face`). If a framing puts a face that high,
  re-frame the shot (lower `cy`) rather than moving the attribution.
- Change it per campaign in the style JSON (`attribution.text`), or set it to `""` for none.

## 5. Camera and framing

- Medium close-up for strong statements; wider crop for setup and breathing room. Alternate them; do not
  stay in one framing longer than ~3.5 s unless the moment needs it.
- Target 1.2–3.5 s per shot. The reference averages 1.6 s; that is a ceiling on energy, not a cut quota.
- Jump cuts remove dead air and repeats. Inside one camera shot a jump cut alternates a 12 % punch-in so it
  reads as intentional.
- Slow push-in ≈ 1.2 %/s, max 5 % per piece. No shakes, whips, spins or random zooms.
- Don't upscale the source past ~1.6× (`max_source_upscale`). The 1080p source gives 1.0–1.45 zoom room in
  practice. Beyond that, choose a wider framing.
- Keep the chin above ~62 % of the height in close-ups, so captions can sit at the chest without shrinking.
- Never cut inside a word: cuts snap to the quietest 10 ms within ±80 ms and to frame boundaries.

## 6. Audio

- Speech only. −14 LUFS integrated, −1.5 dBTP, 70 Hz high-pass, 12–18 ms fades at every cut so joins don't click.
- No music or SFX unless licensed and added on purpose. Never the reference's audio.
- Never generated speech, never words the speaker did not say, never re-ordered words that change meaning.

## 7. Editorial standards

Production 001: 30.2 % chose to watch, 12 s average view on a 39 s video. The opening and pacing must improve
without bending the speaker's meaning.

Every clip: **HOOK → BUILDUP → PAYOFF → CLEAN END**.

- The subject is clear within 1–2 s. Open on the most striking complete line (cold open allowed, as in 001).
- Meaningful information early; cut filler ("you know", restarts, repeated clauses).
- Keep the speaker's intended meaning. A cold open must not change what the line means in context.
- End right after a fully finished sentence and thought. Never end because a time limit was reached.
- No forced CTA or loop when it weakens the ending.

## 8. Good and bad

| Good | Bad |
|---|---|
| `I THINK 90%`: one line under the chin, the number in cyan | a 6-word, two-line chunk across the mouth |
| One red `PROCRASTINATION` at the turn of the argument | every third word coloured |
| Hard cut from wide to close-up on the punchline | zoom-in on every sentence |
| Attribution small at the top, the same all video | attribution under the captions, read as part of them |
| Ending on "…that's the system." | ending mid-sentence at 0:59 |

## 9. Running it

```bash
docker cp scripts/shorts supoclip-worker:/tmp/shorts
docker exec supoclip-worker /app/.venv/bin/python -I /tmp/shorts/edit_short.py /tmp/shorts/<edl>.json
```

EDL additions: `"style": "styles/take_bureau_clean_kinetic_v1.json"` (relative to the EDL),
`"highlights": {"WORD": "cyan"}`, optional `"caption_drop"`, and `"preview": true` for short tests.
Output: the MP4, `captions.ass` and `qc.json` (caption placements, flags, MP4 checks, loudness) in `workdir`.
Then go through `QC_CHECKLIST.md`.
