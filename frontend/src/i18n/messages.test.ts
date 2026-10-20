import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { NAMESPACES, locales } from "./config";

const messagesRoot = join(process.cwd(), "messages");

function readCatalog(locale: string) {
  const dir = join(messagesRoot, locale);
  const files = readdirSync(dir).filter((file) => file.endsWith(".json")).sort();
  const catalog: Record<string, unknown> = {};
  for (const file of files) {
    catalog[file.replace(/\.json$/, "")] = JSON.parse(readFileSync(join(dir, file), "utf8"));
  }
  return { files, catalog };
}

function flatten(value: unknown, prefix = "", output = new Map<string, string>()) {
  if (typeof value === "string") {
    output.set(prefix, value);
    return output;
  }
  if (value && typeof value === "object" && !Array.isArray(value)) {
    for (const [key, child] of Object.entries(value)) {
      flatten(child, prefix ? `${prefix}.${key}` : key, output);
    }
    return output;
  }
  throw new Error(`Unsupported message at ${prefix || "<root>"}`);
}

function placeholderNames(message: string) {
  const names: string[] = [];
  let depth = 0;
  let start = -1;
  for (let index = 0; index < message.length; index += 1) {
    const char = message[index];
    if (char === "{") {
      if (depth === 0) start = index + 1;
      depth += 1;
    } else if (char === "}") {
      depth -= 1;
      if (depth === 0 && start !== -1) {
        const name = message.slice(start, index).split(",")[0]?.trim() ?? "";
        if (/^[A-Za-z_][\w]*$/.test(name)) names.push(name);
        start = -1;
      }
    }
  }
  return names;
}

describe("message catalogs", () => {
  const english = readCatalog("en");

  it("ships the same namespaces for every locale", () => {
    const expected = NAMESPACES.map((namespace) => `${namespace}.json`).sort();
    for (const locale of locales) {
      expect(readCatalog(locale).files).toEqual(expected);
    }
  });

  it("keeps keys and placeholders aligned with English", () => {
    const source = flatten(english.catalog);
    for (const locale of locales) {
      if (locale === "en") continue;
      const translated = flatten(readCatalog(locale).catalog);
      expect([...translated.keys()].sort()).toEqual([...source.keys()].sort());
      for (const [key, message] of source) {
        const other = translated.get(key);
        expect(other, `${locale}:${key}`).toBeTruthy();
        expect(placeholderNames(other ?? "").sort()).toEqual(placeholderNames(message).sort());
      }
    }
  });
});
