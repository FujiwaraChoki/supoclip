# THE TAKE BUREAU — Quality checklist (every Short)

**[auto]** = checked by the render (console + `qc.json`). **[eye]** = needs a person watching with sound.
Don't publish with an open item.

## Story
- [ ] [eye] **First 3 s:** the subject is clear by 2 s; the first frame already has speech and a caption.
- [ ] [eye] **Hook → buildup → payoff → clean end** all present; nothing that changes the speaker's meaning.
- [ ] [eye] **Ending:** last line is a finished sentence and a finished thought; no trailing word from the next one.
- [ ] [eye] No filler restarts left in; no repeated clause unless it is the point.

## Captions
- [ ] [auto] `qc.json` → every caption has `"issues": []` (or each flag reviewed and accepted).
- [ ] [auto] Faces found in every piece (`faces found in N/N pieces`); else check those pieces by eye.
- [ ] [eye] **Face/text:** no caption over the eyes, nose or mouth in any shot, including during push-ins.
- [ ] [eye] **Stability:** no text jumps within a shot; no reflow or line-break changes while a chunk is up.
- [ ] [eye] **Sync:** each chunk appears with its first word (±2 frames); none lingers into the next speaker.
- [ ] [eye] Caption text matches the audio word for word (ASR errors fixed via `caption_drop` / EDL).
- [ ] [eye] Highlights are meaningful, ≤ 1 per chunk, roughly every 2–3 chunks.

## Attribution
- [ ] [auto] No `attribution_over_face` / `attribution_collision` flags.
- [ ] [eye] `YOUTUBE.COM/@THEGAPKING` readable on a phone for the whole video and clear of the platform's top icons.

## Picture and audio
- [ ] [eye] No shot is soft from over-zooming; nothing shakes; cuts feel motivated.
- [ ] [eye] No clicks or chopped syllables at cuts (listen on headphones).
- [ ] [auto] Loudness within 1 LU of −14 LUFS (`loudness_within_1lu`).

## File
- [ ] [auto] `qc.json → output.ok: true`: H.264, 1080×1920, yuv420p, AAC audio.
- [ ] [auto] Duration as planned (printed `total`); between 10 and 90 s.
- [ ] [eye] Plays start to end in a normal player; first frame isn't black.
