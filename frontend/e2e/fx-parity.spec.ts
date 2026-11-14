import { expect, test } from "@playwright/test";
import { execFileSync, spawnSync } from "node:child_process";
import { mkdirSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { build } from "esbuild";
import { PNG } from "pngjs";

// Cross-renderer parity (FINAL §20 WP21): the same fx-core scene drawn by real Chromium (browser FxHost, the studio
// preview and browser export) and by the server's deno + @napi-rs/canvas host (P2 of the render chain, through the
// committed bundle backend/src/fx_runtime/fx-core.js), compared at full resolution: mean absolute difference
// < 6/255 and IoU > 0.9 of the "FX mask" (pixels either renderer changed versus the input picture).
// Scenes are the server's studio scene fixtures (shared/fx/fixtures/scene, made by gen_scene_fixtures.py), so a
// scene the server would render is exactly what both sides draw. No backend or Next page is needed.
// deno 2.9.8 must be on PATH with the fx_runtime npm cache (`deno task test` in backend/src/fx_runtime); without it
// the spec skips unless FX_REQUIRE_PARITY=1. FX_PARITY_CORE=<bundle.js> renders the server side with another bundle
// (e.g. `node scripts/build-fx-core.mjs --out /tmp/fx-core.js` to try a change before rebuilding the committed one).

const REPO = path.resolve(__dirname, "../..");
const RUNTIME = path.join(REPO, "backend/src/fx_runtime");
const FONTS = path.join(REPO, "backend/fonts");
const ATLAS = path.join(REPO, "backend/src/fx_assets/emoji");
const SCENES = path.join(REPO, "shared/fx/fixtures/scene");
const OUT = path.resolve("test-results/fx-parity");
const ORIGIN = "http://fx-parity.local";

const MAX_MEAN_ABS = 6 / 255;
const MIN_IOU = 0.9;
const MASK_DELTA = 24;  // a channel moved more than this from the input picture ⇒ the pixel carries FX

interface Case { name: string; fixture: string; frame: number; what: string }
// o = frame / 30 lands on one exact output frame on both sides. Times sit inside the cue windows of the fixtures.
const CASES: Case[] = [
  { name: "portrait_zoom_hook", fixture: "single_segment", frame: 18, what: "zoom punch-in, marker_circle captions, hook title" },
  { name: "highlighter_accent", fixture: "caption_placements", frame: 117, what: "highlighter_swipe accent, placed captions" },
  { name: "square_flash_shake", fixture: "square_contain_framing", frame: 63, what: "1080x1080 flash + shake" },
  { name: "landscape_glitch", fixture: "landscape_original_cover_zoom", frame: 135, what: "1920x1080 glitch + accent" },
  { name: "grade_grain_progress", fixture: "hook_timing_and_globals", frame: 30, what: "teal_orange grade, grain, progress bar, hook" },
  { name: "warp_freeze_shake", fixture: "warp_across_cuts", frame: 69, what: "warped clock (freeze hold) + shake" },
];

const fontStems = () => readdirSync(FONTS).filter((f) => /\.(ttf|otf)$/i.test(f)).map((f) => f.replace(/\.(ttf|otf)$/i, ""));
const fontFile = (stem: string) => readdirSync(FONTS).find((f) => f.replace(/\.(ttf|otf)$/i, "") === stem)!;
const sceneOf = (fixture: string) => JSON.parse(readFileSync(path.join(SCENES, `${fixture}.json`), "utf8")).expected;

/** A deterministic opaque test picture in pure integer maths (no canvas, so both sides get identical bytes). */
function testPicture(w: number, h: number): Uint8Array {
  const px = new Uint8Array(w * h * 4);
  const stripe = Math.max(4, Math.round(Math.min(w, h) / 18));
  const fx = w * 0.5, fy = h * 0.34, rx = w * 0.14, ry = h * 0.1;
  for (let y = 0; y < h; y++) {
    const t = y / h;
    for (let x = 0; x < w; x++) {
      let r = 34 + 104 * t, g = 48 + 42 * t, b = 104 - 30 * t;
      if (Math.floor((x + y * 0.6) / stripe) % 2 === 0) { r += 14; g += 14; b += 14; }
      const gx = (x - w * 0.7) / Math.max(w, h), gy = (y - h * 0.2) / Math.max(w, h);
      const glow = Math.max(0, 1 - Math.sqrt(gx * gx + gy * gy) * 2) * 70;
      r += glow; g += glow * 0.75; b += glow * 0.4;
      const dx = (x - fx) / rx, dy = (y - fy) / ry;
      if (dx * dx + dy * dy <= 1) { r = 201; g = 143; b = 111; }
      else if (y > fy + ry * 1.3 && Math.abs(x - fx) < rx * 2.4 * Math.min(1, (y - fy - ry * 1.3) / (ry * 0.8) + 0.5)) { r = 29; g = 31; b = 38; }
      const i = (y * w + x) * 4;
      px[i] = Math.min(255, Math.round(r));
      px[i + 1] = Math.min(255, Math.round(g));
      px[i + 2] = Math.min(255, Math.round(b));
      px[i + 3] = 255;
    }
  }
  return px;
}

/** The deno half: the fx_runtime host, the committed fx-core bundle and the chain's per-frame steps (main.ts). */
function denoScript(): string {
  const at = (file: string) => JSON.stringify(pathToFileURL(path.join(RUNTIME, file)).href);
  return `
import { DenoFxHost } from ${at("host.ts")};
import { editTimeAt } from ${at("protocol.ts")};
const manifest = JSON.parse(await Deno.readTextFile(Deno.args[0]));
const core = await import(manifest.core);
const warn = (m) => console.error("warn: " + m);
const report = [];
let fontsDone = false;
for (const c of manifest.cases) {
  const scene = c.scene, w = scene.width, h = scene.height;
  const host = new DenoFxHost(w, h, { hasMatte: false });
  if (!fontsDone) { host.registerFonts(manifest.fonts, warn); fontsDone = true; }
  await host.loadAtlas(manifest.atlas, warn);
  host.setInputFrame(await Deno.readFile(c.picture), 0);
  const prepared = core.prepareScene(scene, host);
  const o = c.frame / 30;
  const clock = { e: editTimeAt(scene.warp, scene.edit_duration, o), o, frame: c.frame };
  const out = host.createCanvas(w, h);
  const ctx = out.getContext("2d");
  ctx.fillStyle = "#000";
  ctx.fillRect(0, 0, w, h);
  core.renderFrame(ctx, prepared, clock, host);
  await Deno.writeFile(c.out, await out.encode("png"));
  report.push({ name: c.name, passthrough: core.isPassthrough(prepared, clock), core: core.FX_CORE_VERSION });
}
console.log(JSON.stringify(report));
`;
}

function hasDeno(): boolean {
  return spawnSync("deno", ["--version"], { stdio: "ignore" }).status === 0;
}

function compare(browser: PNG, server: PNG, picture: Uint8Array) {
  const a = browser.data, b = server.data, n = browser.width * browser.height;
  let sum = 0, inter = 0, union = 0, maxDiff = 0, bad = 0;
  const diff = new PNG({ width: browser.width, height: browser.height });
  for (let p = 0; p < n; p++) {
    const i = p * 4;
    let ma = 0, mb = 0, md = 0;
    for (let k = 0; k < 3; k++) {
      const d = Math.abs(a[i + k] - b[i + k]);
      sum += d;
      md = Math.max(md, d);
      ma = Math.max(ma, Math.abs(a[i + k] - picture[i + k]));
      mb = Math.max(mb, Math.abs(b[i + k] - picture[i + k]));
    }
    if (md > 32) bad++;
    maxDiff = Math.max(maxDiff, md);
    const inA = ma > MASK_DELTA, inB = mb > MASK_DELTA;
    if (inA && inB) inter++;
    if (inA || inB) union++;
    diff.data[i] = diff.data[i + 1] = diff.data[i + 2] = Math.min(255, md * 4);
    diff.data[i + 3] = 255;
  }
  return {
    meanAbs: sum / (n * 3) / 255,
    iou: union === 0 ? 1 : inter / union,
    fxPixels: union,
    over32: bad / n,
    maxDiff,
    diff,
  };
}

test.describe("fx-core parity: Chromium canvas vs deno/napi host", () => {
  test.describe.configure({ mode: "serial", timeout: 180_000 });

  const server = new Map<string, PNG>();
  const pictures = new Map<string, Uint8Array>();
  let bundle = "";

  test.beforeAll(async () => {
    if (!hasDeno()) {
      if (process.env.FX_REQUIRE_PARITY === "1") throw new Error("deno is not on PATH (FX_REQUIRE_PARITY=1)");
      test.skip(true, "deno is not on PATH");
    }
    mkdirSync(OUT, { recursive: true });
    const fonts = Object.fromEntries(fontStems().map((stem) => [stem, path.join(FONTS, fontFile(stem))]));
    const cases = CASES.map((c) => {
      const scene = sceneOf(c.fixture);
      const key = `${scene.width}x${scene.height}`;
      if (!pictures.has(key)) {
        const bytes = testPicture(scene.width, scene.height);
        pictures.set(key, bytes);
        writeFileSync(path.join(OUT, `picture-${key}.rgba`), bytes);
      }
      return { name: c.name, scene, frame: c.frame, picture: path.join(OUT, `picture-${key}.rgba`), out: path.join(OUT, `${c.name}.deno.png`) };
    });
    writeFileSync(path.join(OUT, "manifest.json"), JSON.stringify({
      core: pathToFileURL(process.env.FX_PARITY_CORE ? path.resolve(process.env.FX_PARITY_CORE) : path.join(RUNTIME, "fx-core.js")).href,
      fonts, atlas: { png: path.join(ATLAS, "atlas.png"), json: path.join(ATLAS, "atlas.json") }, cases,
    }));
    writeFileSync(path.join(OUT, "render.js"), denoScript());
    const stdout = execFileSync("deno", [
      "run", "--config", path.join(RUNTIME, "deno.json"), "--frozen", "--cached-only", "--no-prompt",
      "--allow-ffi", "--allow-read", "--allow-write", "--allow-env", "--allow-sys=homedir",
      path.join(OUT, "render.js"), path.join(OUT, "manifest.json"),
    ], { encoding: "utf8", env: { ...process.env, DISABLE_SYSTEM_FONTS_LOAD: "1" }, maxBuffer: 1 << 24 });
    const report = JSON.parse(stdout.trim().split("\n").pop()!) as { name: string; passthrough: boolean }[];
    for (const r of report) expect(r.passthrough, `${r.name} must draw FX, not pass through`).toBe(false);
    for (const c of cases) server.set(c.name, PNG.sync.read(readFileSync(c.out)));

    const built = await build({
      entryPoints: [path.join(__dirname, "fx-parity/browser-entry.ts")],
      bundle: true, format: "iife", platform: "browser", target: "es2022", write: false, logLevel: "warning",
    });
    bundle = built.outputFiles[0].text;
  });

  for (const c of CASES) {
    test(`${c.name}: ${c.what}`, async ({ page }) => {
      await page.route(`${ORIGIN}/**`, async (route) => {
        const url = new URL(route.request().url());
        if (url.pathname === "/") return route.fulfill({ contentType: "text/html", body: "<!doctype html><html><body></body></html>" });
        if (url.pathname === "/bundle.js") return route.fulfill({ contentType: "text/javascript", body: bundle });
        if (url.pathname === "/atlas.png") return route.fulfill({ contentType: "image/png", body: readFileSync(path.join(ATLAS, "atlas.png")) });
        if (url.pathname === "/atlas.json") return route.fulfill({ contentType: "application/json", body: readFileSync(path.join(ATLAS, "atlas.json")) });
        if (url.pathname.startsWith("/fonts/")) {
          const stem = decodeURIComponent(url.pathname.slice("/fonts/".length));
          return route.fulfill({ contentType: "font/ttf", body: readFileSync(path.join(FONTS, fontFile(stem))) });
        }
        return route.fulfill({ status: 404, body: "" });
      });
      await page.goto(`${ORIGIN}/`);
      await page.addScriptTag({ url: `${ORIGIN}/bundle.js` });
      await page.evaluate((stems) => (globalThis as unknown as { fxParity: { setup(s: string[]): Promise<void> } }).fxParity.setup(stems), fontStems());

      const scene = sceneOf(c.fixture);
      const picture = pictures.get(`${scene.width}x${scene.height}`)!;
      const png = await page.evaluate(
        ([s, o, pic]) => (globalThis as unknown as { fxParity: { render(s: unknown, o: number, p: string): Promise<string> } }).fxParity.render(s, o, pic),
        [scene, c.frame / 30, Buffer.from(picture).toString("base64")] as const,
      );
      const browser = PNG.sync.read(Buffer.from(png, "base64"));
      writeFileSync(path.join(OUT, `${c.name}.chrome.png`), Buffer.from(png, "base64"));
      const deno = server.get(c.name)!;
      expect([browser.width, browser.height]).toEqual([deno.width, deno.height]);

      const result = compare(browser, deno, picture);
      writeFileSync(path.join(OUT, `${c.name}.diff.png`), PNG.sync.write(result.diff));
      const line = `${c.name}: meanAbs=${(result.meanAbs * 255).toFixed(3)}/255 iou=${result.iou.toFixed(4)} ` +
        `fxPixels=${result.fxPixels} over32=${(result.over32 * 100).toFixed(3)}% maxDiff=${result.maxDiff}`;
      console.log(line);
      test.info().annotations.push({ type: "parity", description: line });
      expect(result.fxPixels, "the frame must carry FX").toBeGreaterThan(1000);
      expect(result.meanAbs, line).toBeLessThan(MAX_MEAN_ABS);
      expect(result.iou, line).toBeGreaterThan(MIN_IOU);
    });
  }
});
