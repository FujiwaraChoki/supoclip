# Announcement renders

Seamless 5 s loops (120 frames at 24 fps, 1600×800) for feature-tour announcements,
rendered with Cycles on the GPU. Every motion is driven by the loop phase
`(frame - 1) / FRAMES`, so the last frame flows into the first.

| Script | Status |
|---|---|
| `prism-wall.py` | **In use**: the workflows announcement (`frontend/public/tours/workflows.*`). A wall of glass prisms turning in a wave, slicing a five-colour field into shards. |
| `ribbon-gate.py` | **In use**: the welcome tour for new accounts (`frontend/public/tours/welcome.*`). A twisting satin band of five colours streams in from the left and passes behind a row of tall 9:16 glass panels that slice, refract and disperse it: one long video becoming clips. |
| `slice-loaf.py` | **In use**: the free-plan card above the composer (`frontend/public/billing/start-plan.*`, 900×900). A long glass loaf of colour slides through a glowing 9:16 gate; every cut leaves a tile with its own clip on its face (subject, hook bar, captions) that turns to the camera and marches off, with sparks at each cut. |
| `black-friday-studio.py` | For the Black Friday 2026 sale. The finished files are on R2, not in the repo: `https://pub-314b53c7628942c2901e78d15a8ce0b7.r2.dev/black-friday-2026/black-friday-studio.{mp4,webm,webp} ("Pro and Creator plans"; `-v2` says "Yearly Pro and Creator plans", from the day the sale was yearly-only)`. A creator edits a clip in a dark studio cubicle under a neon strip cycling through the five colours. The right half is left dark for the copy in `black-friday-copy.html` (Geist, the app's dark-mode brand orange), set over the frames after rendering. |
| `silk-cascade.py` | Kept for a future announcement (`renders/silk-cascade.*`). Hundreds of satin ribbons wrung through one knot, leaving as five colour bands. |
| `liquid-chrome.py` | Kept for a future announcement (`renders/liquid-chrome.*`). A rolling sea of oil-slick chrome under neon light. |

The finished files in `renders/` aren't served. Copy one into `frontend/public/tours/` to use it.

## Rendering

```bash
blender -b -P assets/blender/announcement/<name>.py -- OUT_DIR [SAMPLES] [FRAMES_SPEC] [RES_PERCENT]
```

`CYCLES_DEVICE=CPU` renders on the CPU (handy for test stills). Then encode, two-pass
constrained bitrate keeps the fine detail under ~1.5 MB:

```bash
ffmpeg -framerate 24 -i OUT_DIR/frame_%04d.png -c:v libvpx-vp9 -b:v 1750k -pass 1 -an -f null /dev/null
ffmpeg -framerate 24 -i OUT_DIR/frame_%04d.png -c:v libvpx-vp9 -b:v 1750k -pass 2 -an out.webm
ffmpeg -framerate 24 -i OUT_DIR/frame_%04d.png -c:v libx264 -b:v 2200k -pass 1 -an -f null /dev/null
ffmpeg -framerate 24 -i OUT_DIR/frame_%04d.png -c:v libx264 -b:v 2200k -pass 2 -pix_fmt yuv420p -movflags +faststart -an out.mp4
ffmpeg -i OUT_DIR/frame_0001.png -quality 60 out.webp
```

### Copy set over the frames

`black-friday-studio` renders no text. Screenshot `black-friday-copy.html` at 1600×800 with a
transparent background (Playwright: `page.screenshot({ omitBackground: true })`) to `copy.png`,
then add `-loop 1 -i copy.png -filter_complex "[0:v]format=rgb24[bg];[bg][1:v]overlay=0:0:format=auto,format=yuv420p" -frames:v 120`
to each encode above.
