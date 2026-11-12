import { describe, expect, it } from "vitest";
import {
  alignTranslations,
  applyTranslation,
  buildTranslation,
  distributePhraseWords,
  missingTranslationsInCut,
  parseTranslationResponse,
  restoreOriginalWords,
  splitPhrases,
  splitSentences,
  type SourcePhrase,
} from "./translation";
import { captionGroupsFor, replaceWords, type CaptionWord } from "./document";
import { draft } from "./document.test-fixture";

/** The words of the clip the translation was reported on. */
const clipWords: CaptionWord[] = [
  ["I", 0.09999999999999999, 0.188],
  ["got", 2.993, 3.138],
  ["in", 3.138, 3.218],
  ["some", 3.218, 3.394],
  ["trouble", 3.394, 3.715],
  ["in", 3.715, 3.795],
  ["the", 3.795, 3.939],
  ["Army.", 3.939, 4.131],
  ["Didn't", 4.901000000000001, 5.061],
  ["leave", 5.061, 5.157],
  ["on", 5.157, 5.238],
  ["the", 5.238, 5.3180000000000005],
  ["best", 5.3180000000000005, 5.574000000000001],
  ["of", 5.574000000000001, 5.638000000000001],
  ["terms.", 5.638000000000001, 6.007000000000001],
  ["They", 7.065, 7.097],
  ["don't", 7.097, 7.2410000000000005],
  ["give", 7.2410000000000005, 7.402],
  ["loans", 7.466, 7.69],
  ["or", 7.8020000000000005, 7.995000000000001],
  ["anything", 8.187000000000001, 8.492],
  ["to", 8.604000000000001, 8.700000000000001],
  ["people", 8.700000000000001, 8.924],
  ["like", 8.924, 9.085],
  ["me.", 9.085, 9.245000000000001],
  ["This", 10.047, 10.351],
  ["is", 10.351, 10.495000000000001],
  ["a", 10.752, 10.848],
  ["free", 10.848, 11.201],
  ["call", 11.249, 11.537],
  ["from", 11.874, 12.034],
  ["Lucia", 12.275, 12.627],
  ["Caminos.", 12.756, 13.301],
  ["be", 44.745, 45.085],
  ["okay?", 45.085, 46.184999999999995],
].map(([text, start, end], index) => ({
  id: `w${index}`,
  text: text as string,
  start: start as number,
  end: end as number,
  highlight: false,
}));

const russian = [
  "Я",
  "Попал в беду в армии.",
  "Не ушёл на лучших условиях.",
  "Они не дают кредитов.",
  "Никому ничего не говорят.",
  "Это бесплатный звонок от Люсии Каминос.",
  "Хорошо?",
];

describe("phrase segmentation", () => {
  it("starts a new phrase where the speaker pauses", () => {
    const phrases = splitPhrases(clipWords);
    expect(phrases[0].text).toBe("I");
    expect(phrases[1].text).toBe("got in some trouble in the Army.");
    expect(phrases[0].end).toBeLessThanOrEqual(phrases[1].start);
  });

  it("keeps words of one sentence together when the speech is continuous", () => {
    const phrases = splitPhrases(clipWords);
    expect(phrases[2].text).toBe("Didn't leave on the best of terms.");
    expect(phrases.some((phrase) => phrase.text === "They don't give")).toBe(false);
  });

  it("splits after a sentence that ends with a short pause", () => {
    const words: CaptionWord[] = [
      { id: "a", text: "One.", start: 0, end: 0.4, highlight: false },
      { id: "b", text: "Two", start: 0.55, end: 0.9, highlight: false },
    ];
    expect(splitPhrases(words)).toHaveLength(2);
  });

  it("splits a long stretch of speech into translation units", () => {
    const words: CaptionWord[] = Array.from({ length: 30 }, (_, index) => ({
      id: `w${index}`,
      text: `word${index}`,
      start: index * 0.2,
      end: index * 0.2 + 0.15,
      highlight: false,
    }));
    const phrases = splitPhrases(words);
    expect(phrases.length).toBeGreaterThan(1);
    expect(phrases.every((phrase) => phrase.words.length <= 14)).toBe(true);
  });
});

