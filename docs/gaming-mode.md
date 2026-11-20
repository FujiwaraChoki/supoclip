# Gaming mode

Gameplay footage gets its own 9:16 layout: the streamer's camera in a panel at the top, the gameplay cropped to
the middle of the screen below it, and the HUD elements the crop would lose (minimap, health, ammo, kill feed)
cut out of their corners and placed back on the edges of the gameplay panel. Detection is automatic and
deterministic (OpenCV on ~90 small frames of the clip, no model call).

Status: first version, behind `GAMING_LAYOUT` (default `off`). Code: `backend/src/media/gaming_layout.py`.
Tests: `backend/tests/unit/test_gaming_layout.py`.

## Switch

| `GAMING_LAYOUT` | Behaviour |
| --- | --- |
| `off` (default) | The analysis never runs. Plans, filters and reports are what they were before. |
| `auto` | Each vertical clip is checked; gameplay gets the gaming layout, everything else its usual layout. |

Read in one place, `gaming_layout.gaming_layout_mode()`. Anything else in the variable counts as `off`.

## Integration

- `reframing.describe_vertical_layout`: one marked block after the portrait-source early return calls
  `choose_gaming_layout(path, width, height, duration)`. A plan returns kind `gaming`; `None` continues into the
  existing layouts untouched. `build_vertical_filter_plan` copies the new `gaming` report key.
- `video_utils.FX_PANEL_LAYOUTS` includes `gaming` (no whole-frame zooms across the panel seam), and
  `_interview_hook_kwargs` keeps a gaming hook title on the seam.
- `editor_service.PRESERVED_LAYOUT_KINDS` includes `gaming`, so the studio bakes the composed frame like a
  reaction clip. Not yet exercised end to end.

## Detection

The clip is decoded once at 640 px wide, at most 3 fps and 90 frames. Per pixel:

- **Edge persistence**: share of samples with a Sobel edge (magnitude > 90) at that pixel. An edge present in
  >= 60% of samples is drawn by the game or the stream, not by the world. Horizontal and vertical line
  persistence (>= 50%) are kept separately for the camera border.
- **Change rate**: share of consecutive sample pairs in which the grey value moved by more than 6. A pixel above
  25% belongs to moving footage.

A clip is gameplay when all of these hold:

| Signal | Threshold | Measured |
| --- | --- | --- |
| Moving share of the frame outside camera and HUD | >= 0.5 | gameplay 0.71-1.0; podcast 0.09-0.45; screen recordings 0.0 |
| HUD regions | >= 3, or >= 1 with a facecam | LoL 4-6, Elden Ring 6, Warzone 8-9, Minecraft 1 + camera |
| Persistent edge pixels outside the camera | >= 0.4% of the frame | gameplay 0.7-9%; vlog 0.03-3% |
| Samples with a real face (>= 0.4% of the frame) outside the camera | <= 25% | gameplay 0-18%; vlog 72-100%; podcast 100% |
| Samples in which the detected layout is on screen | >= 70% | |
| Samples | >= 16 | |

The reason for a "no" is logged (`static_picture`, `people_on_screen`, `no_hud`, `layout_changes`, `too_short`).

**HUD regions.** Persistent edge pixels (camera rectangle removed) are merged when closer than 1.9% of the width
and boxed. A box is dropped when it has fewer than 30 edge pixels, covers more than 12% of the frame, or sits in
the central 40% x 40% (that is the world). A small persistent mark at the exact centre is the crosshair: it is
recorded, never cut out.

**Facecam.** A face that stays in one spot for >= 50% of samples and wanders less than
0.6 of its own size (see "Several cameras in one clip" for how spots are tracked). Around it the camera rectangle is searched as four persistent straight lines (or the frame
edge): each side must be a line for >= 70% of its length, the shape between 0.9 and 2.5 wide per high, at most
16% of the frame. Of the rectangles with clean sides (>= 92%) the largest wins, so shelves and door frames
behind the streamer do not close a smaller one. 4 px (at 640) are trimmed from each side to lose a drawn frame.
No rectangle, no facecam: a face alone is never enough.

**Action focus.** The gameplay crop is the full-height centre of the screen: the crosshair of a first-person
game and the character a follow camera keeps in the middle. It moves sideways, by at most 10% of the width, only
to keep the camera inset out of the gameplay panel. Motion-weighted tracking was looked at and left out: in the
samples the world moves everywhere while the character is the calmest part of the picture, so a motion centroid
points away from the action, and a static crop cannot jitter.

