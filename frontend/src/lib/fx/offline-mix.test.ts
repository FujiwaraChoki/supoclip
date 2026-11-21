import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { FxAssetManifest } from "./assets-client";
import { duckEnvelope, envelopeDbAt, speechSpans } from "./core/duck";
import type { BleepCue, FxCue, MusicBed, SfxCue, AccentCue } from "./core/types";
import { buildWarp, identityWarp } from "./core/warp";
import {
  MIX_SAMPLE_RATE,
  SFX_BUS_GAIN_DB,
  applyMutes,
  avDuration,
  bleepTone,
  dbToGain,
  fxAudioProgram,
  hasFxAudio,
  loopSeam,
  muteSpansE,
  renderBus,
  renderOfflineMix,
  sfxBusGainDb,
  softClipSample,
  tickerEndE,
  voiceLoopCrossfade,
  type FxAudioInput,
  type FxAudioProgram,
} from "./offline-mix";

const SR = MIX_SAMPLE_RATE;

const manifest: FxAssetManifest = {
  version: 1,
  sfx: [
    { id: "whoosh_0", sound: "whoosh", variant: 0, url: "/fx/assets/w0.wav", duration: 0.5, gain_db: -1 },
    { id: "whoosh_1", sound: "whoosh", variant: 1, url: "/fx/assets/w1.wav", duration: 0.4, gain_db: 0 },
    { id: "ticker_0", sound: "ticker", variant: 0, url: "/fx/assets/t0.wav", duration: 1.2, gain_db: 0 },
    { id: "stamp_0", sound: "stamp", variant: 0, url: "/fx/assets/s0.wav", duration: 0.35, gain_db: 0 },
  ],
  music: [{ id: "hype", mood: "hype", url: "/fx/assets/hype.m4a", duration: 8, loop_seconds: 8 }],
  emoji: { atlas: "/fx/assets/a.png", map: "/fx/assets/a.json", cell: 160 },
};

const base = { seed: 7, beat: "", origin: "auto" as const };
const sfx = (id: string, start: number, sound: SfxCue["sound"] = "whoosh", gain_db = 0, variant = 0): SfxCue =>
  ({ ...base, id, kind: "sfx", start, end: start, sound, variant, gain_db });
const bleep = (id: string, start: number, end: number, tone = true): BleepCue =>
  ({ ...base, id, kind: "bleep", start, end, tone, cover: "bar" });
const bed: MusicBed = {
  mood: "hype", bed: "hype", gain_db: -20, duck_db: -12, attack: 0.15, release: 0.4, offset: 0, fade_in: 0.5,
  fade_out: 0.5,
};

const input = (over: Partial<FxAudioInput> = {}): FxAudioInput => ({
  cues: [], words: [], warp: identityWarp(4), music: null, style: { intensity: "bold" }, sensitivity: "normal",
  seed: 3, loopEnding: "none", ...over,
});

/** A minimal AudioBuffer (what decodeAudioData returns), filled by `fill(i)` on every channel. */
function buffer(seconds: number, fill: (i: number) => number, channels = 1, sr = SR): AudioBuffer {
  const length = Math.round(seconds * sr);
  const data = Array.from({ length: channels }, () => Float32Array.from({ length }, (_, i) => fill(i)));
  return {
    length, duration: length / sr, sampleRate: sr, numberOfChannels: channels,
    getChannelData: (c: number) => data[c],
  } as unknown as AudioBuffer;
}

const rms = (data: Float32Array, from: number, to: number) => {
  const a = Math.max(0, Math.round(from * SR)), b = Math.min(data.length, Math.round(to * SR));
  let sum = 0;
  for (let i = a; i < b; i++) sum += data[i] * data[i];
  return b > a ? Math.sqrt(sum / (b - a)) : 0;
};

