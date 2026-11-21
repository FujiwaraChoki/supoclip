// Studio audio preview for dynamic edits (§9 browser mirror, §14.2): SFX, the ducked music bed and bleeps played on the
// preview's existing AudioContext, in step with the F clock.
//
// - A 2 s look-ahead scheduler: `AudioBufferSourceNode.start(when, offset)` for every SFX that begins (or is still
//   sounding) inside [now, now + LOOK_AHEAD]. Everything is torn down and rescheduled on play, seek, segment jump,
//   rate change and program change, so a seek never leaves a stray hit behind.
// - Music: one looping source from `offset + o`, its GainNode automated from `duckEnvelope` breakpoints (dB → linear,
//   `linearRampToValueAtTime`; long dB moves are split into ≤ 1 dB ramps so the curve stays linear in dB like the
//   server's per-sample interpolation).
// - Bleeps: the voice passes through `voiceInput` (gain 0 over each bleep span) and a 1 kHz OscillatorNode at −12 dBFS
//   with 5 ms ramps plays over tone spans.
//
// Times: F (final) seconds. At play, context time c0 maps to F o0; F advances at `rate` (the preview's speed control),
// so ctx(o) = c0 + (o − o0)/rate and every source plays at `playbackRate = rate`. The preview keeps the clock honest
// with `sync(o)`, which re-anchors when the video drifts.
import type { EditDocument } from "@/lib/editor/document";
import { decodeFxAudio, loadFxManifest, type FxAssetManifest } from "./assets-client";
import type { Breakpoint } from "./core/duck";
import { envelopeDbAt } from "./core/duck";
import {
  BLEEP_TONE_DBFS, BLEEP_TONE_HZ, BLEEP_TONE_RAMP, TICKER_FADE, dbToGain, documentAudioInput, fxAudioProgram,
  programUrls, type FxAudioProgram, type MusicProgram, type SfxEvent,
} from "./offline-mix";

export const LOOK_AHEAD = 2;
/** Scheduler period while playing (seconds of wall time). */
export const TICK_SECONDS = 0.25;
/** Largest dB move a single linear-gain ramp covers. */
export const RAMP_STEP_DB = 1;
/** |preview clock − audio clock| above this re-anchors the audio (seconds of F). */
export const DRIFT_TOLERANCE = 0.08;

type Decode = (context: BaseAudioContext, url: string) => Promise<AudioBuffer>;
type Schedule = (tick: () => void, ms: number) => () => void;

export interface AudioPreviewOptions {
  /** Where SFX, music and tones (and the voice, through `voiceInput`) go. Default: context.destination. */
  destination?: AudioNode;
  decode?: Decode;
  /** Runs the look-ahead loop; tests pass a no-op and call `tick()` themselves. */
  schedule?: Schedule;
}

const defaultSchedule: Schedule = (tick, ms) => {
  const id = setInterval(tick, ms);
  return () => clearInterval(id);
};

/**
 * Ramp targets for an envelope over the F window (from, to]: every breakpoint inside it plus intermediate points so
 * no single ramp moves more than RAMP_STEP_DB. Values are dB (normalisation not included).
 */
export function envelopeRamps(points: readonly Breakpoint[], from: number, to: number, stepDb = RAMP_STEP_DB): Breakpoint[] {
  const out: Breakpoint[] = [];
  if (!points.length || !(to > from)) return out;
  let prevT = from;
  let prevV = envelopeDbAt(points, from);
  const push = (t: number, v: number) => {
    const steps = Math.max(1, Math.ceil(Math.abs(v - prevV) / stepDb - 1e-9));
    for (let k = 1; k < steps; k++) {
      const tk = prevT + ((t - prevT) * k) / steps;
      out.push([tk, prevV + ((v - prevV) * k) / steps]);
    }
    out.push([t, v]);
    prevT = t;
    prevV = v;
  };
  for (const [t, v] of points) {
    if (t <= from) continue;
    if (t > to) break;
    push(t, v);
  }
  if (prevT < to) {
    const v = envelopeDbAt(points, to);
    if (Math.abs(v - prevV) > 1e-9) push(to, v);
  }
  return out;
}

interface Playing {
  /** Context time at which F == o0. */
  c0: number;
  o0: number;
  rate: number;
  /** F up to which events have been scheduled. */
  until: number;
  sfx: Set<string>;
  tones: Set<number>;
  mutes: Set<number>;
  nodes: Set<AudioScheduledSourceNode>;
  music: AudioBufferSourceNode | null;
  musicGain: GainNode | null;
  stop: () => void;
}

