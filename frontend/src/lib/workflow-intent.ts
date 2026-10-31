/**
 * Reads what people type into the link box. Some paste a link followed by
 * what they want ("https://youtu.be/… make it a 3 part series"); some type
 * only the request. These helpers split the link from the request and guess
 * which workflow it asks for, so the create screen can offer it. Nothing here
 * switches anything on its own: the UI only suggests.
 */

import { SERIES_MAX_PARTS, SERIES_MIN_PARTS, TOPIC_MAX_CHARS, topicIssue, type Workflow } from "@/lib/workflows";

export type WorkflowIntent = { workflow?: Workflow; partCount?: number; topic?: string };

// Chinese and Japanese often run straight on from a link ("…watch?v=x做成三集"):
// video links are ASCII, so CJK and full-width characters end the link.
const URL_PATTERN = /https?:\/\/[^\s<>"'“”‘’«»\u3000-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af\uf900-\ufaff\uff00-\uffef]+/i;
// "youtube.com/watch?v=…", "www.twitch.tv/videos/…": a link without its scheme.
const SCHEMELESS_LINK = /^(?:www\.)?[\p{L}\p{N}-]+(?:\.[\p{L}\p{N}-]+)+\/\S*$/u;
const IDEOGRAPHS = /[぀-ヿ㐀-䶿一-鿿豈-﫿]/gu;
const EDGE_SEPARATORS = /^[\s\-–—:;,|·•>，、：；。]+|[\s\-–—:;,|·•<，、：；]+$/g;

function trimUrl(raw: string): string {
  let url = raw.replace(/[.,;:!?。，；：！？…]+$/u, "");
  // Drop closing brackets the link did not open, e.g. "(https://youtu.be/x)".
  for (const [open, close] of [["(", ")"], ["[", "]"], ["{", "}"]] as const) {
    while (url.endsWith(close) && url.split(open).length < url.split(close).length) {
      url = url.slice(0, -1).replace(/[.,;:!?]+$/, "");
    }
  }
  return url;
}

/** The first http(s) link in `text` and everything else, tidied, as `rest`. */
export function splitSourceAndInstructions(text: string): { url: string | null; rest: string } {
  const match = URL_PATTERN.exec(text);
  if (!match) return { url: null, rest: text.trim() };
  const url = trimUrl(match[0]);
  const before = text.slice(0, match.index);
  const after = text.slice(match.index + url.length);
  const rest = `${before} ${after}`
    .replace(/\(\s*\)|\[\s*\]|<\s*>|“\s*”|"\s*"|（\s*）|【\s*】|「\s*」/g, " ")
    .replace(/\s+/g, " ")
    .replace(EDGE_SEPARATORS, "")
    .trim();
  return { url, rest };
}

/** Text that reads like a request rather than a link: no link in it, and at least two words. */
export function looksLikeInstruction(text: string): boolean {
  const trimmed = text.trim();
  if (!trimmed || URL_PATTERN.test(trimmed)) return false;
  const tokens = trimmed.split(/\s+/);
  if (tokens.some((token) => SCHEMELESS_LINK.test(token))) return false;
  const words = tokens.filter((token) => /[\p{L}\p{N}]/u.test(token.replace(IDEOGRAPHS, "")));
  // Chinese and Japanese do not separate words with spaces; each ideograph counts.
  const ideographs = trimmed.match(IDEOGRAPHS)?.length ?? 0;
  return words.length + ideographs >= 2;
}

// Matching runs on lowercase text without accents ("Tráiler" → "trailer",
// "três" → "tres"), so each list only needs one spelling.
function fold(text: string): string {
  return text.normalize("NFKD").replace(/\p{M}+/gu, "").toLowerCase();
}

const WORD_START = "(?<![\\p{L}\\p{N}])";
const WORD_END = "(?![\\p{L}\\p{N}])";

const NUMBER_WORDS: Record<string, number> = {
  // en
  two: 2, three: 3, four: 4, five: 5, six: 6, seven: 7, eight: 8, nine: 9, ten: 10,
  // de
  zwei: 2, drei: 3, vier: 4, funf: 5, sechs: 6, sieben: 7, acht: 8, neun: 9, zehn: 10,
  // es / pt
  dos: 2, dois: 2, duas: 2, tres: 3, cuatro: 4, quatro: 4, cinco: 5, seis: 6, siete: 7, sete: 7,
  ocho: 8, oito: 8, nueve: 9, nove: 9, diez: 10, dez: 10,
  // id
  dua: 2, tiga: 3, empat: 4, lima: 5, enam: 6, tujuh: 7, delapan: 8, sembilan: 9, sepuluh: 10,
  // zh
  两: 2, 二: 2, 三: 3, 四: 4, 五: 5, 六: 6, 七: 7, 八: 8, 九: 9, 十: 10,
};

const LATIN_NUMBER = `(\\d{1,2}|${Object.keys(NUMBER_WORDS).filter((word) => /^[a-z]+$/.test(word)).join("|")})`;
// "the 2 parts where he cries", "skip the first 3 parts": parts of the video, not a series.
const NAMED_PARTS = "(?<!(?:the|first|last|these|those|other|die|der|den|ersten|letzten|beiden|los|las|primeros|primeras|ultimos|ultimas|os|as|primeiros|primeiras)\\s+)";
// "3 parts", "three-part", "drei Teile", "dreiteilig", "en 4 partes", "5 bagian", "6 episodes".
const LATIN_PART_COUNT = new RegExp(
  `${WORD_START}${NAMED_PARTS}${LATIN_NUMBER}\\s*-?\\s*(?:parts?|episodes?|teile?n?|teilig\\w*|folgen?|partes?|episodios?|bagian)${WORD_END}`,
  "u",
);
// "三集", "3个部分", "分成三段". A bare "两段" is "two clips" and "第三段" is "the third
// segment", so 段 and 篇 only count after a verb that splits.
const CHINESE_PART_COUNT = /(?<!第)(\d{1,2}|[两二三四五六七八九十])\s*[个個]?\s*(?:集|部分)|(?:分成|拆成|切成|分为|分為)\s*(\d{1,2}|[两二三四五六七八九十])\s*[个個]?\s*(?:段|篇)/u;

// "a series of clips" means "several clips", not a series.
const SERIES_WORDS = new RegExp(
  `${WORD_START}(?:(?:mini-?)?series|serie|miniserie|seri|berseri|multi-?part|mehrteilig\\w*)${WORD_END}(?!\\s+(?:of|de|di|von)${WORD_END})`,
  "u",
);
const PART_NOUNS = "(?:parts|pieces|episodes|teile|folgen|partes|episodios|bagian)";
const SEVERAL = "(?:multiple|several|a\\s+few|mehrere|einige|ein\\s+paar|varias|varios|algunas|algunos|algumas|alguns|beberapa)";
// "into several parts", "in mehrere Teile", "em várias partes", "jadi beberapa bagian",
// "split it into parts", "teile es in Folgen", "divídelo en partes", "bagi menjadi bagian".
// "cut the boring parts" stays out: the parts must follow "into", "in", "en", "em" or "jadi".
const SPLIT_INTO_PARTS = new RegExp(
  `${WORD_START}(?:(?:into|in|en|em|jadi|menjadi)\\s+${SEVERAL}\\s+|into\\s+|` +
    `(?:split|break|divide|chop|teil|aufteil|unterteil|divid|separ|bagi|pecah)\\w*(?:\\s+\\S+){0,3}?\\s+(?:into|in|en|em|jadi|menjadi)\\s+(?:\\S+\\s+)?)` +
    `${PART_NOUNS}${WORD_END}`,
  "u",
);
const CHINESE_SERIES = /系列|连载|連載|(?:分成|拆成|切成|分为|分為)[^，。,.]{0,4}(?:集|部分|段|篇)/u;

const RECAP = new RegExp(
  `${WORD_START}(?:recap\\w*|summary|summari[sz]\\w*|sum\\s+(?:it\\s+|this\\s+|everything\\s+)?up|tl;?dr|` +
    `zusammenfass\\w*|resumen|resumir|resumelo|resumela|resumo|resuma|resumid[oa]|` +
    `ringkas\\w*|rangkum\\w*)${WORD_END}|${WORD_START}fass\\w*\\s+(?:\\S+\\s+){0,4}zusammen${WORD_END}|总结|摘要|回顾|概括|總結|回顧`,
  "u",
);
const TRAILER = new RegExp(
  `${WORD_START}(?:trailer|teaser|preview|avance|vorschau|pratinjau|cuplikan\\s+film)${WORD_END}|预告|預告`,
  "u",
);

// "about X", "only the parts about X", "über X", "sobre X", "tentang X", "关于X".
// Idioms are left out: "about as long as", "about the same", "sobre todo" ("above all").
const TOPIC_LATIN = /(?<![\p{L}\p{N}])(?:about(?!\s+(?:as|the\s+same)(?![\p{L}\p{N}]))|regarding|related\s+to|zum\s+thema|über|ueber|sobre(?!\s+todo(?![\p{L}\p{N}]))|acerca\s+de|tentang|mengenai)\s+(.+)/iu;
const TOPIC_CHINESE = /(?:关于|有关|關於|有關)\s*(.+?)(?:的(?:部分|内容|片段|时刻|地方|事情?)|[，。；！？,.;!?]|$)/u;
const CLAUSE_END = /[\n.,;:!?()。，；：！？]/u;
const CLAUSE_WORDS = /\s+(?:and|but|then|so|because|und|aber|dann|weil|y|pero|luego|porque|e|mas|depois|dan|tapi|lalu|karena)\s+/iu;
const POLITE_TAIL = /\s+(?:please|pls|thanks|bitte|danke|por\s+favor|gracias|obrigad[oa]|tolong|ya|dong)$/iu;
const LEADING_ARTICLE = /^(?:the|die|der|das|den|dem|el|la|los|las|o|a|os|as)\s+/iu;
// "das Thema Geld", "el tema del dinero", "the topic of money": the subject is what follows.
const TOPIC_WORD = /^(?:topic\s+of|thema|tema(?:\s+(?:de\s+la|del|de|da|do))?|topik|assunto(?:\s+(?:de|da|do))?)\s+/iu;
const NOT_A_TOPIC = new Set([
  "it", "this", "that", "these", "those", "them", "him", "her", "me", "us", "you", "what", "anything", "something",
  "es", "das", "dies", "eso", "esto", "ello", "isso", "isto", "itu", "ini",
]);
const TOPIC_MAX_WORDS = 6;
// "about a minute", "sobre um minuto": a length, not a subject.
const TIME_UNIT = /^(?:(?:a|an|one|half|half\s+an?|un|una|um|uma|ein|eine|einer|satu|setengah)\s+)?(?:minutes?|mins?|seconds?|secs?|hours?|minutos?|segundos?|horas?|minuten?|sekunden?|stunden?|menit|detik|jam)(?![\p{L}\p{N}])/u;

function cleanTopic(raw: string): string | undefined {
  let topic = raw.split(CLAUSE_END)[0].trim().replace(POLITE_TAIL, "").trim();
  if (TIME_UNIT.test(fold(topic))) return undefined;
  if (topic.split(/\s+/).length > TOPIC_MAX_WORDS) topic = topic.split(CLAUSE_WORDS)[0].trim();
  if (topic.split(/\s+/).length > TOPIC_MAX_WORDS) return undefined;
  const subject = topic.replace(LEADING_ARTICLE, "").replace(TOPIC_WORD, "").replace(LEADING_ARTICLE, "");
  if (subject) topic = subject;
  topic = topic.replace(/^["'“”‘’«»]+|["'“”‘’«»]+$/g, "").trim();
  // "about 30 seconds" is a length, not a subject.
  if (/^\d/.test(topic) || NUMBER_WORDS[fold(topic.split(/\s+/)[0] ?? "")] !== undefined) return undefined;
  if (NOT_A_TOPIC.has(fold(topic))) return undefined;
  if (topic.length > TOPIC_MAX_CHARS || topicIssue(topic) !== null) return undefined;
  return topic;
}

function readTopic(text: string): string | undefined {
  const chinese = TOPIC_CHINESE.exec(text);
  if (chinese) {
    const topic = cleanTopic(chinese[1]);
    if (topic) return topic;
  }
  const latin = TOPIC_LATIN.exec(text);
  return latin ? cleanTopic(latin[1]) : undefined;
}

function readPartCount(folded: string): number | undefined {
  const match = LATIN_PART_COUNT.exec(folded) ?? CHINESE_PART_COUNT.exec(folded);
  if (!match) return undefined;
  const count = match[1] ?? match[2];
  const value = /^\d+$/.test(count) ? Number(count) : NUMBER_WORDS[count];
  // "1 part" is not a series; more than the maximum still means "as many as you can".
  if (!value || value < SERIES_MIN_PARTS) return undefined;
  return Math.min(SERIES_MAX_PARTS, value);
}

/**
 * The workflow a request reads like, in en, de, es, pt, id and zh. An explicit
 * part count, a trailer or a recap wins over "about X", so "a recap about the
 * money advice" stays a recap and keeps its focus as instructions.
 */
export function detectWorkflowIntent(text: string): WorkflowIntent {
  const original = text.normalize("NFKC");
  const folded = fold(original);
  const partCount = readPartCount(folded);
  if (partCount) return { workflow: "series", partCount };
  if (TRAILER.test(folded)) return { workflow: "trailer" };
  if (RECAP.test(folded)) return { workflow: "recap" };
  if (SERIES_WORDS.test(folded) || SPLIT_INTO_PARTS.test(folded) || CHINESE_SERIES.test(folded)) return { workflow: "series" };
  const topic = readTopic(original);
  if (topic) return { workflow: "topic", topic };
  return {};
}