Ideas evaluated and not used: a median-difference "moving" test (LoL's scrolling map scores 0.02-0.37; the
change rate separates cleanly); "the camera's surroundings move independently" (the fixed face plus four border
lines already decides it); the tightest camera rectangle (picked furniture behind the streamer).

## Layouts

Canvas 1080 x 1920.

**Facecam + gameplay** (camera found). Camera panel on top at full width, 520-680 px high: the whole camera
when its shape allows, otherwise cropped around the face. Gameplay panel below (1240-1400 px), a full-height
centre crop of 41-47% of the screen width.

**Gameplay only** (no camera). The gameplay crop fills the frame (9:16, 31.6% of the width), as today's crop
does, plus the HUD cut-outs.

**HUD cut-outs.** An element at least 90% inside the crop (50% for one centred on the screen, whose middle is
what matters) stays where it is. The others are cut from the source and overlaid on the gameplay panel:

- same height as on the screen, so top elements stay on top and bars at the bottom;
- slid inwards: left elements keep their distance from the left edge, right elements from the right;
- at the gameplay scale, capped at 1.3x (a 9:16 crop scales the world 1.78x; the HUD stays readable, not huge);
- side elements at mid height (36-68%) are left out: chat, notifications, weapon lists;
- colliding cut-outs shrink together down to 60%, then the smaller gives way; nothing covers the middle of the
  panel; cut-outs take at most 34% of it.

**Captions and hook title.** Reported with the existing fields:

- `caption_y`: the first row from 0.78, 0.76, 0.80, 0.74, ... that crosses no HUD element (cut-out or still in
  the picture) and not the middle of the gameplay panel. Below the crosshair, above the bottom HUD, never on
  the face.
- `hook_center_y`: the seam under the camera panel (about 0.31-0.35). Without a camera it is not set and the
  title keeps its usual place at the top. On the FX path the gaming hook stays on the seam (the interview rule
  that moves it low does not apply: gaming captions never sit on the seam).

**Cuts and scene changes.** The layout is planned once per clip, so a jump cut inside one game session changes
nothing. When the stream shows something else for a while (full-screen camera, a cutscene, an editor's zoom),
the samples in which under 45% of the HUD edges or the camera border are present mark that span. Spans of 1.5 s
or more show the whole frame over a blurred fill (the regular "fit"), switched inside the same filter graph;
each switch is moved onto the exact frame by decoding the seconds around it at 30 fps. If the detected layout
holds for less than 70% of the clip, the clip is not treated as gameplay.

## Report

`describe_vertical_layout` returns kind `gaming`, mode `complex`, one self-contained `filter_complex` ending in
`[vout]`, `caption_y`, `hook_center_y` (with a camera) and one new key:

```json
"gaming": {
  "facecam": [x, y, w, h] | null,          // source crop shown in the camera panel
  "facecam_panel": [0, 0, 1080, h] | null,
  "gameplay": [x, y, w, h],                // source crop
  "gameplay_panel": [0, y, 1080, h],
  "hud": [{"source": [x, y, w, h], "at": [x, y, w, h]}],   // cut-outs: source rect, canvas rect
  "fallback_ranges": [[start, end]],       // seconds shown as the whole frame
  "crosshair": true | false
}
```

The clip's `.source_map.json` stores kind, filter and mode as for every layout, so the studio replays it. The
`gaming` geometry is in the render report only; `save_clip_vertical_layout` does not persist it yet.

## Fallbacks

- Switch off, portrait source, analysis error, fewer than 16 samples: regular layout.
- Not classified as gameplay: regular layout, with the reason in the log.
- Classified, but no camera rectangle: gameplay-only layout.
- Layout on screen for 70-100% of the clip: gaming layout with whole-frame spans.

## Override

Automatic by default once the switch is `auto`. A user control belongs next to the layout/format choice on the
create screen ("Gaming layout: Auto / Off") and as a task option passed down to `describe_vertical_layout`, with
the same choice in the studio's layout menu (beside "Auto layout"). Not built.

## Known limits

- The decision is per clip, from the clip's own frames. A profile of the whole source (camera and HUD positions
  found once, reused by every clip) would be steadier and would let a 10-second clip with a hidden HUD inherit
  the layout.
