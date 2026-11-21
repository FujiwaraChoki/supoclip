// Dynamic-edit audio for the studio: the audio program (what plays when, on the F clock) and the browser export's
// offline mix. Mirror of backend/src/media/fx_audio.py `build_bus` + the studio (`loudness="none"`) branch of
// `audio_graph` (§9): keep the constants and rules in lockstep.
//
// - SFX: each SfxCue at warp.toOutput(start), file from `sfxAsset` (== mix_sfx_file), gain = cue gain_db + the file's
//   normalisation gain + the intensity bus gain (§8.3). A counter's ticker is trimmed to its count span with a 30 ms
//   fade. Overlaps sum.
// - Bleep tone: a 1 kHz sine at −12 dBFS over each tone=true BleepCue span (F) with 5 ms ramps.
// - Music: `bedAsset` (== mix_bed_file), looped from `offset` (built-ins tile at their loop length, drop-ins get a
//   50 ms equal-power seam), times 10^((envelope + norm)/20) with envelope = duckEnvelope(speechSpans(words in F)).
// - Bus: loop seam (equal power, 0.4 s) when loop_ending == "crossfade", then the soft clip.
// - Voice: muted (gain 0) over every bleep span (E, quantised outward to 1 ms like the server's volume filter), times
//   the document volume (0 when muted); loop crossfade (linear fades) like the server's voice graph.
// - Out: voice + bus and a final soft clip. There is no loudness stage (same as the server studio export).
//
// The program is pure. The preview (`audio-preview.ts`) schedules it on the page's AudioContext; the export renders it
// here with an OfflineAudioContext at 48 kHz (resampling and decoding) and sample math for the bus (exact dB envelope).
import { fxWarp, mappedFx, mappedWords, type EditDocument } from "@/lib/editor/document";
import { bedAsset, decodeFxAudio, sfxAsset, type FxAssetManifest, type FxMixAsset } from "./assets-client";
import { duckEnvelope, envelopeDbAt, speechSpans, type Breakpoint } from "./core/duck";
import { FPS, type EditStyle, type FxCue, type MusicBed, type Sensitivity, type SfxCue } from "./core/types";
import { isNontrivial, toOutput, type Warp } from "./core/warp";

export const MIX_SAMPLE_RATE = 48000;
export const BLEEP_TONE_HZ = 1000;
export const BLEEP_TONE_DBFS = -12;
export const BLEEP_TONE_RAMP = 0.005;
export const TICKER_FADE = 0.03;
export const BED_SEAM_CROSSFADE = 0.05;
export const LOOP_CROSSFADE_SECONDS = 0.4;
export const SOFT_CLIP_KNEE = 0.8;
/** The server mutes the voice in 1 ms frames (`asetnsamples=n=48`): a frame starting inside a span is silent. */
export const MUTE_FRAME = 0.001;

/** §8.3 "SFX bus gain" (resolver.SFX_BUS_GAIN_DB), applied on top of each cue's gain_db. */
export const SFX_BUS_GAIN_DB = { style: -16, subtle: -20, bold: -16, max: -10 } as const;

/** resolver.sfx_bus_gain_db: style-only when intensity is off; serious mode forces subtle. */
export function sfxBusGainDb(style: Pick<EditStyle, "intensity">, sensitivity: Sensitivity): number {
  if (style.intensity === "off") return SFX_BUS_GAIN_DB.style;
  if (sensitivity === "serious") return SFX_BUS_GAIN_DB.subtle;
  return SFX_BUS_GAIN_DB[style.intensity];
}

export const dbToGain = (db: number): number => 10 ** (db / 20);

/** Everything the program needs, on the E clock (cues and words already mapped through the segments). */
export interface FxAudioInput {
  cues: readonly FxCue[];
  words: readonly { start: number; end: number }[];
  warp: Warp;
  music: MusicBed | null;
  style: Pick<EditStyle, "intensity">;
  sensitivity: Sensitivity;
  seed: number;
  loopEnding: "none" | "crossfade";
}

