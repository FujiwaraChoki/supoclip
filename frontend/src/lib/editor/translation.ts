import {
  clamp,
  uid,
  type CaptionWord,
  type EditDocument,
  type TranslationPhrase,
  type TranslationState,
} from "./document";

/**
 * Translation alignment.
 *
 * The English transcription stays the only source of timing. A translation is
 * aligned on whole spoken phrases - the stretches of speech between pauses -
 * and the translated words are distributed inside the boundaries of the phrase
 * they belong to. A pause therefore stays a pause: no translated word is ever
 * scheduled across it.
 */

/** A silence at least this long separates two spoken phrases. */
export const PHRASE_PAUSE_SECONDS = 0.3;
/** A sentence that ends with at least this much silence also starts a phrase. */
export const SENTENCE_PAUSE_SECONDS = 0.12;
/** Longer stretches of speech are split so one translation unit stays short. */
export const MAX_PHRASE_WORDS = 14;
/** The shortest span a single caption word may occupy. */
export const MIN_WORD_SECONDS = 0.005;

export interface SourcePhrase {
  index: number;
  words: CaptionWord[];
  start: number;
  end: number;
  text: string;
}

const SENTENCE_END = /[.!?…][»"'’)\]]?$/;

export function tokenize(text: string): string[] {
  return text.replace(/\s+/g, " ").trim().split(" ").filter(Boolean);
}

/** Split text into sentences, keeping the punctuation with its sentence. */
export function splitSentences(text: string): string[] {
  return text
    .replace(/\r/g, "")
    .split(/\n+/)
    .flatMap((line) => line.split(/(?<=[.!?…])\s+(?=\S)/))
    .map((sentence) => sentence.trim())
    .filter(Boolean);
}

/** A rough syllable count: enough to rank how long a word is spoken. */
export function syllables(token: string): number {
  const letters = token.toLowerCase().replace(/[^\p{L}\p{N}]/gu, "");
  if (!letters) return 1;
  const groups = letters.match(/[aeiouyаеёиоуыэюя]+/g);
  return groups ? groups.length : Math.max(1, Math.round(letters.length / 3));
}

/**
 * How long a word holds the caption. Punctuation at the end adds a little, so
 * a word that ends a sentence is spoken a touch longer than a bare word.
 */
export function wordWeight(token: string): number {
  const trailing = /[,;:—–]$/.test(token) ? 0.5 : /[.!?…]$/.test(token) ? 0.8 : 0;
  return syllables(token) + trailing;
}

/** The cut points of an over-long phrase, chosen by the widest internal gap. */
function splitLongPhrase(group: CaptionWord[]): CaptionWord[][] {
  if (group.length <= MAX_PHRASE_WORDS) return [group];
  const parts = Math.ceil(group.length / MAX_PHRASE_WORDS);
  const result: CaptionWord[][] = [];
  let start = 0;
  for (let part = 1; part < parts; part++) {
    const target = Math.round((part * group.length) / parts);
    const low = Math.max(start + 1, target - 3);
    const high = Math.min(group.length - 1, target + 3);
    let best = target;
    let bestScore = -1;
    for (let index = low; index <= high; index++) {
      const previous = group[index - 1];
      const gap = group[index].start - previous.end;
      const endsSentence = SENTENCE_END.test(previous.text.trim()) ? 1 : 0;
      const score = endsSentence * 10 + gap - Math.abs(index - target) * 0.001;
      if (score > bestScore) {
        bestScore = score;
        best = index;
      }
    }
    result.push(group.slice(start, best));
    start = best;
  }
  result.push(group.slice(start));
  return result;
}

/**
 * Group the spoken words into phrases. A phrase ends where the speaker stops
 * for long enough to breathe, and the phrases are the units a translation is
 * aligned on.
 */
export function splitPhrases(
  words: CaptionWord[],
  pause: number = PHRASE_PAUSE_SECONDS,
): SourcePhrase[] {
  const ordered = [...words]
    .filter((word) => word.text.trim())
    .sort((a, b) => a.start - b.start);
  const groups: CaptionWord[][] = [];
  let current: CaptionWord[] = [];
  for (const word of ordered) {
    if (current.length) {
      const previous = current[current.length - 1];
      const gap = word.start - previous.end;
      const endsSentence = SENTENCE_END.test(previous.text.trim());
      if (gap >= pause || (endsSentence && gap >= SENTENCE_PAUSE_SECONDS)) {
        groups.push(current);
        current = [];
      }
    }
    current.push(word);
  }
  if (current.length) groups.push(current);
  return groups
    .flatMap(splitLongPhrase)
    .map((group, index) => ({
      index,
      words: group,
      start: group[0].start,
      end: group[group.length - 1].end,
      text: group.map((word) => word.text).join(" "),
    }));
}

/**
 * Place the translated words inside the boundaries of their phrase.
 *
 * The words are spread over the speaking time of the phrase in proportion to
 * their weight, and each word lands inside the English word it overlaps. The
 * pauses inside the phrase stay empty; the pauses between phrases are never
 * touched because a phrase never reaches into the next one.
 */
export function distributePhraseWords(
  phrase: SourcePhrase,
  text: string,
): CaptionWord[] {
  const tokens = tokenize(text);
  if (!tokens.length) return [];
  const weights = tokens.map(wordWeight);
  const total = weights.reduce((sum, weight) => sum + weight, 0) || 1;
  const intervals = phrase.words
    .map((word) => ({
      start: word.start,
      end: word.end,
      duration: Math.max(0, word.end - word.start),
    }))
    .filter((interval) => interval.duration > 0);
  const speech = intervals.reduce((sum, interval) => sum + interval.duration, 0);
  const span = Math.max(0, phrase.end - phrase.start);
  /** A position in [0, 1] of the phrase's speaking time, as wall-clock time. */
  const toWallClock = (position: number): number => {
    if (!intervals.length || speech <= 0)
      return phrase.start + span * clamp(position, 0, 1);
    let remaining = clamp(position, 0, 1) * speech;
    for (const interval of intervals) {
      if (remaining <= interval.duration) return interval.start + remaining;
      remaining -= interval.duration;
    }
    return intervals[intervals.length - 1].end;
  };
  const result: CaptionWord[] = [];
  let consumed = 0;
  let previousEnd = phrase.start;
  for (let index = 0; index < tokens.length; index++) {
    const from = toWallClock(consumed);
    consumed += weights[index] / total;
    const to = toWallClock(consumed);
    const start = clamp(Math.max(from, previousEnd), phrase.start, phrase.end);
    const minimum = Math.min(MIN_WORD_SECONDS, Math.max(0, phrase.end - start));
    const end = clamp(
      Math.max(to, start + minimum),
      start + minimum,
      Math.max(phrase.end, start + minimum),
    );
    result.push({
      id: uid(),
      text: tokens[index].slice(0, 120),
      start,
      end,
      highlight: false,
    });
    previousEnd = end;
  }
  return result;
}

export interface TranslationAlignment {
  texts: string[];
  /** The mapping is a guess: the creator should check it. */
  needsReview: boolean;
}

/**
 * Match the translated sentences to the phrases they belong to.
 *
 * An exact one-to-one list is used as it is. A different number of sentences
 * is matched by position and length, which is a guess the creator can correct
 * phrase by phrase.
 */
export function alignTranslations(
  phrases: SourcePhrase[],
  translations: string[],
): TranslationAlignment {
  const cleaned = translations.map((text) => text.trim());
  if (cleaned.length === phrases.length)
    return { texts: cleaned, needsReview: false };
  const sentences = splitSentences(cleaned.filter(Boolean).join(" "));
  if (sentences.length === phrases.length)
    return { texts: sentences, needsReview: false };
  if (!sentences.length)
    return {
      texts: phrases.map((phrase) => phrase.text),
      needsReview: true,
    };
  return {
    texts: proportionalAlignment(phrases, sentences),
    needsReview: true,
  };
}

/** Match sentences to phrases by their relative length in the two texts. */
function proportionalAlignment(
  phrases: SourcePhrase[],
  sentences: string[],
): string[] {
  const sourceWeights = phrases.map((phrase) => Math.max(1, phrase.text.length));
  const targetWeights = sentences.map((sentence) => Math.max(1, sentence.length));
  const sourceTotal = sourceWeights.reduce((sum, weight) => sum + weight, 0);
  const targetTotal = targetWeights.reduce((sum, weight) => sum + weight, 0);
  const texts: string[] = [];
  let sourceUsed = 0;
  let targetUsed = 0;
  let target = 0;
  phrases.forEach((phrase, index) => {
    const from = sourceUsed / sourceTotal;
    sourceUsed += sourceWeights[index];
    const to = sourceUsed / sourceTotal;
    const parts: string[] = [];
    while (target < sentences.length) {
      const targetFrom = targetUsed / targetTotal;
      targetUsed += targetWeights[target];
      const targetTo = targetUsed / targetTotal;
      if (targetTo > from && targetFrom < to) parts.push(sentences[target]);
      target++;
      if (targetTo >= to) break;
    }
    // A phrase without a sentence keeps the original text: dropping it would
    // lose a part of the speech.
    texts.push(parts.join(" ").trim() || phrase.text);
  });
  return texts;
}

export interface TranslatedPhrases {
  words: CaptionWord[];
  phrases: TranslationPhrase[];
}

/**
 * Build the translated caption words and the phrase metadata that keeps the
 * translation attached to the English phrases it came from.
 */
export function buildTranslation(
  phrases: SourcePhrase[],
  texts: string[],
): TranslatedPhrases {
  const words: CaptionWord[] = [];
  const metadata: TranslationPhrase[] = [];
  phrases.forEach((phrase, index) => {
    const text = (texts[index] ?? "").trim();
    const phraseWords = distributePhraseWords(phrase, text);
    // Carry the emphasis of the English phrase onto the word at the same
    // relative position, so a highlighted source word stays highlighted.
    const highlighted = phrase.words
      .map((word, position) => (word.highlight ? position / phrase.words.length : -1))
      .filter((position) => position >= 0);
    phraseWords.forEach((word, position) => {
      const relative = phraseWords.length ? position / phraseWords.length : 0;
      if (
        highlighted.some(
          (source) => Math.abs(source - relative) < 0.5 / phraseWords.length,
        )
      )
        word.highlight = true;
    });
    metadata.push({
      id: uid(),
      start: phrase.start,
      end: phrase.end,
      sourceText: phrase.text,
      targetText: text,
      wordIds: phraseWords.map((word) => word.id),
    });
    words.push(...phraseWords);
  });
  return { words, phrases: metadata };
}

/**
 * Replace the caption words with the translation, keeping the English
 * transcription and its timings as the basis of the synchronization.
 */
export function applyTranslation(
  doc: EditDocument,
  sourceWords: CaptionWord[],
  phrases: SourcePhrase[],
  texts: string[],
  mode: TranslationState["mode"],
): EditDocument {
  const built = buildTranslation(phrases, texts);
  const translation: TranslationState = {
    sourceLanguage: doc.translation?.sourceLanguage ?? "en",
    targetLanguage: doc.translation?.targetLanguage ?? "ru",
    mode,
    sourceWords: sourceWords.map((word) => ({ ...word })),
    phrases: built.phrases,
    updatedAt: new Date().toISOString(),
  };
  return { ...doc, words: built.words, translation };
}

/** Put the original English words back, dropping the translation. */
export function restoreOriginalWords(doc: EditDocument): EditDocument {
  if (!doc.translation) return doc;
  const { translation, ...rest } = doc;
  return { ...rest, words: translation.sourceWords.map((word) => ({ ...word })) };
}

export interface TranslationResponse {
  /** The answer carried the list of translations. */
  ok: boolean;
  translations: string[];
  needsReview: boolean;
  /** A machine readable reason, e.g. "translation_timeout". */
  code?: string;
  /** The message the server sent, if it sent one. */
  message?: string;
}

/**
 * Read the answer of the translation endpoint.
 *
 * The answer is not always JSON: a gateway error page or an empty body
 * arrives when the request was cut short. The panel has to tell that apart
 * from a refused request instead of failing with a JSON parse error.
 */
export function parseTranslationResponse(raw: string): TranslationResponse {
  const empty: TranslationResponse = {
    ok: false,
    translations: [],
    needsReview: false,
  };
  const text = (raw ?? "").trim();
  if (!text) return { ...empty, code: "empty_response" };

  let payload: unknown;
  try {
    payload = JSON.parse(text);
  } catch {
    return { ...empty, code: "malformed_response" };
  }
  if (!payload || typeof payload !== "object") {
    return { ...empty, code: "malformed_response" };
  }

  const record = payload as Record<string, unknown>;
  let code: string | undefined;
  let message: string | undefined;
  const detail = record.detail;
  if (typeof detail === "string") {
    message = detail;
  } else if (detail && typeof detail === "object") {
    const fields = detail as Record<string, unknown>;
    if (typeof fields.code === "string") code = fields.code;
    if (typeof fields.message === "string") message = fields.message;
  }
  if (typeof record.code === "string") code = record.code;
  if (typeof record.message === "string") message = record.message;

  if (!Array.isArray(record.translations)) {
    return { ok: false, translations: [], needsReview: false, code, message };
  }
  return {
    ok: true,
    translations: record.translations.filter(
      (value): value is string => typeof value === "string",
    ),
    needsReview: Boolean(record.needsReview),
    code,
    message,
  };
}

/** A stretch of the video that reaches the export. */
export interface CutRange {
  start: number;
  end: number;
}

/**
 * The phrases that have no translation and still reach the export.
 *
 * A phrase outside the cut never shows up in the video, so it may stay empty.
 * A phrase inside the cut would leave a hole in the subtitles, so the
 * translation must not be applied while such a phrase is untranslated.
 */
export function missingTranslationsInCut(
  phrases: SourcePhrase[],
  texts: string[],
  ranges: CutRange[],
): number[] {
  return phrases
    .map((phrase, index) => ({ phrase, text: texts[index] ?? "" }))
    .filter(({ text }) => !text.trim())
    .filter(({ phrase }) =>
      ranges.some((range) => phrase.start < range.end && phrase.end > range.start),
    )
    .map(({ phrase }) => phrase.index);
}
