import { describe, expect, it, vi } from "vitest";
import { DEFAULT_DOC_FX, type EditDocument } from "@/lib/editor/document";
import { draft } from "@/lib/editor/document.test-fixture";
import { FxAudioPreview, createPreviewFxAudio, envelopeRamps, LOOK_AHEAD, RAMP_STEP_DB } from "./audio-preview";
import type { FxAssetManifest } from "./assets-client";
import type { FxCue } from "./core/types";
import { duckEnvelope, envelopeDbAt, speechSpans } from "./core/duck";
import type { FxMixAsset } from "./assets-client";
import { dbToGain, type FxAudioProgram } from "./offline-mix";

type Event = { type: "set" | "ramp" | "cancel"; value: number; time: number };

class FakeParam {
  value = 0;
  events: Event[] = [];
  setValueAtTime(value: number, time: number) { this.events.push({ type: "set", value, time }); }
  linearRampToValueAtTime(value: number, time: number) { this.events.push({ type: "ramp", value, time }); }
  cancelScheduledValues(time: number) { this.events.push({ type: "cancel", value: 0, time }); }
}

class FakeNode {
  outputs: unknown[] = [];
  connect(node: unknown) { this.outputs.push(node); return node; }
  disconnect() { this.outputs = []; }
}

class FakeGain extends FakeNode { gain = new FakeParam(); }

class FakeSource extends FakeNode {
  buffer: AudioBuffer | null = null;
  playbackRate = new FakeParam();
  loop = false;
  loopStart = 0;
  loopEnd = 0;
  started: { when: number; offset?: number; duration?: number } | null = null;
  stopped: number | null = null;
  onended: (() => void) | null = null;
  constructor(public ctx: FakeContext) { super(); }
  start(when = 0, offset?: number, duration?: number) { this.started = { when, offset, duration }; }
  stop(when = 0) { this.stopped = when; }
}

class FakeOscillator extends FakeSource {
  type = "sine";
  frequency = new FakeParam();
}

class FakeContext {
  currentTime = 10;
  destination = new FakeNode();
  sources: FakeSource[] = [];
  gains: FakeGain[] = [];
  oscillators: FakeOscillator[] = [];
  createGain() { const g = new FakeGain(); this.gains.push(g); return g; }
  createBufferSource() { const s = new FakeSource(this); this.sources.push(s); return s; }
  createOscillator() { const o = new FakeOscillator(this); this.oscillators.push(o); return o; }
}

const asset = (url: string, duration: number, extra: Partial<FxMixAsset> = {}): FxMixAsset =>
  ({ id: url, url, gainDb: 0, duration, custom: false, ...extra });
const fakeBuffer = (duration: number) => ({ duration, length: Math.round(duration * 48000) }) as AudioBuffer;

const program = (over: Partial<FxAudioProgram> = {}): FxAudioProgram => ({
  outDuration: 6,
  sfx: [
    { id: "a", at: 0.5, asset: asset("/s/a", 0.4), gainDb: -6, keep: null },
    { id: "b", at: 3, asset: asset("/s/b", 0.4), gainDb: 0, keep: null },
  ],
  tones: [],
  mutes: [],
  music: null,
  loopCrossfade: 0,
  ...over,
});

async function setup(p: FxAudioProgram, decode?: (ctx: BaseAudioContext, url: string) => Promise<AudioBuffer>) {
  const ctx = new FakeContext();
  const preview = new FxAudioPreview(ctx as unknown as BaseAudioContext, {
    decode: decode ?? (async (_c, url) => fakeBuffer(url.includes("music") ? 8 : 0.4)),
    schedule: () => () => {},
  });
  preview.setProgram(p);
  await Promise.resolve();
  await Promise.resolve();
  return { ctx, preview };
}


