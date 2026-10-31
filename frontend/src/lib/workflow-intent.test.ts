import { describe, expect, it } from "vitest";

import { detectWorkflowIntent, looksLikeInstruction, splitSourceAndInstructions } from "./workflow-intent";

const LINK = "https://www.youtube.com/watch?v=dQw4w9WgXcQ";

describe("splitSourceAndInstructions", () => {
  it("separates the first link from the request around it", () => {
    expect(splitSourceAndInstructions(`${LINK} make it a 3 part series`)).toEqual({ url: LINK, rest: "make it a 3 part series" });
    expect(splitSourceAndInstructions(`make a recap: ${LINK}`)).toEqual({ url: LINK, rest: "make a recap" });
    expect(splitSourceAndInstructions(`only the funny bits (${LINK}) please`)).toEqual({ url: LINK, rest: "only the funny bits please" });
    expect(splitSourceAndInstructions(`  ${LINK}  `)).toEqual({ url: LINK, rest: "" });
  });

  it("keeps sentence punctuation out of the link and only takes the first one", () => {
    expect(splitSourceAndInstructions(`Watch ${LINK}. Focus on pricing`).url).toBe(LINK);
    expect(splitSourceAndInstructions(`${LINK}, https://youtu.be/other`)).toEqual({ url: LINK, rest: "https://youtu.be/other" });
    const wiki = "https://en.wikipedia.org/wiki/Foo_(bar)";
    expect(splitSourceAndInstructions(`${wiki} recap`).url).toBe(wiki);
  });

  it("ends the link where Chinese or full-width text runs straight on from it", () => {
    expect(splitSourceAndInstructions(`${LINK}做成三集系列`)).toEqual({ url: LINK, rest: "做成三集系列" });
    expect(splitSourceAndInstructions(`${LINK}，只要关于创业的部分。`)).toEqual({ url: LINK, rest: "只要关于创业的部分。" });
    expect(splitSourceAndInstructions(`请把（${LINK}）剪成预告`)).toEqual({ url: LINK, rest: "请把 剪成预告" });
  });

  it("returns the whole text as the request when there is no link", () => {
    expect(splitSourceAndInstructions(" make a trailer ")).toEqual({ url: null, rest: "make a trailer" });
  });
});

describe("looksLikeInstruction", () => {
  it("accepts requests of two or more words, including Chinese", () => {
    expect(looksLikeInstruction("make a series")).toBe(true);
    expect(looksLikeInstruction("nur die lustigen Stellen")).toBe(true);
    expect(looksLikeInstruction("做成系列")).toBe(true);
  });

  it("rejects links, link fragments and single words", () => {
    expect(looksLikeInstruction(LINK)).toBe(false);
    expect(looksLikeInstruction(`${LINK} recap`)).toBe(false);
    expect(looksLikeInstruction("youtube.com/watch?v=dQw4w9WgXcQ please")).toBe(false);
    expect(looksLikeInstruction("dQw4w9WgXcQ")).toBe(false);
    expect(looksLikeInstruction("recap")).toBe(false);
    expect(looksLikeInstruction("  ")).toBe(false);
    expect(looksLikeInstruction("- -")).toBe(false);
  });
});

