import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { FxCue } from "@/lib/fx/core/types";
import { BROWSER_EXPORT_REFUSAL_MESSAGES, SFX_BUS_GAIN_DB, browserExportRefusal, dbToGain } from "@/lib/fx/offline-mix";
import { DEFAULT_DOC_FX, type DocFx, type EditDocument, type EditorState } from "./document";
import { draft } from "./document.test-fixture";
import { assertBrowserExportable, exportAudioProgram, exportDraft, fxFrameTimes } from "./export-draft";

// ----------------------------------------------------------------------------------------------------- mocks

const render = vi.hoisted(() => ({
  drawEditorFrame: vi.fn(),
  loadEditorFont: vi.fn(async () => {}),
  loadEditorFxResources: vi.fn(async () => {}),
}));
vi.mock("./render", () => render);

const download = vi.hoisted(() => ({ downloadBlob: vi.fn() }));
vi.mock("@/lib/clip-actions", () => download);

const manifest = vi.hoisted(() => ({
  version: 1,
  sfx: [{ id: "impact_0", sound: "impact", variant: 0, url: "/fx/assets/i0.wav", duration: 0.5, gain_db: 0 }],
  music: [],
  emoji: { atlas: "/fx/assets/a.png", map: "/fx/assets/a.json", cell: 160 },
}));
const assets = vi.hoisted(() => ({ loadFxManifest: vi.fn(async () => manifest) }));
vi.mock("@/lib/fx/assets-client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/fx/assets-client")>()),
  loadFxManifest: assets.loadFxManifest,
}));

const SR = 48000;
const recorded = vi.hoisted(() => ({
  video: [] as { at: number; duration: number }[],
  audio: [] as { data: Float32Array; format: string; channels: number; rate: number; timestamp: number }[],
  sinkTimestamps: [] as number[][],
  imported: 0,
  /** Timestamps the fake sink has no frame for (yields null), like a source whose first frame starts late. */
  missingBefore: -Infinity,
}));

vi.mock("mediabunny", () => {
  recorded.imported += 1;
  class Track { async canDecode() { return true; } }
  class FakeVideoSample {
    displayWidth = 1080; displayHeight = 1920;
    constructor(public timestamp: number) {}
    draw() {}
    close() {}
  }
  class FakeAudioSample {
    numberOfChannels = 1; sampleRate = SR; numberOfFrames: number;
    constructor(public timestamp: number, seconds: number) { this.numberOfFrames = Math.round(seconds * SR); }
    allocationSize() { return this.numberOfFrames * 4; }
    copyTo(dest: Float32Array, options: { format: string; frameOffset?: number; frameCount?: number }) {
      expect(["f32", "f32-planar"]).toContain(options.format);
      dest.fill(0.25, 0, options.frameCount ?? this.numberOfFrames - (options.frameOffset ?? 0));
    }
    close() {}
  }
  return {
    ALL_FORMATS: [],
    canEncodeAudio: async (codec: string) => codec === "aac",
    canEncodeVideo: async () => true,
    BlobSource: class { constructor(public blob: Blob) {} },
    Mp4OutputFormat: class {},
    BufferTarget: class { buffer: ArrayBuffer | null = null; },
    Input: class {
      async getPrimaryVideoTrack() { return new Track(); }
      async getPrimaryAudioTrack() { return new Track(); }
      dispose() {}
    },
    Output: class {
      target: { buffer: ArrayBuffer | null };
      constructor(options: { target: { buffer: ArrayBuffer | null } }) { this.target = options.target; }
      addVideoTrack() {}
      addAudioTrack() {}
      async start() {}
      async finalize() { this.target.buffer = new ArrayBuffer(8); }
      async cancel() {}
    },
    CanvasSource: class {
      async add(at: number, duration: number) { recorded.video.push({ at, duration }); }
      close() {}
    },
    AudioSampleSource: class {
      async add(sample: { init: { data: Float32Array; format: string; numberOfChannels: number; sampleRate: number; timestamp: number } }) {
        const i = sample.init;
        recorded.audio.push({ data: i.data, format: i.format, channels: i.numberOfChannels, rate: i.sampleRate, timestamp: i.timestamp });
      }
      close() {}
    },
    AudioSample: class { constructor(public init: unknown) {} close() {} },
    VideoSampleSink: class {
      async *samplesAtTimestamps(timestamps: number[]) {
        recorded.sinkTimestamps.push([...timestamps]);
        for (const t of timestamps) yield t < recorded.missingBefore ? null : new FakeVideoSample(t);
      }
      async *samples(start: number, end: number) {
        for (let t = start; t < end - 1e-9; t += 1 / 30) yield Object.assign(new FakeVideoSample(t), { duration: 1 / 30 });
      }
    },
    AudioSampleSink: class {
      async *samples(start: number, end: number) {
        for (let t = start; t < end - 1e-9; t += 0.5) yield new FakeAudioSample(t, Math.min(0.5, end - t));
      }
    },
  };
});

