# Automatic vertical framing

`vertical` now chooses between a tracked subject crop and the complete source
frame on a blurred 9:16 canvas. `vertical_fit` always preserves the complete
source on that canvas; it is available in the generation UI, REST settings and
MCP. `original` still keeps the source aspect ratio.

## Decision policy

The existing sequential decode supplies 480-pixel-wide frames at 3 fps to YuNet
and a local OpenCV context analyzer. No additional model download, GPU, provider
credentials or paid inference is needed.

- Small or missing faces do not justify discarding the rest of the scene.
- Multiple significant subjects that extend beyond a vertical crop favor fit.
- Distributed motion outside the proposed crop favors fit. Sparse optical flow,
  forward/backward consistency and a robust affine transform compensate for
  camera movement when there is reliable support across the image.
- Text rows outside the crop and small peripheral presenters favor fit. The
  latter protects desktop/game footage even when its text is too small to read.
- Faces wider than the crop favor fit rather than cutting off the subject.
- Repeated evidence is aggregated within each detected shot. A single missed
  face does not make a talking-head shot change layout. Camera cuts reset crop
  tracking; sustained rapid motion is not treated as a sequence of edits.
- Empty, failed or partial analysis preserves the full frame. Fit-only footage
  uses a simpler two-branch compositor with no unused tracked-crop branch.

The planner logs intervals and decision reasons under `Vertical scene layout`.
The thresholds in `backend/src/media/scene_context.py` are conservative visual
heuristics, not semantic recognition of a sport or tracking of its ball. Small
text is preserved spatially but may still be hard to read on a phone. Cuts are
localized to the sampling interval (about 0.33 seconds); very short shots and
subtle edits can be missed. Uncertain or busy footage can therefore stay wide
longer than a human editor would choose.

## Interviews

A sit-down interview cuts between a wide two-shot and single close-ups. The two
people in the two-shot sit too far apart for one 9:16 crop, which used to make
that shot, and through the hysteresis above the whole clip, a letterboxed fit.
`backend/src/media/interview_layout.py` composes such clips shot by shot
instead (layout kind `interview`, logged as `Interview layout`):

- **pair**: two people of similar size, side by side, too far apart for one
  crop. Each gets a face-centred 1080×960 panel (face about a third of the
  panel, eyes near its upper third), stacked with the left-hand person on top.
  Three or four people in a row (a table, a booth, friends walking) are divided
  at the widest gap between them: the left-hand people share the top panel and
  the right-hand people the bottom one (reason `group`), so everyone is on
  screen once and the frame is full. A panel on someone at the frame edge stops
  at the neighbour's face. A face the frame itself cuts off is not a second
  person, and a third face seen in under three quarters of the shot (a
  passer-by) is left out when the row cannot be divided with it.

  **When the panels are shown.** Inside one camera shot the layout changes between stacked and single as often
  as the picture does, without waiting for a cut (`_settle_pair`). The rules, in order:

  1. *Everyone once.* No face is ever inside both panels. If one is (a handheld camera closing in on one person,
     so both windows find the same head), that stretch is a single crop. This is never overridden.
  2. *Both sides in the picture.* A panel whose people are not found is kept only if its part of the picture is
     still the same place (compared on the cut probe's thumbnails): a person who turned away in a tripod shot is
     still sitting there, however long the detector loses them. If the picture there changed, the camera left them and the stretch is a single crop on
     whoever is on screen.
  3. *No flicker.* A person lost for at most 0.7 s in the middle of a two-shot, or 0.34 s at a cut, is a missed
     detection and the panels hold. Stacked panels shorter than 1 s between single crops stay single.
  4. *On the moment.* Shots with two people are sampled again at 10 fps (clips up to 3 minutes), so a change lands
     within about three frames. The unknown time between two samples goes to the single crop: panels never run
     past the last sample that showed both sides, and start on the first that does.
  5. *The crop keeps its person.* A crop follows its face with the eased path; where that would leave the face
     more than 22 % of the crop away (a whip pan, someone crossing the frame), it follows the face directly. A
     single crop cut out of a two-shot starts from where its person was in the panels.
- **single**: one person, or two close enough to share a crop. A tight
  head-and-shoulders 9:16 crop (face about a quarter of the frame height).
- **cover**: live footage without a close subject (a plate of food, a product,
  a street, a distant figure). A still full-height 9:16 crop on a steady face,
  however small, or else on the picture's subject (`focus_center`: edges and
  saturated colour per column, weighted towards the middle). The layout report
  lists it as `single` with reason `cover`. "Live" means the shot moves (the
  scene plan's motion thresholds, or a mean frame change of 10 where a whip pan
  defeats the motion measurement) and shows a paragraph in at most 35 % of its
  samples.
- **fit**: an on-screen paragraph (a tweet, a document, an explainer graphic:
  two or more aligned lines of text, which book spines and foliage rarely
  form), a still picture without people (a chart, a screenshot), five or more
  people, or a group that is not a row (a call grid, faces of very different
  size). A paragraph in a handheld shot of people is a sign behind them, not a
  graphic: the people are composed as usual.

