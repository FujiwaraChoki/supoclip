// P2 frame protocol (docs: FINAL §4.4). Pure helpers shared by main.ts, host.ts and the tests: the job file, the
// warp lookup (F → E), the input frame index, alpha → matte split, framed stdin/stdout I/O and stderr events.
//
// Clocks: E = edit seconds (what the input frames and every cue are timed in), F = final seconds (what P2 writes,
// after freezes and speed pieces). Output frame n sits at o = n/30 in F.

export const FPS = 30;

export const EXIT_OK = 0;
export const EXIT_BAD_JOB = 2;
export const EXIT_RENDER = 3;
export const EXIT_STDOUT_CLOSED = 4;

/** The job file is unreadable or does not match the protocol (exit 2). */
export class JobError extends Error {
  override name = "JobError";
}

/** The reader of stdout went away before every frame was written (exit 4). */
export class StdoutClosed extends Error {
  override name = "StdoutClosed";
}

export interface WarpPieceJson {
  e0: number;
  e1: number;
  o0: number;
  rate: number;
  hold: number;
}

export interface MatteWindow {
  start: number;
  end: number;
  person: boolean;
}

/** The parts of FxScene (§4.1) the host reads itself. Everything else is passed to fx-core untouched. */
export interface SceneHeader {
  version: 1;
  width: number;
  height: number;
  edit_duration: number;
  out_duration: number;
  warp: WarpPieceJson[];
  cues: { kind: string; variant?: string; start: number; end: number }[];
  loop_ending: "none" | "crossfade";
  matte_windows: MatteWindow[];
  [key: string]: unknown;
}

export interface FxJobFile {
  version: 1;
  width: number;
  height: number;
  fps: 30;
  inFrames: number; // frames P1 will send: round(min(plan.edit_duration, probed P1 length)*30) (AR-M13)
  outFrames: number; // frames P2 must write (F timeline, round(out_duration*30))
  scene: SceneHeader; // §3.1 / §4.1 FxScene
  fonts: Record<string, string>; // font stem → absolute .ttf path (registered as "FX-<stem>")
  atlas: { png: string; json: string };
}

const MAX_SIDE = 8192;
const MAX_FRAMES = 30 * 60 * 60; // one hour of output; anything longer is a broken job

