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