export interface SfxEvent {
  id: string;
  /** F seconds. */
  at: number;
  asset: FxMixAsset;
  /** Total gain in dB: cue gain_db + file normalisation + bus gain. */
  gainDb: number;
  /** Seconds of the file to play (a counter's ticker), with a TICKER_FADE fade-out, when shorter than the file;
   *  null = the whole file. */
  keep: number | null;
}

export interface MusicProgram {
  asset: FxMixAsset;
  /** Seconds into the bed at F 0. */
  offset: number;
  /** duckEnvelope breakpoints (F, dB) without the file normalisation. */
  points: Breakpoint[];
  /** The file's normalisation gain (dB), added to every point. */
  normDb: number;
}

export interface FxAudioProgram {
  /** F seconds the mix lasts: the video's length, round(warp.outDuration·30)/30 (fx_chain's av_duration). */
  outDuration: number;
  sfx: SfxEvent[];
  /** F spans of the 1 kHz bleep tone. */
  tones: [number, number][];
  /** F spans where the voice is silent (bleeps). */
  mutes: [number, number][];
  music: MusicProgram | null;
  /** Seconds of the loop crossfade (0 = none). */
  loopCrossfade: number;
}

export const EMPTY_PROGRAM: FxAudioProgram = { outDuration: 0, sfx: [], tones: [], mutes: [], music: null, loopCrossfade: 0 };

/** True when the program adds or removes any sound (SFX, tone, music or a voice mute). */
export const hasFxAudio = (program: FxAudioProgram | null | undefined): boolean =>
  !!program && (program.sfx.length > 0 || program.tones.length > 0 || program.mutes.length > 0 || !!program.music);

/** fx_audio._ticker_end_e: the end (E) of a counter's count span, from the lock sound or the counter accent. */
export function tickerEndE(cue: SfxCue, cues: readonly FxCue[]): number | null {
  const at = cue.id.indexOf("@");
  const base = at < 0 ? cue.id : cue.id.slice(0, at);
  const repeat = at < 0 ? "" : cue.id.slice(at + 1);
  if (!base.endsWith(".sfx")) return null;
  const accentId = base.slice(0, -".sfx".length);
  const suffix = repeat ? `@${repeat}` : "";
  const byId = new Map(cues.map((c) => [c.id, c]));
  const lock = byId.get(`${accentId}.sfx2${suffix}`);
  if (lock && lock.start > cue.start) return lock.start;
  const accent = byId.get(`${accentId}${suffix}`);
  if (accent && accent.kind === "accent" && accent.variant === "counter") return Math.max(cue.start, accent.end - 0.9);
  return null;
}

/** fx_audio._mute_filters: E spans widened outward to whole milliseconds. */
export function muteSpansE(cues: readonly FxCue[]): [number, number][] {
  const spans: [number, number][] = [];
  for (const cue of cues) {
    if (cue.kind !== "bleep" || !(cue.end > cue.start)) continue;
    const a = Math.floor(Math.max(0, cue.start) * 1000) / 1000;
    const b = Math.ceil(Math.max(0, cue.end) * 1000) / 1000;
    spans.push([a, b]);
  }
  return spans.sort((x, y) => x[0] - y[0] || x[1] - y[1]);
}

/**
 * fx_chain's av_duration: the audio is padded/trimmed to the video's whole frames at 30 fps (at least one frame), so
 * the mix, the duck fade-out and the loop seam end exactly where the picture does.
 */
export function avDuration(outDuration: number): number {
  return outDuration > 0 ? Math.max(1, Math.round(outDuration * FPS)) / FPS : 0;
}

