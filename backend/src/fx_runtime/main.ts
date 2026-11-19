// P2 of the dynamic-edits chain (FINAL §4.4, §6.3): raw RGBA frames in E from P1 on stdin, fx-core draws each OUTPUT
// frame (F clock, through the warp), raw RGBA out on stdout to P3.
//
//   deno run --cached-only --frozen --no-prompt --allow-ffi --allow-read=… --allow-env=… --allow-sys=homedir \
//     src/fx_runtime/main.ts render <scratch>/job.json
//   deno run … src/fx_runtime/main.ts selftest
//
// Exit codes: 0 ok, 2 bad job file, 3 render error, 4 stdout closed early.

import type { Canvas } from "@napi-rs/canvas";
import { createCanvas } from "@napi-rs/canvas";
import { cssFont, type Ctx, DenoFxHost, type PictureOp } from "./host.ts";
import {
  type ByteSink,
  type ByteSource,
  editTimeAt,
  emitStderr,
  EXIT_BAD_JOB,
  EXIT_OK,
  EXIT_RENDER,
  EXIT_STDOUT_CLOSED,
  FPS,
  FrameReader,
  type FxEvent,
  type FxJobFile,
  inputIndexFor,
  JobError,
  parseJob,
  sceneHasMatte,
  StdoutClosed,
  writeAll,
} from "./protocol.ts";

/** What the host uses from fx-core (§4.3). `PreparedScene` is opaque here. */
export interface FxCore {
  prepareScene(scene: unknown, host: unknown): unknown;
  isPassthrough(prepared: unknown, clock: FxClock): boolean;
  renderFrame(ctx: Ctx, prepared: unknown, clock: FxClock, host: unknown): void;
  FX_CORE_VERSION?: string;
}

export interface FxClock {
  e: number; // edit seconds
  o: number; // final seconds
  frame: number; // final frame index
}

export interface RenderIO {
  stdin: ByteSource;
  stdout: ByteSink;
  emit(event: FxEvent): void;
  debug?: boolean;
}

export interface RenderStats {
  framesWritten: number;
  framesRead: number;
  passthrough: number;
  msTotal: number;
  msDraw: number;
  drained: number;
}

export class RenderError extends Error {
  override name = "RenderError";
}

export async function loadCore(): Promise<FxCore> {
  return (await import("./fx-core.js")) as unknown as FxCore;
}

function resetContext(ctx: Ctx, w: number, h: number): void {
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.globalAlpha = 1;
  ctx.globalCompositeOperation = "source-over";
  ctx.filter = "none";
  ctx.shadowBlur = 0;
  ctx.shadowColor = "rgba(0,0,0,0)";
  ctx.fillStyle = "#000";
  ctx.fillRect(0, 0, w, h);
}

/**
 * Renders one job. For output frame n: o = n/30, e = toEdit(o), input index i = min(floor(e*30 + 1e-6), inFrames-1).
 * Input is read forward until i is current (skipped frames are discarded); holds and slow motion reuse the current
 * frame; if input ends early the last frame repeats; extra input is drained before returning.
 */
