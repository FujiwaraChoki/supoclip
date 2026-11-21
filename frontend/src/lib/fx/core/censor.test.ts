// @vitest-environment node
import { describe, expect, it } from "vitest";

import { censorText, censoredWords } from "./censor";

describe("fx-core censor (mirror of edit_fx/censor.py)", () => {
  it("keeps the first letter and stars the rest, keeping punctuation", () => {
    expect(censorText("fuck")).toBe("f***");
    expect(censorText("Shit!")).toBe("S***!");
    expect(censorText("\"damn,\"")).toBe("\"d***,\"");
    expect(censorText("f-ing")).toBe("f-***");
    expect(censorText("")).toBe("");
    expect(censorText("...")).toBe("...");
  });

  it("is unicode-aware (code points, letters only)", () => {
    expect(censorText("Scheiße")).toBe("S******");
    expect(censorText("¡mierda!")).toBe("¡m*****!");
    expect(censorText("блять")).toBe("б****");
    expect(censorText("くそ")).toBe("く*");
    expect(censorText("a2b")).toBe("a2*");
    expect(censorText("💩shit")).toBe("💩s***");
    // A combining mark is not a letter (Python isalpha() is False too): kept as is.
    expect(censorText("ée")).toBe("é*");
  });

  it("censors words a span covers by at least 30 ms", () => {
    const words = [
      { id: "w0", start: 0, end: 0.4, text: "what" },
      { id: "w1", start: 0.4, end: 0.8, text: "the" },
      { id: "w2", start: 0.8, end: 1.2, text: "hell" },
    ];
    const out = censoredWords(words, [[0.77, 1.3]]);
    expect(out.map((w) => w.text)).toEqual(["what", "t**", "h***"]);
    expect(censoredWords(words, [[0.78, 1.3]]).map((w) => w.text)).toEqual(["what", "the", "h***"]);
    expect(out[0]).not.toBe(words[0]);
    expect(words[2].text).toBe("hell");      // inputs untouched
    expect(out[2]).toEqual({ id: "w2", start: 0.8, end: 1.2, text: "h***" });
    expect(censoredWords(words, [])).toEqual(words);
  });
});