export class FxAudioPreview {
  readonly context: BaseAudioContext;
  /** Connect the voice (the media element's gain) here instead of to the destination: bleeps mute it. */
  readonly voiceInput: GainNode;
  private readonly destination: AudioNode;
  private readonly decode: Decode;
  private readonly schedule: Schedule;
  private program: FxAudioProgram | null = null;
  private buffers = new Map<string, AudioBuffer>();
  private loading = new Set<string>();
  private playing: Playing | null = null;
  private disposed = false;

  constructor(context: BaseAudioContext, options: AudioPreviewOptions = {}) {
    this.context = context;
    this.destination = options.destination ?? context.destination;
    this.decode = options.decode ?? decodeFxAudio;
    this.schedule = options.schedule ?? defaultSchedule;
    this.voiceInput = context.createGain();
    this.voiceInput.gain.value = 1;
    this.voiceInput.connect(this.destination);
  }

  /** The program to play (null = none). Starts decoding its files; reschedules from the current position if playing. */
  setProgram(program: FxAudioProgram | null): void {
    if (this.disposed || program === this.program) return;
    this.program = program;
    if (program) for (const url of programUrls(program)) this.load(url);
    if (this.playing) this.restart(this.position());
  }

  /** True while playing. */
  get isPlaying(): boolean {
    return this.playing !== null;
  }

  /** Current F position (audio clock). */
  position(): number {
    const p = this.playing;
    if (!p) return 0;
    return p.o0 + (this.context.currentTime - p.c0) * p.rate;
  }

  /** Start (or restart) playback at F `o`, advancing at `rate`. */
  play(o: number, rate = 1): void {
    if (this.disposed) return;
    this.teardown();
    const r = rate > 0 && Number.isFinite(rate) ? rate : 1;
    const c0 = this.context.currentTime;
    const cancel = this.schedule(() => this.tick(), TICK_SECONDS * 1000);
    this.playing = {
      c0, o0: Math.max(0, o), rate: r, until: Math.max(0, o), sfx: new Set(), tones: new Set(), mutes: new Set(),
      nodes: new Set(), music: null, musicGain: null, stop: cancel,
    };
    this.voiceAt(Math.max(0, o), c0);
    this.tick();
  }

  /** Seek or segment jump while playing: drop everything scheduled and start again at `o`. */
  seek(o: number): void {
    if (this.playing) this.restart(o);
  }

  /** The preview's speed changed. */
  setRate(rate: number): void {
    const p = this.playing;
    if (p && rate !== p.rate) this.play(this.position(), rate);
  }

  /** Re-anchor when the preview clock (`o`, F) drifts from the audio clock by more than DRIFT_TOLERANCE. */
  sync(o: number): void {
    const p = this.playing;
    if (p && Math.abs(this.position() - o) > DRIFT_TOLERANCE) this.restart(o);
  }

  /** Pause: every scheduled sound stops now; the voice is unmuted. */
  pause(): void {
    this.teardown();
  }

  dispose(): void {
    this.teardown();
    this.disposed = true;
    try {
      this.voiceInput.disconnect();
    } catch {
      /* already disconnected */
    }
  }

  /** Schedule everything that starts before position + LOOK_AHEAD. Called by the scheduler loop. */
  tick(): void {
    const p = this.playing;
    const program = this.program;
    if (!p || !program || this.disposed) return;
    const now = this.position();
    const horizon = Math.min(program.outDuration, now + LOOK_AHEAD);
    for (const event of program.sfx) this.scheduleSfx(p, event, now, horizon);
    program.tones.forEach(([a, b], i) => this.scheduleTone(p, i, a, b, now, horizon));
    program.mutes.forEach(([a, b], i) => this.scheduleMute(p, i, a, b, now, horizon));
    if (program.music) this.scheduleMusic(p, program.music, now, horizon);
    p.until = Math.max(p.until, horizon);
  }

  // ------------------------------------------------------------------------------------------------- internals

  private restart(o: number): void {
    const rate = this.playing?.rate ?? 1;
    this.play(o, rate);
  }

  private when(p: Playing, o: number): number {
    return p.c0 + (o - p.o0) / p.rate;
  }

