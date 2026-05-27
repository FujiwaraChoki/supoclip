# Spike: Full-clip speaker reframing for vertical clips (ENG-5595)

**Goal:** Replace the current single static crop / teleporting 2-speaker pan with proper
speaker-aware reframing across the **entire** clip.

## DECISION (post-council): hard-cut, not smooth pan

We will **hard-cut to the active speaker (Opus Clip style)**, not smooth-pan. Rationale: cheaper,
jitter-free, no torch/ASD model, and the native editorial grammar for podcasts. A 6-perspective
council flagged that smooth panning risks "seasickness"/lag and that the heavy LR-ASD+torch stack
is avoidable.

**Linchpin finding:** supoclip already runs AssemblyAI with `speaker_labels=True` and extracts
`utterances` (speaker, start, end ms, per-word speaker) in `task_service` — so **speaker diarization
already exists**. It just isn't threaded into the reframe stage (`render_reframed_clip_ffmpeg` only
gets the clip path today).

**Consequence:** `build_pan_expression`'s nested `if(lt(t,...))` **step function** is the *correct*
primitive for cuts (it was only a bug for *panning*). Reuse it, fed by diarization-derived segments.

### Implementation (torch-free) — `vertical` IS hard-cut reframing
1. Thread existing AssemblyAI utterances into the reframe stage.
2. Build a whole-clip cut timeline: merge consecutive same-speaker utterances, enforce a **minimum
   segment duration** (debounce rapid back-and-forth), hold a wider shot on crosstalk/short interjections.
3. Map each diarized speaker label → face zone (per-region lip-motion aggregated over that speaker's
   utterances). Generalise beyond the current 2-face limit.
4. Render hard cuts via the existing step expression (no smoothing/SavGol/torch). Even-dim clamp,
   center-crop fallback.

**Format naming (decided with product):**
- `vertical` (the default) now performs hard-cut speaker reframing, with a **cheap single-speaker
  early-out → static centre crop** (so single-speaker clips render exactly as before and pay no extra
  cost; only multi-speaker clips change). The earlier separate `vertical_cut` format was folded into
  `vertical`.
- `horizontal` renames the old `original` (keep-source 16:9) to match BN template terminology;
  `original` is kept as a backward-compatible alias.
- Follow-up: map the BN-selected template orientation (`horizontal.video` / `vertical.video`) →
  `output_format` in the `SUPOCLIP_CLIP_VIDEO` skill (+ `sync-skills`).

**Dropped vs original plan:** smooth pan, Savitzky-Golay smoothing, vendoring LR-ASD + torch.

### Increment 2: per-shot reframing for edited clips

The first cut only handled locked 2-shots: `detect_speaker_reframe_plan` bailed when a
clip had `>2` scene cuts (its fixed left/right zones, sampled once from the first 12 s,
mis-frame after a camera change). Real edited podcast clips cut frequently, so they always
fell back to the static crop. Diagnosed on prod job `baeb9911` (4/8/10 scene cuts per clip
→ `Skipping speaker reframe` → static).

Fix — route by scene-cut count in the `vertical` path:
- **≤2 cuts** → existing whole-clip 2-zone motion + diarization logic (locked 2-shots).
- **>2 cuts** → `build_per_shot_cut_plan`: segment at scene-cut *times*
  (`detect_scene_cut_times`), detect faces *within each shot*, frame the weighted face
  centre of that shot (lands on the single person the editor cut to; centres between faces
  for a wide shot), and **hard-cut `crop_x` between shots** via a generalized step
  expression (`build_step_x_expression`). Near-equal / sub-0.6 s segments coalesce
  (`merge_x_segments`) to avoid micro-cuts; falls back to static when framing never moves
  or there are >60 segments. The pan/split modes still bail on >2 cuts.

Cost stays bounded: per-shot face *detection* only (the ffmpeg motion pass runs only on the
≤2-cut locked-2-shot path). Single-speaker / no-diarization clips still early-out to static.

### Increment 3: don't frame the gap in a wide two-shot

Increment 2 framed each shot at the *weighted face centre*. For a held two-shot where both
people are too far apart to fit in 9:16, that average lands on the **gap between them**, so
the crop shows neither face (diagnosed on prod job `35c41936`, source `84a92cfd`: a wide
two-shot framed on the couch between the two hosts).

`pick_shot_crop_x` replaces the plain average: it median-splits a shot's faces into left/right
clusters and, when the two cluster centres are **more than ~one crop-width apart** (can't both
fit), frames the **higher-weight (larger/closer) cluster** instead of the midpoint — with
near-ties broken toward the previous shot's framing for continuity. Shots with one person, or
two people who *do* fit, still frame the weighted centre. Guarantees the crop lands on a real
face, never the empty gap.

