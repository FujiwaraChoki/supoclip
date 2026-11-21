import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import { clockAt, drawerErrors, frameOps } from "./core/frame";
import { prepareScene } from "./core/scene";
import { fakeMeasureHost, recordingContext } from "./core/test-utils/fake-host";
import { REPO_ROOT } from "./core/test-utils/napi-host";
import { FX_CAPTION_TEMPLATES, type FxCaptionTemplate } from "./core/types";
import {
  DEMO_DURATION, DEMO_FACE, DEMO_HEIGHT, DEMO_LINE, DEMO_STATIC_T, DEMO_TEMPLATE_LOOKS, DEMO_WIDTH, demoCaptionStyle,
  demoScene, demoWords, paintDemoBackdrop, paintDemoPerson, parseSpokenNumber,
} from "./demo-scene";
import { FX_ASS_FALLBACK, FX_NEEDS_FACE } from "./edit-style-request";

/** The FX template dicts in backend/src/caption_templates.py, read from the source (keys we mirror only). */
function backendFxTemplates(): Record<string, Record<string, unknown>> {
  const source = readFileSync(join(REPO_ROOT, "backend", "src", "caption_templates.py"), "utf8");
  const body = source.slice(source.indexOf("FX_CAPTION_TEMPLATES_DICT"));
  const out: Record<string, Record<string, unknown>> = {};
  for (const id of FX_CAPTION_TEMPLATES) {
    const start = body.indexOf(`"${id}": {`);
    expect(start, id).toBeGreaterThanOrEqual(0);
    const block = body.slice(start, body.indexOf("\n    },", start));
    const fields: Record<string, unknown> = {};
    for (const match of block.matchAll(/^\s+"(\w+)": (.+?),\s*(?:#.*)?$/gm)) {
      const raw = match[2];
      fields[match[1]] = raw === "True" ? true : raw === "False" ? false : raw === "None" ? null
        : raw.startsWith('"') ? JSON.parse(raw) : Number(raw);
    }
    out[id] = fields;
  }
  return out;
}

const backend = backendFxTemplates();

describe("demo scenes", () => {
  it("mirror the backend's animated template looks and fallbacks", () => {
    for (const id of FX_CAPTION_TEMPLATES) {
      const look = DEMO_TEMPLATE_LOOKS[id], dict = backend[id];
      expect({
        font: look.font, size: look.size, color: look.color, accent: look.accent, stroke: look.stroke,
        strokeWidth: look.strokeWidth, shadow: look.shadow, background: look.background, y: look.y,
        wordsPerLine: look.wordsPerLine, uppercase: look.uppercase,
      }, id).toEqual({
        font: dict.font_family, size: dict.font_size, color: dict.font_color, accent: dict.highlight_color,
        stroke: dict.stroke_color, strokeWidth: dict.stroke_width, shadow: dict.shadow,
        background: dict.background ? dict.background_color : null, y: dict.position_y,
        wordsPerLine: dict.max_words_per_line, uppercase: dict.uppercase,
      });
      expect(FX_ASS_FALLBACK[id], id).toBe(dict.ass_fallback);
      expect(FX_NEEDS_FACE.has(id), id).toBe(dict.needs_face);
    }
  });

  it("sizes captions like the generation path", () => {
    // fx_caption_style at 1080 wide: clamp(round(30 · 1080 / 560), 26, 132) = 58, outline round(58 · 3 / 26) = 7.
    expect(demoCaptionStyle("marker_circle")).toMatchObject({ size_px: 58, stroke_px: 7, stroke_color: "#000000", y: 0.78 });
    // The listed styles demo their Bold look (look_caption_style: 72 px at 1080, the highlighter's dark pill).
    expect(demoCaptionStyle("highlighter_swipe")).toMatchObject({ size_px: 72, stroke_px: 0, stroke_color: null,
      background: "#170904D9", accent: "#34FD87", look: "bold" });
    // Tiles enlarge captions (and their outline) so a thumbnail stays legible.
    expect(demoCaptionStyle("marker_circle", 1080, 1.7)).toMatchObject({ size_px: 99, stroke_px: 11 });
    expect(demoScene("slam", { captionScale: 1.7 }).caption?.size_px).toBe(Math.round(66 * 1.7));
  });

  it.each(FX_CAPTION_TEMPLATES)("%s draws through the whole loop without drawer errors", (template: FxCaptionTemplate) => {
    const scene = demoScene(template);
    expect(scene).toMatchObject({ width: DEMO_WIDTH, height: DEMO_HEIGHT, edit_duration: DEMO_DURATION, template });
    const ids = scene.cues.map((cue) => cue.id);
    expect(new Set(ids).size).toBe(ids.length);
    for (const cue of scene.cues) {
      expect(cue.start).toBeGreaterThanOrEqual(0);
      expect(cue.end).toBeLessThanOrEqual(DEMO_DURATION);
      expect(cue.end).toBeGreaterThanOrEqual(cue.start);
    }
    const prepared = prepareScene(scene, fakeMeasureHost);
    for (let t = 0; t < DEMO_DURATION; t += 1 / 15) frameOps(prepared, clockAt(prepared, t));
    expect(drawerErrors(prepared)).toEqual([]);
  });

  it.each(FX_CAPTION_TEMPLATES)("%s shows its accent in the reduced-motion frame", (template: FxCaptionTemplate) => {
    const scene = demoScene(template);
    const withCues = frameOps(prepareScene(scene, fakeMeasureHost), clockAt(prepareScene(scene, fakeMeasureHost), DEMO_STATIC_T));
    const bare = prepareScene({ ...scene, cues: [] }, fakeMeasureHost);
    const without = frameOps(bare, clockAt(bare, DEMO_STATIC_T));
    expect(JSON.stringify(withCues)).not.toEqual(JSON.stringify(without));
  });

  it("puts the face track on the silhouette's face and the behind cue on a person", () => {
    const prepared = prepareScene(demoScene("face_doodles"), fakeMeasureHost);
    for (const t of [0, DEMO_STATIC_T, DEMO_DURATION - 0.01]) {
      const face = prepared.faceAt(t);
      expect(face).not.toBeNull();
      expect(face!.x).toBeCloseTo(DEMO_FACE.x, 0);
      expect(face!.y).toBeCloseTo(DEMO_FACE.y, 0);
      expect(face!.w).toBeCloseTo(DEMO_FACE.w, 0);
      expect(face!.landmarks).toHaveLength(10);
    }
    const behind = demoScene("behind_head");
    expect(behind.matte_windows).toEqual([{ start: behind.cues[0].start, end: behind.cues[0].end, person: true }]);
  });

  it("counts the spoken number", () => {
    const counter = demoScene("stat_counter").cues[0];
    expect(counter).toMatchObject({ variant: "counter", text: "300%", value: 300, prefix: "", suffix: "%" });
    expect(parseSpokenNumber("$2.5M")).toEqual({ value: 2.5, prefix: "$", suffix: "M" });
    expect(parseSpokenNumber("1,250")).toEqual({ value: 1250, prefix: "", suffix: "" });
    expect(parseSpokenNumber("7")).toEqual({ value: 7, prefix: "", suffix: "" });
    expect(parseSpokenNumber("revenue")).toBeNull();
  });

  it("uses every language's demo line, and falls back when one can't carry the demo", () => {
    const root = join(process.cwd(), "messages");
    for (const locale of readdirSync(root)) {
      const line = JSON.parse(readFileSync(join(root, locale, "home.json"), "utf8")).animatedCaptions.demoLine;
      const words = demoWords(line);
      expect(words.map((word) => word.text).join(" "), locale).toBe(line);
      expect(words.some((word) => word.highlight), locale).toBe(true);
      expect(words[words.length - 1].end, locale).toBeLessThan(DEMO_DURATION);
    }
    expect(demoWords("too short").map((word) => word.text).join(" ")).toBe(DEMO_LINE);
    expect(demoWords("a line with no number in it at all").map((word) => word.text).join(" ")).toBe(DEMO_LINE);
  });

  it("paints the backdrop and the speaker deterministically", () => {
    const draw = (paint: typeof paintDemoBackdrop) => {
      const { ctx, calls } = recordingContext(360, 640);
      paint(ctx, 360, 640);
      return JSON.stringify(calls);
    };
    expect(draw(paintDemoBackdrop)).toBe(draw(paintDemoBackdrop));
    expect(draw(paintDemoPerson)).toBe(draw(paintDemoPerson));
    // Balanced save/restore: painting never leaks state into the host's canvas.
    for (const paint of [paintDemoBackdrop, paintDemoPerson]) {
      const { ctx, calls } = recordingContext(360, 640);
      paint(ctx, 360, 640);
      const saves = calls.filter(([name]) => name === "save").length;
      expect(saves).toBeGreaterThan(0);
      expect(calls.filter(([name]) => name === "restore").length).toBe(saves);
    }
  });
});