export async function runRender(job: FxJobFile, core: FxCore, io: RenderIO): Promise<RenderStats> {
  const started = performance.now();
  const { width: w, height: h, scene } = job;
  const frameBytes = w * h * 4;
  const warn = (message: string) => io.emit({ ev: "warn", message });

  const host = new DenoFxHost(w, h, { hasMatte: sceneHasMatte(scene) });
  host.registerFonts(job.fonts, warn);
  await host.loadAtlas(job.atlas, warn);

  let prepared: unknown;
  try {
    prepared = core.prepareScene(scene, host);
  } catch (error) {
    throw new RenderError(`prepareScene failed: ${(error as Error).message}`);
  }

  const out: Canvas = host.createCanvas(w, h);
  const ctx = out.getContext("2d");
  const reader = new FrameReader(io.stdin);
  // Two buffers: a frame that ends early (partial read at EOF) must not overwrite the last good frame.
  let current = new Uint8Array(frameBytes);
  let spare = new Uint8Array(frameBytes);
  let currentIndex = -1;
  const crossfade = scene.loop_ending === "crossfade";
  const stats: RenderStats = {
    framesWritten: 0,
    framesRead: 0,
    passthrough: 0,
    msTotal: 0,
    msDraw: 0,
    drained: 0,
  };

  for (let n = 0; n < job.outFrames; n++) {
    const o = n / FPS;
    const e = editTimeAt(scene.warp, scene.edit_duration, o);
    const want = inputIndexFor(e, job.inFrames);
    let advanced = false;
    while (currentIndex < want && !reader.eof) {
      if (!(await reader.next(spare))) break;
      [current, spare] = [spare, current];
      currentIndex++;
      advanced = true;
    }
    if (currentIndex < 0) throw new RenderError("no input frames");
    if (advanced) host.setInputFrame(current, currentIndex);

    const clock: FxClock = { e, o, frame: n };
    let bytes: Uint8Array;
    if (core.isPassthrough(prepared, clock)) {
      // G8: the input buffer is written as is, with no canvas round trip.
      bytes = host.passthroughBytes();
      stats.passthrough++;
      if (n === 0 && crossfade) host.captureFrame0("input");
    } else {
      const t0 = performance.now();
      resetContext(ctx, w, h);
      core.renderFrame(ctx, prepared, clock, host);
      // The canvas's own premultiplied RGBA, zero-copy (aliases the canvas; written before the next draw). Any pixel a
      // drawer left translucent therefore comes out composited over black, which is what "alpha ignored" means in P3.
      const data = out.data();
      bytes = new Uint8Array(data.buffer, data.byteOffset, data.byteLength);
      if (n === 0 && crossfade) host.captureFrame0(out);
      stats.msDraw += performance.now() - t0;
    }
    await writeAll(io.stdout, bytes);
    stats.framesWritten = n + 1;
    if (stats.framesWritten % FPS === 0) {
      io.emit({ ev: "progress", frame: stats.framesWritten, of: job.outFrames });
    }
  }

  stats.drained = await reader.drain();
  stats.framesRead = reader.framesRead;
  const received = stats.framesRead + Math.floor(stats.drained / frameBytes);
  if (received < job.inFrames) {
    // Not fatal (the last frame repeated), but P1 sent less than promised: the chain checks P1's exit status.
    warn(`input ended early: ${received} of ${job.inFrames} frames`);
  }
  stats.msTotal = performance.now() - started;
  if (io.debug) {
    io.emit({
      ev: "debug",
      frames_read: stats.framesRead,
      bytes_drained: stats.drained,
      core: core.FX_CORE_VERSION ?? null,
    });
  }
  io.emit({
    ev: "stats",
    ms_total: Math.round(stats.msTotal),
    ms_draw: Math.round(stats.msDraw),
    passthrough: stats.passthrough,
  });
  return stats;
}

function debugEnabled(): boolean {
  try {
    return !!Deno.env.get("FX_DEBUG");
  } catch {
    return false;
  }
}

/** CLI entry. Returns the process exit code. */
export async function main(args: string[], core?: FxCore): Promise<number> {
  const [command, jobPath] = args;
  if (command === "selftest") {
    try {
      return (await runSelftest(core ?? (await loadCore()), emitStderr)) ? EXIT_OK : EXIT_RENDER;
    } catch (error) {
      emitStderr({ ev: "error", message: `selftest: ${(error as Error).stack ?? error}` });
      return EXIT_RENDER;
    }
  }
  if (command !== "render" || !jobPath) {
    emitStderr({ ev: "error", message: "usage: main.ts render <job.json> | main.ts selftest" });
    return EXIT_BAD_JOB;
  }

  let job: FxJobFile;
  try {
    job = parseJob(await Deno.readTextFile(jobPath));
  } catch (error) {
    emitStderr({ ev: "error", message: `bad job file: ${(error as Error).message}` });
    return EXIT_BAD_JOB;
  }

  try {
    await runRender(job, core ?? (await loadCore()), {
      stdin: Deno.stdin,
      stdout: Deno.stdout,
      emit: emitStderr,
      debug: debugEnabled(),
    });
    return EXIT_OK;
  } catch (error) {
    if (error instanceof StdoutClosed) {
      emitStderr({ ev: "error", message: "stdout closed early" });
      return EXIT_STDOUT_CLOSED;
    }
    if (error instanceof JobError) {
      emitStderr({ ev: "error", message: `bad job file: ${error.message}` });
      return EXIT_BAD_JOB;
    }
    emitStderr({ ev: "error", message: `render failed: ${(error as Error).stack ?? error}` });
    return EXIT_RENDER;
  }
}