describe("sfxBusGainDb (resolver.SFX_BUS_GAIN_DB)", () => {
  it("follows the intensity tier; serious forces subtle; off is style-only", () => {
    expect(sfxBusGainDb({ intensity: "off" }, "normal")).toBe(-16);
    expect(sfxBusGainDb({ intensity: "subtle" }, "normal")).toBe(-20);
    expect(sfxBusGainDb({ intensity: "bold" }, "normal")).toBe(-16);
    expect(sfxBusGainDb({ intensity: "max" }, "normal")).toBe(-10);
    expect(sfxBusGainDb({ intensity: "max" }, "serious")).toBe(-20);
    expect(sfxBusGainDb({ intensity: "off" }, "serious")).toBe(-16);
  });
});

// The mixing tests below read amplitudes at 0 dB bus gain (bold used to be 0 dB); the level table itself is pinned by
// the sfxBusGainDb test above and by the backend resolver tests.
const REAL_BUS = { ...SFX_BUS_GAIN_DB };
beforeEach((ctx) => {
  if (ctx.task.name.startsWith("follows the intensity tier")) return;
  Object.assign(SFX_BUS_GAIN_DB as Record<string, number>, { style: 0, subtle: -4, bold: 0, max: 2 });
});
afterEach(() => { Object.assign(SFX_BUS_GAIN_DB as Record<string, number>, REAL_BUS); });

