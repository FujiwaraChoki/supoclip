# Automatic vertical framing

`vertical` fills the 9:16 frame. `vertical_fit` always preserves the complete
source on a blurred canvas; it is available in the generation UI, REST settings
and MCP. `original` still keeps the source aspect ratio.

## Decision policy: fill first

A 16:9 picture shown whole in a 9:16 frame is a strip a third of the screen
tall between two blurred bands. Until November 2026 that strip was the safe
default: any shot without a close face, with people spread wide, with a line of
text the crop would cut, or whose analysis failed kept the whole frame, and a
clip that mixed such shots with crops was letterboxed from start to finish.
Ordinary talking heads, interviews, vlogs and gameplay came out small.

Now every shot fills the frame unless it is *content*:

- **face**: a confident subject. The tracked full-height crop follows it.
- **cover**: any other footage (no close subject, a group, a wide shot,
  b-roll, gameplay, a subtitled scene). A still full-height 9:16 crop on the
  shot's subject: the face seen in half of the shot (the largest of a group),
  else the visible detail, which is the middle of the frame for most footage.
- **fit**: content, where the full width is what the viewer came for. Only
  this shows the picture small, and only for the shots that are content.

**What counts as content is read, not guessed.** Shape tests for "lines of
text" also fire on brick courses, window bars, shelves and posters; they sent
plainly filmed scenes to the letterbox. They are now only a hint. A shot that
hints at text has up to three frames read by the local OCR (Tesseract,
`media/screen_text.py`), and what is read decides (`scene_context.content_from_words`). The image ships
Tesseract's English data only, so this reads Latin script; text in other scripts is not recognised, and such
shots fill the frame:

| Read on the frame | Example | Layout |
|---|---|---|
| A page: 20 words or more, 6 or more outside the crop | slides, a desktop, a document, a tweet | fit |
| A card: 3 lines or more, 10 words or more, 4 or more outside the crop | a quiz card, a list, a comparison's labels | fit |
| The same card again (4 words shared with content already found in the clip) | the question before its answers appear, the card behind a close-up | fit |
| One or two lines | a subtitle, a lower third, a scoreboard | fills the frame |
| Nothing readable | walls, shelves, posters behind a speaker | fills the frame |

Without a working OCR only a picture that does not move can be content (an
aligned paragraph, rows of small text, or a faceless still whose text the crop
cuts); moving footage always fills the frame. Empty, failed or partial analysis
fills the frame from the centre. Each clip reads at most 24 frames.

Hysteresis only works towards the full frame: a fitted run shorter than 2.5 s
beside a frame-filling shot fills the frame too (nobody reads a document in
that time), and a clip is never letterboxed as a whole because parts of it are.

The same rule holds in the other layouts:

- **Interviews** (below): a wide shot, b-roll, a crowd or a group that cannot
  be divided into two panels fills the frame; only content read as above keeps
  the whole picture, and a glance at a contestant shorter than 2.5 s between
  two stretches of a card stays with the card.
- **Reactions**: when the source shows people in a wide shot, or nobody at all
  (a game, b-roll), a crop of it fills the source panel under the reactor.
  Only stretches the scene plan read as content show the whole source frame.
- **Talking heads**: a lone face too small or too low for the tight crop keeps
  the tracked full-height crop. It used to be "staged" at about 60% of the
  frame height on a blurred canvas. Three people in one shot are not a call
  gallery; only tiles on two rows or more (a call grid) are shown whole.
- **Gameplay** (`docs/gaming-mode.md`): spans without the camera were already a
  full-height crop of the middle of the picture.

A portrait picture set in a 16:9 frame (a Short re-published wide, between
blurred or decorated bars) is found by its two edges: they run the height of
the frame, stay put while the picture moves, and mirror each other about the
centre (`inner_picture`). A cover crop of such a shot is moved inside that
picture, so no strip of bar shows and the picture's own captions stay whole. A
still camera on a scene full of uprights has many such edges, not two, and is
left alone.

The planner logs intervals and decision reasons under `Vertical scene layout`.
Cuts are localized to the sampling interval (about 0.33 seconds); very short
shots and subtle edits can be missed. A cover crop is still: it does not follow
a ball or a player, so a wide sports shot shows the middle of the play.

## Text the source burns in