// ------------------------------------------------------------------------------------------------------- selftest
// `main.ts selftest`: no stdin. Checks the native canvas, ctx.filter colour maths, font registration, the emoji atlas
// (when the image ships one), the alpha → matte split, and renders 3 synthetic frames of every cue kind through the
// real fx-core bundle with the same loop as `render`. One JSON line {"ev":"selftest",...} on stderr; exit 0 when all
// checks pass.

const SELFTEST_W = 540;
const SELFTEST_H = 960;
const SELFTEST_FRAMES = 3;

type Rgb = [number, number, number];

/** Expected result of one CSS filter function on an opaque sRGB colour (Filter Effects Level 1 matrices). */
function cssFilterReference(fn: string, amount: number, [r, g, b]: Rgb): Rgb {
  const m = (a: number[]): Rgb => [
    a[0] * r + a[1] * g + a[2] * b,
    a[3] * r + a[4] * g + a[5] * b,
    a[6] * r + a[7] * g + a[8] * b,
  ];
  const s = amount;
  switch (fn) {
    case "saturate":
      return m([
        0.213 + 0.787 * s,
        0.715 - 0.715 * s,
        0.072 - 0.072 * s,
        0.213 - 0.213 * s,
        0.715 + 0.285 * s,
        0.072 - 0.072 * s,
        0.213 - 0.213 * s,
        0.715 - 0.715 * s,
        0.072 + 0.928 * s,
      ]);
    case "grayscale":
      return cssFilterReference("saturate", 1 - s, [r, g, b]);
    case "sepia": {
      const k = 1 - s;
      return m([
        0.393 + 0.607 * k,
        0.769 - 0.769 * k,
        0.189 - 0.189 * k,
        0.349 - 0.349 * k,
        0.686 + 0.314 * k,
        0.168 - 0.168 * k,
        0.272 - 0.272 * k,
        0.534 - 0.534 * k,
        0.131 + 0.869 * k,
      ]);
    }
    case "hue-rotate": {
      const a = (s * Math.PI) / 180;
      const c = Math.cos(a);
      const n = Math.sin(a);
      return m([
        0.213 + c * 0.787 - n * 0.213,
        0.715 - c * 0.715 - n * 0.715,
        0.072 - c * 0.072 + n * 0.928,
        0.213 - c * 0.213 + n * 0.143,
        0.715 + c * 0.285 + n * 0.14,
        0.072 - c * 0.072 - n * 0.283,
        0.213 - c * 0.213 - n * 0.787,
        0.715 - c * 0.715 + n * 0.715,
        0.072 + c * 0.928 + n * 0.072,
      ]);
    }
    case "brightness":
      return [r * s, g * s, b * s];
    case "contrast":
      return [r, g, b].map((v) => (v - 127.5) * s + 127.5) as Rgb;
    case "invert":
      return [r, g, b].map((v) => v * (1 - s) + (255 - v) * s) as Rgb;
    case "blur":
      return [r, g, b];
    default:
      throw new Error(`no reference for ${fn}`);
  }
}

const FILTER_CASES: [string, number, string][] = [
  ["saturate", 0, "saturate(0)"],
  ["saturate", 1.6, "saturate(1.6)"],
  ["grayscale", 1, "grayscale(1)"],
  ["sepia", 0.6, "sepia(0.6)"],
  ["hue-rotate", 90, "hue-rotate(90deg)"],
  ["brightness", 0.6, "brightness(0.6)"],
  ["contrast", 1.3, "contrast(1.3)"],
  ["invert", 1, "invert(1)"],
  ["blur", 4, "blur(4px)"],
];

/** Per-function ctx.filter colour check against the CSS matrices (the browser computes the same in sRGB). */
export function checkFilters(
  base: Rgb = [200, 50, 50],
): Record<string, { got: number[]; want: number[]; ok: boolean }> {
  const result: Record<string, { got: number[]; want: number[]; ok: boolean }> = {};
  const src = createCanvas(32, 32);
  const sctx = src.getContext("2d");
  sctx.fillStyle = `rgb(${base.join(",")})`;
  sctx.fillRect(0, 0, 32, 32);
  for (const [fn, amount, css] of FILTER_CASES) {
    const dst = createCanvas(32, 32);
    const ctx = dst.getContext("2d");
    ctx.filter = css;
    ctx.drawImage(src, 0, 0);
    const got = Array.from(ctx.getImageData(16, 16, 1, 1).data.slice(0, 3));
    const want = cssFilterReference(fn, amount, base).map((v) => Math.round(Math.min(255, Math.max(0, v))));
    result[css] = { got, want, ok: got.every((v, i) => Math.abs(v - want[i]) <= 2) };
  }
  return result;
}