describe("fxAudioProgram", () => {
  it("places SFX at F times with cue + file + bus gain and the mix_sfx_file pick", () => {
    const program = fxAudioProgram(input({
      cues: [sfx("b", 2, "whoosh", -3, 1), sfx("a", 1, "whoosh", -2, 0)], style: { intensity: "max" },
    }), manifest);
    expect(program.sfx.map((e) => [e.id, e.at, e.asset.id, e.gainDb])).toEqual([
      ["a", 1, "whoosh_0", -2 + -1 + 2],
      ["b", 2, "whoosh_1", -3 + 0 + 2],
    ]);
    expect(program.sfx[0].asset.url).toBe("/api/fx/assets/w0.wav");
  });

  it("maps every time through the warp (a freeze pushes later sounds back)", () => {
    const freeze: FxCue = { ...base, id: "f", kind: "freeze", start: 1, end: 1, hold: 0.5, label: "" };
    const warp = buildWarp([freeze], 4);
    const program = fxAudioProgram(input({
      cues: [sfx("a", 2), bleep("x", 2.5, 3)], warp, words: [{ start: 2, end: 2.4 }], music: bed,
    }), manifest);
    expect(program.outDuration).toBeCloseTo(4.5, 9);
    expect(program.sfx[0].at).toBeCloseTo(2.5, 9);
    expect(program.tones[0][0]).toBeCloseTo(3, 9);
    expect(program.tones[0][1]).toBeCloseTo(3.5, 9);
    expect(program.mutes[0][0]).toBeCloseTo(3, 9);
    expect(program.music!.points).toEqual(duckEnvelope(speechSpans([[2.5, 2.9]]), {
      ...bed, out_duration: 4.5,
    }));
  });

  it("skips unknown sounds and keeps bleeps without a manifest", () => {
    const program = fxAudioProgram(input({ cues: [sfx("a", 1, "boom"), bleep("x", 1, 1.25, false)], music: bed }), null);
    expect(program.sfx).toEqual([]);
    expect(program.music).toBeNull();
    expect(program.tones).toEqual([]);
    expect(program.mutes).toEqual([[1, 1.25]]);
    expect(hasFxAudio(program)).toBe(true);
    expect(hasFxAudio(fxAudioProgram(input(), manifest))).toBe(false);
  });

  it("trims a counter's ticker to its lock (the .sfx2 stamp) or the accent's lock", () => {
    const counter: AccentCue = {
      ...base, id: "c1", kind: "accent", variant: "counter", start: 1, end: 3, text: "", emoji: "", doodle: "sparkles",
      value: 10, prefix: "", suffix: "",
    };
    const ticker = sfx("c1.sfx", 1, "ticker");
    const lock = sfx("c1.sfx2", 1.6, "stamp");
    expect(tickerEndE(ticker, [counter, ticker, lock])).toBe(1.6);
    expect(tickerEndE(ticker, [counter, ticker])).toBeCloseTo(2.1, 9);
    expect(tickerEndE({ ...ticker, id: "c1.sfx@1" }, [{ ...counter, id: "c1@1" }])).toBeCloseTo(2.1, 9);
    expect(tickerEndE(sfx("other", 1, "ticker"), [counter])).toBeNull();
    const program = fxAudioProgram(input({ cues: [counter, ticker, lock] }), manifest);
    expect(program.sfx.find((e) => e.id === "c1.sfx")!.keep).toBeCloseTo(0.6, 9);
    expect(program.sfx.find((e) => e.id === "c1.sfx2")!.keep).toBeNull();
  });

  it("quantises voice mutes outward to whole milliseconds (the server's volume enable)", () => {
    expect(muteSpansE([bleep("x", 1.0004, 1.2501)])).toEqual([[1, 1.251]]);
  });

  it("lasts the video's whole 30 fps frames (fx_chain av_duration)", () => {
    expect(avDuration(0)).toBe(0);
    expect(avDuration(0.001)).toBeCloseTo(1 / 30, 12);
    expect(avDuration(2.0123)).toBeCloseTo(60 / 30, 12);
    expect(avDuration(2.02)).toBeCloseTo(61 / 30, 12);
    const program = fxAudioProgram(input({ warp: identityWarp(2.02), music: bed }), manifest);
    expect(program.outDuration).toBeCloseTo(61 / 30, 12);
    expect(program.music!.points.at(-1)![0]).toBeCloseTo(61 / 30, 9);
  });

  it("does not depend on the manifest duration to cut a ticker (the decoded length decides)", () => {
    const counter: AccentCue = {
      ...base, id: "c", kind: "accent", variant: "counter", start: 1, end: 3, text: "", emoji: "", doodle: "sparkles",
      value: 5, prefix: "", suffix: "",
    };
    const short = { ...manifest, sfx: manifest.sfx.map((e) => (e.sound === "ticker" ? { ...e, duration: 0.5 } : e)) };
    const program = fxAudioProgram(input({ cues: [counter, sfx("c.sfx", 1, "ticker")] }), short);
    expect(program.sfx[0].keep).toBeCloseTo(1.1, 9);
    // The decoded file is 1.2 s, so the bus still cuts it at 1.1 s like the server.
    const bus = renderBus(program, new Map([["/api/fx/assets/t0.wav", buffer(1.2, () => 0.5)]]))!;
    expect(bus[0][Math.round(2.05 * SR)]).toBeCloseTo(0.5, 6);
    expect(rms(bus[0], 2.1, 4)).toBe(0);
  });

  it("sets the loop crossfade only for loop_ending crossfade", () => {
    expect(fxAudioProgram(input({ loopEnding: "crossfade" }), manifest).loopCrossfade).toBe(0.4);
    expect(fxAudioProgram(input(), manifest).loopCrossfade).toBe(0);
  });
});

