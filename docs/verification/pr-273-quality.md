# Clip studio and quality integration verification

This branch includes the changes from quality PRs #296–#301, resolves their conflicts with the studio, and adds regression coverage at the generation/editor/export boundaries.

## Behavior checked

- Title timing can be dragged at either edge of its timeline block, changed with the keyboard, saved, undone/redone, and exported. Existing drafts retain their original timing.
- Captions update the preview while typing. Each caption retains its own placement across editing, history, reload, and export.
- B-roll moves across removed, reordered, and contiguous split segments without losing capture, changing length, or snapping to an ordinary split.
- Silent cleanup protects local action/audio windows and compresses quiet intervals around them. Uncertain decodes preserve the gap.
- Wide diagrams keep their complete frame. The editor preserves the raw source and offers landscape; generated compositions are separately stored and restored from media storage.
- Caption collision detection handles both text polarities and measures the actual composed output. The generated caption center is saved for editor initialization.
- Interruption notices are read from three picture samples using local Tesseract. A majority must match; presenter faces remain protected. Ranked reserve candidates are kept until visual checks finish, then the final clip budget is enforced.
- Crossfade output offsets survive trim, split, combine, and restoration from partial-download source time. Server exports normalize to 30 fps and stop at the requested duration when stock footage has another frame rate.

## Automated checks

Run from the repository root, using separate test databases and Redis from any interactive editor session:

```sh
make check
make test
make test-e2e
cd frontend && pnpm run test:polish --workers=2
```

On 2026-10-28: backend 1,651 passed; frontend 589 passed; isolated workflow browser suite 87 passed; database-backed browser smoke suite 20 passed. Production build, lint and TypeScript checks passed. Backend coverage for the configured critical modules exceeded its required threshold. The container image security workflow remains a separate CI check.

## Real media checks

Only local `qwen3.8:27b` was used for AI. A fresh isolated worker job selected and generated a Huberman clip from cached source media and word timings; a second job exercised the final picture checks using the cached analysis. No hosted transcription or model request was needed. In Chrome, the generated clip decoded changing frames, accepted a square/uppercase AI edit, and restored it with undo/redo. Fresh Pexels auto-fill returned real footage, which decoded under the title and captions.

Dragging the title end from 4.00 to 6.08 seconds survived history and export. Both the Chrome export (H.264/Opus) and background export (H.264/AAC) were 1080×1920 and exactly 8.000 seconds with audio. Frame checks found the title at 5.5 seconds and absent at 6.5 seconds. The two exports' sampled frame difference was 3.27 mean absolute RGB levels on a 0–255 scale.

Read-only saved audit footage also verified:

- Fortnite's formerly removed gap retained 6.45 seconds of detected action.
- A 39.2-second cooking render kept a consistent full-frame composition across close/wide cuts.
- A real diagram rendered in both vertical full-content and landscape formats.
- Local OCR recognized the actual 21.9-second news interruption-slate output; animated notice and clean-picture controls are covered separately.

The audit lacks the original MKBHD source and the original news-slate source interval. Those exact source-to-export cases cannot be replayed from the available evidence. Caption polarity/composition tests and the saved slate output cover the associated paths, but do not constitute a fresh audit of every original source or a guarantee for every language, font, or visual layout. Existing user drafts were preserved; UI changes were exercised on separate QA clips. No production restart, GitHub merge, or deployment was performed.