function fontsDir(): string {
  return new URL("../../fonts/", import.meta.url).pathname;
}

function assetsDir(): string {
  return new URL("../fx_assets/", import.meta.url).pathname;
}

function exists(path: string): boolean {
  try {
    Deno.statSync(path);
    return true;
  } catch {
    return false;
  }
}

const BASE_CUE = { seed: 1234, beat: "b0", origin: "auto" as const };

/** One cue of every kind (and every accent variant) over the selftest's 0.1 s. */
export function selftestCues(): Record<string, unknown>[] {
  const span = { start: 0, end: 0.1 };
  const accents = [
    "circle",
    "underline",
    "strike",
    "arrow",
    "highlight",
    "emoji",
    "emoji_burst",
    "slam",
    "behind",
    "counter",
    "sticker",
    "decode",
    "kinetic",
    "doodle",
  ].map((variant, i) => ({
    ...BASE_CUE,
    ...span,
    id: `accent_${variant}`,
    seed: 1000 + i,
    kind: "accent",
    variant,
    text: variant === "counter" ? "1,200" : "WAIT",
    emoji: "🔥",
    doodle: "sparkles",
    value: variant === "counter" ? 1200 : null,
    prefix: variant === "counter" ? "$" : "",
    suffix: "",
  }));
  return [
    ...accents,
    { ...BASE_CUE, ...span, id: "zoom", kind: "zoom", mode: "punch", scale: 1.12, anchor: "face" },
    { ...BASE_CUE, ...span, id: "shake", kind: "shake", amp: 12, freq: 9 },
    { ...BASE_CUE, ...span, id: "flash", kind: "flash", color: "#ffffff", peak: 0.8 },
    { ...BASE_CUE, ...span, id: "glitch", kind: "glitch", amount: 0.6 },
    { ...BASE_CUE, ...span, id: "desaturate", kind: "desaturate", amount: 0.8, dim: 0.2 },
    { ...BASE_CUE, start: 0.05, end: 0.05, id: "transition", kind: "transition", style: "whip" },
    { ...BASE_CUE, start: 0, end: 0, id: "sfx", kind: "sfx", sound: "whoosh", variant: 0, gain_db: 0 },
    { ...BASE_CUE, ...span, id: "bleep", kind: "bleep", tone: true, cover: "bar" },
    { ...BASE_CUE, ...span, id: "speed", kind: "speed", rate: 1 },
    { ...BASE_CUE, start: 0.1, end: 0.1, id: "freeze", kind: "freeze", hold: 0.2, label: "WAIT FOR IT" },
    { ...BASE_CUE, ...span, id: "sticker", kind: "sticker", sticker: "wait_for_it", text: "" },
    {
      ...BASE_CUE,
      ...span,
      id: "reaction",
      kind: "reaction",
      query: "wow",
      provider: "giphy",
      asset_id: "selftest",
      url: "",
    },
  ];
}

/** A complete FxScene (§4.1) exercising every cue kind, a caption, a face track, a matte window and the retention UI. */
export function selftestScene(w: number, h: number, fontStem: string): FxJobFile["scene"] {
  const dur = SELFTEST_FRAMES / FPS;
  return {
    version: 1,
    width: w,
    height: h,
    edit_duration: dur,
    out_duration: dur,
    warp: [],
    template: "slam",
    cues: selftestCues() as FxJobFile["scene"]["cues"],
    words: [
      { id: "w0", start: 0, end: 0.05, text: "SUPOCLIP", highlight: true, run: "r0" },
      { id: "w1", start: 0.05, end: 0.1, text: "selftest", highlight: false, run: "r0" },
    ],
    caption: {
      template: "slam",
      font: fontStem,
      size_px: 72,
      color: "#FFFFFF",
      accent: "#FFD400",
      stroke_color: "#000000",
      stroke_px: 6,
      shadow: true,
      background: null,
      y: 0.75,
      words_per_line: 3,
      uppercase: true,
      enabled: true,
      placements: [],
    },
    grade: "punchy",
    grain: 0.3,
    progress_bar: true,
    progress_color: "#FF5A1F",
    loop_ending: "crossfade",
    hook_window: [0, dur],
    hook_title_suppressed: false,
    hook: {
      text: "90% of selftests pass",
      font: fontStem,
      accent: "#34FD87",
      look: "max",
      start: 0,
      end: dur,
      x: 0,
      y: 0,
      scale: 1,
    },
    face_track: {
      version: 1,
      space: "output",
      fps: 6,
      samples: [[0, [[w * 0.3, h * 0.25, w * 0.4, h * 0.25]]]],
    },
    broll_windows: [],
    matte_windows: [{ start: 0, end: dur, person: true }],
    seed: 42,
  };
}

