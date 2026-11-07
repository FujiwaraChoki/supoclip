# Announcement renders

Seamless 5 s loops (120 frames at 24 fps, 1600×800) for feature-tour announcements,
rendered with Cycles on the GPU. Every motion is driven by the loop phase
`(frame - 1) / FRAMES`, so the last frame flows into the first.

| Script | Status |
|---|---|
| `prism-wall.py` | **In use**: the workflows announcement (`frontend/public/tours/workflows.*`). A wall of glass prisms turning in a wave, slicing a five-colour field into shards. |
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