- Stream overlays look like HUD: sponsor logos, a hand camera and alert boxes are cut out or kept like game
  elements. Nothing ranks elements by importance.
- HUD that comes and goes (kill feed, pop-ups) is below the persistence threshold and is not cut out.
- A HUD element cut by the crop edge stays partly visible next to its cut-out when the cut-out is smaller.
- A keyed-out streamer (no rectangle) is shown as head and shoulders with the game behind them, and only when the
  game draws a HUD. Round cameras are not found.
- A dark camera beside a dark, slow cutscene has no visible border and no calmer inside: it is still missed
  in that clip, which keeps the regular layout.
- A picture that stands still beside a camera is fitted whole under the camera, as a strip, unless what
  changes in it sits in one part of the frame. Slides with a webcam get the same layout.
- A logo or avatar the gameplay crop still shows stays where it is, over the game.
- A cutscene without a HUD in which one character's face stays in one place for 80 % of the clip, 1.4 times the
  size of the streamer's, is still read as a person being filmed and keeps the regular layout.
- A keyed-out streamer whose head is larger than 0.6 % of the frame and nowhere near its edge is not recognised.
- The camera panel upscales a small inset (often 350-500 px wide in a 1080p source) to 1080 px: it is soft.
- A sports broadcast with three or more persistent graphics and no detected faces would pass as gameplay.
- Games without a HUD, cutscenes and menus are not recognised and keep today's layout.
- The gameplay-only crop is as tight as today's 9:16 crop; wide-view games (MOBA, strategy) lose their flanks.
- Hook title and captions are placed for the camera layout; on the gameplay-only layout the hook can cross a
  top-left cut-out for its four seconds.

## What the first production day showed (2026-11-20)

518 clips from one afternoon were checked against their logged decisions. Four mistakes came up more than once,
and each has a rule now (the second and third shipped first, in #497):

- **A face that never moves is a picture.** A channel logo with a face in it, or a drawn avatar, was taken for a
  keyed-out streamer and filled the top panel. A head's own pixels change between samples; a picture's do not.
  Over 30 samples or more (`FACE_MOTION_MIN_SAMPLES`), the share of the face box whose pixels changed in 5 % of
  the sample pairs (`FACE_PIXEL_CHANGE_RATE`) measures 0.60-1.0 for a streamer sitting still and 0.0-0.13 for a
  picture; the line is `MIN_FACE_MOTION` = 0.3. With fewer samples a calm moment cannot be told from a picture
  and the face counts as a person, as before. A pictured face is no camera and is left out of the count of
  people on screen. The clip then gets the game-only layout when the game draws three HUD elements, and the
  regular layout otherwise.
- **A keyed-out streamer sits small or at the edge.** A person filmed in the middle of the frame, with a sign
  in view, passed as a keyed-out streamer over a HUD. A head larger than `KEYED_FREE_FACE_AREA` (0.6 % of the
  frame) now only counts at the edge: the head-and-shoulders rectangle within `KEYED_EDGE_REACH` of a side, the
  head nearly touching the top, or the body running out of the bottom. In the logged production rectangles,
  every large keyed-out head sat at an edge.
- **Game characters are not hosts.** The rule that rejects a small inset beside a larger, still face (a reaction
  video, a podcast) fired on story games: the player's character seen from behind, a conversation, a cutscene.
  It now needs the other face in `HOST_MIN_PRESENCE` (80 %) of the samples at `HOST_MIN_SIZE` (1.4) times the
  camera's face, alive, and it does not apply while the game draws a HUD (two persistent elements or a
  crosshair; one is a watermark).
- **A pictured face becomes a sticker.** With no camera on screen, the logo or avatar is the channel's mark.
  `sticker_region` finds it: the patch of pixels that never change, near persistent lines, that holds the face
  (flat sky does not change either but has no lines). Its outline is kept as a small mask
  (`STICKER_GRID` cells, joined into rectangles), drawn into the filter graph itself, so the layout needs no
  file beside it and replays on any machine. The clip gets the game-only layout when one HUD element is found
  beside the mark, and the mark is set at the top of the frame, on the side it came from, once the hook title
  has gone (`STICKER_FROM_SECONDS`), clear of the HUD cut-outs. When the crop still shows the picture where it
  is, no sticker is added: it cannot be taken out of the game.
