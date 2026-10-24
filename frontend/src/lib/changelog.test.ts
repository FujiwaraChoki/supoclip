import { readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { parseChangelog, readChangelog } from "./changelog";

const SAMPLE = `# Changelog

Intro that the page does not render.

## 2026-10-24

- **Fixed:** Editing keeps the layout. ([#238](https://github.com/FujiwaraChoki/supoclip/pull/238))
- **New:** Two sources in one line. ([#227](https://github.com/FujiwaraChoki/supoclip/pull/227), [#228](https://github.com/FujiwaraChoki/supoclip/pull/228))

## 2026-10-02

- **Under the hood:** Installs can use a CPU-only image. ([#153](https://github.com/FujiwaraChoki/supoclip/pull/153))
`;

describe("parseChangelog", () => {
  it("keeps dates in file order and splits category, text, and links", () => {
    const releases = parseChangelog(SAMPLE);

    expect(releases.map((release) => release.date)).toEqual(["2026-10-24", "2026-10-02"]);
    expect(releases[0]?.entries[0]).toEqual({
      category: "fixed",
      text: "Editing keeps the layout.",
      links: [{ label: "#238", href: "https://github.com/FujiwaraChoki/supoclip/pull/238" }],
    });
    expect(releases[0]?.entries[1]).toMatchObject({
      category: "new",
      text: "Two sources in one line.",
      links: [
        { label: "#227", href: "https://github.com/FujiwaraChoki/supoclip/pull/227" },
        { label: "#228", href: "https://github.com/FujiwaraChoki/supoclip/pull/228" },
      ],
    });
    expect(releases[1]?.entries[0]?.category).toBe("underTheHood");
  });

  it("rejects an entry with no link or an unknown category", () => {
    expect(() => parseChangelog("## 2026-10-01\n\n- **New:** No link here.")).toThrow(/missing a link/);
    expect(() => parseChangelog("## 2026-10-01\n\n- **Internal:** Hidden. ([#1](https://example.com/1))")).toThrow(
      /Unknown changelog category/,
    );
  });
});

describe("repository changelog", () => {
  const releases = readChangelog();
  const markdown = readFileSync(path.join(process.cwd(), "..", "CHANGELOG.md"), "utf8");

  it("starts on or after 2026-10-01 and lists newer dates first", () => {
    expect(releases[0]?.date >= "2026-10-01").toBe(true);
    const dates = releases.map((release) => release.date);
    expect([...dates].sort((a, b) => b.localeCompare(a))).toEqual(dates);
    expect(releases.at(-1)?.date ?? "").toBe(dates[dates.length - 1]);
    expect((releases.at(-1)?.date ?? "") >= "2026-10-01").toBe(true);
  });

  it("covers the shipped product changes and stays free of private details", () => {
    const text = releases.flatMap((release) => release.entries.map((entry) => entry.text)).join("\n");
    expect(text).toMatch(/Twitch/);
    expect(text).toMatch(/Kick/);
    expect(text).toMatch(/4 GB/);
    expect(text).toMatch(/split-screen/);
    expect(text).toMatch(/Phone videos recorded in portrait/);
    expect(text).toMatch(/\$9\.99/);
    expect(text).toMatch(/Sonnet/);
    expect(markdown).not.toMatch(/[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/i);
    expect(markdown).not.toMatch(/shiori/i);
  });
});