A cropped frame cuts a subtitle line at both ends, and a second caption layer
on top of the source's own is unreadable. `media/source_captions.py` reads the
cut clip while somebody speaks (16 moments, the whole frame: a creator's
captions sit at the top as often as at the bottom). Two readers vote per frame:
the OCR, which reads each frame three ways (as it is, inverted, and with only
its bright pixels as ink, for heavy display fonts), and a shape test for bold
outlined rows the OCR cannot read. What is written is compared with the words
spoken at that moment, allowing for the OCR's slips (a dropped accent, a
garbled letter, "507" for "50?").

A subtitle that is up for under a second is in none of the sampled frames. The
bottom strip is therefore watched four times a second for light glyphs with
dark edges arriving (`strip_text_moments`), and the strongest arrivals are
read up to three times each, the strip alone and enlarged. Rows a subtitle
shares with a score bar or a title count as captions only where the subtitle
was read (`mixed`), and only those moments are painted. Up to five heights
("bands") are judged, at most three of them painted, and each is one of:

- **speech**: captions of what is said. Two frames whose line says the speech
  are enough, so captions that only appear on stressed phrases count.
- **text**: changing text that is not the speech. In the lower part of the
  frame and centred it is subtitles (a translation, a game's own lines); a few
  frames of it elsewhere is a prompt or a sign and is ignored.
- **static**: a label that says the same thing in most of the frames. (A
  subtitle line stays up for seconds, so two neighbouring frames often show the
  same one; the clip as a whole is what tells them apart.)

The render then does one of three things:

| The source's text | The layout | What happens |
|---|---|---|
| Captions of the speech, in 60% of speech moments or more | shows them whole: a Short, `original`, a fitted frame, or crops that hold every line (a portrait picture set in a 16:9 frame) | **Our captions are left off.** The hook title stays. |
| Captions or subtitles (speech, or subtitle-like text) that cannot carry the clip: cut by the crop, or whole but only there now and then | crops the width: a full-height crop, the panels of a split screen, the game panel under a streamer's camera, the source panel under a reactor | **They are painted out** of the cropped shots and ours are drawn for every line. A band that is there most of the time is painted throughout; one that comes and goes only while it holds text. |
| A label, a document, a screen the video is about, captions that cover part of an uncropped clip | any | **Ours move clear** of the band. Nothing is painted. |

Whether a crop cuts the lines is measured, not assumed: the scene plan reports
the columns each full-height crop shows (`crop_windows`), and
`cut_intervals` compares them with the widest line that was read. Composed
layouts (stacked, game, reaction) always count as cutting.

A band that comes and goes is timed by the edge energy of its box
(`caption_presence`). The moments a line was actually read always count as
text, and gaps shorter than a second and a half between two lines stay
painted: a line left in costs more than a moment painted for nothing. A
subtitle that sometimes runs to two lines is one band (`_with_stacked_lines`).

Painting covers the band's rows across the whole width (a line longer than
any that was read would stick out of a narrower box, cut off and legible),
fills them from the edges (`delogo`) and softens the patch, so what is left is
smooth, not streaks. It is kept to the strips at the top and the bottom where
captions live; text in the middle of the picture is painted only when it is a
caption track (it says the speech in 80% of speech moments or more). Captions
the user edited are never left off. In the gameplay and reaction layouts, lines the picture still shows
(nothing was painted) keep the older rule: no caption for words written on the
screen (`media/screen_words.py`).

What this does not do: graphics that are part of the picture (a title, a logo,
a stat counter, a lower third, a word set next to a person) are cut by a 9:16
crop like the rest of the picture; a source caption that shows for a second
or less can fall between the sampled frames; and a motion-graphics explainer
whose text moves all over the frame is only cleaned where its text sits in a
caption strip.

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
- **cover**: footage without a close subject (a plate of food, a product, a
  street, a distant figure), five or more people, or a group that is not a row
  (faces of very different size). A still full-height 9:16 crop on a steady
  face, however small, on the part of a group that shares one crop, or else on
  the picture's subject (`focus_center`: edges and saturated colour per column,
  weighted towards the middle). The layout report lists it as `single` with
  reason `cover`.
- **fit**: content read off the frame (see the table above): a tweet, a
  document, a quiz card, a labelled chart or screenshot. A paragraph in a
  handheld shot of people is a sign behind them, not a graphic: the people are
  composed as usual.

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