describe("FxAudioPreview scheduler", () => {
  it("starts SFX at their F time on the context clock, only inside the look-ahead", async () => {
    const { ctx, preview } = await setup(program());
    preview.play(0);
    expect(ctx.sources).toHaveLength(1);
    expect(ctx.sources[0].started).toEqual({ when: 10.5, offset: 0, duration: undefined });
    const gain = ctx.sources[0].outputs[0] as FakeGain;
    expect(gain.gain.events[0]).toEqual({ type: "set", value: dbToGain(-6), time: 10.5 });
    ctx.currentTime = 11.2;                                    // F 1.2: 3.0 is now within 2 s
    preview.tick();
    expect(ctx.sources).toHaveLength(2);
    expect(ctx.sources[1].started!.when).toBeCloseTo(13, 9);
    preview.tick();
    expect(ctx.sources).toHaveLength(2);                       // never scheduled twice
    expect(LOOK_AHEAD).toBe(2);
  });

  it("reschedules on seek: stops what was queued, starts mid-sound with an offset, skips the past", async () => {
    const { ctx, preview } = await setup(program());
    preview.play(0);
    const first = ctx.sources[0];
    preview.seek(3.1);
    expect(first.stopped).toBe(10);
    const after = ctx.sources.slice(1);
    expect(after).toHaveLength(1);                             // "a" (0.5–0.9) is over
    expect(after[0].started!.when).toBeCloseTo(10, 9);
    expect(after[0].started!.offset).toBeCloseTo(0.1, 9);
    expect(preview.position()).toBeCloseTo(3.1, 9);
  });

  it("follows the preview speed: halves the wait and plays the sources at the same rate", async () => {
    const { ctx, preview } = await setup(program());
    preview.play(0, 2);
    expect(ctx.sources[0].started!.when).toBeCloseTo(10.25, 9);
    expect(ctx.sources[0].playbackRate.value).toBe(2);
    ctx.currentTime = 10.1;                                    // F 0.2
    preview.setRate(1);
    const restarted = ctx.sources[ctx.sources.length - 1];
    expect(restarted.started!.when).toBeCloseTo(10.4, 9);
    expect(restarted.playbackRate.value).toBe(1);
  });

  it("re-anchors only when the video drifts past the tolerance", async () => {
    const { ctx, preview } = await setup(program());
    preview.play(0);
    const count = ctx.sources.length;
    preview.sync(0.05);
    expect(ctx.sources).toHaveLength(count);
    preview.sync(0.4);
    expect(ctx.sources.length).toBeGreaterThan(count);
    expect(preview.position()).toBeCloseTo(0.4, 9);
  });

  it("schedules a sound as soon as its file is decoded, from where the playhead is", async () => {
    let release: (b: AudioBuffer) => void = () => {};
    const decode = vi.fn((_c: BaseAudioContext, url: string) =>
      url === "/s/a" ? new Promise<AudioBuffer>((r) => { release = r; }) : Promise.resolve(fakeBuffer(0.4)));
    const { ctx, preview } = await setup(program(), decode);
    preview.play(0);
    expect(ctx.sources).toHaveLength(0);
    ctx.currentTime = 10.6;                                    // F 0.6: "a" is sounding
    release(fakeBuffer(0.4));
    await Promise.resolve();
    await Promise.resolve();
    expect(ctx.sources).toHaveLength(1);
    expect(ctx.sources[0].started!.when).toBeCloseTo(10.6, 9);
    expect(ctx.sources[0].started!.offset).toBeCloseTo(0.1, 9);
  });

  it("plays a trimmed ticker for its kept length with a fade to zero", async () => {
    const p = program({ sfx: [{ id: "c.sfx", at: 1, asset: asset("/s/t", 1.2), gainDb: 0, keep: 0.5 }] });
    const { ctx, preview } = await setup(p, async () => fakeBuffer(1.2));
    preview.play(0);
    expect(ctx.sources[0].started).toEqual({ when: 11, offset: 0, duration: 0.5 });
    const events = (ctx.sources[0].outputs[0] as FakeGain).gain.events;
    expect(events.at(-1)).toEqual({ type: "ramp", value: 0, time: 11.5 });
    expect(events.at(-2)!.time).toBeCloseTo(11.47, 9);
  });

  it("pause stops everything and restores the voice", async () => {
    const { ctx, preview } = await setup(program({ mutes: [[0, 1]] }));
    preview.play(0.2);
    expect(preview.voiceInput.gain).toBeDefined();
    preview.pause();
    expect(ctx.sources.every((s) => s.stopped !== null)).toBe(true);
    const events = (preview.voiceInput as unknown as FakeGain).gain.events;
    expect(events.at(-1)).toEqual({ type: "set", value: 1, time: 10 });
    expect(preview.isPlaying).toBe(false);
  });

  it("reschedules when the program changes during playback", async () => {
    const { ctx, preview } = await setup(program());
    preview.play(0);
    const first = ctx.sources[0];
    preview.setProgram(program({ sfx: [{ id: "z", at: 1, asset: asset("/s/a", 0.4), gainDb: 0, keep: null }] }));
    expect(first.stopped).not.toBeNull();
    expect(ctx.sources.at(-1)!.started!.when).toBeCloseTo(11, 9);
  });
});