describe("renderBus (build_bus)", () => {
  it("puts SFX energy in the planned windows and nowhere else", () => {
    const program = fxAudioProgram(input({ cues: [sfx("a", 1), sfx("b", 2.5, "whoosh", -6, 1)] }), manifest);
    const buffers = new Map([
      ["/api/fx/assets/w0.wav", buffer(0.5, (i) => (i % 2 ? 0.5 : -0.5))],
      ["/api/fx/assets/w1.wav", buffer(0.4, (i) => (i % 2 ? 0.5 : -0.5), 2)],
    ]);
    const bus = renderBus(program, buffers)!;
    expect(bus[0].length).toBe(4 * SR);
    expect(rms(bus[0], 0, 1)).toBe(0);
    expect(rms(bus[0], 1, 1.5)).toBeCloseTo(0.5 * dbToGain(-1), 4);    // file norm -1 dB
    expect(rms(bus[1], 1, 1.5)).toBeCloseTo(0.5 * dbToGain(-1), 4);    // mono → both sides
    expect(rms(bus[0], 1.5, 2.5)).toBe(0);
    expect(rms(bus[0], 2.5, 2.9)).toBeCloseTo(0.5 * dbToGain(-6), 4);
    expect(rms(bus[0], 2.9, 4)).toBe(0);
  });

  it("returns null when nothing is mixed (missing files are skipped)", () => {
    const program = fxAudioProgram(input({ cues: [sfx("a", 1)] }), manifest);
    expect(renderBus(program, new Map())).toBeNull();
  });

  it("trims the ticker with a 30 ms fade to silence", () => {
    const counter: AccentCue = {
      ...base, id: "c", kind: "accent", variant: "counter", start: 1, end: 2, text: "", emoji: "", doodle: "sparkles",
      value: 5, prefix: "", suffix: "",
    };
    const program = fxAudioProgram(input({ cues: [counter, sfx("c.sfx", 1, "ticker")] }), manifest);
    const bus = renderBus(program, new Map([["/api/fx/assets/t0.wav", buffer(1.2, () => 0.5)]]))!;
    const keep = Math.round(0.1 * SR);                      // accent end − 0.9 = 1.1 → 0.1 s kept
    expect(bus[0][SR + 10]).toBeCloseTo(0.5, 6);
    expect(bus[0][SR + keep - 1]).toBe(0);
    expect(bus[0][SR + keep - Math.round(0.015 * SR)]).toBeGreaterThan(0.2);
    expect(bus[0][SR + keep - Math.round(0.015 * SR)]).toBeLessThan(0.3);
    expect(rms(bus[0], 1.1, 4)).toBe(0);
  });

  it("draws the bleep tone at −12 dBFS with 5 ms ramps", () => {
    const tone = bleepTone(SR / 10);
    expect(tone[0]).toBe(0);
    let peak = 0;
    for (const v of tone) peak = Math.max(peak, Math.abs(v));
    expect(peak).toBeCloseTo(dbToGain(-12), 3);
    const program = fxAudioProgram(input({ cues: [bleep("x", 1, 1.5)] }), manifest);
    const bus = renderBus(program, new Map())!;
    expect(rms(bus[0], 1.01, 1.49)).toBeCloseTo(dbToGain(-12) / Math.SQRT2, 3);
    expect(rms(bus[0], 0, 1)).toBe(0);
    expect(rms(bus[0], 1.5, 4)).toBe(0);
  });

  it("ducks the bed exactly by the envelope (dB-linear, normalisation added)", () => {
    const program = fxAudioProgram(input({ music: bed, words: [{ start: 1.5, end: 2.5 }] }), manifest);
    const bus = renderBus(program, new Map([["/api/fx/assets/hype.m4a", buffer(8, () => 0.5)]]))!;
    for (const t of [0.25, 0.9, 1.4, 1.6, 2.0, 2.7, 3.2, 3.8]) {
      const i = Math.round(t * SR);
      expect(bus[0][i]).toBeCloseTo(0.5 * dbToGain(envelopeDbAt(program.music!.points, i / SR)), 6);
    }
    const quiet = rms(bus[0], 1.6, 2.4), open = rms(bus[0], 3.1, 3.4);
    expect(20 * Math.log10(quiet / open)).toBeCloseTo(-12, 3);
  });

  it("starts the bed at its offset and tiles built-ins at their loop length", () => {
    const program = fxAudioProgram(input({ music: { ...bed, offset: 1.5, fade_in: 0, fade_out: 0 }, warp: identityWarp(10) }), manifest);
    const ramp = buffer(8, (i) => i / (8 * SR));
    const bus = renderBus(program, new Map([["/api/fx/assets/hype.m4a", ramp]]))!;
    const g = dbToGain(-20);
    expect(bus[0][0] / g).toBeCloseTo(1.5 / 8, 4);
    expect(bus[0][Math.round(7 * SR)] / g).toBeCloseTo(0.5 / 8, 4);   // 1.5 + 7 = 8.5 → wraps to 0.5
  });

  it("tiles a built-in bed from its loop start, never playing the padding around the loop", () => {
    // Built-in files hold a little of the loop's own audio before and after it: [loop_start, loop_start + loop_seconds)
    const padded: FxAssetManifest = {
      ...manifest,
      music: [{ id: "hype", mood: "hype", url: "/fx/assets/hype.mp3", duration: 8, loop_start: 0.5, loop_seconds: 7 }],
    };
    const program = fxAudioProgram(input({ music: { ...bed, offset: 1.5, fade_in: 0, fade_out: 0 }, warp: identityWarp(10) }), padded);
    expect(program.music!.asset).toMatchObject({ loopStart: 0.5, loopSeconds: 7 });
    const ramp = buffer(8, (i) => i / (8 * SR));                       // level encodes the file position
    const bus = renderBus(program, new Map([["/api/fx/assets/hype.mp3", ramp]]))!;
    const g = dbToGain(-20);
    expect(bus[0][0] / g).toBeCloseTo(2 / 8, 4);                            // 0.5 + 1.5 s into the file
    expect(bus[0][Math.round(5.4 * SR)] / g).toBeCloseTo(7.4 / 8, 4);       // just before the seam
    expect(bus[0][Math.round(5.5 * SR)] / g).toBeCloseTo(0.5 / 8, 4);       // 1.5 + 5.5 = 7 → back to the loop start
    expect(bus[0][Math.round(9 * SR)] / g).toBeCloseTo(4 / 8, 4);           // 10.5 % 7 = 3.5 → 0.5 + 3.5
  });

  it("crossfades a drop-in bed's seam with equal power", () => {
    const dropIn: FxAssetManifest = {
      ...manifest, music: [{ id: "hype-mine", mood: "hype", url: "/fx/assets/m.wav", duration: 1, loop_seconds: 1, custom: true }],
    };
    const program = fxAudioProgram(input({ music: { ...bed, fade_in: 0, fade_out: 0, duck_db: 0 } }), dropIn);
    const bus = renderBus(program, new Map([["/api/fx/assets/m.wav", buffer(1, () => 0.5)]]))!;
    const g = dbToGain(-20);
    // Seam 1 at 0.95 s: tail·cos + head·sin peaks at √2 mid-fade on a constant signal.
    expect(bus[0][Math.round(0.975 * SR)] / g).toBeGreaterThan(0.5 * 1.3);
    expect(bus[0][Math.round(0.5 * SR)] / g).toBeCloseTo(0.5, 6);
    // The period after the first pass is 0.95 s.
    expect(bus[0][Math.round(1.5 * SR)] / g).toBeCloseTo(0.5, 6);
  });

  it("starts a drop-in past its length at offset % length, without a seam (_looped)", () => {
    const dropIn: FxAssetManifest = {
      ...manifest, music: [{ id: "hype-mine", mood: "hype", url: "/fx/assets/m.wav", duration: 1, loop_seconds: 1, custom: true }],
    };
    const program = fxAudioProgram(input({ music: { ...bed, offset: 1.25, fade_in: 0, fade_out: 0, duck_db: 0 } }), dropIn);
    const ramp = buffer(1, (i) => i / SR);
    const bus = renderBus(program, new Map([["/api/fx/assets/m.wav", ramp]]))!;
    const g = dbToGain(-20);
    // 1.25 % 1 = 0.25: the first pass plays the file from 0.25 s, straight through (no seam before 0.95 − 0.25).
    expect(bus[0][0] / g).toBeCloseTo(0.25, 5);
    expect(bus[0][Math.round(0.5 * SR)] / g).toBeCloseTo(0.75, 5);
  });

  it("uses the later point at a repeated breakpoint time, like np.interp", () => {
    const program: FxAudioProgram = {
      outDuration: 0.01, sfx: [], tones: [], mutes: [], loopCrossfade: 0,
      music: {
        asset: { id: "b", url: "/b", gainDb: 0, duration: 1, loopSeconds: 1, custom: false }, offset: 0, normDb: 0,
        points: [[0, -60], [0, -20], [1, -20]],
      },
    };
    const bus = renderBus(program, new Map([["/b", buffer(1, () => 0.5)]]))!;
    expect(bus[0][0]).toBeCloseTo(0.5 * dbToGain(-20), 7);
  });

  it("keeps a one-sample ticker cut at full level (np.linspace(1, 0, 1) == [1])", () => {
    const program: FxAudioProgram = {
      outDuration: 1, tones: [], mutes: [], loopCrossfade: 0, music: null,
      sfx: [{ id: "t", at: 0, keep: 1 / SR, gainDb: 0,
        asset: { id: "t", url: "/t", gainDb: 0, duration: 1, custom: false } }],
    };
    const bus = renderBus(program, new Map([["/t", buffer(1, () => 0.5)]]))!;
    expect(bus[0][0]).toBeCloseTo(0.5, 7);
    expect(bus[0][1]).toBe(0);
  });

  it("soft-clips: identity below 0.8, never reaching 1", () => {
    expect(softClipSample(0.5)).toBe(0.5);
    expect(softClipSample(-0.79)).toBe(-0.79);
    expect(softClipSample(3)).toBeLessThan(1);
    expect(softClipSample(-3)).toBeGreaterThan(-1);
    expect(softClipSample(0.9)).toBeCloseTo(0.8 + 0.2 * Math.tanh(0.5), 9);
  });

  it("crossfades the bus seam with the head for loop endings", () => {
    const ch = [Float32Array.from({ length: 100 }, (_, i) => (i < 10 ? 1 : 0))];
    loopSeam(ch, 10 / SR);
    expect(ch[0][90]).toBeCloseTo(0, 6);
    expect(ch[0][99]).toBeCloseTo(1, 6);
  });
});