### Increment 4: speaker-select within a held two-shot

Increment 3 framed a wide two-shot on the more *prominent* person for the whole shot — not
necessarily the active speaker. Increment 4 closes that: when a shot is a held two-shot
(`cluster_two_face_regions` finds two distinct, far-apart clusters), run the lip-motion pass
**scoped to that shot** (`measure_region_motion` with `-ss/-t`) and reuse the locked-2-shot
machinery — `map_speaker_labels_to_sides` + `build_speaker_timeline_from_utterances` — to
**cut between the two people by who's talking**. One speaker holding the two-shot → frame the
higher-motion (talking) one. So the per-shot path *contains* the locked-2-shot logic as the
single-shot case.

Cost stays bounded: the lip-motion pass runs only on held-two-shot shots, capped at 8 per clip
(beyond that, or on motion/mapping failure, it falls back to increment 3's dominant framing).
Single-person shots — still the common edited pattern — never trigger a motion pass.

### Increment 5: hold framing through weak/no-face shots

A wide two-shot where both subjects look *down/away* (e.g. reading) yields almost no detections,
so the framing fell back to the geometric **centre** — which on a wide two-shot is the empty gap
between the people (diagnosed on prod job `aa760c17`: a shot logged only `Detected 1 reliable
face center` and framed the side table). Now, a shot with fewer than `min_shot_faces` (3)
detections is treated as **unknown** and `fill_weak_shot_framing` **holds a neighbour's framing**
— carry the previous framed shot forward, backfill leading weak shots from the first framed shot
— instead of snapping to the centre. Per-shot decisions are now logged
(`vertical (per-shot): … two-shot speaker-cut | weak → hold neighbour | faces=N → x=…`) so the
framing path is diagnosable from CloudWatch.

**Guards to keep (council):** min-segment debounce, single-speaker → static crop, model-asset
integrity check (#15 pattern), per-clip static-crop escape hatch, validation gating each step.

---

## Original research (smooth-pan investigation — retained for reference)

> Note: the smooth-pan/ASD architecture below was the *first* investigation. We pivoted to hard-cut
> (above). Kept for context and in case a future iteration wants smooth motion.

## 1. Current state (what we're replacing)

All in `backend/src/video_utils.py`:

- **`detect_faces_in_clip`** (~577) — samples frames every ~0.5 s, runs OpenCV DNN
  (SSD/ResNet) primary + Haar fallback, returns a flat `List[(x, y, area, confidence)]`
  with **no per-frame timestamps** — temporal information is discarded.
- **`detect_optimal_crop_region` / `build_static_vertical_filter`** (~415 / ~1455) — the
  default `vertical` path. Computes **one static crop** = area×confidence weighted average
  of all faces over the first `min(duration, 12.0)` s. Two-host podcast → average lands
  centre → both hosts clipped, crop never moves. This is the "stays centered" bug.
- **`detect_speaker_reframe_plan`** (~1353) — the `vertical_pan` / `vertical_split` path.
  More advanced but limited:
  - Handles **exactly two** faces (`cluster_two_face_regions` splits on median X).
  - ASD is a **frame-differencing motion proxy** in ffmpeg
    (`tblend=all_mode=difference,signalstats` YAVG per region) — cheap but any facial
    movement, not just speech, scores as "speaking".
  - **`build_pan_expression`** (~1338) emits a nested `if(lt(t,...))` **step function** —
    the crop *teleports* between two fixed X positions. No easing → hard jump cuts. **This
    is the single biggest quality gap.**
  - Bails at `>2` scene cuts; only samples the first 12 s.

MediaPipe is **unusable** in our image (cp311 stub wheel + protobuf<5 vs pydantic-ai
conflict — see Integrity-Labs/supoclip#15), so Google AutoFlip is off the table. OpenCV DNN
model files also shipped as empty placeholders historically (the #15 root cause).

## 2. Recommended architecture (CPU-only, cost-bounded)

```
                 ┌─ scene-cut detection (count_scene_cuts) → per-shot processing
input 16:9 clip ─┤
                 └─ extract 16kHz mono audio
        ▼
[1] DETECT: YuNet (cv2.FaceDetectorYN, ONNX bundled in OpenCV) every 1–3 frames
        ▼      (replaces broken DNN SSD/ResNet; Haar stays as last-resort fallback)
[2] TRACK: IoU linking (thr ~0.5) + linear interpolation across gaps, per shot
        ▼      (ByteTrack only if 3+ people cross)
[3] ASD:  single speaker → skip.  Multi → LR-ASD (MIT, ~1M params, CPU/ONNX)
        ▼      → per-track per-frame speaking score.  Fallback: existing
               lip-motion (tblend diff) × audio-RMS correlation heuristic
[4] TARGET CENTER per frame = bbox centre of the argmax-speaking track
        ▼
[5] SMOOTH: hysteresis / dead-zone (hold-and-cut, min dwell ~0.5 s)
        ▼      → Savitzky-Golay forward-backward (offline, zero-lag) or OneEuro
               → decimate to ~tens of keyframes → clamp to even px
[6] RENDER: ffmpeg  crop=W:H:x='<piecewise-linear lerp>':y=0,
                    scale=1080:1920:flags=lanczos,setsar=1
        └─ "can't frame everyone" → letterbox fit, or existing vstack split-screen
```

## 3. Ranked decisions

| Stage | Recommendation | Fallback | Why |
|---|---|---|---|
| **Detector** | **YuNet** (`cv2.FaceDetectorYN`, ~337 KB ONNX bundled in OpenCV, ~77 FPS@640×480 CPU) | Haar cascade | Fixes the empty-model-file crash, faster than SSD/ResNet, no external download, no MediaPipe |
| **Tracker** | **IoU linking + linear interpolation** per shot (what LR-ASD ships) | ByteTrack (pure NumPy, MIT) for 3+ crossing people | Faces are large/slow in talking-head content; Kalman unneeded. Avoid CSRT (too slow) |
| **ASD** | **LR-ASD** (MIT, ~1M params, 94.45% mAP, sub-5 ms/frame CPU; PyTorch→ONNX) | existing lip-motion × audio-RMS heuristic | Already structured as detect→track→per-frame speaking score; smallest + most accurate |
| **Smoothing** | **dead-zone/hysteresis (hold-and-cut)** + **Savitzky-Golay forward-backward** (offline, zero-lag) | OneEuro filter | Highest-leverage change. Offline processing lets us use non-causal zero-lag smoothing |
| **Render** | keep `crop` `x'='`/`y'='` expr, change **step → piecewise-linear**, decimate keyframes, even-dim clamp | `sendcmd` if expr too long or dynamic zoom needed | `t`/`n` valid in crop x/y (not w/h); single-pass, no re-encode hacks |

**Biggest single win:** replace `build_pan_expression`'s nested `if(lt(t,...))` step function
with a hysteresis-gated, Savitzky-Golay-smoothed, piecewise-linear interpolation, and lift the
2-speaker / 12 s-window limits via full-clip detect→track→ASD. Converts "centered on the
average / teleporting between two fixed X's" into smooth, speaker-aware cinematic motion.

## 4. Reference projects

- **`gauravzazz/smart-reframe`** — closest analog (face + audio ASD, OneEuro smoothing,
  asymmetric fast-out/slow-in pan, ffmpeg). Study its smoothing/ASD orchestration. (Uses
  MediaPipe for detection — we swap YuNet.)
- **`Junhua-Liao/LR-ASD`** (MIT) and **`Junhua-Liao/Light-ASD`** — ASD engine; see
  `Columbia_test.py` for the detect→IoU-track→interpolate→score pipeline to mirror.
- **`kamilstanuch/Autocrop-vertical`** — letterbox-when-subjects-too-spread fallback logic.
- **Google AutoFlip** — canonical "hold vs pan vs cut" camera-path reference (conceptual only).

## 5. Key pitfalls

- **ffmpeg expression length / parse cost** — a per-sample nested `if`/`lerp` chain over a long
  clip gets huge & slow. Decimate keyframes *after* smoothing (dead-zone collapses most);
  emit a keyframe only where slope changes. Tens, not thousands, of segments. Use `sendcmd`
  if it still blows up.
- **Even dimensions** — yuv420p needs even crop W/H **and** even X/Y. Keep `round_to_even` /
  `clamp_even` on the expression output / keyframe values.
- **LR-ASD defaults to CUDA** — swap to CPU (or replace its S3FD with YuNet to keep one detector).
- **Render cost on ECS** — detect every 1–3 frames at downscaled resolution; keep the
  center-crop fallback when detection fails.

## Sources

ByteTrack (arXiv 2110.06864); Light-ASD (arXiv 2303.04439) & LR-ASD (IJCV 2025, MIT,
github.com/Junhua-Liao/LR-ASD); OneEuroFilter (casiez/OneEuroFilter); Savitzky-Golay
(Wronski 2021); smart-reframe (gauravzazz/smart-reframe); Autocrop-vertical
(kamilstanuch/Autocrop-vertical); Google AutoFlip; OpenCV YuNet (opencv/opencv_zoo);
FFmpeg filter docs (crop t/n/pos, sendcmd, zoompan).
