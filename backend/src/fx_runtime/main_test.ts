// deno test for P2 (FINAL §4.4, WP6). Runs against whatever fx-core.js is in the tree (the placeholder until gate 1,
// the generated bundle after): scenes without cues are pass-through in both. The alpha/cutout and frame0 cases use a
// small fake core so they test the host, not the drawers.
//
//   cd backend/src/fx_runtime && deno task test

import { assert, assertEquals } from "@std/assert";
import { DenoFxHost, FONT_FALLBACK, fxFontCss, type PictureOp } from "./host.ts";
import { type FxClock, type FxCore, loadCore, main, runRender, runSelftest, selftestCues } from "./main.ts";
import {
  type ByteSink,
  type ByteSource,
  editTimeAt,
  EXIT_BAD_JOB,
  EXIT_OK,
  EXIT_STDOUT_CLOSED,
  type FxEvent,
  type FxJobFile,
  inputIndexFor,
  JobError,
  parseJob,
  type WarpPieceJson,
} from "./protocol.ts";

const W = 16;
const H = 8;
const FRAME = W * H * 4;
const MAIN = new URL("./main.ts", import.meta.url).pathname;

// ----------------------------------------------------------------------------------------------------- helpers

/** Input frame n: a flat colour that encodes n, opaque. */
function frame(n: number, alpha = 255): Uint8Array {
  const out = new Uint8Array(FRAME);
  for (let i = 0; i < out.length; i += 4) {
    out[i] = n;
    out[i + 1] = 255 - n;
    out[i + 2] = (n * 7) & 255;
    out[i + 3] = alpha;
  }
  return out;
}

function frames(count: number): Uint8Array {
  const out = new Uint8Array(FRAME * count);
  for (let n = 0; n < count; n++) out.set(frame(n), n * FRAME);
  return out;
}