describe("distributing the translated words", () => {
  it("never schedules a translated word across the pause of the source", () => {
    const phrases = splitPhrases(clipWords);
    const words = distributePhraseWords(phrases[1], "Попал в беду в армии.");
    expect(words.every((word) => word.start >= phrases[1].start)).toBe(true);
    expect(words.every((word) => word.end <= phrases[1].end)).toBe(true);
    // The silence between the first word and the second phrase stays empty.
    expect(words.every((word) => word.end <= 0.188 || word.start >= 2.993)).toBe(
      true,
    );
  });

  it("keeps the first phrase inside the first English word", () => {
    const phrases = splitPhrases(clipWords);
    const words = distributePhraseWords(phrases[0], "Я");
    expect(words).toHaveLength(1);
    expect(words[0].start).toBeCloseTo(0.09999999999999999, 5);
    expect(words[0].end).toBeCloseTo(0.188, 5);
  });

  it("keeps the words ordered, without overlaps and without zero length", () => {
    const phrases = splitPhrases(clipWords);
    const words = distributePhraseWords(phrases[1], "Попал в беду в армии.");
    words.forEach((word, index) => {
      expect(word.end).toBeGreaterThan(word.start);
      if (index) expect(word.start).toBeGreaterThanOrEqual(words[index - 1].end);
    });
  });

  it("spreads a translation with more words than the source", () => {
    const phrases = splitPhrases(clipWords);
    const words = distributePhraseWords(
      phrases[1],
      "Я попал в серьёзную беду в армии",
    );
    expect(words).toHaveLength(7);
    expect(words[0].start).toBeCloseTo(phrases[1].start, 5);
    expect(words[words.length - 1].end).toBeLessThanOrEqual(phrases[1].end + 0.0001);
  });

  it("spreads a translation with fewer words than the source", () => {
    const phrases = splitPhrases(clipWords);
    const words = distributePhraseWords(phrases[1], "Попал в беду.");
    expect(words).toHaveLength(3);
    expect(words[0].start).toBeCloseTo(phrases[1].start, 5);
  });

  it("holds a word that ends a sentence a little longer", () => {
    const phrases = splitPhrases(clipWords);
    const words = distributePhraseWords(phrases[1], "Попал в беду в армии.");
    const last = words[words.length - 1];
    const previous = words[words.length - 2];
    expect(last.end - last.start).toBeGreaterThan(previous.end - previous.start);
  });

  it("returns nothing for an empty translation", () => {
    const phrases = splitPhrases(clipWords);
    expect(distributePhraseWords(phrases[0], "   ")).toEqual([]);
  });
});

describe("matching the translation to the phrases", () => {
  it("uses a list of the same length as it is", () => {
    const phrases = splitPhrases(clipWords);
    const alignment = alignTranslations(phrases, russian);
    expect(alignment.texts).toEqual(russian);
    expect(alignment.needsReview).toBe(false);
  });

  it("matches sentences to the phrases when the counts differ", () => {
    const phrases = splitPhrases(clipWords);
    const alignment = alignTranslations(phrases, ["Я. Попал в беду в армии."]);
    expect(alignment.texts).toHaveLength(phrases.length);
    expect(alignment.needsReview).toBe(true);
    expect(alignment.texts[0]).toContain("Я");
  });

  it("keeps a phrase that got no sentence", () => {
    const phrases = splitPhrases(clipWords);
    const alignment = alignTranslations(phrases, ["Я."]);
    expect(alignment.texts).toHaveLength(phrases.length);
    expect(alignment.texts.every((text) => text.length > 0)).toBe(true);
  });

  it("splits a text into sentences", () => {
    expect(splitSentences("Я попал в беду. Не ушёл! Хорошо?")).toEqual([
      "Я попал в беду.",
      "Не ушёл!",
      "Хорошо?",
    ]);
  });
});

describe("the translated document", () => {
  it("keeps the English transcription as the basis of the synchronization", () => {
    const phrases = splitPhrases(clipWords);
    const doc = applyTranslation(
      { ...draft, words: clipWords },
      clipWords,
      phrases,
      russian,
      "auto",
    );
    expect(doc.translation?.sourceWords).toEqual(clipWords);
    expect(doc.words.map((word) => word.text)).toEqual(
      russian.flatMap((text) => text.split(" ")),
    );
    expect(doc.translation?.phrases).toHaveLength(phrases.length);
  });

  it("puts the original words back", () => {
    const phrases = splitPhrases(clipWords);
    const doc = applyTranslation(
      { ...draft, words: clipWords },
      clipWords,
      phrases,
      russian,
      "auto",
    );
    const restored = restoreOriginalWords(doc);
    expect(restored.words).toEqual(clipWords);
    expect(restored.translation).toBeUndefined();
  });

  it("links every translated word to its phrase", () => {
    const phrases = splitPhrases(clipWords);
    const built = buildTranslation(phrases, russian);
    const ids = built.words.map((word) => word.id);
    expect(built.phrases.flatMap((phrase) => phrase.wordIds)).toEqual(ids);
  });

  it("does not let a caption line span the pause of a translated draft", () => {
    const phrases = splitPhrases(clipWords);
    const doc = applyTranslation(
      { ...draft, words: clipWords, segments: [{ id: "s", start: 0, end: 47 }] },
      clipWords,
      phrases,
      russian,
      "auto",
    );
    const groups = captionGroupsFor(doc);
    const first = groups[0];
    expect(first.every((word) => word.end <= 0.188)).toBe(true);
  });

  it("keeps the fixed-size lines of a draft without a translation", () => {
    const groups = captionGroupsFor({
      ...draft,
      words: clipWords,
      segments: [{ id: "s", start: 0, end: 47 }],
    });
    expect(groups[0].length).toBe(4);
  });
});