/** A synthetic P1 frame: a gradient picture whose alpha is a person matte (ellipse = 255, background = 0). */
export function syntheticFrame(w: number, h: number, n: number): Uint8Array {
  const rgba = new Uint8Array(w * h * 4);
  const cx = w / 2;
  const cy = h * 0.55;
  const rx = w * 0.28;
  const ry = h * 0.35;
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      const i = (y * w + x) * 4;
      rgba[i] = (x * 255) / w;
      rgba[i + 1] = (y * 255) / h;
      rgba[i + 2] = (n * 70 + 40) & 255;
      const dx = (x - cx) / rx;
      const dy = (y - cy) / ry;
      rgba[i + 3] = dx * dx + dy * dy <= 1 ? 255 : 0;
    }
  }
  return rgba;
}

class MemorySource implements ByteSource {
  #data: Uint8Array;
  #off = 0;
  constructor(data: Uint8Array) {
    this.#data = data;
  }
  read(p: Uint8Array): Promise<number | null> {
    if (this.#off >= this.#data.length) return Promise.resolve(null);
    const n = Math.min(p.length, this.#data.length - this.#off);
    p.set(this.#data.subarray(this.#off, this.#off + n));
    this.#off += n;
    return Promise.resolve(n);
  }
}

class CountingSink implements ByteSink {
  bytes = 0;
  write(p: Uint8Array): Promise<number> {
    this.bytes += p.length;
    return Promise.resolve(p.length);
  }
}

export async function runSelftest(core: FxCore, emit: (event: FxEvent) => void): Promise<boolean> {
  const started = performance.now();
  const checks: Record<string, unknown> = {};
  let ok = true;
  const warnings: string[] = [];

  // 1. ctx.filter colour maths.
  const filters = checkFilters();
  checks.filters = filters;
  if (!Object.values(filters).every((f) => f.ok)) ok = false;

  // 2. A job font registers as FX-<stem> and draws.
  const fontPath = `${fontsDir()}TikTokSans-Bold.ttf`;
  const fonts: Record<string, string> = exists(fontPath) ? { "TikTokSans-Bold": fontPath } : {};
  {
    const host = new DenoFxHost(64, 64, { hasMatte: false });
    host.registerFonts(fonts, (m) => warnings.push(m));
    const canvas = createCanvas(200, 80);
    const ctx = canvas.getContext("2d");
    ctx.fillStyle = "#fff";
    ctx.font = cssFont(host.fontFamily("TikTokSans-Bold"), 48);
    ctx.fillText("Aa", 10, 60);
    const px = ctx.getImageData(0, 0, 200, 80).data;
    let inked = 0;
    for (let i = 3; i < px.length; i += 4) if (px[i] > 0) inked++;
    checks.font = { registered: Object.keys(fonts).length > 0, inked };
    if (inked === 0) ok = false;
  }

  // 3. The emoji atlas, when the image ships one.
  const atlasPng = `${assetsDir()}emoji/atlas.png`;
  const atlasJson = `${assetsDir()}emoji/atlas.json`;
  const atlas = exists(atlasPng) && exists(atlasJson) ? { png: atlasPng, json: atlasJson } : { png: "", json: "" };
  {
    const host = new DenoFxHost(64, 64, { hasMatte: false });
    const atlasWarnings: string[] = [];
    await host.loadAtlas(atlas, (m) => atlasWarnings.push(m));
    checks.atlas = atlas.png ? (host.atlas() ? "loaded" : `failed: ${atlasWarnings.join("; ")}`) : "absent";
    if (atlas.png && !host.atlas()) ok = false;
  }

  // 4. AR-B1: a pixel with alpha 0 keeps its RGB in the base picture; the cutout keeps only the person.
  {
    const host = new DenoFxHost(4, 2, { hasMatte: true });
    const frame = new Uint8Array([
      10,
      200,
      30,
      0,
      10,
      200,
      30,
      0,
      200,
      30,
      10,
      255,
      200,
      30,
      10,
      255,
      10,
      200,
      30,
      0,
      10,
      200,
      30,
      0,
      200,
      30,
      10,
      255,
      200,
      30,
      10,
      255,
    ]);
    host.setInputFrame(frame, 0);
    const canvas = createCanvas(4, 2);
    const ctx = canvas.getContext("2d");
    const op: PictureOp = {
      op: "picture",
      transform: [1, 0, 0, 1, 0, 0],
      filter: "",
      alpha: 1,
      blend: "source-over",
      channel: "rgb",
      dx: 0,
      dy: 0,
      cutout: false,
      clip: null,
    };
    host.drawPicture(ctx, op);
    const base = Array.from(ctx.getImageData(0, 0, 1, 1).data);
    ctx.fillStyle = "#ff00ff";
    ctx.fillRect(0, 0, 4, 2);
    host.drawPicture(ctx, { ...op, cutout: true });
    const bg = Array.from(ctx.getImageData(0, 0, 1, 1).data);
    const person = Array.from(ctx.getImageData(3, 0, 1, 1).data);
    const alphaOk = base.join() === "10,200,30,255" && bg.join() === "255,0,255,255" &&
      person.join() === "200,30,10,255";
    checks.alpha_split = { ok: alphaOk, base, behind_bg: bg, behind_person: person };
    if (!alphaOk) ok = false;
  }

  // 5. Three synthetic frames of every cue kind through the bundled core, pass-through decision bypassed so the
  //    full draw path runs, then the same frames through the `render` loop.
  {
    const w = SELFTEST_W;
    const h = SELFTEST_H;
    const scene = selftestScene(w, h, "TikTokSans-Bold");
    const job: FxJobFile = {
      version: 1,
      width: w,
      height: h,
      fps: 30,
      inFrames: SELFTEST_FRAMES,
      outFrames: SELFTEST_FRAMES,
      scene,
      fonts,
      atlas,
    };
    const host = new DenoFxHost(w, h, { hasMatte: true });
    host.registerFonts(fonts, (m) => warnings.push(m));
    await host.loadAtlas(atlas, (m) => warnings.push(m));
    const prepared = core.prepareScene(scene, host);
    const canvas = host.createCanvas(w, h);
    const ctx = canvas.getContext("2d");
    const t0 = performance.now();
    let opaque = true;
    for (let n = 0; n < SELFTEST_FRAMES; n++) {
      host.setInputFrame(syntheticFrame(w, h, n), n);
      const clock = { e: n / FPS, o: n / FPS, frame: n };
      core.isPassthrough(prepared, clock);
      resetContext(ctx, w, h);
      core.renderFrame(ctx, prepared, clock, host);
      if (n === 0) host.captureFrame0(canvas);
      const data = ctx.getImageData(0, 0, w, h).data;
      for (let i = 3; i < data.length; i += 4 * 97) if (data[i] !== 255) opaque = false;
    }
    const msPerFrame = (performance.now() - t0) / SELFTEST_FRAMES;

    const input = new Uint8Array(w * h * 4 * SELFTEST_FRAMES);
    for (let n = 0; n < SELFTEST_FRAMES; n++) input.set(syntheticFrame(w, h, n), n * w * h * 4);
    const sink = new CountingSink();
    const stats = await runRender(job, core, {
      stdin: new MemorySource(input),
      stdout: sink,
      emit: (event) => {
        if (event.ev === "warn") warnings.push(event.message);
      },
    });
    const bytesOk = sink.bytes === w * h * 4 * SELFTEST_FRAMES && stats.framesWritten === SELFTEST_FRAMES;
    checks.render = {
      ok: bytesOk && opaque,
      opaque,
      bytes: sink.bytes,
      frames: stats.framesWritten,
      cues: scene.cues.length,
      ms_per_frame: Math.round(msPerFrame * 10) / 10,
      core: core.FX_CORE_VERSION ?? null,
    };
    if (!bytesOk || !opaque) ok = false;
  }

  emit({
    ev: "selftest",
    ok,
    deno: Deno.version.deno,
    arch: Deno.build.arch,
    os: Deno.build.os,
    ms_total: Math.round(performance.now() - started),
    warnings,
    checks,
  });
  return ok;
}

if (import.meta.main) {
  Deno.exit(await main(Deno.args));
}
