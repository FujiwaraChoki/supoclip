# Clip quality review, 21 Nov 2026

A second-by-second review of 90 delivered clips, what it found, what was fixed, and what is still open.
The clips came from 17 tasks on the hosted service, made between 16 and 21 Nov 2026. No customer data is
in this document: clips are counted, not named, and the review material itself stays private.

## Method

1. **Every frame, measured.** Each clip went through ffmpeg's black-frame, frozen-picture, scene-change,
   loudness and silence detectors.
2. **Every second, looked at.** Each clip was laid out as contact sheets, one frame per second, and read in
   full. Openings, endings, every layout change and anything that looked wrong were then extracted frame
   by frame. Every problem was written down with its time range, a category and a severity.
3. **Checked against the data.** Captions were compared with the stored transcript and its word timings,
   cut points with the stored source ranges, scores with the stored analyses.
4. **Rendered again.** Each clip was rendered from the same cached source footage and the same cut points
   on the fix branch, and compared with the original side by side.

## What the review found

Technically the clips were sound: all 720x1280 at 30 fps, loudness between -12.4 and -15.8 LUFS, no black
or frozen frames produced by the pipeline. The problems were editorial and visual.

Reviewers scored each clip from 1 (broken) to 5 (ready to post): 9 clips got 1, 55 got 2, 25 got 3 and
one got 4. They recorded 747 problems. Grouped by pattern (a clip can be in several):