describe("FxAudioPreview bleeps", () => {
  it("mutes the voice over each span and plays a 1 kHz tone with ramps", async () => {
    const { ctx, preview } = await setup(program({ sfx: [], tones: [[1, 1.5]], mutes: [[1, 1.5]] }));
    preview.play(0);
    const voice = (preview.voiceInput as unknown as FakeGain).gain.events.filter((e) => e.type === "set");
    expect(voice).toEqual([
      { type: "set", value: 1, time: 10 },
      { type: "set", value: 0, time: 11 },
      { type: "set", value: 1, time: 11.5 },
    ]);
    expect(ctx.oscillators).toHaveLength(1);
    const osc = ctx.oscillators[0];
    expect(osc.frequency.value).toBe(1000);
    expect(osc.started!.when).toBe(11);
    expect(osc.stopped).toBe(11.5);
    const g = (osc.outputs[0] as FakeGain).gain.events;
    expect(g[0]).toEqual({ type: "set", value: 0, time: 11 });
    expect(g[1].type).toBe("ramp");
    expect(g[1].value).toBeCloseTo(dbToGain(-12), 9);
    expect(g[1].time).toBeCloseTo(11.005, 9);
    expect(g.at(-1)).toEqual({ type: "ramp", value: 0, time: 11.5 });
  });

  it("starts muted when playback begins inside a bleep", async () => {
    const { ctx, preview } = await setup(program({ sfx: [], tones: [[1, 1.5]], mutes: [[1, 1.5]] }));
    preview.play(1.2);
    const voice = (preview.voiceInput as unknown as FakeGain).gain.events.filter((e) => e.type === "set");
    expect(voice[0]).toEqual({ type: "set", value: 0, time: 10 });
    expect(voice.at(-1)!.value).toBe(1);
    expect(voice.at(-1)!.time).toBeCloseTo(10.3, 9);
    expect(ctx.oscillators[0].started!.when).toBe(10);
  });
});