describe("the behaviour that is being replaced", () => {
  it("shows that the plain word replacement stretches the translation over the pause", () => {
    const replaced = replaceWords(
      clipWords,
      "Я попал в беду в армии.",
      46.18,
    );
    const covering = replaced.filter(
      (word) => word.start < 2.993 && word.end > 0.188,
    );
    expect(covering.length).toBeGreaterThan(0);
    // The first Russian word runs from the first English word to a quarter of
    // the clip: the translation is stretched over the pause and the silence.
    expect(covering[0].end).toBeGreaterThan(4);
  });
});

describe("reading the answer of the translation endpoint", () => {
  it("reads the translations of a normal answer", () => {
    const response = parseTranslationResponse(
      JSON.stringify({ translations: ["Я", "Попал в беду"], needsReview: false }),
    );
    expect(response.ok).toBe(true);
    expect(response.translations).toEqual(["Я", "Попал в беду"]);
    expect(response.needsReview).toBe(false);
  });

  it("keeps the flag of an answer that has to be checked", () => {
    const response = parseTranslationResponse(
      JSON.stringify({ translations: ["Я. Попал в беду."], needsReview: true }),
    );
    expect(response.needsReview).toBe(true);
  });

  it("survives an empty body instead of failing to parse it", () => {
    const response = parseTranslationResponse("");
    expect(response.ok).toBe(false);
    expect(response.code).toBe("empty_response");
  });

  it("survives a gateway error page instead of failing to parse it", () => {
    const response = parseTranslationResponse(
      "<html><body>504 Gateway Time-out</body></html>",
    );
    expect(response.ok).toBe(false);
    expect(response.code).toBe("malformed_response");
    expect(response.translations).toEqual([]);
  });

  it("reads the code of a refused request", () => {
    const response = parseTranslationResponse(
      JSON.stringify({
        detail: {
          code: "translation_timeout",
          message: "The translation took too long. Try again.",
        },
      }),
    );
    expect(response.ok).toBe(false);
    expect(response.code).toBe("translation_timeout");
    expect(response.message).toBe("The translation took too long. Try again.");
  });

  it("reads a refused request that only carries a message", () => {
    const response = parseTranslationResponse(
      JSON.stringify({ detail: "There are no phrases to translate" }),
    );
    expect(response.code).toBeUndefined();
    expect(response.message).toBe("There are no phrases to translate");
  });

  it("reads the code the server sends next to a plain message", () => {
    const response = parseTranslationResponse(
      JSON.stringify({
        detail: "The translation took too long. Try again.",
        code: "translation_timeout",
      }),
    );
    expect(response.code).toBe("translation_timeout");
    expect(response.message).toBe("The translation took too long. Try again.");
  });
});

describe("the phrases that still reach the cut", () => {
  const phrases = [
    { index: 0, words: [], start: 0.1, end: 1, text: "first" },
    { index: 1, words: [], start: 22.6, end: 24, text: "second" },
  ] as SourcePhrase[];

  it("lists a phrase without a translation that lies inside the cut", () => {
    const missing = missingTranslationsInCut(
      phrases,
      ["первый", ""],
      [{ start: 0, end: 30 }],
    );
    expect(missing).toEqual([1]);
  });

  it("ignores a phrase without a translation that stays outside the cut", () => {
    const missing = missingTranslationsInCut(
      phrases,
      ["первый", ""],
      [{ start: 0, end: 20 }],
    );
    expect(missing).toEqual([]);
  });

  it("finds nothing when every phrase is translated", () => {
    const missing = missingTranslationsInCut(
      phrases,
      ["первый", "второй"],
      [{ start: 0, end: 30 }],
    );
    expect(missing).toEqual([]);
  });
});