/** The audio program for one plan (pure). Unknown sounds and an empty music manifest are skipped, like the server. */
export function fxAudioProgram(input: FxAudioInput, manifest: FxAssetManifest | null): FxAudioProgram {
  const { warp, cues } = input;
  const outDuration = avDuration(warp.outDuration);
  const busGain = sfxBusGainDb(input.style, input.sensitivity);
  const sfx: SfxEvent[] = [];
  const tones: [number, number][] = [];
  for (const cue of cues) {
    if (cue.kind === "sfx") {
      const asset = manifest ? sfxAsset(manifest, cue.sound, cue.variant, cue.seed) : null;
      if (!asset) continue;
      const at = toOutput(warp, cue.start);
      let keep: number | null = null;
      if (cue.sound === "ticker") {
        const endE = tickerEndE(cue, cues);
        if (endE !== null) {
          const span = toOutput(warp, endE) - at;
          // The server compares with the decoded length; the renderers do (a manifest duration can differ).
          if (span > 0) keep = span;
        }
      }
      sfx.push({ id: cue.id, at, asset, gainDb: cue.gain_db + asset.gainDb + busGain, keep });
    } else if (cue.kind === "bleep" && cue.tone) {
      const a = toOutput(warp, cue.start);
      const b = toOutput(warp, cue.end);
      if (b > a) tones.push([a, b]);
    }
  }
  const mutes = muteSpansE(cues).map(([a, b]) => [toOutput(warp, a), toOutput(warp, b)] as [number, number]);

  let music: MusicProgram | null = null;
  const bed = input.music;
  const asset = bed && manifest ? bedAsset(manifest, bed.bed, bed.mood, input.seed) : null;
  if (bed && asset) {
    const wordsF: [number, number][] = [];
    for (const w of input.words) {
      if (Number.isFinite(w.start) && Number.isFinite(w.end) && w.end > w.start)
        wordsF.push([toOutput(warp, w.start), toOutput(warp, w.end)]);
    }
    const points = duckEnvelope(speechSpans(wordsF), {
      gain_db: bed.gain_db, duck_db: bed.duck_db, attack: bed.attack, release: bed.release,
      fade_in: bed.fade_in, fade_out: bed.fade_out, out_duration: outDuration,
    });
    music = { asset, offset: Math.max(0, bed.offset), points, normDb: asset.gainDb };
  }
  sfx.sort((a, b) => a.at - b.at || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
  tones.sort((a, b) => a[0] - b[0]);
  return {
    outDuration, sfx, tones, mutes, music,
    loopCrossfade: input.loopEnding === "crossfade" ? LOOP_CROSSFADE_SECONDS : 0,
  };
}

/** == editor_fx_render.MAX_PLAN_CUES (FxPlan.cues max_length). */
export const MAX_PLAN_CUES = 400;

/** The program input for a studio document, or null when it has no `fx`. Cues and words are mapped to E. */
export function documentAudioInput(doc: EditDocument): FxAudioInput | null {
  const fx = doc.fx;
  if (!fx) return null;
  // editor_fx_render.studio_plan: mapped cues sorted by (start, id), at most MAX_PLAN_CUES.
  const cues = [...mappedFx(doc)]
    .sort((a, b) => a.start - b.start || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0))
    .slice(0, MAX_PLAN_CUES);
  return {
    cues,
    words: mappedWords(doc),
    warp: fxWarp(doc),
    music: fx.music,
    style: fx.style,
    sensitivity: fx.sensitivity,
    seed: fx.seed,
    loopEnding: fx.loopEnding,
  };
}

/** Why the browser cannot export a document (§10, §14.2), or null. The export panel shows it; export throws it. */
export type BrowserExportRefusal = "warp" | "reaction" | "behind";

/**
 * Speed ramps and freezes (a non-identity warp), reaction GIFs and text behind the speaker (behind accents, which need
 * the person matte) render only on the server in v1. Cues of a switched-off kind do not count. The behind_head
 * template's captions themselves need no matte, so the template alone exports in the browser.
 */
