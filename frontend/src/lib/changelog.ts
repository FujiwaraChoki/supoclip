import { existsSync, readFileSync } from "node:fs";
import path from "node:path";

export const CHANGELOG_CATEGORIES = ["new", "improved", "fixed", "underTheHood"] as const;

export type ChangelogCategory = (typeof CHANGELOG_CATEGORIES)[number];

export interface ChangelogLink {
  label: string;
  href: string;
}

export interface ChangelogEntry {
  category: ChangelogCategory;
  /** User-facing sentence, without the markdown links. */
  text: string;
  links: ChangelogLink[];
}

export interface ChangelogRelease {
  /** ISO date, `YYYY-MM-DD`. */
  date: string;
  entries: ChangelogEntry[];
}

const CATEGORY_BY_LABEL: Record<string, ChangelogCategory> = {
  new: "new",
  improved: "improved",
  fixed: "fixed",
  "under the hood": "underTheHood",
};

const DATE_HEADING = /^## (\d{4}-\d{2}-\d{2})\s*$/;
const ENTRY = /^- \*\*([^*]+):\*\*\s+(\S.*)$/;
const LINK = /\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g;
const LINK_CLUSTER = /\s*\((?:\s*\[[^\]]+\]\(https?:\/\/[^)\s]+\)\s*,?)+\s*\)/g;

export function parseChangelog(markdown: string): ChangelogRelease[] {
  const releases: ChangelogRelease[] = [];
  let current: ChangelogRelease | null = null;

  for (const [index, rawLine] of markdown.split(/\r?\n/).entries()) {
    const line = rawLine.trim();
    const lineNumber = index + 1;
    if (!line || line.startsWith("# ")) continue;

    const dateMatch = DATE_HEADING.exec(line);
    if (dateMatch) {
      const date = dateMatch[1];
      if (releases.some((release) => release.date === date)) {
        throw new Error(`Changelog repeats ${date} (line ${lineNumber})`);
      }
      current = { date, entries: [] };
      releases.push(current);
      continue;
    }

    if (!line.startsWith("- ")) continue;

    if (!current) {
      throw new Error(`Changelog entry has no date heading (line ${lineNumber})`);
    }

    const entryMatch = ENTRY.exec(line);
    if (!entryMatch) {
      throw new Error(`Changelog entry must look like "- **New:** …" (line ${lineNumber})`);
    }

    const category = CATEGORY_BY_LABEL[entryMatch[1].trim().toLowerCase()];
    if (!category) {
      throw new Error(`Unknown changelog category "${entryMatch[1].trim()}" (line ${lineNumber})`);
    }

    const body = entryMatch[2].trim();
    const links = extractLinks(body);
    if (links.length === 0) {
      throw new Error(`Changelog entry is missing a link (line ${lineNumber})`);
    }

    const text = body.replace(LINK_CLUSTER, "").replace(LINK, "").replace(/\s{2,}/g, " ").trim();
    if (!text) {
      throw new Error(`Changelog entry has no user-facing text (line ${lineNumber})`);
    }

    current.entries.push({ category, text, links });
  }

  if (releases.length === 0) {
    throw new Error("Changelog has no dated sections");
  }

  for (const release of releases) {
    if (release.entries.length === 0) {
      throw new Error(`Changelog date ${release.date} has no entries`);
    }
  }

  return releases;
}

function extractLinks(body: string): ChangelogLink[] {
  return [...body.matchAll(LINK)].map((match) => ({
    label: match[1].trim(),
    href: match[2].trim(),
  }));
}

/** Repo-root file in local builds; copied beside the app in the frontend image. */
export function changelogCandidates(cwd = process.cwd()): string[] {
  return [path.join(cwd, "CHANGELOG.md"), path.join(cwd, "..", "CHANGELOG.md")];
}

export function readChangelog(cwd = process.cwd()): ChangelogRelease[] {
  for (const candidate of changelogCandidates(cwd)) {
    if (existsSync(candidate)) {
      return parseChangelog(readFileSync(candidate, "utf8"));
    }
  }
  throw new Error("CHANGELOG.md was not found next to the frontend app or at the repository root.");
}
