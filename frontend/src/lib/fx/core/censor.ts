// Caption censoring for bleeped words: mirror of backend/src/edit_fx/censor.py.

const LETTER = /\p{L}/u;          // == Python str.isalpha() per code point (categories Lu, Ll, Lt, Lm, Lo)
const MIN_OVERLAP = 0.03;          // a word is censored when a bleep covers ≥ 30 ms of it (or all of it)
const EPS = 1e-9;

/** First letter kept, every later letter → "*"; digits, punctuation and marks kept. Unicode-aware. */
export function censorText(text: string): string {
  let seen = false;
  let out = "";
  for (const ch of text) {     // iterates code points, like Python
    if (LETTER.test(ch)) {
      out += seen ? "*" : ch;
      seen = true;
    } else {
      out += ch;
    }
  }
  return out;
}

const covered = (start: number, end: number, spans: readonly (readonly [number, number])[]) =>
  spans.some(([s, e]) => Math.min(end, e) - Math.max(start, s) >= MIN_OVERLAP - EPS || (s <= start && end <= e));

/**
 * Copies of the words (same order) with text censored where a span overlaps the word by ≥ 30 ms, or covers it
 * entirely (a word shorter than 30 ms inside a bleep is still masked).
 */
export function censoredWords<W extends { start: number; end: number; text: string }>(
  words: readonly W[], spans: readonly (readonly [number, number])[],
): W[] {
  return words.map((word) =>
    spans.length && covered(Number(word.start ?? 0), Number(word.end ?? 0), spans)
      ? { ...word, text: censorText(String(word.text ?? "")) }
      : { ...word });
}