export function browserExportRefusal(doc: EditDocument): BrowserExportRefusal | null {
  if (!doc.fx) return null;
  if (isNontrivial(fxWarp(doc))) return "warp";
  const cues = mappedFx(doc);
  if (cues.some((c) => c.kind === "reaction")) return "reaction";
  if (cues.some((c) => c.kind === "accent" && c.variant === "behind")) return "behind";
  return null;
}

export const BROWSER_EXPORT_REFUSAL_MESSAGES: Record<BrowserExportRefusal, string> = {
  warp: "Speed ramps and freeze frames render on the server. Use background export.",
  reaction: "Reaction clips render on the server. Use background export.",
  behind: "Text behind the speaker renders on the server. Use background export.",
};

// ----------------------------------------------------------------------------- sample math (mirror of build_bus)

type Stereo = [Float32Array, Float32Array];

const stereo = (n: number): Stereo => [new Float32Array(n), new Float32Array(n)];

/** Channel `c` of a decoded buffer, the first channel standing in for missing ones (mono files → both sides). */
function channel(buffer: AudioBuffer, c: number): Float32Array {
  return buffer.getChannelData(Math.min(c, buffer.numberOfChannels - 1));
}

/** _add: sum `length` samples of `src` (from `src0`) times `gain` into `bus` at `at`. False when nothing fits. */
function addInto(bus: Stereo, src: (c: number) => Float32Array, srcLength: number, at: number,
  gain: (i: number) => number): boolean {
  const n = bus[0].length;
  const src0 = Math.max(0, -at);
  const dst0 = Math.max(0, at);
  const length = Math.min(srcLength - src0, n - dst0);
  if (length <= 0) return false;
  for (let c = 0; c < 2; c++) {
    const from = src(c);
    const to = bus[c];
    for (let i = 0; i < length; i++) to[dst0 + i] += from[src0 + i] * gain(src0 + i);
  }
  return true;
}

function mixSfx(bus: Stereo, program: FxAudioProgram, buffers: Map<string, AudioBuffer>, sr: number): boolean {
  let mixed = false;
  for (const event of program.sfx) {
    const buffer = buffers.get(event.asset.url);
    if (!buffer || !buffer.length) continue;
    let length = buffer.length;
    let fadeFrom = Infinity;
    if (event.keep !== null) {
      const keep = Math.round(event.keep * sr);
      if (keep > 0 && keep < length) {
        length = keep;
        const fade = Math.min(keep, Math.round(TICKER_FADE * sr));
        fadeFrom = keep - fade;
      }
    }
    const g = dbToGain(event.gainDb);
    const fadeLen = length - fadeFrom;
    // np.linspace(1, 0, fade): the last kept sample is silent (a 1-sample fade is [1.0]).
    const gain = (i: number) => (i < fadeFrom ? g : g * (fadeLen > 1 ? 1 - (i - fadeFrom) / (fadeLen - 1) : 1));
    mixed = addInto(bus, (c) => channel(buffer, c), length, Math.round(event.at * sr), gain) || mixed;
  }
  return mixed;
}

/** One bleep tone (mono samples), as _mix_bleep_tones builds it. */
export function bleepTone(samples: number, sr: number = MIX_SAMPLE_RATE): Float32Array {
  const amplitude = dbToGain(BLEEP_TONE_DBFS);
  const ramp = Math.max(1, Math.round(BLEEP_TONE_RAMP * sr));
  const tone = new Float32Array(samples);
  for (let i = 0; i < samples; i++) tone[i] = amplitude * Math.sin((2 * Math.PI * BLEEP_TONE_HZ * i) / sr);
  const edge = Math.min(ramp, Math.floor(samples / 2));
  for (let i = 0; i < edge; i++) {
    const shape = i / edge;                 // np.linspace(0, 1, edge, endpoint=False)
    tone[i] *= shape;
    tone[samples - 1 - i] *= shape;
  }
  return tone;
}