- **A still picture's subject fills the panel.** In a still picture every edge persists, so detail alone often
  spans the frame and the picture was fitted as a thin strip between blurred bands. `still_focus` also looks at
  the pixels that change at least twice (`STILL_ACTIVITY_RATE`), when they cover 0.4 % of the frame or more
  (a clock or a counter does not count). If one window as wide as the panel shows at full height holds three
  quarters of them (`STILL_SUBJECT_SHARE`) and its share of the picture's lines, that window is the subject
  (`focus_fill`): `still_crop` crops to the panel around it, without blurred bands, and steps off the camera
  sideways when the frame has the room, so the camera is not shown twice. A menu with chat on one side and a
  counter on the other, or a blurred backdrop shimmering behind a menu, stays fitted whole.

### Later the same day

- **A HUD sits at the edge.** Without a camera, a sticker or a crosshair, two of the persistent elements have to
  lie within `HUD_ANCHOR_REACH` (2 %) of the frame's edge (`MIN_ANCHORED_HUD_REGIONS`). Labels on a drawn map
  panned across the screen were three "HUD elements" and the map was cropped as gameplay.
- **A keyed-out streamer who leaves.** There is no rectangle to check, so the head itself says whether the
  streamer is on screen: samples without a face in the drawn rectangle (median over seven) become fallback
  spans. The gameplay crop also slides off a keyed-out head that sits at the side of the frame, and off a
  pictured logo at the crop's edge, so neither shows twice or is cut in half.
- **Stickers are small and whole.** A patch that runs out of the search window, or is larger than
  `STICKER_MAX_FACE_AREAS` face boxes, is a HUD panel or a wordmark beside the picture, not the picture.
- **The host rule** needs the other face in 90 % of the samples (`HOST_MIN_PRESENCE`).
- **Captions and the game's own subtitles** are handled outside this module: `media/screen_words.py` reads the
  lower part of the picture while somebody speaks and leaves out the captions of a voice whose words are
  written there (words of five letters or more; shorter ones are on any screen with text). It runs for
  gaming layouts only.
- **Text a crop would cut** is the regular layout's business (`scene_context._cover_x`): a shot whose captions,
  title card or labels the full-height crop would cut keeps the whole frame.

Still open: a menu with content at both sides of the screen stays a strip under the camera (it cannot be
shown larger without cutting one side); a logo in the middle of the gameplay crop stays where it is; a
cutscene without a HUD where one character holds the frame for 90 % of the clip keeps the regular layout; in
heavily edited videos a game character can be taken for a keyed-out streamer while the creators are off screen.

## Streams of story games (GTA 6)

Checked on 2026-11-19 against the GTA 6 videos people were submitting (single-streamer playthroughs with a camera
inset). What changed for them:

- **The camera is found among the game's own faces.** A photoreal game shows faces all the time. The camera face is
  the one spot with a face of one size in most samples (`find_fixed_face`); faces beside it never count for or
  against it. Faces down to 0.03 % of the frame are read (analysis at 960 px), so a small inset is found.
- **A camera inset over a moving picture is a stream.** With a camera rectangle found, the clip needs no HUD
  (cutscenes and story missions have none) and other faces on screen do not rule it out.
- **Camera hidden.** Streams hide the camera during cutscenes. Those spans show a full-height crop of the middle
  of the picture instead of a letterboxed strip, and the layout is used when the camera is there in at least 35 %
  of the clip.
- **The camera's own decorations** (a name plate or ornament on its frame) are not lifted into the gameplay panel.

Gameplay without a camera keeps the regular layouts.

## Several cameras in one clip

Checked on 2026-11-20 against every production clip rendered since the layout went live (497 clips of 91 tasks,
117 of them viewed) and fixed for the cases found there.

- **Tracks, not one face.** `fixed_face_tracks` follows every head that stays in one spot at one size. A spot is
  seeded by detections that repeat almost exactly; each detection then joins the spot it fits best, so two cameras
  that sit close together in different segments stay two tracks, and a streamer who leans forward stays one.
- **Timeline.** `camera_timeline` gives every sample to one track: the most seen when two are on screen, the
  camera around a gap when none is (it is hidden for a cutscene), half each between two different cameras. Runs
  shorter than three samples are stray detections and join the longer run beside them.
