// Caption phrase groups for the intensity looks (mirror of backend/src/edit_fx/caption_groups.py, pinned by
// shared/fx/fixtures/groups). A group breaks at sentence or clause punctuation, at a pause of PAUSE_BREAK seconds, before
// a conjunction once it holds two words, at a run change (studio segments) and when it holds `maxWords` words. Times
// are E seconds without the look's lead.
import type { CaptionLook } from "../types";

export const PAUSE_BREAK = 0.25;
export const GROUP_BRIDGE = 0.3;
export const MIN_HOLD = 0.4;
export const CONJUNCTIONS: ReadonlySet<string> = new Set(["and", "but", "so", "because", "or", "then", "cause", "cuz"]);
export const LOOK_MAX_WORDS: Record<Exclude<CaptionLook, "template">, number> = { subtle: 4, bold: 3, max: 3 };
/** Display time minus the transcript's word start (s); == caption_groups.LOOK_LEAD (calibrated with the study's own
 *  scripts: transcript starts sit 1-3 frames after the energy onset, so -1 frame shows a word about one frame after
 *  it is heard, like the references). */
export const LOOK_LEAD: Record<Exclude<CaptionLook, "template">, number> = { subtle: -2 / 30, bold: -1 / 30, max: -1 / 30 };

const BREAK_AFTER = /[.,!?;:…。！？，、]["'”’)\]]*$/u;

const norm = (text: string): string => text.toLowerCase().replace(/[^\p{L}\p{N}_']+/gu, "").replace(/'/g, "");

/** True when a word ends a phrase (sentence or clause punctuation, closing quotes allowed after it). */
export const breaksAfter = (text: string): boolean => BREAK_AFTER.test(text.trim());

export interface PhraseGroup { first: number; last: number; start: number; end: number }
interface GroupWord { start: number; end: number; text: string; run?: string }

/** Words with text, by start (stable). */
export function groupedWords<T extends GroupWord>(words: readonly T[]): T[] {
  return words.filter((w) => w.text.trim() !== "").sort((a, b) => a.start - b.start);
}

/** Phrase groups over `words` (already `groupedWords`), in time order. `maxWords` is clamped to 1..8. */
export function phraseGroups(words: readonly GroupWord[], maxWords: number): PhraseGroup[] {
  const limit = Math.max(1, Math.min(8, Math.trunc(maxWords) || 1));
  const spans: [number, number][] = [];
  let first = 0;
  for (let i = 1; i < words.length; i++) {
    const prev = words[i - 1], word = words[i], size = i - first;
    const gap = word.start - prev.end;
    if (size >= limit || breaksAfter(prev.text) || gap >= PAUSE_BREAK - 1e-9
      || (size >= 2 && CONJUNCTIONS.has(norm(word.text))) || (word.run ?? "clip") !== (prev.run ?? "clip")) {
      spans.push([first, i - 1]);
      first = i;
    }
  }
  if (words.length) spans.push([first, words.length - 1]);
  return spans.map(([a, b], k): PhraseGroup => {
    const start = words[a].start;
    let end = Math.max(start, words[b].end);
    if (k + 1 < spans.length) {
      const next = words[spans[k + 1][0]].start;
      end = next - end < GROUP_BRIDGE ? next : end;
      end = Math.min(Math.max(end, start + MIN_HOLD), Math.max(next, start));
    } else {
      end = Math.max(end, start + MIN_HOLD);
    }
    return { first: a, last: b, start, end };
  });
}