function mixTones(bus: Stereo, program: FxAudioProgram, sr: number): boolean {
  let mixed = false;
  for (const [start, end] of program.tones) {
    const a = Math.round(start * sr);
    const b = Math.round(end * sr);
    if (b <= a) continue;
    const tone = bleepTone(b - a, sr);
    mixed = addInto(bus, () => tone, tone.length, a, () => 1) || mixed;
  }
  return mixed;
}

/**
 * _looped: the sample index into the bed for bus sample `i` (bed started `start` samples in). Built-ins tile at their
 * loop length; drop-ins restart `xf` samples in after a crossfade region handled by `loopedSample`.
 */
function loopedReader(bed: AudioBuffer, loopSamples: number | null, sr: number, start: number, loopStart = 0) {
  const skip = loopSamples ? Math.min(Math.max(0, loopStart), bed.length) : 0;
  const length = loopSamples && loopSamples > 0 ? Math.min(loopSamples, bed.length - skip) : bed.length;
  const xf = Math.round(BED_SEAM_CROSSFADE * sr);
  const seamless = !!loopSamples || length <= 2 * xf;
  const left = channel(bed, 0).subarray(skip, skip + length);
  const right = channel(bed, 1).subarray(skip, skip + length);
  const data = [left, right];
  const period = length - xf;
  // _looped: a drop-in's start wraps at its file length (the first pass plays the whole head, no seam).
  const begin = seamless ? start : ((start % length) + length) % length;
  return (c: number, i: number): number => {
    const src = data[c];
    const position = begin + i;
    if (seamless) return src[((position % length) + length) % length];
    // Timeline: bed[0 : L-xf], then forever X ++ bed[xf : L-xf] where X = tail·cos + head·sin over xf samples.
    if (position < period) return src[position];
    const q = (position - period) % period;
    if (q >= xf) return src[q];
    const theta = xf > 1 ? (q / (xf - 1)) * (Math.PI / 2) : 0;   // np.linspace(0, π/2, xf)
    return src[period + q] * Math.cos(theta) + src[q] * Math.sin(theta);
  };
}

function mixMusic(bus: Stereo, program: FxAudioProgram, buffers: Map<string, AudioBuffer>, sr: number): boolean {
  const music = program.music;
  if (!music) return false;
  const buffer = buffers.get(music.asset.url);
  if (!buffer || !buffer.length) return false;
  const loopSamples = music.asset.custom ? null : Math.round((music.asset.loopSeconds ?? 0) * sr) || null;
  const loopStart = music.asset.custom ? 0 : Math.round((music.asset.loopStart ?? 0) * sr);
  const read = loopedReader(buffer, loopSamples, sr, Math.round(music.offset * sr), loopStart);
  const n = bus[0].length;
  const points = music.points;
  let k = 0;                                  // breakpoint cursor (times are increasing)
  for (let i = 0; i < n; i++) {
    const t = i / sr;
    while (k + 1 < points.length && points[k + 1][0] <= t) k++;
    let db: number;
    if (!points.length) db = 0;
    else if (t < points[0][0]) db = points[0][1];   // np.interp: at a repeated time the last point wins
    else if (k + 1 >= points.length) db = points[points.length - 1][1];
    else {
      const [t0, v0] = points[k];
      const [t1, v1] = points[k + 1];
      db = t1 <= t0 ? v1 : v0 + ((v1 - v0) * (t - t0)) / (t1 - t0);
    }
    const g = dbToGain(db + music.normDb);
    bus[0][i] += read(0, i) * g;
    bus[1][i] += read(1, i) * g;
  }
  return true;
}