/** OfflineAudioContext stand-in: renders one buffer source → gain at the context rate. */
function fakeOffline(channels: number, length: number, sampleRate: number) {
  let source: AudioBuffer | null = null;
  let gain = 1;
  return {
    sampleRate, destination: {},
    createBuffer: (n: number, len: number, rate: number) => {
      const data = Array.from({ length: n }, () => new Float32Array(len));
      return { length: len, sampleRate: rate, numberOfChannels: n, getChannelData: (c: number) => data[c],
        copyToChannel: (src: Float32Array, c: number) => data[c].set(src) };
    },
    createBufferSource: () => {
      const node = { buffer: null as AudioBuffer | null, connect() {}, start() { source = node.buffer; } };
      return node;
    },
    createGain: () => ({ gain: { set value(v: number) { gain = v; }, get value() { return gain; } }, connect() {} }),
    startRendering: async () => {
      const out = Array.from({ length: channels }, () => new Float32Array(length));
      if (source) for (let c = 0; c < channels; c++) {
        const src = source.getChannelData(Math.min(c, source.numberOfChannels - 1));
        for (let i = 0; i < Math.min(length, src.length); i++) out[c][i] = src[i] * gain;
      }
      return { length, numberOfChannels: channels, sampleRate, getChannelData: (c: number) => out[c] };
    },
  } as unknown as OfflineAudioContext;
}

// ---------------------------------------------------------------------------------------------------- fixtures

const base = { seed: 1, beat: "", origin: "auto" as const };
const cue = {
  sfx: (id: string, start: number): FxCue => ({ ...base, id, kind: "sfx", start, end: start, sound: "impact", variant: 0, gain_db: 0 }),
  bleep: (id: string, start: number, end: number): FxCue => ({ ...base, id, kind: "bleep", start, end, tone: true, cover: "bar" }),
  speed: (id: string, start: number, end: number): FxCue => ({ ...base, id, kind: "speed", start, end, rate: 1.25 }),
  freeze: (id: string, at: number): FxCue => ({ ...base, id, kind: "freeze", start: at, end: at, hold: 0.4, label: "" }),
  reaction: (id: string): FxCue => ({ ...base, id, kind: "reaction", start: 0.5, end: 1.5, query: "wow", provider: "giphy", asset_id: "x", url: "https://media.giphy.com/x.mp4" }),
  behind: (id: string): FxCue => ({ ...base, id, kind: "accent", variant: "behind", start: 0.5, end: 1.5, text: "BIG", emoji: "", doodle: "sparkles", value: null, prefix: "", suffix: "" }),
  zoom: (id: string): FxCue => ({ ...base, id, kind: "zoom", start: 0.5, end: 1, mode: "punch", scale: 1.2, anchor: "center" }),
};

const withFx = (fx: Partial<DocFx>, doc: EditDocument = draft): EditDocument => ({
  ...doc, fx: { ...DEFAULT_DOC_FX, style: { ...DEFAULT_DOC_FX.style, intensity: "bold" }, ...fx },
});

const asset: EditorState = {
  status: "ready", basis: "b", duration: 3, width: 1080, height: 1920, fps: 30, hasAudio: true, waveform: [],
  draft: { revision: 1, document: draft }, original: draft, jobs: [],
};

// ------------------------------------------------------------------------------------------------------- tests