  private load(url: string): void {
    if (this.buffers.has(url) || this.loading.has(url)) return;
    this.loading.add(url);
    this.decode(this.context, url).then(
      (buffer) => {
        this.loading.delete(url);
        if (this.disposed) return;
        this.buffers.set(url, buffer);
        this.tick();                       // a sound still due (or sounding) starts as soon as it is decoded
      },
      (error) => {
        this.loading.delete(url);
        console.warn("fx_preview_asset_skipped", url, error);
      },
    );
  }

  private track(p: Playing, node: AudioScheduledSourceNode): void {
    p.nodes.add(node);
    node.onended = () => p.nodes.delete(node);
  }

  private scheduleSfx(p: Playing, event: SfxEvent, now: number, horizon: number): void {
    if (p.sfx.has(event.id) || event.at > horizon) return;
    const buffer = this.buffers.get(event.asset.url);
    if (!buffer) return;
    // A ticker is cut (and faded) only when its count span is shorter than the decoded file, as in the mix.
    const keep = event.keep !== null && event.keep < buffer.duration ? event.keep : null;
    const length = keep ?? buffer.duration;
    const into = Math.max(0, now - event.at);
    if (into >= length) {
      p.sfx.add(event.id);                 // already over: never play a tail from before the playhead
      return;
    }
    p.sfx.add(event.id);
    const source = this.context.createBufferSource();
    source.buffer = buffer;
    source.playbackRate.value = p.rate;
    const gain = this.context.createGain();
    const g = dbToGain(event.gainDb);
    const start = Math.max(this.context.currentTime, this.when(p, Math.max(event.at, now)));
    gain.gain.setValueAtTime(g, start);
    if (keep !== null) {
      const fadeAt = this.when(p, event.at + Math.max(0, keep - TICKER_FADE));
      const end = this.when(p, event.at + keep);
      if (fadeAt > start) gain.gain.setValueAtTime(g, fadeAt);
      gain.gain.linearRampToValueAtTime(0, Math.max(start, end));
    }
    source.connect(gain);
    gain.connect(this.destination);
    if (keep !== null) source.start(start, into, keep - into);
    else source.start(start, into);
    this.track(p, source);
  }

  private scheduleTone(p: Playing, index: number, a: number, b: number, now: number, horizon: number): void {
    if (p.tones.has(index) || a > horizon) return;
    p.tones.add(index);
    if (b <= now) return;
    const amplitude = dbToGain(BLEEP_TONE_DBFS);
    const ramp = Math.min(BLEEP_TONE_RAMP, (b - a) / 2);
    const osc = this.context.createOscillator();
    osc.type = "sine";
    osc.frequency.value = BLEEP_TONE_HZ;           // the voice keeps its pitch at any speed; so does the bleep
    const gain = this.context.createGain();
    const start = Math.max(this.context.currentTime, this.when(p, Math.max(a, now)));
    const end = this.when(p, b);
    if (a >= now) {
      gain.gain.setValueAtTime(0, start);
      gain.gain.linearRampToValueAtTime(amplitude, this.when(p, a + ramp));
    } else {
      gain.gain.setValueAtTime(amplitude, start);
    }
    gain.gain.setValueAtTime(amplitude, Math.max(start, this.when(p, b - ramp)));
    gain.gain.linearRampToValueAtTime(0, end);
    osc.connect(gain);
    gain.connect(this.destination);
    osc.start(start);
    osc.stop(end);
    this.track(p, osc);
  }

  private scheduleMute(p: Playing, index: number, a: number, b: number, now: number, horizon: number): void {
    if (p.mutes.has(index) || a > horizon) return;
    p.mutes.add(index);
    if (b <= now) return;
    const param = this.voiceInput.gain;
    param.setValueAtTime(0, Math.max(this.context.currentTime, this.when(p, Math.max(a, now))));
    // Unmute unless another span still covers b.
    const covered = (this.program?.mutes ?? []).some(([x, y]) => x <= b && b < y && !(x === a && y === b));
    if (!covered) param.setValueAtTime(1, this.when(p, b));
  }

  /** Voice gain at `o` right now (inside a mute span → 0). */
  private voiceAt(o: number, at: number): void {
    const param = this.voiceInput.gain;
    param.cancelScheduledValues(0);
    const muted = (this.program?.mutes ?? []).some(([a, b]) => a <= o && o < b);
    param.setValueAtTime(muted ? 0 : 1, at);
  }