describe("voice processing", () => {
  it("mutes every 1 ms frame that starts inside a span", () => {
    const voice = [new Float32Array(SR).fill(1)];
    applyMutes(voice, [[0.1, 0.2]]);
    expect(voice[0][Math.round(0.1 * SR) - 1]).toBe(1);
    expect(voice[0][Math.round(0.1 * SR)]).toBe(0);
    expect(voice[0][Math.round(0.2 * SR) + 47]).toBe(0);     // the frame starting at 0.200 s
    expect(voice[0][Math.round(0.2 * SR) + 48]).toBe(1);
  });

  it("crossfades the voice tail into its head linearly", () => {
    const voice = [Float32Array.from({ length: 1000 }, (_, i) => (i < 100 ? 1 : 0))];
    voiceLoopCrossfade(voice, 100 / SR);
    expect(voice[0][900]).toBe(0);
    expect(voice[0][950]).toBeCloseTo(0.5, 6);
  });
});

/** An OfflineAudioContext that renders a buffer source → gain → destination at the context rate (no resampling). */
function fakeOffline() {
  const created: { channels: number; length: number; sampleRate: number }[] = [];
  const decode = vi.fn();
  const factory = (channels: number, length: number, sampleRate: number) => {
    created.push({ channels, length, sampleRate });
    let source: { buffer: AudioBuffer | null; at: number } | null = null;
    let gainValue = 1;
    const ctx = {
      sampleRate,
      destination: {},
      createBuffer: (n: number, len: number, rate: number) => {
        const data = Array.from({ length: n }, () => new Float32Array(len));
        return {
          length: len, duration: len / rate, sampleRate: rate, numberOfChannels: n,
          getChannelData: (c: number) => data[c],
          copyToChannel: (src: Float32Array, c: number) => data[c].set(src),
        };
      },
      createBufferSource: () => {
        const node = { buffer: null as AudioBuffer | null, connect: () => {}, start: (at = 0) => { source = { buffer: node.buffer, at }; } };
        return node;
      },
      createGain: () => ({ gain: { set value(v: number) { gainValue = v; }, get value() { return gainValue; } }, connect: () => {} }),
      startRendering: async () => {
        const out = [new Float32Array(length), new Float32Array(length)];
        if (source?.buffer) {
          const b = source.buffer;
          expect(b.sampleRate).toBe(sampleRate);
          for (let c = 0; c < 2; c++) {
            const src = b.getChannelData(Math.min(c, b.numberOfChannels - 1));
            for (let i = 0; i < Math.min(length, src.length); i++) out[c][i] = src[i] * gainValue;
          }
        }
        return { length, numberOfChannels: 2, sampleRate, getChannelData: (c: number) => out[c] } as unknown as AudioBuffer;
      },
    };
    return ctx as unknown as OfflineAudioContext;
  };
  return { factory, created, decode };
}

