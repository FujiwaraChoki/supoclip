/**
 * Mirrors the accent-word logic in backend/src/video_utils.py::build_hook_title_ass
 * (POWER_WORDS from backend/src/emoji_captions.py) so the frontend preview
 * highlights the same words the actual burned-in render will highlight,
 * instead of a fixed sample word. Keep this list in sync with the backend's
 * POWER_WORDS set if that ever changes.
 */
const POWER_WORDS = new Set([
  "never", "always", "everything", "nothing", "everyone", "nobody", "anyone",
  "best", "worst", "most", "biggest", "huge", "massive", "tiny", "every",
  "only", "first", "last", "free", "now", "today", "instantly", "forever",
  "guaranteed", "proven", "secret", "truth", "fact", "literally", "actually",
  "exactly", "must", "need", "stop", "warning", "danger", "critical", "key",
  "important", "remember", "mistake", "wrong", "right", "perfect", "ultimate",
  "powerful", "insane", "crazy", "incredible", "amazing", "shocking", "viral",
  "million", "billion", "thousand", "percent", "double", "triple", "ten",
]);

export function normalizeHookToken(text: string): string {
  return (text || "").toLowerCase().replace(/[^a-z0-9%]+/g, "");
}

export interface HookWordSpan {
  text: string;
  highlighted: boolean;
}

/** Splits hook text into words, flagging each the same way the backend does:
 * a power word, a word containing a digit, or an explicitly requested
 * highlight word. */
export function splitHookIntoHighlightSpans(
  text: string,
  highlightWords: string[] = []
): HookWordSpan[] {
  const requested = new Set(
    highlightWords.map(normalizeHookToken).filter((w) => w.length > 0)
  );
  return (text || "").split(/\s+/).filter(Boolean).map((word) => {
    const token = normalizeHookToken(word);
    const highlighted =
      token.length > 0 &&
      (POWER_WORDS.has(token) || /\d/.test(token) || requested.has(token));
    return { text: word, highlighted };
  });
}