/** _loop_seam: equal-power crossfade of the last `seconds` with the first `seconds`. */
export function loopSeam(bus: readonly Float32Array[], seconds: number, sr: number = MIX_SAMPLE_RATE): void {
  const n = bus[0]?.length ?? 0;
  const x = Math.min(Math.round(seconds * sr), Math.floor(n / 2));
  if (x <= 0) return;
  for (const ch of bus) {
    for (let i = 0; i < x; i++) {
      const theta = x > 1 ? (i / (x - 1)) * (Math.PI / 2) : 0;
      ch[n - x + i] = ch[n - x + i] * Math.cos(theta) + ch[i] * Math.sin(theta);
    }
  }
}

/** x below the knee, else sign·(0.8 + 0.2·tanh((|x| − 0.8)/0.2)): never above 1.0. */
export function softClipSample(x: number): number {
  const m = Math.abs(x);
  if (m < SOFT_CLIP_KNEE) return x;
  return Math.sign(x) * (SOFT_CLIP_KNEE + (1 - SOFT_CLIP_KNEE) * Math.tanh((m - SOFT_CLIP_KNEE) / (1 - SOFT_CLIP_KNEE)));
}

export function softClip(channels: readonly Float32Array[]): void {
  for (const ch of channels) for (let i = 0; i < ch.length; i++) ch[i] = softClipSample(ch[i]);
}

/** Every asset URL the program plays. */
export const programUrls = (program: FxAudioProgram): string[] =>
  [...new Set([...program.sfx.map((e) => e.asset.url), ...(program.music ? [program.music.asset.url] : [])])];

/** Shown when the export cannot load its music or sound effects (as for the edit fonts). */
export const FX_AUDIO_UNAVAILABLE_MESSAGE = "Could not load the edit sounds. Use background export.";

/**
 * Decode every asset of the program on `context` (cached per context). A file that fails to load fails the export
 * with the background-export message: it never ships silently without its music or sound effects.
 */
export async function decodeProgram(context: BaseAudioContext, program: FxAudioProgram,
  decode: (context: BaseAudioContext, url: string) => Promise<AudioBuffer> = decodeFxAudio): Promise<Map<string, AudioBuffer>> {
  const buffers = new Map<string, AudioBuffer>();
  await Promise.all(programUrls(program).map(async (url) => {
    try {
      buffers.set(url, await decode(context, url));
    } catch (error) {
      console.warn("fx_audio_asset_unavailable", url, error);
      throw new Error(FX_AUDIO_UNAVAILABLE_MESSAGE);
    }
  }));
  return buffers;
}

/**
 * build_bus: SFX, bleep tones and the ducked bed at `sr`, loop seam, soft clip. Null when nothing was mixed.
 * `buffers` must be decoded at `sr` (decode on a context running at that rate).
 */
export function renderBus(program: FxAudioProgram, buffers: Map<string, AudioBuffer>,
  sr: number = MIX_SAMPLE_RATE): Stereo | null {
  const count = Math.round(Math.max(0, program.outDuration) * sr);
  if (count <= 0) return null;
  const bus = stereo(count);
  let mixed = mixSfx(bus, program, buffers, sr);
  mixed = mixTones(bus, program, sr) || mixed;
  mixed = mixMusic(bus, program, buffers, sr) || mixed;
  if (!mixed) return null;
  if (program.loopCrossfade > 0) loopSeam(bus, program.loopCrossfade, sr);
  softClip(bus);
  return bus;
}

/** Silence every 1 ms frame that starts inside a mute span (F), like `volume=0:enable='between(t,a,b)'`. */
export function applyMutes(voice: readonly Float32Array[], mutes: readonly [number, number][], sr: number = MIX_SAMPLE_RATE): void {
  const n = voice[0]?.length ?? 0;
  const frame = Math.max(1, Math.round(MUTE_FRAME * sr));
  for (const [a, b] of mutes) {
    const first = Math.max(0, Math.ceil(Math.round(a * sr) / frame - 1e-9));
    const last = Math.floor(Math.round(b * sr) / frame + 1e-9);
    const from = first * frame;
    const to = Math.min(n, (last + 1) * frame);
    for (const ch of voice) ch.fill(0, Math.min(from, n), Math.max(from, to));
  }
}

