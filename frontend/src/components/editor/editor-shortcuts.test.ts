import { describe, expect, it } from "vitest";
import de from "../../../messages/de/editor.json";
import en from "../../../messages/en/editor.json";
import es from "../../../messages/es/editor.json";
import id from "../../../messages/id/editor.json";
import pt from "../../../messages/pt/editor.json";
import zh from "../../../messages/zh/editor.json";
import { SHORTCUTS } from "./editor-workspace";

const LOCALES = { de, en, es, id, pt, zh } as Record<string, Record<string, unknown>>;

function lookup(messages: Record<string, unknown>, key: string): unknown {
  return key.split(".").reduce<unknown>((node, part) => (node as Record<string, unknown> | undefined)?.[part], messages);
}

describe("studio shortcuts dialog", () => {
  it("says Delete removes the selected B-roll or edit", () => {
    expect(SHORTCUTS.find(([keys]) => keys[0] === "Delete")?.[1]).toBe("shortcuts.removeSelection");
    expect(lookup(en, "shortcuts.removeSelection")).toBe("Remove the selected B-roll or edit");
  });

  it("has every label in every language", () => {
    for (const [locale, messages] of Object.entries(LOCALES))
      for (const [, key] of SHORTCUTS)
        expect(typeof lookup(messages, key), `${locale}: ${key}`).toBe("string");
  });
});