describe("detectWorkflowIntent", () => {
  it.each([
    // en
    ["make it a 3 part series", { workflow: "series", partCount: 3 }],
    ["split this into three parts", { workflow: "series", partCount: 3 }],
    ["turn it into a series", { workflow: "series" }],
    ["break it up into several parts", { workflow: "series" }],
    ["give me a quick recap", { workflow: "recap" }],
    ["summarize the main points", { workflow: "recap" }],
    ["sum it up in a minute", { workflow: "recap" }],
    ["cut a teaser for the episode", { workflow: "trailer" }],
    ["only the parts about building a business", { workflow: "topic", topic: "building a business" }],
    // de
    ["mach eine Serie draus", { workflow: "series" }],
    ["in vier Teile aufteilen", { workflow: "series", partCount: 4 }],
    ["eine dreiteilige Reihe", { workflow: "series", partCount: 3 }],
    ["eine kurze Zusammenfassung bitte", { workflow: "recap" }],
    ["fass das Video kurz zusammen", { workflow: "recap" }],
    ["einen Trailer, der neugierig macht", { workflow: "trailer" }],
    ["nur die Stellen über Inflation", { workflow: "topic", topic: "Inflation" }],
    // es
    ["hazlo una serie de 5 partes", { workflow: "series", partCount: 5 }],
    ["divídelo en partes", { workflow: "series" }],
    ["un resumen corto", { workflow: "recap" }],
    ["un tráiler sin spoilers", { workflow: "trailer" }],
    ["solo lo que dice sobre el dinero", { workflow: "topic", topic: "dinero" }],
    // pt
    ["faz uma série em duas partes", { workflow: "series", partCount: 2 }],
    ["quero um resumo do vídeo", { workflow: "recap" }],
    ["um trailer curto", { workflow: "trailer" }],
    ["os momentos sobre investimentos", { workflow: "topic", topic: "investimentos" }],
    // id
    ["jadikan seri 4 bagian", { workflow: "series", partCount: 4 }],
    ["bagi menjadi beberapa bagian", { workflow: "series" }],
    ["buat ringkasan singkat", { workflow: "recap" }],
    ["buat trailer yang bikin penasaran", { workflow: "trailer" }],
    ["bagian tentang bisnis", { workflow: "topic", topic: "bisnis" }],
    // zh
    ["分成三集", { workflow: "series", partCount: 3 }],
    ["做成一个系列", { workflow: "series" }],
    ["帮我总结一下", { workflow: "recap" }],
    ["做一个预告片", { workflow: "trailer" }],
    ["只要关于创业的部分", { workflow: "topic", topic: "创业" }],
  ])("%s", (text, expected) => {
    expect(detectWorkflowIntent(text)).toEqual(expected);
  });

  it("reads part counts in digits and words, and clamps them to 2–10", () => {
    expect(detectWorkflowIntent("a 3-part series").partCount).toBe(3);
    expect(detectWorkflowIntent("six episodes").partCount).toBe(6);
    expect(detectWorkflowIntent("in 12 parts").partCount).toBe(10);
    expect(detectWorkflowIntent("3个部分").partCount).toBe(3);
    expect(detectWorkflowIntent("３集").partCount).toBe(3);
    expect(detectWorkflowIntent("1 part only")).toEqual({});
  });

  it("lets a count, a trailer or a recap win over a subject", () => {
    expect(detectWorkflowIntent("a recap about the money advice")).toEqual({ workflow: "recap" });
    expect(detectWorkflowIntent("a 4 part series about the first startup")).toEqual({ workflow: "series", partCount: 4 });
    expect(detectWorkflowIntent("a trailer about the heist")).toEqual({ workflow: "trailer" });
  });

  it("does not read ordinary instructions as a workflow", () => {
    for (const text of [
      "only the funny moments",
      "cut the boring parts",
      "the best part is the ending",
      "make a series of funny clips",
      "skip the intro of this episode",
      "keep clips about 30 seconds long",
      "clips about a minute long",
      "talk about it",
      "第三段最精彩",
      "剪两段精彩的",
      "nur die besten Stellen",
      "skip the first 2 parts",
      "remove the 3 parts with ads",
      "the 2 parts where he cries",
      "clips about as long as a TikTok",
      "about the same length as before",
      "sobre todo las partes graciosas",
    ]) {
      expect(detectWorkflowIntent(text), text).toEqual({});
    }
  });

  it("reads the subject after a word for topic", () => {
    expect(detectWorkflowIntent("über das Thema Geld")).toEqual({ workflow: "topic", topic: "Geld" });
    expect(detectWorkflowIntent("sobre el tema del dinero")).toEqual({ workflow: "topic", topic: "dinero" });
    expect(detectWorkflowIntent("clips about the topic of money")).toEqual({ workflow: "topic", topic: "money" });
    expect(detectWorkflowIntent("分成三段")).toEqual({ workflow: "series", partCount: 3 });
  });

  it("keeps the subject short and drops politeness", () => {
    expect(detectWorkflowIntent("everything about pricing, and keep it short")).toEqual({ workflow: "topic", topic: "pricing" });
    expect(detectWorkflowIntent("clips about AI please")).toEqual({ workflow: "topic", topic: "AI" });
    expect(detectWorkflowIntent("about hiring and firing people")).toEqual({ workflow: "topic", topic: "hiring and firing people" });
    expect(detectWorkflowIntent("about how he built the company and then sold it to a bigger rival")).toEqual({ workflow: "topic", topic: "how he built the company" });
    expect(detectWorkflowIntent("only where he talks about how the whole thing started back in the nineties in Berlin")).toEqual({});
  });
});
