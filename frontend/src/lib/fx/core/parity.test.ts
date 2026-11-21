// @vitest-environment node
// Cross-language parity (G2): replays shared/fx/fixtures/{rng,warp,mapping,duck,censor,geometry}/*.json, written by
// backend/scripts/fx/gen_fixtures.py from the Python implementation, through the TS mirror. Every value bit-exact
// (same IEEE operations in the same order on both sides). Skipped when the fixtures are missing unless
// FX_REQUIRE_FIXTURES=1 (CI after gate 1).
// FX_SHARED_DIR overrides the shared/fx root (local checks against fixtures generated elsewhere).
import { existsSync, readdirSync, readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import { captionSafe, HOOK_BAND, reactionRect, unit } from "./constants";
import { censorText, censoredWords } from "./censor";
import { duckEnvelope, envelopeDbAt, speechSpans, type DuckOptions } from "./duck";
import { mappedCaptionRows, mappedCues, type SegmentSpan } from "./mapping";
import { groupRow } from "./scene";
import { fnv1a32, hash01, mulberry32 } from "./rng";
import { groupedWords, phraseGroups } from "./captions/groups";
import { rowCuts, type CaptionRow, type FxCue } from "./types";
import { buildWarp, isNontrivial, toEdit, toOutput, warpToJson } from "./warp";

const SHARED = process.env.FX_SHARED_DIR
  ? resolve(process.env.FX_SHARED_DIR)
  : resolve(fileURLToPath(new URL(".", import.meta.url)), "../../../../../shared/fx");
const ROOT = join(SHARED, "fixtures");
const FAMILIES = ["rng", "warp", "mapping", "duck", "censor", "geometry", "groups", "rows"] as const;
const REQUIRED = process.env.FX_REQUIRE_FIXTURES === "1";
const TOL = 0;   // the mirror is bit-for-bit (§20 WP3a); a non-zero tolerance would hide drift

interface Fixture { family: string; case: string; fn: string; input: Record<string, unknown>; expected: unknown }

function load(family: string): { file: string; fixture: Fixture }[] {
  const dir = join(ROOT, family);
  if (!existsSync(dir)) return [];
  return readdirSync(dir)
    .filter((f) => f.endsWith(".json"))
    .sort()
    .map((file) => ({ file, fixture: JSON.parse(readFileSync(join(dir, file), "utf8")) as Fixture }));
}

/** Deep comparison with an absolute float tolerance; returns the mismatching paths. */
function diff(actual: unknown, expected: unknown, path = "$", out: string[] = []): string[] {
  if (typeof expected === "number" && typeof actual === "number") {
    if (!(Math.abs(actual - expected) <= TOL || actual === expected)) out.push(`${path}: ${actual} != ${expected}`);
  } else if (Array.isArray(expected)) {
    if (!Array.isArray(actual) || actual.length !== expected.length) {
      out.push(`${path}: ${JSON.stringify(actual)} != ${JSON.stringify(expected)}`);
    } else expected.forEach((v, i) => diff(actual[i], v, `${path}[${i}]`, out));
  } else if (expected && typeof expected === "object") {
    if (!actual || typeof actual !== "object" || Array.isArray(actual)) {
      out.push(`${path}: ${JSON.stringify(actual)} != ${JSON.stringify(expected)}`);
    } else {
      const a = actual as Record<string, unknown>;
      const e = expected as Record<string, unknown>;
      const keys = new Set([...Object.keys(a), ...Object.keys(e)]);
      for (const k of [...keys].sort()) {
        if (!(k in e)) out.push(`${path}.${k}: unexpected key`);
        else if (!(k in a)) out.push(`${path}.${k}: missing`);
        else diff(a[k], e[k], `${path}.${k}`, out);
      }
    }
  } else if (actual !== expected) {
    out.push(`${path}: ${JSON.stringify(actual)} != ${JSON.stringify(expected)}`);
  }
  return out;
}

const num = (v: unknown) => v as number;

/** Runs one fixture through the TS mirror. Returns the value to compare with `expected`. */
function run(fx: Fixture): unknown {
  const i = fx.input;
  switch (`${fx.family}/${fx.fn}`) {
    case "rng/fnv1a32":
      return fnv1a32(i.text as string);
    case "rng/mulberry32": {
      const next = mulberry32(num(i.seed));
      return Array.from({ length: num(i.n) }, () => next());
    }
    case "rng/hash01":
      return hash01(num(i.seed), num(i.n));
    case "warp/build_warp": {
      const w = buildWarp(i.cues as FxCue[], num(i.edit_duration));
      return {
        pieces: w.pieces,
        edit_duration: w.editDuration,
        out_duration: w.outDuration,
        nontrivial: isNontrivial(w),
        to_output: ((i.probes_e as number[]) ?? []).map((e) => toOutput(w, e)),
        to_edit: ((i.probes_o as number[]) ?? []).map((o) => toEdit(w, o)),
        json: warpToJson(w),
      };
    }
    case "mapping/mapped_cues":
      return mappedCues(i.segments as SegmentSpan[], i.cues as FxCue[], (i.min_piece as number | undefined) ?? 0.04);
    case "duck/duck_envelope": {
      const spans = speechSpans(i.words_final as [number, number][], (i.merge_gap as number | undefined) ?? 0.35);
      const points = duckEnvelope(spans, i.params as unknown as DuckOptions);
      return { spans, points, at: ((i.probes as number[]) ?? []).map((t) => envelopeDbAt(points, t)) };
    }
    case "censor/censor_text":
      return censorText(i.text as string);
    case "censor/censored_words":
      return censoredWords(i.words as { start: number; end: number; text: string }[], i.spans as [number, number][]);
    case "groups/phrase_groups": {
      const order = groupedWords(i.words as { start: number; end: number; text: string; run?: string }[]);
      return phraseGroups(order, num(i.max_words)).map((g) => ({
        ...g, text: order.slice(g.first, g.last + 1).map((w) => w.text).join(" "),
      }));
    }
    case "rows/group_rows": {
      // == layoutLookCaptions: phrase groups ended by the rows' cuts, each on the row where it starts showing.
      const order = groupedWords(i.words as { start: number; end: number; text: string; run?: string }[]);
      const style = { y: num(i.y), rows: i.rows as CaptionRow[] };
      return phraseGroups(order, num(i.max_words), rowCuts(style.rows)).map((g) => ({
        first: g.first, last: g.last, start: g.start, end: g.end, cut: g.cut ?? null, until: g.until ?? null,
        y: groupRow(style, g.cut ?? g.start) ?? style.y,
      }));
    }
    case "rows/mapped_caption_rows":
      return mappedCaptionRows(i.segments as SegmentSpan[], i.rows as CaptionRow[]);
    case "geometry/geometry": {
      const w = num(i.width);
      const h = num(i.height);
      return { unit: unit(w, h), reaction_rect: reactionRect(w, h), caption_safe: captionSafe(w, h), hook_band: HOOK_BAND };
    }
    default:
      throw new Error(`no TS mirror for fixture fn ${fx.family}/${fx.fn}`);
  }
}

const present = FAMILIES.some((f) => load(f).length > 0);

describe.skipIf(!present && !REQUIRED)("fx-core ↔ edit_fx parity fixtures", () => {
  it("has fixtures for every family", () => {
    for (const family of FAMILIES) expect(load(family).length, `${family} fixtures in ${ROOT}`).toBeGreaterThan(0);
  });

  for (const family of FAMILIES) {
    describe(family, () => {
      for (const { file, fixture } of load(family)) {
        it(`${fixture.case ?? file} (${fixture.fn})`, () => {
          const actual = run(fixture);
          if (family === "rng") {
            expect(actual).toEqual(fixture.expected);     // bit-exact
          } else {
            const problems = diff(JSON.parse(JSON.stringify(actual)), fixture.expected);
            expect(problems, `${family}/${file}`).toEqual([]);
          }
        });
      }
    });
  }
});