| Pattern | Clips (of 90) | Status |
|---|---|---|
| Caption lines join the end of one sentence to the start of the next | 79 | Fixed (#529) |
| Picture reduced to a third of the frame, with blur above and below | 77 | Partly addressed by layout work already on `main`; streamers side by side fixed in #550; open (#282) |
| Caption line frozen through a pause (up to 19.5 s) | 55 | Fixed (#529) |
| Wrong words from transcription burned in | 50 | Open (#543) |
| Clip ends before the payoff, mid-sentence or on a question | 47 | Open (#542) |
| Hook title on the seam between two bands, or over a face | 43 | One cause fixed in #548; open (#540) |
| Frames of a neighbouring shot, or a source transition, at the start or end | 37 | Hard cuts within 0.4 s, black frames and fades up to 0.6 s fixed (#530); other transitions and longer overruns open (#542) |
| Layout changes with no cut in the source, or layouts shorter than 1.5 s | 35 | Flashes under 1.2 s fixed in #545; longer ones open (#539) |
| On-screen counters, timers and labels cut by the crop | 34 | Open (#539, #541) |
| A band or crop showing the wrong thing (a wall, a back of a head, extras) | 32 | A back of a head with no face in view fixed in #545; the rest open (#539) |
| Hook title describes something that is not on screen | 20 | Open (#543) |
| Source letterbox bars kept, shown as black bands | 15 | Fixed for the Auto format in #546; Fit format open (#538) |
| Footage repeated between clips of one task | 10 | Fixed for overlaps above a third (#531) |
| Clips shown without a score (25 clips, all from cached analyses) | 25 | Fixed (#528) |

## Fixed in #534

**Captions follow speech (#529).** Lines were four words by count and stayed up from their first word to
their last. Measured on the 90 reviewed clips from their transcripts: 1,321 of 2,807 lines joined two
sentences, and lines sat frozen through pauses for 1,012 seconds in total. After the change, on the same
clips and transcripts: 14 of 3,652 lines join two sentences (kept together because splitting them would
leave a line on screen for under 0.3 s), and none is frozen. A line now ends at a sentence end and at a
pause longer than a second, and leaves with its last word.

**Scores survive the analysis cache (#528).** A run that started from another run's saved analysis read
the scores from the wrong place, saved every clip with score 0, and wrote the zeros back to the cache.
On the hosted service that was 15.5% of completed highlight tasks over 30 days. For 22 of the 25 reviewed
clips without a score, the first analysis of the same video could still be found: it had scored them
between 28 and 92.

**Clip edges (#530).** A clip is cut a moment before its first word and after its last. A shot change
inside that moment showed as a flash of another scene on the first or last frames. Up to 0.4 s of a
neighbouring shot is now replaced by a held frame of the clip's own shot, with the audio untouched. The
same is done for black frames at an edge and for a fade from or to black of up to 0.6 s, so a clip does
not open or close on a black or dimmed picture.

**Near-duplicate clips (#531).** One reviewed task delivered three clips from one two-minute stretch; the
middle one had a second of footage of its own. The overlap limit between two picks went from a half to a
third, and a pick mostly covered by stronger picks together is dropped.

## Follow-up pull requests (framing)

After #534, framing was what still held the re-rendered clips back, so the layout planner was measured on
the same clips (the real planner on each clip's source cut, its plan rendered as a preview and compared
with the source) and four targeted fixes were made, each in its own pull request:

- **#545, interview layout.** A person whose face is not in view (a back of a head) gets no split-screen
  half, and a layout under 1.2 s inside one continuous shot stays with its neighbour. On the 23 clips that
  use this layout the most: picture jumps with no cut in the source 100 to 64, sub-second layouts 33 to 14.
  A person in profile keeps their half: a first version that judged by the eyes alone removed more bad
  halves but three times dropped someone speaking in profile.
- **#546, letterbox bars.** Black bars baked into a 16:9 file are measured and the layout is planned on the
  picture between them, unless that would crop into close-up faces.
- **#548, hook title row.** The title sits on the seam between two halves only when the split screen fills
  most of the title's four seconds.
- **#550, streamers side by side.** Two or three creators keyed out next to each other over a game share
  one camera panel. Seven of the reviewed clips that were a small picture or a two-face split screen become
  gameplay layouts; on the 14 clips it touches, time as a small picture goes from 91.7 s to none.

Reviewers who compared the re-rendered clips again found the targeted defects gone where the rules fire,
and the scores of those clips mostly unchanged: each still has other problems from the open list below.

## Already different on `main`

Most reviewed clips were rendered before the gaming layout and the full-height crop for live footage
reached production (#471, #482, #483, #493, #497, #501). Rendered again today, a gameplay stream with a
camera gets the camera on top and the game filling the rest where it used to be a small picture between
blurred bars, and many filmed scenes fill the frame instead of being fitted. Reviewers rated those clips
higher. The same code also frames some shots worse than before (a close-up that became a split screen
with a back of a head, a full-height crop that became a small picture). None of this is part of #534;
the open list below is what the re-renders still show.

## Still open

What the re-renders on today's code still show, by issue:

- #538: letterboxed sources keep their black bars in the Fit format, and bars that differ from shot to shot.
- #539: framing in multi-person scenes (a listener who turns away mid-shot, halves with an extra or an
  object, crops that slide after a cut or miss the action) and layout changes off the cut that last.
- #540: the hook title and the captions are placed over faces and the source's own text.
- #541: in the gameplay layout the pasted minimap hides the game's subtitles on lines whose captions are
  left off.
- #542: clip boundaries ignore scene changes and source transitions; clips end before the payoff.
- #543: hook titles that describe what is not on screen; transcript errors burned in next to correct
  on-screen text.
- #282: the fit layout is still chosen where the content is too small to read.
- #535: fx-core's base caption layout still groups words by count (a code-level follow-up; none of the
  reviewed clips used it).

Two side effects of the new caption rules were seen and left as they are: a single word can stand alone
for about 0.4 s when the speaker pauses for over a second on both sides of it, and in very fast speech a
line can still be on screen for under 0.3 s when joining it to a neighbour would exceed the words-per-line
limit.

## Repeating the review

The measurements are plain ffmpeg filters (`blackdetect`, `freezedetect`, `select='gte(scene,0)'`,
`ebur128`, `silencedetect`). The caption numbers come from the stored transcript: group the clip's words
the old way and the new way, then count lines in which a word other than the last ends a sentence, and sum
the gaps longer than a second inside a line. A clip can be rendered again from its `.source_map.json`
(`source_ranges`, `output_offsets`) by passing those ranges as the segment's `keep_ranges` to
`VideoService.create_single_clip`.