/** The voice loop crossfade of audio_graph: linear fade-out of the tail plus the linear-faded-in head on top. */
export function voiceLoopCrossfade(voice: readonly Float32Array[], seconds: number, sr: number = MIX_SAMPLE_RATE): void {
  const n = voice[0]?.length ?? 0;
  const x = Math.min(Math.round(Math.max(0, seconds) * sr), Math.floor(n / 2));
  if (x <= 0) return;
  for (const ch of voice) {
    for (let i = 0; i < x; i++) {
      const w = i / x;
      ch[n - x + i] = ch[n - x + i] * (1 - w) + ch[i] * w;
    }
  }
}

export interface VoiceInput {
  /** Planar channels on the E (== F) timeline, 0 where the source is silent. */
  channels: Float32Array[];
  sampleRate: number;
  /** doc.volume, or 0 when muted. */
  gain: number;
}

export interface OfflineMixOptions {
  /** OfflineAudioContext factory (tests inject a fake). */
  createContext?: (channels: number, length: number, sampleRate: number) => OfflineAudioContext;
  decode?: (context: BaseAudioContext, url: string) => Promise<AudioBuffer>;
  signal?: AbortSignal;
}

const defaultContext = (channels: number, length: number, sampleRate: number) =>
  new OfflineAudioContext(channels, length, sampleRate);

/**
 * The browser export's audio: `OfflineAudioContext(2, outDur·48000, 48000)` renders the voice (resampled, × volume);
 * the bus is mixed at 48 kHz from buffers decoded on that context; then mutes, loop crossfade, sum and soft clip.
 * Returns planar stereo at 48 kHz, `round(outDuration·48000)` samples long.
 */
export async function renderOfflineMix(program: FxAudioProgram, voice: VoiceInput | null,
  options: OfflineMixOptions = {}): Promise<{ channels: Stereo; sampleRate: number }> {
  const sr = MIX_SAMPLE_RATE;
  const length = Math.max(1, Math.round(program.outDuration * sr));
  const create = options.createContext ?? defaultContext;
  const context = create(2, length, sr);
  const buffers = await decodeProgram(context, program, options.decode);
  options.signal?.throwIfAborted();

  let mix: Stereo = stereo(length);
  if (voice && voice.channels.length && voice.channels[0].length && voice.gain > 0) {
    const source = context.createBufferSource();
    const buffer = context.createBuffer(voice.channels.length, voice.channels[0].length, voice.sampleRate);
    voice.channels.forEach((data, c) => buffer.copyToChannel(data as Float32Array<ArrayBuffer>, c));
    source.buffer = buffer;
    const gain = context.createGain();
    gain.gain.value = voice.gain;
    source.connect(gain);
    gain.connect(context.destination);
    source.start(0);
    const rendered = await context.startRendering();
    options.signal?.throwIfAborted();
    mix = [Float32Array.from(channel(rendered, 0).subarray(0, length)), Float32Array.from(channel(rendered, 1).subarray(0, length))];
    if (mix[0].length < length) mix = [padTo(mix[0], length), padTo(mix[1], length)];
    applyMutes(mix, program.mutes, sr);
    if (program.loopCrossfade > 0) voiceLoopCrossfade(mix, program.loopCrossfade, sr);
  }
  const bus = renderBus(program, buffers, sr);
  if (bus) for (let c = 0; c < 2; c++) for (let i = 0; i < length; i++) mix[c][i] += bus[c][i];
  softClip(mix);
  return { channels: mix, sampleRate: sr };
}

function padTo(data: Float32Array, length: number): Float32Array {
  const out = new Float32Array(length);
  out.set(data.subarray(0, length));
  return out;
}

/** The envelope level (dB, normalisation included) the bed plays at F `t`. */
export const musicDbAt = (music: MusicProgram, t: number): number => envelopeDbAt(music.points, t) + music.normDb;