  private scheduleMusic(p: Playing, music: MusicProgram, now: number, horizon: number): void {
    const buffer = this.buffers.get(music.asset.url);
    if (!buffer) return;
    const outDuration = this.program?.outDuration ?? 0;
    if (!p.music) {
      if (now >= outDuration) return;
      const source = this.context.createBufferSource();
      source.buffer = buffer;
      source.playbackRate.value = p.rate;
      const loopStart = music.asset.custom ? 0 : Math.min(music.asset.loopStart ?? 0, buffer.duration);
      const loopSeconds = !music.asset.custom && music.asset.loopSeconds
        ? Math.min(music.asset.loopSeconds, buffer.duration - loopStart)
        : buffer.duration;
      source.loop = true;
      source.loopStart = loopStart;
      source.loopEnd = loopStart + loopSeconds;
      const gain = this.context.createGain();
      const start = this.context.currentTime;
      gain.gain.setValueAtTime(dbToGain(envelopeDbAt(music.points, now) + music.normDb), start);
      source.connect(gain);
      gain.connect(this.destination);
      const offset = loopStart + (loopSeconds > 0 ? (music.offset + now) % loopSeconds : 0);
      source.start(start, offset);
      source.stop(this.when(p, outDuration));
      p.music = source;
      p.musicGain = gain;
      this.track(p, source);
      p.until = now;                       // the envelope is scheduled from here
    }
    const gain = p.musicGain;
    if (!gain) return;
    for (const [t, db] of envelopeRamps(music.points, p.until, horizon)) {
      gain.gain.linearRampToValueAtTime(dbToGain(db + music.normDb), this.when(p, t));
    }
  }

  private teardown(): void {
    const p = this.playing;
    this.playing = null;
    if (!p) return;
    p.stop();
    const now = this.context.currentTime;
    for (const node of p.nodes) {
      try {
        node.onended = null;
        node.stop(now);
        node.disconnect();
      } catch {
        /* never started or already stopped */
      }
    }
    p.nodes.clear();
    p.musicGain?.disconnect();
    const param = this.voiceInput.gain;
    param.cancelScheduledValues(0);
    param.setValueAtTime(1, now);
  }
}

/** What `Preview` (components/editor/preview.tsx `PreviewFxAudio`) drives: the draft, play/jump, pause, unmount. */
export interface PreviewFxAudioController {
  setDocument(doc: EditDocument): void;
  start(o: number, rate: number): void;
  stop(): void;
  dispose(): void;
}

export interface PreviewFxAudioOptions extends AudioPreviewOptions {
  /** The asset manifest (default: the page's cached `/api/fx/assets`). */
  manifest?: () => Promise<FxAssetManifest>;
}

/**
 * The preview's `fxAudio` factory (`PreviewFxAudioFactory`): an FxAudioPreview on the preview's AudioContext, with the
 * voice (the media element's volume gain) rerouted through `voiceInput` so bleeps mute it. The program follows the
 * draft; SFX and music wait for the manifest (a failed manifest keeps the bleeps, like the export). `dispose` puts the
 * voice back on the destination.
 */
export function createPreviewFxAudio(
  audio: { context: BaseAudioContext; destination: AudioNode; voice: GainNode },
  doc: EditDocument,
  options: PreviewFxAudioOptions = {},
): PreviewFxAudioController {
  const preview = new FxAudioPreview(audio.context, { ...options, destination: audio.destination });
  const reroute = (to: AudioNode) => {
    try {
      audio.voice.disconnect();
    } catch {
      /* not connected */
    }
    audio.voice.connect(to);
  };
  reroute(preview.voiceInput);
  let current = doc;
  let manifest: FxAssetManifest | null = null;
  let requested = false;
  let disposed = false;
  const update = () => {
    if (disposed) return;
    const input = documentAudioInput(current);
    if (input && !manifest && !requested && (input.music !== null || input.cues.some((c) => c.kind === "sfx"))) {
      requested = true;
      (options.manifest ?? (() => loadFxManifest()))().then(
        (loaded) => {
          manifest = loaded;
          update();
        },
        (error) => {
          // Not asked again by itself: the next document change retries.
          requested = false;
          console.warn("fx_preview_manifest_unavailable", error);
        },
      );
    }
    preview.setProgram(input ? fxAudioProgram(input, manifest) : null);
  };
  update();
  return {
    setDocument(next) {
      if (next === current) return;
      current = next;
      update();
    },
    start(o, rate) {
      preview.play(o, rate);
    },
    stop() {
      preview.pause();
    },
    dispose() {
      if (disposed) return;
      disposed = true;
      preview.dispose();
      reroute(audio.destination);
    },
  };
}
