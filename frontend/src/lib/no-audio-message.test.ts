import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";

import { expect, it } from "vitest";

const root = join(process.cwd(), "messages");
const locales = readdirSync(root);
const english = JSON.parse(readFileSync(join(root, "en", "home.json"), "utf8")).errors.noAudio as string;

it("includes every shipped UI locale", () => {
  expect(locales).toEqual(expect.arrayContaining(["en", "es", "pt", "id", "de", "zh"]));
});

it.each(locales)("%s localizes a video that has no audio track", (locale) => {
  const home = JSON.parse(readFileSync(join(root, locale, "home.json"), "utf8"));
  const task = JSON.parse(readFileSync(join(root, locale, "task.json"), "utf8"));
  expect(home.errors.noAudio.length).toBeGreaterThan(40);
  expect(task.noAudio).toBe(home.errors.noAudio);
  if (locale !== "en") expect(home.errors.noAudio).not.toBe(english);
});