describe("browser export refusal (§10, §14.2)", () => {
  it("allows legacy drafts and dynamic edits without warp, reactions or behind text", () => {
    expect(browserExportRefusal(draft)).toBeNull();
    expect(browserExportRefusal(withFx({ cues: [cue.zoom("z"), cue.sfx("s", 1), cue.bleep("b", 1, 1.2)] }))).toBeNull();
    expect(browserExportRefusal({ ...draft, captions: { ...draft.captions, template: "slam" } })).toBeNull();
  });

  it("refuses speed ramps and freezes", () => {
    expect(browserExportRefusal(withFx({ cues: [cue.speed("v", 1, 2)] }))).toBe("warp");
    expect(browserExportRefusal(withFx({ cues: [cue.freeze("f", 1)] }))).toBe("warp");
  });

  it("allows a warp cue that is switched off or cut out of the edit", () => {
    expect(browserExportRefusal(withFx({ cues: [cue.speed("v", 1, 2)], disabled: ["speed"] }))).toBeNull();
    const cut = { ...draft, segments: [{ id: "s", start: 0, end: 1 }] };
    expect(browserExportRefusal(withFx({ cues: [cue.freeze("f", 2.5)] }, cut))).toBeNull();
  });

  it("refuses reactions and text behind the speaker", () => {
    expect(browserExportRefusal(withFx({ cues: [cue.reaction("r")] }))).toBe("reaction");
    expect(browserExportRefusal(withFx({ cues: [cue.behind("a")] }))).toBe("behind");
    // The behind_head template's captions need no matte; only its behind accents do.
    const behindHead = { ...draft, captions: { ...draft.captions, template: "behind_head" } };
    expect(browserExportRefusal(behindHead)).toBeNull();
    expect(browserExportRefusal(withFx({ cues: [cue.behind("a")] }, behindHead))).toBe("behind");
    expect(browserExportRefusal(withFx({ cues: [cue.behind("a")], disabled: ["accent"] }, behindHead))).toBeNull();
  });

  it("throws the background-export message", () => {
    expect(() => assertBrowserExportable(withFx({ cues: [cue.freeze("f", 1)] })))
      .toThrow(BROWSER_EXPORT_REFUSAL_MESSAGES.warp);
    expect(BROWSER_EXPORT_REFUSAL_MESSAGES.warp).toMatch(/Use background export\.$/);
    expect(() => assertBrowserExportable(draft)).not.toThrow();
  });
});

describe("exportAudioProgram", () => {
  beforeEach(() => assets.loadFxManifest.mockClear());

  it("is null for drafts without dynamic edits or with nothing audible", async () => {
    expect(await exportAudioProgram(draft)).toBeNull();
    expect(await exportAudioProgram(withFx({ cues: [cue.zoom("z")] }))).toBeNull();
    expect(assets.loadFxManifest).not.toHaveBeenCalled();
  });

  it("maps SFX through the segments with the manifest file", async () => {
    const doc = withFx({ cues: [cue.sfx("s", 2.5)] }, { ...draft, segments: [{ id: "a", start: 0, end: 1 }, { id: "b", start: 2, end: 3 }] });
    const program = (await exportAudioProgram(doc))!;
    expect(program.outDuration).toBe(2);
    expect(program.sfx.map((e) => [e.id, e.at, e.asset.id])).toEqual([["s", 1.5, "impact_0"]]);
  });

  it("keeps bleeps when the manifest cannot be loaded", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const program = (await exportAudioProgram(withFx({ cues: [cue.sfx("s", 1), cue.bleep("b", 2, 2.5)] }),
      async () => { throw new Error("offline"); }))!;
    expect(program.sfx).toEqual([]);
    expect(program.mutes).toEqual([[2, 2.5]]);
    expect(warn).toHaveBeenCalled();
    warn.mockRestore();
  });
});

describe("fxFrameTimes", () => {
  it("emits one frame per 1/30 s of the edit, grouped by segment, in source order inside each", () => {
    const doc = { ...draft, segments: [{ id: "b", start: 2, end: 2.5 }, { id: "a", start: 0, end: 0.2 }] };
    const groups = fxFrameTimes(doc);
    expect(groups.map((g) => g.frames.length)).toEqual([15, 6]);
    expect(groups[0].frames[0]).toEqual({ index: 0, at: 0, source: 2.0001 });
    expect(groups[1].frames[0].index).toBe(15);
    expect(groups[1].frames[0].at).toBeCloseTo(0.5, 12);
    expect(groups[1].frames[0].source).toBeCloseTo(0.0001, 9);
    expect(groups[1].frames.at(-1)!.source).toBeLessThan(0.2);
  });
});