People are face clusters that follow their person through a pan, and two
clusters that never appear together a face width or so apart are one person
who moved. A person needs two samples; a pair needs both people on screen
together in 30 % of the shot.

Crops follow their face with the tracked crop's dead-zoned spring (still on a
tripod, followed when handheld), never enlarge the source more than 2.25×
(2.6× for a panel kept clear of a neighbour sitting close), and keep headroom
for hair and caps. Every return to the same camera setup gets the same zoom.

Layouts switch only at edits. A full-rate 64×36 decode finds each cut on its
exact frame (straight cuts and one-to-three-frame whips, not pans or flashes),
including cuts the 3 fps analysis missed; a lasting change in how many people
are on screen also divides a shot, unless the one face left sits where one of
the pair sat: that is the same two-shot with someone turned away in profile, so
it stays stacked instead of swinging a tight crop to that edge of the room and
back. The graph decodes the source once: each
composition `trim`s its own time range and the pieces are joined with
`concat`, so the stored plan replays in the studio like any other.

It takes over only when a clip has at least 1.5 s of a real two-shot (a divided
group does not count: a three-host podcast keeps speaker framing), after the
reaction layout. Two low faces side by side with nothing else (webcam tiles)
keep the call stack. Clips without a two-shot (single talking heads, slides,
sports) keep the policy above. Diarized speaker cuts are not used in an
interview clip: both people stay on screen while the camera shows both.

## Reproduce the video checks

With backend dependencies installed and `ffmpeg`/`ffprobe` on `PATH`, run from
the repository root:

```sh
python scripts/validate_reframing.py --download --render --variants
python scripts/validate_reframing.py --render --variants --input /path/to/local.mp4
```

Use the backend virtualenv's Python. The first command uses local `yt-dlp` to
download nine publicly accessible samples: two basketball videos, a presenter,
a four-person interview, desktop browsing, a scientific diagram, a game,
pedestrians and animated dialogue. Source URLs are in the script; Wikimedia
file pages carry attribution and license details. Videos are not committed.

Five controlled variants exercise desktop/sports webcam overlays, portrait
footage, camera movement and presenter → desktop → sports → presenter cuts.
Local inputs can be added without persisting signed URLs. The supplied 28.1s
court clip was also analyzed, rendered and visually reviewed.

Artifacts go to ignored `temp/framing-validation/`:

- `index.html`: review gallery with playable outputs and source/output frames.
- `report.json`: expected layouts, previous/new fit fractions, intervals,
  measurements and analysis timings.
- `*-vertical.mp4`: actual 1080×1920 production renders.
- `*-contact.jpg`: five source/output comparisons per video.

The harness asserts expected layouts where labeled, checks output dimensions
and duration, and decodes every rendered frame. Unlabeled local, animated and
portrait cases require visual review; a successful encode is not a claim of
semantic correctness. The test corpus is a regression set, not an accuracy
benchmark over arbitrary videos.

Offline unit tests cover the decision boundaries, camera compensation,
distributed motion, similar-color edits, missed detections, decoder failure,
and scene boundaries. Real FFmpeg regression tests check source-edge retention,
audio, layout switches and immediate crop resets. A mobile Playwright test
checks selection and submission of `vertical_fit`.

## Validation run — 2026-10-23

All 15 cases (nine downloaded sources, five controlled variants and the supplied
clip) were rendered, fully decoded and visually reviewed. All 12 automatically
labeled layout checks passed. The supplied clip remained full-frame throughout;
the previous face-presence policy would have cropped it throughout. Basketball
practice changed from 22% to 100% full-frame, while the presenter and simulated
camera-pan presenter remained cropped. The mixed sequence selected crop → fit
→ fit → crop at the expected shot boundaries.

- Backend: 1,029 unit/integration tests passed in Linux with isolated PostgreSQL
  and Redis. Existing configured coverage gates passed (89.36%). The optional
  Whisper/Torch transcription runtime was not installed in this test container.
- Frontend: 396 tests passed; lint and TypeScript checks passed.
- Browser workflows: 33 passed, including the mobile framing selection and
  submitted payload. One existing settings font-selector keyboard-focus test
  failed (`font info stays open in a real selector and preserves the selected
  font`). No font-selector implementation was changed for this feature.
- Native Windows: all 40 focused context/render regression cases passed with
  local OpenCV, FFmpeg and ffprobe.

These results do not exercise paid transcription/LLM providers or measure
semantic accuracy beyond this corpus.

When integrating reaction layouts, detected context risks take priority over
inset stacking. Crop-safe shots retain reaction layouts and their higher-detail
face pass; missing context evidence retains the existing reaction fallback.

After rebasing onto the reaction/speaker-layout changes on main, all 944 backend
unit tests passed. The focused context/reaction/render suite passed all 55 tests.