function fail(message: string): never {
  throw new JobError(message);
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function finite(value: unknown, path: string): number {
  if (typeof value !== "number" || !Number.isFinite(value)) fail(`${path} must be a finite number`);
  return value;
}

function count(value: unknown, path: string, min: number, max: number): number {
  const n = finite(value, path);
  if (!Number.isInteger(n) || n < min || n > max) fail(`${path} must be an integer in [${min}, ${max}]`);
  return n;
}

function text(value: unknown, path: string): string {
  if (typeof value !== "string" || value.length === 0) fail(`${path} must be a non-empty string`);
  return value;
}

/** Parses and checks a job file. Throws JobError on anything P2 cannot render. */
export function parseJob(raw: string): FxJobFile {
  let data: unknown;
  try {
    data = JSON.parse(raw);
  } catch (error) {
    fail(`job.json is not JSON: ${(error as Error).message}`);
  }
  if (!isObject(data)) fail("job.json must be an object");
  if (data.version !== 1) fail(`unsupported job version ${JSON.stringify(data.version)}`);
  if (data.fps !== FPS) fail(`fps must be ${FPS}`);
  const width = count(data.width, "width", 2, MAX_SIDE);
  const height = count(data.height, "height", 2, MAX_SIDE);
  if (width % 2 || height % 2) fail("width and height must be even");
  count(data.inFrames, "inFrames", 1, MAX_FRAMES);
  count(data.outFrames, "outFrames", 1, MAX_FRAMES);

  const scene = data.scene;
  if (!isObject(scene)) fail("scene must be an object");
  if (scene.version !== 1) fail(`unsupported scene version ${JSON.stringify(scene.version)}`);
  if (scene.width !== width || scene.height !== height) fail("scene size differs from the job size");
  if (finite(scene.edit_duration, "scene.edit_duration") < 0) fail("scene.edit_duration must be >= 0");
  if (finite(scene.out_duration, "scene.out_duration") < 0) fail("scene.out_duration must be >= 0");
  if (!Array.isArray(scene.cues)) fail("scene.cues must be a list");
  scene.cues.forEach((cue, i) => {
    if (!isObject(cue)) fail(`scene.cues[${i}] must be an object`);
    text(cue.kind, `scene.cues[${i}].kind`);
    finite(cue.start, `scene.cues[${i}].start`);
    finite(cue.end, `scene.cues[${i}].end`);
  });
  if (!Array.isArray(scene.warp)) fail("scene.warp must be a list");
  // Same shape rules as warp_from_json / warpFromJson: pieces tile E and F from 0 with no gaps (±1e-6). Those readers
  // turn a broken warp into identity; P2 refuses it instead (exit 2), so the fallback ladder sees the bad job.
  let eCursor = 0;
  let oCursor = 0;
  scene.warp.forEach((piece, i) => {
    if (!isObject(piece)) fail(`scene.warp[${i}] must be an object`);
    piece.hold ??= 0;
    for (const key of ["e0", "e1", "o0", "rate", "hold"]) finite(piece[key], `scene.warp[${i}].${key}`);
    const p = piece as unknown as WarpPieceJson;
    if (p.rate < 0 || p.hold < 0 || p.e1 < p.e0) fail(`scene.warp[${i}] is not monotone`);
    if (p.rate === 0 && (p.hold <= 0 || Math.abs(p.e1 - p.e0) > 1e-6)) {
      fail(`scene.warp[${i}] is a hold piece without a hold`);
    }
    if (p.rate > 0 && p.e1 <= p.e0) fail(`scene.warp[${i}] is an empty speed piece`);
    if (Math.abs(p.e0 - eCursor) > 1e-6 || Math.abs(p.o0 - oCursor) > 1e-6) {
      fail(`scene.warp[${i}] does not start where the previous piece ends`);
    }
    eCursor = p.e1;
    oCursor = p.o0 + pieceLength(p);
  });
  if (scene.matte_windows !== undefined && !Array.isArray(scene.matte_windows)) {
    fail("scene.matte_windows must be a list");
  }
  scene.matte_windows ??= [];
  scene.loop_ending ??= "none";

  if (!isObject(data.fonts)) fail("fonts must be an object");
  for (const [stem, path] of Object.entries(data.fonts)) text(path, `fonts.${stem}`);
  if (!isObject(data.atlas)) fail("atlas must be an object");
  // An empty atlas path means "no emoji atlas"; drawers then skip emoji (FxHost.atlas() returns null).
  if (typeof data.atlas.png !== "string" || typeof data.atlas.json !== "string") {
    fail("atlas.png and atlas.json must be strings");
  }
  return data as unknown as FxJobFile;
}

/** Output length of one warp piece in F seconds. */
export function pieceLength(p: WarpPieceJson): number {
  return p.rate === 0 ? p.hold : (p.e1 - p.e0) / p.rate;
}

/**
 * F → E through the warp (mirror of `Warp.to_edit`): monotone non-decreasing, constant across holds, clamped to
 * [0, edit_duration]. An empty warp is the identity.
 */
export function editTimeAt(warp: readonly WarpPieceJson[], editDuration: number, o: number): number {
  if (o <= 0) return 0;
  if (warp.length === 0) return Math.min(o, editDuration);
  // Last piece whose start is at or before o (pieces are sorted by o0).
  let lo = 0;
  let hi = warp.length - 1;
  if (o < warp[0].o0) return Math.min(warp[0].e0, editDuration);
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (warp[mid].o0 <= o + 1e-9) lo = mid;
    else hi = mid - 1;
  }
  const p = warp[lo];
  if (p.rate === 0) return Math.min(p.e0, editDuration);
  const e = p.e0 + Math.max(0, o - p.o0) * p.rate;
  return Math.min(e, p.e1, editDuration);
}

/** The input frame shown at edit time e: `min(floor(e*30 + 1e-6), inFrames-1)`. */
export function inputIndexFor(e: number, inFrames: number): number {
  return Math.max(0, Math.min(Math.floor(e * FPS + 1e-6), inFrames - 1));
}