describe("FxAudioPreview music", () => {
  const points = duckEnvelope(speechSpans([[1, 2], [3.5, 4.2]]), {
    gain_db: -20, duck_db: -12, attack: 0.15, release: 0.4, fade_in: 0.5, fade_out: 0.8, out_duration: 6,
  });
  const music = { asset: asset("/music/hype", 8, { loopSeconds: 7.5 }), offset: 1, points, normDb: -2 };

  it("loops the bed from offset + position and ramps its gain through every duckEnvelope breakpoint", async () => {
    const { ctx, preview } = await setup(program({ sfx: [], music }));
    preview.play(0);
    for (let c = 10; c <= 16; c += 0.25) {
      ctx.currentTime = c;
      preview.tick();
    }
    const bedSource = ctx.sources.find((s) => s.loop)!;
    expect(bedSource.started).toEqual({ when: 10, offset: 1, duration: undefined });
    expect(bedSource.loopEnd).toBe(7.5);
    expect(bedSource.stopped).toBe(16);
    const events = (bedSource.outputs[0] as FakeGain).gain.events;
    expect(events[0]).toEqual({ type: "set", value: dbToGain(envelopeDbAt(points, 0) - 2), time: 10 });
    const ramps = events.filter((e) => e.type === "ramp");
    // Every breakpoint after 0 is a ramp target at its context time with its exact (linear) value …
    for (const [t, db] of points.slice(1)) {
      const hit = ramps.find((r) => Math.abs(r.time - (10 + t)) < 1e-9);
      expect(hit, `breakpoint ${t}`).toBeDefined();
      expect(hit!.value).toBeCloseTo(dbToGain(db - 2), 12);
    }
    // … every extra point lies on the envelope, ramps are time-ordered and move ≤ RAMP_STEP_DB.
    let prev = { time: 10, db: envelopeDbAt(points, 0) };
    for (const r of ramps) {
      const db = 20 * Math.log10(r.value) + 2;
      expect(db).toBeCloseTo(envelopeDbAt(points, r.time - 10), 6);
      expect(r.time).toBeGreaterThan(prev.time);
      expect(Math.abs(db - prev.db)).toBeLessThanOrEqual(RAMP_STEP_DB + 1e-6);
      prev = { time: r.time, db };
    }
  });

  it("starts the bed mid-loop after a seek with the envelope level of that moment", async () => {
    const { ctx, preview } = await setup(program({ sfx: [], music: { ...music, offset: 3 } }));
    preview.play(0);
    preview.seek(4.6);                                         // offset + position = 7.6 → wraps to 0.1 (loop 7.5)
    const bedSource = ctx.sources.filter((s) => s.loop).at(-1)!;
    expect(bedSource.started!.offset).toBeCloseTo(0.1, 9);
    const first = (bedSource.outputs[0] as FakeGain).gain.events[0];
    expect(first.value).toBeCloseTo(dbToGain(envelopeDbAt(points, 4.6) - 2), 12);
  });

  it("loops a built-in bed between its loop start and end, starting at loop start + offset", async () => {
    const padded = { ...music, asset: asset("/music/hype", 8, { loopStart: 0.1, loopSeconds: 7.5 }), offset: 3 };
    const { ctx, preview } = await setup(program({ sfx: [], music: padded }));
    preview.play(0);
    let bedSource = ctx.sources.find((s) => s.loop)!;
    expect([bedSource.loopStart, bedSource.loopEnd]).toEqual([0.1, 7.6]);
    expect(bedSource.started!.offset).toBeCloseTo(3.1, 9);
    preview.seek(4.6);                                         // 3 + 4.6 = 7.6 → 0.1 into the loop → 0.2 in the file
    bedSource = ctx.sources.filter((s) => s.loop).at(-1)!;
    expect(bedSource.started!.offset).toBeCloseTo(0.2, 9);
  });

  it("ignores a loop start on a drop-in bed", async () => {
    const dropIn = { ...music, asset: asset("/music/mine", 8, { loopStart: 0.1, loopSeconds: 7.5, custom: true }) };
    const { ctx, preview } = await setup(program({ sfx: [], music: dropIn }));
    preview.play(0);
    const bedSource = ctx.sources.find((s) => s.loop)!;
    expect([bedSource.loopStart, bedSource.loopEnd]).toEqual([0, 8]);
    expect(bedSource.started!.offset).toBeCloseTo(1, 9);
  });

  it("envelopeRamps covers (from, to] and splits big dB moves", () => {
    const pts: [number, number][] = [[0, -60], [1, -20], [2, -20]];
    const ramps = envelopeRamps(pts, 0, 1.5);
    expect(ramps).toHaveLength(40);
    expect(ramps.at(-1)).toEqual([1, -20]);
    expect(envelopeRamps(pts, 1, 2)).toEqual([[2, -20]]);
    expect(envelopeRamps(pts, 2, 3)).toEqual([]);
  });
});