describe("renderOfflineMix", () => {
  const program = (over: Partial<FxAudioProgram> = {}): FxAudioProgram => ({
    ...fxAudioProgram(input({ cues: [sfx("a", 1), bleep("x", 2, 2.5)], warp: identityWarp(3) }), manifest), ...over,
  });

  it("renders voice × volume, mutes the bleep, adds SFX and tone at 48 kHz stereo", async () => {
    const { factory, created } = fakeOffline();
    const voice = { channels: [new Float32Array(3 * SR).fill(0.25)], sampleRate: SR, gain: 0.5 };
    const decode = vi.fn(async () => buffer(0.5, (i) => (i % 2 ? 0.2 : -0.2)));
    const mix = await renderOfflineMix(program(), voice, { createContext: factory, decode });
    expect(created).toEqual([{ channels: 2, length: 3 * SR, sampleRate: SR }]);
    expect(decode).toHaveBeenCalledTimes(1);
    expect(mix.sampleRate).toBe(SR);
    const [left, right] = mix.channels;
    expect(left.length).toBe(3 * SR);
    expect(rms(left, 0, 0.9)).toBeCloseTo(0.125, 6);                       // voice only
    expect(rms(right, 0, 0.9)).toBeCloseTo(0.125, 6);
    expect(rms(left, 1, 1.5)).toBeGreaterThan(0.2);                        // SFX energy in its window
    expect(rms(left, 1.6, 1.9)).toBeCloseTo(0.125, 6);
    // Inside the bleep: no voice, only the tone.
    expect(rms(left, 2.01, 2.49)).toBeCloseTo(dbToGain(-12) / Math.SQRT2, 3);
    expect(rms(left, 2.6, 3)).toBeCloseTo(0.125, 6);
  });

  it("works without a voice track (silent source with a bus)", async () => {
    const { factory } = fakeOffline();
    const mix = await renderOfflineMix(program(), null, { createContext: factory, decode: async () => buffer(0.5, () => 0.3) });
    expect(rms(mix.channels[0], 0, 1)).toBe(0);
    expect(rms(mix.channels[0], 1, 1.5)).toBeCloseTo(0.3 * dbToGain(-1), 4);
  });

  it("fails with the background-export message when an asset cannot be decoded", async () => {
    const { factory } = fakeOffline();
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    await expect(renderOfflineMix(program(), null, {
      createContext: factory, decode: async () => { throw new Error("404"); },
    })).rejects.toThrow("Could not load the edit sounds. Use background export.");
    expect(warn).toHaveBeenCalled();
    warn.mockRestore();
  });

  it("soft-clips the sum", async () => {
    const { factory } = fakeOffline();
    const voice = { channels: [new Float32Array(3 * SR).fill(0.95)], sampleRate: SR, gain: 1 };
    const mix = await renderOfflineMix(program({ sfx: [], tones: [], mutes: [] }), voice, { createContext: factory });
    expect(mix.channels[0][100]).toBeCloseTo(softClipSample(0.95), 6);
  });
});