/** True when the scene draws behind the person, i.e. the input alpha carries a matte (AR-B1). */
export function sceneHasMatte(scene: SceneHeader): boolean {
  return scene.matte_windows.length > 0 || scene.cues.some((cue) => cue.kind === "accent" && cue.variant === "behind");
}

/** Copies the alpha channel of an RGBA frame into `matte` (one byte per pixel). */
export function extractAlpha(rgba: Uint8Array, matte: Uint8Array): void {
  const n = matte.length;
  for (let i = 0, j = 3; i < n; i++, j += 4) matte[i] = rgba[j];
}

/**
 * Sets every alpha byte to 255 in place. Skia canvases are premultiplied: putImageData of a pixel with alpha 0
 * destroys its RGB, so the base picture must be opaque before it touches a canvas (AR-B1).
 */
export function forceOpaque(rgba: Uint8Array): void {
  if (rgba.byteOffset % 4 === 0 && rgba.byteLength % 4 === 0) {
    const u32 = new Uint32Array(rgba.buffer, rgba.byteOffset, rgba.byteLength / 4);
    // RGBA bytes on a little-endian host: alpha is the high byte of each u32.
    for (let i = 0; i < u32.length; i++) u32[i] |= 0xff000000;
    return;
  }
  for (let j = 3; j < rgba.length; j += 4) rgba[j] = 255;
}

export interface ByteSource {
  read(p: Uint8Array): Promise<number | null>;
}

export interface ByteSink {
  write(p: Uint8Array): Promise<number>;
}

/** Reads whole frames from stdin. A partial frame at EOF counts as EOF. */
export class FrameReader {
  #source: ByteSource;
  #eof = false;
  framesRead = 0;

  constructor(source: ByteSource) {
    this.#source = source;
  }

  get eof(): boolean {
    return this.#eof;
  }

  /** Fills `into` with the next frame. Returns false at end of input. */
  async next(into: Uint8Array): Promise<boolean> {
    if (this.#eof) return false;
    let filled = 0;
    while (filled < into.length) {
      const n = await this.#source.read(into.subarray(filled));
      if (n === null || n === 0) {
        this.#eof = true;
        return false;
      }
      filled += n;
    }
    this.framesRead++;
    return true;
  }

  /** Reads and discards everything left on the input, so P1 never blocks on a full pipe. */
  async drain(): Promise<number> {
    if (this.#eof) return 0;
    const scratch = new Uint8Array(1 << 20);
    let total = 0;
    for (;;) {
      const n = await this.#source.read(scratch);
      if (n === null || n === 0) break;
      total += n;
    }
    this.#eof = true;
    return total;
  }
}

function isClosedPipe(error: unknown): boolean {
  if (error instanceof Deno.errors.BrokenPipe) return true;
  if (error instanceof Deno.errors.ConnectionReset) return true;
  const code = (error as { code?: string } | null)?.code;
  return code === "EPIPE" || code === "ECONNRESET";
}

/** Writes every byte or throws StdoutClosed when the reader went away. */
export async function writeAll(sink: ByteSink, bytes: Uint8Array): Promise<void> {
  let off = 0;
  while (off < bytes.length) {
    let n: number;
    try {
      n = await sink.write(bytes.subarray(off));
    } catch (error) {
      if (isClosedPipe(error)) throw new StdoutClosed("stdout closed early");
      throw error;
    }
    if (n <= 0) throw new StdoutClosed("stdout closed early");
    off += n;
  }
}

export type FxEvent =
  | { ev: "progress"; frame: number; of: number }
  | { ev: "stats"; ms_total: number; ms_draw: number; passthrough: number }
  | { ev: "error"; message: string }
  | { ev: "warn"; message: string }
  | { ev: "selftest"; [key: string]: unknown }
  | { ev: "debug"; [key: string]: unknown };

const encoder = new TextEncoder();

/** One JSON line on stderr. Never throws: losing a progress line must not fail a render. */
export function emitStderr(event: FxEvent): void {
  const bytes = encoder.encode(JSON.stringify(event) + "\n");
  try {
    let off = 0;
    while (off < bytes.length) off += Deno.stderr.writeSync(bytes.subarray(off));
  } catch {
    // stderr closed: nothing useful left to do with the event.
  }
}