describe("createPreviewFxAudio (the preview's fxAudio factory)", () => {
  const manifest: FxAssetManifest = {
    version: 1,
    sfx: [{ id: "impact_0", sound: "impact", variant: 0, url: "/fx/assets/i0.wav", duration: 0.4, gain_db: 0 }],
    music: [],
    emoji: { atlas: "/fx/assets/a.png", map: "/fx/assets/a.json", cell: 160 },
  };
  const base = { seed: 1, beat: "", origin: "auto" as const };
  const doc = (cues: FxCue[]): EditDocument => ({
    ...draft,
    segments: [{ id: "a", start: 0, end: 4 }],
    fx: { ...DEFAULT_DOC_FX, style: { ...DEFAULT_DOC_FX.style, intensity: "bold" }, cues },
  });

  it("routes the voice through voiceInput, plays the draft's SFX and bleeps, and restores the voice on dispose", async () => {
    const ctx = new FakeContext();
    const voice = new FakeGain();
    voice.connect(ctx.destination);
    const controller = createPreviewFxAudio(
      { context: ctx as unknown as AudioContext, destination: ctx.destination as unknown as AudioNode, voice: voice as unknown as GainNode },
      doc([
        { ...base, id: "s", kind: "sfx", start: 1, end: 1, sound: "impact", variant: 0, gain_db: 0 },
        { ...base, id: "b", kind: "bleep", start: 0.2, end: 0.4, tone: true, cover: "bar" },
      ]),
      { manifest: async () => manifest, decode: async () => fakeBuffer(0.4), schedule: () => () => {} },
    );
    expect(voice.outputs).toHaveLength(1);
    expect(voice.outputs[0]).not.toBe(ctx.destination);       // the fx voice input
    for (let i = 0; i < 4; i++) await Promise.resolve();
    controller.start(0, 1);
    expect(ctx.oscillators).toHaveLength(1);                   // the bleep tone
    const impact = ctx.sources.find((s) => s.started && !s.loop)!;
    expect(impact.started!.when).toBeCloseTo(11, 9);
    controller.stop();
    expect(impact.stopped).toBe(10);
    controller.dispose();
    expect(voice.outputs).toEqual([ctx.destination]);
  });

  it("asks for the manifest again after a failed request", async () => {
    const ctx = new FakeContext();
    const voice = new FakeGain();
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const manifestLoader = vi.fn<() => Promise<FxAssetManifest>>().mockRejectedValueOnce(new Error("offline")).mockResolvedValue(manifest);
    const sfxCue: FxCue = { ...base, id: "s", kind: "sfx", start: 1, end: 1, sound: "impact", variant: 0, gain_db: 0 };
    const controller = createPreviewFxAudio(
      { context: ctx as unknown as AudioContext, destination: ctx.destination as unknown as AudioNode, voice: voice as unknown as GainNode },
      doc([sfxCue]), { manifest: manifestLoader, decode: async () => fakeBuffer(0.4), schedule: () => () => {} },
    );
    for (let i = 0; i < 4; i++) await Promise.resolve();
    expect(manifestLoader).toHaveBeenCalledTimes(1);
    expect(warn).toHaveBeenCalled();
    // The next change to the draft retries, and the sound plays once the manifest is in.
    controller.setDocument(doc([sfxCue]));
    expect(manifestLoader).toHaveBeenCalledTimes(2);
    for (let i = 0; i < 4; i++) await Promise.resolve();
    controller.start(0, 1);
    expect(ctx.sources.some((s) => s.started && !s.loop)).toBe(true);
    controller.dispose();
    warn.mockRestore();
  });

  it("plays nothing for a draft without dynamic edits", () => {
    const ctx = new FakeContext();
    const voice = new FakeGain();
    const manifestLoader = vi.fn(async () => manifest);
    const controller = createPreviewFxAudio(
      { context: ctx as unknown as AudioContext, destination: ctx.destination as unknown as AudioNode, voice: voice as unknown as GainNode },
      draft, { manifest: manifestLoader, schedule: () => () => {} },
    );
    controller.start(0, 1);
    expect(ctx.sources).toHaveLength(0);
    expect(ctx.oscillators).toHaveLength(0);
    expect(manifestLoader).not.toHaveBeenCalled();
    controller.dispose();
  });
});