/** Reads `stdin` in uneven chunks so frame reassembly is exercised. */
class ChunkedSource implements ByteSource {
  #data: Uint8Array;
  #off = 0;
  #chunk: number;
  consumed = 0;
  constructor(data: Uint8Array, chunk = 1000) {
    this.#data = data;
    this.#chunk = chunk;
  }
  read(p: Uint8Array): Promise<number | null> {
    if (this.#off >= this.#data.length) return Promise.resolve(null);
    const n = Math.min(p.length, this.#chunk, this.#data.length - this.#off);
    p.set(this.#data.subarray(this.#off, this.#off + n));
    this.#off += n;
    this.consumed = this.#off;
    return Promise.resolve(n);
  }
}

class Sink implements ByteSink {
  chunks: Uint8Array[] = [];
  closeAfter: number;
  written = 0;
  constructor(closeAfter = Infinity) {
    this.closeAfter = closeAfter;
  }
  write(p: Uint8Array): Promise<number> {
    if (this.written >= this.closeAfter) return Promise.reject(new Deno.errors.BrokenPipe("closed"));
    this.chunks.push(p.slice());
    this.written += p.length;
    return Promise.resolve(p.length);
  }
  bytes(): Uint8Array {
    const out = new Uint8Array(this.written);
    let off = 0;
    for (const c of this.chunks) {
      out.set(c, off);
      off += c.length;
    }
    return out;
  }
  /** The value of the first byte (red) of every written frame: which input frame it came from. */
  frameIds(): number[] {
    const all = this.bytes();
    const ids: number[] = [];
    for (let off = 0; off < all.length; off += FRAME) ids.push(all[off]);
    return ids;
  }
}

function scene(opts: {
  edit: number;
  out?: number;
  warp?: WarpPieceJson[];
  cues?: FxJobFile["scene"]["cues"];
  matte?: { start: number; end: number; person: boolean }[];
  loop?: "none" | "crossfade";
}): FxJobFile["scene"] {
  return {
    version: 1,
    width: W,
    height: H,
    edit_duration: opts.edit,
    out_duration: opts.out ?? opts.edit,
    warp: opts.warp ?? [],
    template: "default",
    cues: opts.cues ?? [],
    words: [],
    caption: null,
    grade: "none",
    grain: 0,
    progress_bar: false,
    progress_color: "#FFFFFF",
    loop_ending: opts.loop ?? "none",
    hook_window: null,
    hook_title_suppressed: false,
    face_track: null,
    broll_windows: [],
    matte_windows: opts.matte ?? [],
    seed: 1,
  };
}

function job(inFrames: number, outFrames: number, sc: FxJobFile["scene"]): FxJobFile {
  return {
    version: 1,
    width: W,
    height: H,
    fps: 30,
    inFrames,
    outFrames,
    scene: sc,
    fonts: {},
    atlas: { png: "", json: "" },
  };
}

async function render(j: FxJobFile, input: Uint8Array, core?: FxCore, sink = new Sink()) {
  const events: FxEvent[] = [];
  const source = new ChunkedSource(input);
  const stats = await runRender(j, core ?? (await loadCore()), {
    stdin: source,
    stdout: sink,
    emit: (e) => events.push(e),
  });
  return { stats, sink, events, source };
}

const IDENTITY: PictureOp = {
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

type FakeOp = PictureOp | { op: "rect"; fill: string } | { op: "frame0"; alpha: number };

/** A core that is never pass-through: picture, then (inside a behind window) magenta "text" and the cutout. */
function fakeCore(opsAt: (clock: FxClock) => FakeOp[]): FxCore {
  return {
    FX_CORE_VERSION: "fake",
    prepareScene: (s) => s,
    isPassthrough: () => false,
    renderFrame(ctx, _p, clock, host) {
      const h = host as DenoFxHost;
      for (const op of opsAt(clock)) {
        if (op.op === "picture") h.drawPicture(ctx, op);
        else if (op.op === "rect") {
          ctx.fillStyle = op.fill;
          ctx.fillRect(0, 0, W, H);
        } else {
          const f0 = h.frame0();
          if (f0) {
            ctx.globalAlpha = op.alpha;
            ctx.drawImage(f0, 0, 0);
            ctx.globalAlpha = 1;
          }
        }
      }
    },
  };
}

function pixel(bytes: Uint8Array, frameIndex: number, x: number, y: number): number[] {
  const off = frameIndex * FRAME + (y * W + x) * 4;
  return Array.from(bytes.subarray(off, off + 4));
}

// ----------------------------------------------------------------------------------------------------- warp maths

Deno.test("editTimeAt is the identity without a warp and clamps to the edit duration", () => {
  assertEquals(editTimeAt([], 2, 0), 0);
  assertEquals(editTimeAt([], 2, 1.5), 1.5);
  assertEquals(editTimeAt([], 2, 3), 2);
  for (let n = 0; n < 300; n++) assertEquals(inputIndexFor(editTimeAt([], 10, n / 30), 300), n);
});

Deno.test("editTimeAt holds across a freeze and scales inside a speed piece", () => {
  const warp: WarpPieceJson[] = [
    { e0: 0, e1: 1, o0: 0, rate: 1, hold: 0 },
    { e0: 1, e1: 1, o0: 1, rate: 0, hold: 0.5 },
    { e0: 1, e1: 2, o0: 1.5, rate: 2, hold: 0 },
  ];
  assertEquals(editTimeAt(warp, 2, 0.5), 0.5);
  assertEquals(editTimeAt(warp, 2, 1.0), 1);
  assertEquals(editTimeAt(warp, 2, 1.49), 1);
  assertEquals(editTimeAt(warp, 2, 1.75), 1.5);
  assertEquals(editTimeAt(warp, 2, 5), 2);
  let last = 0;
  for (let n = 0; n < 60; n++) {
    const e = editTimeAt(warp, 2, n / 30);
    assert(e >= last, "monotone");
    last = e;
  }
});

// ----------------------------------------------------------------------------------------------------- frame counts

Deno.test("identity warp: one output frame per input frame, byte-equal pass-through", async () => {
  const input = frames(10);
  const { sink, stats, events } = await render(job(10, 10, scene({ edit: 10 / 30 })), input);
  assertEquals(sink.written, 10 * FRAME);
  assertEquals(sink.bytes(), input);
  assertEquals(stats.passthrough, 10);
  const statsEvent = events.find((e) => e.ev === "stats");
  assert(statsEvent && statsEvent.ev === "stats" && statsEvent.passthrough === 10);
});

Deno.test("freeze warp: the frozen frame repeats for the hold and the output is longer", async () => {
  // 10 input frames; a 3-frame hold at e = 0.1 (input frame 3).
  const warp: WarpPieceJson[] = [
    { e0: 0, e1: 0.1, o0: 0, rate: 1, hold: 0 },
    { e0: 0.1, e1: 0.1, o0: 0.1, rate: 0, hold: 0.1 },
    { e0: 0.1, e1: 10 / 30, o0: 0.2, rate: 1, hold: 0 },
  ];
  const { sink } = await render(job(10, 13, scene({ edit: 10 / 30, out: 13 / 30, warp })), frames(10));
  assertEquals(sink.written, 13 * FRAME);
  assertEquals(sink.frameIds(), [0, 1, 2, 3, 3, 3, 3, 4, 5, 6, 7, 8, 9]);
});

Deno.test("speed warp: a 2x piece skips frames and the skipped input is consumed", async () => {
  const warp: WarpPieceJson[] = [{ e0: 0, e1: 10 / 30, o0: 0, rate: 2, hold: 0 }];
  const { sink, stats, source } = await render(job(10, 5, scene({ edit: 10 / 30, out: 5 / 30, warp })), frames(10));
  assertEquals(sink.frameIds(), [0, 2, 4, 6, 8]);
  assertEquals(stats.framesRead + Math.round(stats.drained / FRAME), 10);
  assertEquals(source.consumed, 10 * FRAME);
});

Deno.test("slow motion: a 0.5x piece shows every input frame twice", async () => {
  const warp: WarpPieceJson[] = [{ e0: 0, e1: 4 / 30, o0: 0, rate: 0.5, hold: 0 }];
  const { sink } = await render(job(4, 8, scene({ edit: 4 / 30, out: 8 / 30, warp })), frames(4));
  assertEquals(sink.frameIds(), [0, 0, 1, 1, 2, 2, 3, 3]);
});

Deno.test("short input: the last frame repeats until outFrames are written", async () => {
  const { sink, stats, events } = await render(job(10, 10, scene({ edit: 10 / 30 })), frames(6));
  assertEquals(sink.written, 10 * FRAME);
  assertEquals(sink.frameIds(), [0, 1, 2, 3, 4, 5, 5, 5, 5, 5]);
  assertEquals(stats.framesRead, 6);
  assertEquals(events.filter((e) => e.ev === "warn"), [{ ev: "warn", message: "input ended early: 6 of 10 frames" }]);
});

Deno.test("short input with a partial last frame: the last whole frame repeats, intact", async () => {
  const input = frames(4);
  const partial = new Uint8Array(3 * FRAME + FRAME / 2);
  partial.set(input.subarray(0, partial.length));
  partial.fill(99, 3 * FRAME); // garbage in the partial frame must never be shown
  const { sink } = await render(job(4, 4, scene({ edit: 4 / 30 })), partial);
  assertEquals(sink.frameIds(), [0, 1, 2, 2]);
  assertEquals(pixel(sink.bytes(), 3, W - 1, H - 1), Array.from(frame(2).subarray(0, 4)));
});

Deno.test("extra input is drained before exit", async () => {
  const input = frames(15);
  const { sink, stats, source } = await render(job(10, 10, scene({ edit: 10 / 30 })), input);
  assertEquals(sink.frameIds(), [0, 1, 2, 3, 4, 5, 6, 7, 8, 9]);
  assertEquals(source.consumed, input.length);
  assertEquals(stats.drained, 5 * FRAME);
});

Deno.test("input the warp skips at the end is drained and not reported as missing", async () => {
  const warp: WarpPieceJson[] = [{ e0: 0, e1: 10 / 30, o0: 0, rate: 2, hold: 0 }];
  const { events, stats } = await render(job(10, 5, scene({ edit: 10 / 30, out: 5 / 30, warp })), frames(10));
  assertEquals(stats.framesRead + stats.drained / FRAME, 10);
  assertEquals(events.filter((e) => e.ev === "warn"), []);
});

Deno.test("progress every 30 frames and a final stats line", async () => {
  const { events } = await render(job(65, 65, scene({ edit: 65 / 30 })), frames(65));
  const progress = events.filter((e) => e.ev === "progress");
  assertEquals(progress, [{ ev: "progress", frame: 30, of: 65 }, { ev: "progress", frame: 60, of: 65 }]);
  assertEquals(events.at(-1)?.ev, "stats");
});

// ----------------------------------------------------------------------------------------------------- alpha (AR-B1)

const LEFT_BG: [number, number, number] = [10, 200, 30]; // alpha 0 in the input: background around the person
const RIGHT_PERSON: [number, number, number] = [200, 30, 10]; // alpha 255: the person

/** Left half background (alpha 0), right half person (alpha 255). */
function matteFrame(): Uint8Array {
  const out = new Uint8Array(FRAME);
  for (let y = 0; y < H; y++) {
    for (let x = 0; x < W; x++) {
      const i = (y * W + x) * 4;
      const person = x >= W / 2;
      out.set([...(person ? RIGHT_PERSON : LEFT_BG), person ? 255 : 0], i);
    }
  }
  return out;
}

const BEHIND_CUE = { kind: "accent", variant: "behind", start: 0.1, end: 0.2 };

Deno.test("alpha 0 outside a behind window keeps its RGB; inside, the cutout draws the person only", async () => {
  const sc = scene({ edit: 0.3, cues: [BEHIND_CUE], matte: [{ start: 0.1, end: 0.2, person: true }] });
  const core = fakeCore((clock) =>
    clock.e >= 0.1 && clock.e < 0.2
      ? [IDENTITY, { op: "rect", fill: "#ff00ff" }, { ...IDENTITY, cutout: true }]
      : [IDENTITY]
  );
  const input = new Uint8Array(9 * FRAME);
  for (let n = 0; n < 9; n++) input.set(matteFrame(), n * FRAME);
  const { sink } = await render(job(9, 9, sc), input, core);
  const out = sink.bytes();
  // Frame 0 is outside the window: drawn through the canvas, the background keeps its colour (no black).
  assertEquals(pixel(out, 0, 1, 1), [...LEFT_BG, 255]);
  assertEquals(pixel(out, 0, W - 2, 1), [...RIGHT_PERSON, 255]);
  // Frame 4 (e = 0.133) is inside: the behind text shows around the person, the person is drawn over it.
  assertEquals(pixel(out, 4, 1, 1), [255, 0, 255, 255]);
  assertEquals(pixel(out, 4, W - 2, 1), [...RIGHT_PERSON, 255]);
  // Frame 8 is outside again.
  assertEquals(pixel(out, 8, 1, 1), [...LEFT_BG, 255]);
});

Deno.test("pass-through frames of a matte scene keep their RGB", async () => {
  const sc = scene({ edit: 0.1, cues: [BEHIND_CUE], matte: [{ start: 0.1, end: 0.2, person: true }] });
  const passthrough: FxCore = { ...fakeCore(() => []), isPassthrough: () => true };
  const input = new Uint8Array(3 * FRAME);
  for (let n = 0; n < 3; n++) input.set(matteFrame(), n * FRAME);
  const { sink } = await render(job(3, 3, sc), input, passthrough);
  assertEquals(pixel(sink.bytes(), 1, 1, 1).slice(0, 3), LEFT_BG);
  assertEquals(pixel(sink.bytes(), 1, W - 2, 1).slice(0, 3), RIGHT_PERSON);
});

Deno.test("cutout after the frame was forced opaque still uses the saved matte", () => {
  const host = new DenoFxHost(W, H, { hasMatte: true });
  host.setInputFrame(matteFrame(), 0);
  const canvas = host.createCanvas(W, H);
  const ctx = canvas.getContext("2d");
  host.drawPicture(ctx, IDENTITY); // forces the frame opaque
  ctx.fillStyle = "#0000ff";
  ctx.fillRect(0, 0, W, H);
  host.drawPicture(ctx, { ...IDENTITY, cutout: true });
  assertEquals(Array.from(ctx.getImageData(1, 1, 1, 1).data), [0, 0, 255, 255]);
  assertEquals(Array.from(ctx.getImageData(W - 2, 1, 1, 1).data), [...RIGHT_PERSON, 255]);
  assertEquals(host.matte()![0], 0);
  assertEquals(host.matte()![W - 1], 255);
});

Deno.test("channel draws keep a single channel (RGB split)", () => {
  const host = new DenoFxHost(W, H, { hasMatte: false });
  host.setInputFrame(frame(120), 0);
  const canvas = host.createCanvas(W, H);
  const ctx = canvas.getContext("2d");
  ctx.fillStyle = "#000";
  ctx.fillRect(0, 0, W, H);
  host.drawPicture(ctx, { ...IDENTITY, channel: "r", blend: "lighter" });
  host.drawPicture(ctx, { ...IDENTITY, channel: "b", blend: "lighter", dx: 1 });
  assertEquals(Array.from(ctx.getImageData(5, 3, 1, 1).data), [120, 0, (120 * 7) & 255, 255]);
  assertEquals(Array.from(ctx.getImageData(0, 3, 1, 1).data), [120, 0, 0, 255]);
});

Deno.test("frame0 is the first rendered output frame (loop crossfade)", async () => {
  const sc = scene({ edit: 4 / 30, loop: "crossfade" });
  const core = fakeCore((clock) => (clock.frame === 3 ? [IDENTITY, { op: "frame0", alpha: 1 }] : [IDENTITY]));
  const { sink } = await render(job(4, 4, sc), frames(4), core);
  assertEquals(sink.frameIds(), [0, 1, 2, 0]);
});

Deno.test("frame0 is cached from a pass-through first frame too", async () => {
  const sc = scene({ edit: 4 / 30, loop: "crossfade" });
  const base = fakeCore((clock) => (clock.frame === 3 ? [{ op: "frame0", alpha: 1 }] : []));
  const core: FxCore = { ...base, isPassthrough: (_p, clock) => clock.frame < 3 };
  const { sink } = await render(job(4, 4, sc), frames(4), core);
  assertEquals(sink.frameIds(), [0, 1, 2, 0]);
});

Deno.test("a rendered (non pass-through) frame is opaque and differs from pass-through only where drawn", async () => {
  const core = fakeCore(() => [IDENTITY]);
  const input = frames(2);
  const { sink } = await render(job(2, 2, scene({ edit: 2 / 30 })), input, core);
  assertEquals(sink.bytes(), input);
});

// ----------------------------------------------------------------------------------------------------- errors

Deno.test("stdout closed early raises StdoutClosed and main maps it to exit 4", async () => {
  const sink = new Sink(2 * FRAME);
  let caught: unknown = null;
  try {
    await render(job(5, 5, scene({ edit: 5 / 30 })), frames(5), undefined, sink);
  } catch (error) {
    caught = error;
  }
  assertEquals((caught as Error)?.name, "StdoutClosed");
});

Deno.test("parseJob rejects broken jobs", () => {
  const good = job(3, 3, scene({ edit: 0.1 }));
  const bad: unknown[] = [
    "not json",
    [],
    { ...good, version: 2 },
    { ...good, fps: 25 },
    { ...good, width: 15 },
    { ...good, outFrames: 0 },
    { ...good, inFrames: 1.5 },
    { ...good, scene: { ...good.scene, width: 32 } },
    { ...good, scene: { ...good.scene, cues: "x" } },
    { ...good, scene: { ...good.scene, warp: [{ e0: 1, e1: 0, o0: 0, rate: 1, hold: 0 }] } },
    { ...good, scene: { ...good.scene, warp: [{ e0: 0, e1: 0, o0: 0, rate: 0, hold: 0 }] } },
    { ...good, scene: { ...good.scene, warp: [{ e0: 0, e1: 0.05, o0: 0, rate: 0, hold: 0.1 }] } },
    { ...good, scene: { ...good.scene, warp: [{ e0: 0.02, e1: 0.1, o0: 0, rate: 1, hold: 0 }] } },
    {
      ...good,
      scene: {
        ...good.scene,
        warp: [{ e0: 0, e1: 0.05, o0: 0, rate: 1, hold: 0 }, { e0: 0.05, e1: 0.1, o0: 0.1, rate: 1, hold: 0 }],
      },
    },
    { ...good, fonts: { a: 3 } },
    { ...good, atlas: null },
  ];
  for (const b of bad) {
    let caught: unknown = null;
    try {
      parseJob(typeof b === "string" ? b : JSON.stringify(b));
    } catch (error) {
      caught = error;
    }
    assert(caught instanceof JobError, `expected JobError for ${JSON.stringify(b).slice(0, 80)}`);
  }
  assertEquals(parseJob(JSON.stringify(good)).outFrames, 3);
});

// ----------------------------------------------------------------------------------------------------- subprocess

const DENO_FLAGS = [
  "run",
  "--cached-only",
  "--frozen",
  "--no-prompt",
  "--allow-ffi",
];

/** The production permission set (§4.4 plus what @napi-rs/canvas needs to load; see docs/render-worker.md). */
function productionArgs(scratch: string, command: string[]): string[] {
  const denoDir = Deno.env.get("DENO_DIR") ?? `${Deno.env.get("HOME")}/.cache/deno`;
  const backend = new URL("../../", import.meta.url).pathname;
  return [
    ...DENO_FLAGS,
    `--allow-read=${scratch},${backend}src/fx_assets,${backend}fonts,/usr/share/fonts,/usr/bin/ldd,${denoDir}`,
    "--allow-env=DENO_DIR,FX_DEBUG,NAPI_RS_NATIVE_LIBRARY_PATH,NAPI_RS_FORCE_WASI,DISABLE_SYSTEM_FONTS_LOAD",
    "--allow-sys=homedir",
    MAIN,
    ...command,
  ];
}

async function runProcess(args: string[], input: Uint8Array | null) {
  const child = new Deno.Command(Deno.execPath(), {
    args,
    stdin: input ? "piped" : "null",
    stdout: "piped",
    stderr: "piped",
  }).spawn();
  if (input) {
    const writer = child.stdin.getWriter();
    await writer.write(input).catch(() => {});
    await writer.close().catch(() => {});
  }
  const result = await child.output();
  return { code: result.code, stdout: result.stdout, stderr: new TextDecoder().decode(result.stderr) };
}

Deno.test("subprocess: render through real pipes with the production permissions", async () => {
  const scratch = await Deno.makeTempDir();
  try {
    const path = `${scratch}/job.json`;
    await Deno.writeTextFile(path, JSON.stringify(job(40, 40, scene({ edit: 40 / 30 }))));
    const input = frames(40);
    const { code, stdout, stderr } = await runProcess(productionArgs(scratch, ["render", path]), input);
    assertEquals(code, EXIT_OK, stderr);
    assertEquals(stdout.length, 40 * FRAME);
    assertEquals(stdout, input);
    const lines = stderr.trim().split("\n").map((l) => JSON.parse(l));
    assertEquals(lines[0], { ev: "progress", frame: 30, of: 40 });
    assertEquals(lines.at(-1).ev, "stats");
    assertEquals(lines.at(-1).passthrough, 40);
  } finally {
    await Deno.remove(scratch, { recursive: true });
  }
});

Deno.test("subprocess: exit 2 on a bad or missing job file", async () => {
  const scratch = await Deno.makeTempDir();
  try {
    const path = `${scratch}/job.json`;
    await Deno.writeTextFile(path, JSON.stringify({ version: 1, width: 3 }));
    const bad = await runProcess(productionArgs(scratch, ["render", path]), null);
    assertEquals(bad.code, EXIT_BAD_JOB);
    assertEquals(JSON.parse(bad.stderr.trim()).ev, "error");
    const missing = await runProcess(productionArgs(scratch, ["render", `${scratch}/nope.json`]), null);
    assertEquals(missing.code, EXIT_BAD_JOB);
    const usage = await runProcess(productionArgs(scratch, ["frobnicate"]), null);
    assertEquals(usage.code, EXIT_BAD_JOB);
  } finally {
    await Deno.remove(scratch, { recursive: true });
  }
});

Deno.test("subprocess: exit 4 when stdout closes early", async () => {
  const scratch = await Deno.makeTempDir();
  try {
    const path = `${scratch}/job.json`;
    // Large frames so the pipe fills before the reader goes away.
    const w = 640;
    const h = 360;
    const sc = { ...scene({ edit: 30 / 30 }), width: w, height: h };
    await Deno.writeTextFile(path, JSON.stringify({ ...job(30, 30, sc), width: w, height: h }));
    const child = new Deno.Command(Deno.execPath(), {
      args: productionArgs(scratch, ["render", path]),
      stdin: "piped",
      stdout: "piped",
      stderr: "piped",
    }).spawn();
    // P1 keeps writing in the background while P3 (this test) reads one chunk and goes away.
    const writer = child.stdin.getWriter();
    const one = new Uint8Array(w * h * 4);
    const feeding = (async () => {
      for (let n = 0; n < 30; n++) {
        try {
          await writer.write(one);
        } catch {
          return;
        }
      }
    })();
    const reader = child.stdout.getReader();
    await reader.read(); // the first chunk proves rendering started
    await reader.cancel(); // P3 went away
    await feeding;
    await writer.close().catch(() => {});
    const status = await child.status;
    await child.stderr.cancel();
    assertEquals(status.code, EXIT_STDOUT_CLOSED);
  } finally {
    await Deno.remove(scratch, { recursive: true });
  }
});

Deno.test("subprocess: selftest exits 0 with no stdin", async () => {
  const scratch = await Deno.makeTempDir();
  try {
    const { code, stderr } = await runProcess(productionArgs(scratch, ["selftest"]), null);
    assertEquals(code, EXIT_OK, stderr);
    const report = JSON.parse(stderr.trim().split("\n").at(-1)!);
    assertEquals(report.ev, "selftest");
    assert(report.ok);
    assertEquals(report.checks.filters["saturate(0)"].got, [82, 82, 82]);
  } finally {
    await Deno.remove(scratch, { recursive: true });
  }
});

Deno.test("selftest covers every cue kind and accent variant", async () => {
  const kinds = new Set(selftestCues().map((c) => c.kind));
  assertEquals(
    [...kinds].sort(),
    [
      "accent",
      "bleep",
      "desaturate",
      "flash",
      "freeze",
      "glitch",
      "reaction",
      "sfx",
      "shake",
      "speed",
      "sticker",
      "transition",
      "zoom",
    ],
  );
  assertEquals(selftestCues().filter((c) => c.kind === "accent").length, 14);
  const events: FxEvent[] = [];
  const ok = await runSelftest(await loadCore(), (e) => events.push(e));
  assert(ok, JSON.stringify(events));
  const report = events.find((e) => e.ev === "selftest") as unknown as {
    checks: { render: { cues: number; frames: number } };
  };
  assertEquals(report.checks.render.cues, selftestCues().length);
  assertEquals(report.checks.render.frames, 3);
});

Deno.test("main returns exit 2 for usage errors in-process", async () => {
  assertEquals(await main([]), EXIT_BAD_JOB);
});

Deno.test("the hook title is fx-core's card: drawn inside its window, pass-through outside it", async () => {
  const w = 180, h = 320, size = w * h * 4;
  const input = new Uint8Array(size * 3);
  for (let i = 0; i < input.length; i += 4) input.set([40, 60, 200, 255], i);
  const sc = {
    ...scene({ edit: 3 / 30 }),
    width: w,
    height: h,
    hook_window: [1 / 30, 3 / 30] as [number, number],
    hook: {
      text: "90% of guests",
      font: "Poppins-ExtraBold",
      accent: "#34FD87",
      look: "bold",
      start: 1 / 30,
      end: 3 / 30,
      x: 0,
      y: 0,
      scale: 1,
    },
  };
  const font = new URL("../../fonts/Poppins-ExtraBold.ttf", import.meta.url).pathname;
  const j = { ...job(3, 3, sc), width: w, height: h, fonts: { "Poppins-ExtraBold": font } };
  const { sink, stats } = await render(j, input);
  const out = sink.bytes();
  assertEquals(out.subarray(0, size), input.subarray(0, size)); // before the window: untouched
  assertEquals(stats.passthrough, 2); // and its first frame, still fully transparent
  const count = (frameIndex: number, r: number, g: number, b: number) => {
    let n = 0;
    for (let i = frameIndex * size; i < (frameIndex + 1) * size; i += 4) {
      if (out[i] === r && out[i + 1] === g && out[i + 2] === b) n++;
    }
    return n;
  };
  // The last frame of the window: an opaque white card in the top safe area with the marker behind "90%".
  assert(count(2, 255, 255, 255) > 400, `white ${count(2, 255, 255, 255)}`);
  assert(count(2, 0x34, 0xfd, 0x87) > 20, `marker ${count(2, 0x34, 0xfd, 0x87)}`);
  const row = (y: number) => {
    let n = 0;
    for (let x = 0; x < w; x++) {
      const i = 2 * size + (y * w + x) * 4;
      if (out[i] === 255 && out[i + 1] === 255 && out[i + 2] === 255) n++;
    }
    return n;
  };
  assertEquals(row(Math.round(0.08 * h)), 0); // above the card
  assert(row(Math.round(0.115 * h)) > 20); // inside it, at 0.105 H and below
  assertEquals(row(Math.round(0.5 * h)), 0);
});

// ----------------------------------------------------------------------------------------------------- text layout

Deno.test("measure uses fx-core's font string, so caption layout matches what rasterize draws", async () => {
  // The fallback list is copied from fx-core; check the copy against the TypeScript source when it is in the tree.
  const layout = new URL("../../../frontend/src/lib/fx/core/layout.ts", import.meta.url);
  let source: string | null = null;
  try {
    source = await Deno.readTextFile(layout);
  } catch {
    source = null; // image or partial checkout: nothing to compare against
  }
  if (source !== null) {
    const match = source.match(/export const FONT_FALLBACK = '([^']*)'/);
    assert(match, "FONT_FALLBACK not found in fx-core layout.ts");
    assertEquals(FONT_FALLBACK, match[1]);
    assert(source.includes('`${round3(px)}px "${family}", ${FONT_FALLBACK}`'), "fontCss changed in fx-core");
  }
  assertEquals(fxFontCss("FX-Inter", 72.12345), `72.123px "FX-Inter", ${FONT_FALLBACK}`);

  const font = new URL("../../fonts/TikTokSans-Bold.ttf", import.meta.url).pathname;
  const host = new DenoFxHost(W, H, { hasMatte: false });
  host.registerFonts({ "TikTokSans-Bold": font }, () => {});
  const family = host.fontFamily("TikTokSans-Bold");
  const canvas = host.createCanvas(64, 64);
  const ctx = canvas.getContext("2d");
  for (const text of ["SUPOCLIP selftest", "naïve 東京 مرحبا"]) {
    ctx.font = fxFontCss(family, 64);
    assertEquals(host.measure(text, family, 64), ctx.measureText(text).width);
  }
});