- **One profile per camera.** `analyze_camera_segments` cuts the clip where the camera changes, to the frame
  (`find_cut`: the largest frame-to-frame difference between the two samples), and classifies each camera from its
  own samples; a camera with fewer than 16 is sampled again at up to 8 fps. Each profile carries `active`, the
  seconds it applies to, and its own fallback spans inside them.
- **Layout.** `build_camera_layouts` draws the full-height centre crop as the base and lays each camera's layout
  over it while that camera is on screen. Captions and the hook title keep one place for the clip: the row that
  crosses the least HUD weighted by time, and the seam of the camera shown longest. The report lists the cameras
  under `segments` with their `between` ranges.
- **When it is used.** At most four cameras, each on screen for 2.5 s or more. Several layouts replace the single
  one only when they show a layout for at least 10 % more of the clip, and for at least 35 % of it.

This covers compilations of different streamers, and one streamer whose camera changes size or place (an editor's
zoom, a larger camera during cutscenes).

Also changed after that review:

- **Any fixed face can be the camera.** The whole-clip path tries every track seen in half the samples, not only
  the busiest one, which a game character in a long cutscene used to take.
- **Borders without a line.** A camera side is also the place where the picture stops moving (`CHANGE_STEP`): the
  room behind a streamer stands still while the game beside it changes, whatever their colours.
- **A face seen seldom** (12 % of samples) still marks a camera when the rectangle's border is on screen in 70 %
  of the samples.
- **Keyed-out streamers.** A small still head (at most 2 % of the frame) without a rectangle, over a game with a
  HUD and no other faces: the top panel shows head and shoulders (`virtual_camera_rect`).
- **Not a camera:** a rectangle whose inside does not move (a channel logo, a drawn avatar), and an inset beside a
  larger face that also sits still for the clip (a reaction video, a podcast): `people_on_screen`. Both rules
  were tightened after the first production day, see below.
- **Side-by-side comparisons** (`split_screen`). A straight line down the middle of the screen covering 75 % of
  the height in a sample (`centre_line_score`: splits measure 0.8-1.0, gameplay at most 0.5), for 1.5 s or more
  and 20 % of the clip, with nobody on screen and no camera. While the screen is split the left picture is stacked
  over the right one, each cropped to 9:8 around its middle (`build_split_layout`); in between the full-height
  middle of the picture fills the frame. Starts and ends are moved onto the frame (`refine_split_ranges`). The
  report has `split` with both crops and the `between` ranges.
- **A game that stands still beside a camera.** With a camera rectangle the picture needs 15 % moving instead of
  50 % (`MIN_MOVING_FRACTION_WITH_FACECAM`): the map, a menu with an animated background, a slow cutscene.
  Production clips refused as static with a camera measured 0.16-0.5 in 8 of 17 cases.
- **A second look for the camera** (`find_facecam_rect(patient=True)`), only when the first finds none: borders
  present in 35 % of samples instead of 50 %, a camera up to 13 face widths wide (8 on the first look), more
  border candidates including both ends of a thick line (a neon sign or an ornament drawn against the border).
  Such a rectangle counts when its inside is calm and the picture around it is not (mean change inside at most
  0.6 of outside, outside at least 0.2), or when its border is on screen in 70 % of samples.
- **A picture that stands still** (`facecam_still`): under 15 % moving beside a camera. The camera goes on top;
  below it `still_focus` boxes the persistent detail and `still_crop` fills the panel with it when it is narrow
  (a phone held up, a menu) or fits it across the panel over a blurred copy when it is wide (the whole frame).
- **Not a stream:** with less than half of the picture moving, the camera's face may cover at most 2 % of the
  frame (`CALM_PICTURE_FACE_MAX_AREA`; camera heads measure up to 1.7 %). A speaker filmed in front of a window
  or a shelf has a rectangle around the face too, and stays with the regular layouts.
- **After review:** a switch between layouts is placed half a frame before the first frame of the new picture, so
  it is exact however the time is rounded; boundary searches decode at most 1.5 s either side of a sampled
  switch; a divider must be one flat colour along its length (a shaded pole or mullion is not one); and over a
  calm picture any face larger than the inset's, on screen in a quarter of the samples, means people, not a game.
- **Camera decorations** beside the camera, not only above and below it, stay out of the gameplay panel (a thin
  black bar was being pasted on the panel edge).