describe("exportDraft", () => {
  const getContext = vi.spyOn(HTMLCanvasElement.prototype, "getContext");
  beforeEach(() => {
    recorded.video.length = 0;
    recorded.audio.length = 0;
    recorded.sinkTimestamps.length = 0;
    recorded.imported = 0;
    recorded.missingBefore = -Infinity;
    render.drawEditorFrame.mockClear();
    render.loadEditorFxResources.mockClear();
    download.downloadBlob.mockClear();
    getContext.mockReturnValue({} as unknown as RenderingContext);
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, blob: async () => new Blob() })));
  });
  afterEach(() => vi.unstubAllGlobals());

  it("refuses a warped draft before loading anything", async () => {
    const doc = withFx({ cues: [cue.speed("v", 1, 2)] });
    await expect(exportDraft("/x/media/clean.mp4", doc, asset, "tiktok", new AbortController().signal, () => {}))
      .rejects.toThrow("Use background export.");
    expect(fetch).not.toHaveBeenCalled();
    expect(recorded.video).toHaveLength(0);
  });

  it("renders dynamic edits at 30 fps on F and mixes SFX and bleeps into the voice", async () => {
    const doc = withFx(
      { cues: [cue.sfx("s", 2.5), cue.bleep("b", 0.2, 0.4)] },
      { ...draft, segments: [{ id: "a", start: 0, end: 1 }, { id: "b", start: 2, end: 3 }] },
    );
    const decode = vi.fn(async () => {
      const data = Float32Array.from({ length: SR / 2 }, (_, i) => (i % 2 ? 0.4 : -0.4));
      return { length: data.length, duration: 0.5, sampleRate: SR, numberOfChannels: 1, getChannelData: () => data } as unknown as AudioBuffer;
    });
    const progress = vi.fn();
    await exportDraft("/x/media/clean.mp4", doc, asset, "tiktok", new AbortController().signal, progress,
      { createContext: fakeOffline, decode });

    expect(render.loadEditorFxResources).toHaveBeenCalledTimes(1);
    expect(recorded.video).toHaveLength(60);
    recorded.video.forEach((frame, n) => {
      expect(frame.at).toBeCloseTo(n / 30, 12);
      expect(frame.duration).toBeCloseTo(1 / 30, 12);
    });
    expect(recorded.sinkTimestamps.map((t) => t.length)).toEqual([30, 30]);
    const fxArg = render.drawEditorFrame.mock.calls[45][9];
    expect(fxArg.o).toBeCloseTo(1.5, 12);
    expect(fxArg.session).toEqual({ base: "clean", faceTrack: null, matte: false });
    expect(render.drawEditorFrame.mock.calls[45][8]).toBe("all");

    // Audio: 48 kHz planar stereo in 1 s chunks covering the 2 s edit.
    expect(recorded.audio.map((a) => [a.timestamp, a.format, a.channels, a.rate])).toEqual([
      [0, "f32-planar", 2, SR], [1, "f32-planar", 2, SR],
    ]);
    const left = new Float32Array(2 * SR);
    recorded.audio.forEach((a) => left.set(a.data.subarray(0, a.data.length / 2), a.timestamp * SR));
    const rms = (from: number, to: number) => {
      let sum = 0;
      for (let i = Math.round(from * SR); i < Math.round(to * SR); i++) sum += left[i] * left[i];
      return Math.sqrt(sum / (Math.round(to * SR) - Math.round(from * SR)));
    };
    expect(rms(0, 0.19)).toBeCloseTo(0.25, 5);                                // the voice
    expect(rms(0.205, 0.395)).toBeCloseTo(dbToGain(-12) / Math.SQRT2, 3);     // bleep: tone only
    expect(rms(1.1, 1.45)).toBeCloseTo(0.25, 5);
    const sfx = 0.4 * dbToGain(SFX_BUS_GAIN_DB.bold);                           // ±0.4 SFX at the bold bus gain
    expect(rms(1.5, 2)).toBeCloseTo(Math.sqrt(((0.25 + sfx) ** 2 + (0.25 - sfx) ** 2) / 2), 4);   // voice + SFX
    expect(download.downloadBlob).toHaveBeenCalledTimes(1);
    expect(progress).toHaveBeenLastCalledWith(100);
  });

  it("still draws (black picture) the frames before the source's first decodable frame", async () => {
    recorded.missingBefore = 0.05;
    const doc = withFx({ cues: [cue.zoom("z")] }, { ...draft, segments: [{ id: "a", start: 0, end: 1 }] });
    await exportDraft("/x/media/clean.mp4", doc, asset, "tiktok", new AbortController().signal, () => {});
    expect(recorded.video).toHaveLength(30);
    expect(recorded.video[0].at).toBe(0);
    const first = render.drawEditorFrame.mock.calls[0];
    expect([first[2], first[3]]).toEqual([asset.width, asset.height]);
    const draw = first[5] as (rect: unknown, target: unknown) => void;
    expect(() => draw({ x: 0, y: 0, width: 1, height: 1 }, {})).not.toThrow();
  });

  it("keeps the legacy path for drafts without dynamic edits", async () => {
    await exportDraft("/x/media/clean.mp4", draft, asset, "tiktok", new AbortController().signal, () => {});
    expect(render.loadEditorFxResources).not.toHaveBeenCalled();
    expect(recorded.sinkTimestamps).toHaveLength(0);
    expect(recorded.video.length).toBeGreaterThanOrEqual(89);
    expect(recorded.audio.every((a) => a.format === "f32")).toBe(true);
  });
});
