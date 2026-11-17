import {
  bedAsset, decodeFxAudio, fetchFxAsset, loadFxManifest, proxiedAssetUrl, resetFxAssetsCache, sfxAsset,
  type FxAssetManifest,
} from "./assets-client";

const url = (n: number, ext = "wav") => `/fx/assets/${n.toString(16).padStart(64, "0")}.${ext}`;

const manifest: FxAssetManifest = {
  version: 1,
  sfx: [
    { id: "whoosh_1", sound: "whoosh", variant: 1, url: url(2), duration: 0.5, gain_db: -1 },
    { id: "whoosh_0", sound: "whoosh", variant: 0, url: url(1), duration: 0.4, gain_db: 0 },
    { id: "whoosh_2", sound: "whoosh", variant: 2, url: url(3), duration: 0.6, gain_db: 0 },
    { id: "pop_0", sound: "pop", variant: 0, url: url(4), duration: 0.06, gain_db: 0 },
    { id: "pop-a", sound: "pop", variant: 0, url: url(5), duration: 0.1, gain_db: 3.5, custom: true },
    { id: "pop-b", sound: "pop", variant: 1, url: url(6), duration: 0.2, gain_db: -2, custom: true },
  ],
  music: [
    { id: "hype-tent", mood: "hype", url: url(7, "mp3"), duration: 107.72, loop_start: 0.1, loop_seconds: 107.52 },
    { id: "hype-tribal-bass", mood: "hype", url: url(13, "mp3"), duration: 44.5059, loop_start: 0.1, loop_seconds: 44.305896 },
    { id: "chill-unwind", mood: "chill", url: url(8, "mp3"), duration: 81.0421, loop_start: 0.1, loop_seconds: 80.842104 },
    { id: "chill-mine", mood: "chill", url: url(9, "mp3"), duration: 40, loop_seconds: 40, gain_db: 4, custom: true },
    { id: "chill-yours", mood: "chill", url: url(10, "mp3"), duration: 30, loop_seconds: 30, gain_db: -1, custom: true },
  ],
  emoji: { atlas: url(11, "png"), map: url(12, "json"), cell: 160 },
};

beforeEach(() => resetFxAssetsCache());

it("maps backend asset URLs onto the same-origin proxy", () => {
  expect(proxiedAssetUrl(url(1))).toBe(`/api${url(1)}`);
  expect(proxiedAssetUrl("/api/fx/assets/x.wav")).toBe("/api/fx/assets/x.wav");
});

describe("sfxAsset mirrors mix_sfx_file", () => {
  it("uses the built-in variant, falling back to variant 0", () => {
    expect(sfxAsset(manifest, "whoosh", 1, 99)).toMatchObject({ id: "whoosh_1", gainDb: -1, custom: false, url: `/api${url(2)}` });
    expect(sfxAsset(manifest, "whoosh", 2, 0)?.id).toBe("whoosh_2");
    expect(sfxAsset(manifest, "whoosh", 7, 0)?.id).toBe("whoosh_0");
    expect(sfxAsset(manifest, "whoosh", -1, 0)?.id).toBe("whoosh_0");
  });

  it("replaces the built-ins with drop-ins picked by seed % len", () => {
    expect(sfxAsset(manifest, "pop", 0, 0)).toMatchObject({ id: "pop-a", gainDb: 3.5, custom: true });
    expect(sfxAsset(manifest, "pop", 0, 1)?.id).toBe("pop-b");
    expect(sfxAsset(manifest, "pop", 0, 4294967295)?.id).toBe("pop-b"); // 2^32-1 is odd
  });

  it("returns null for a sound the manifest lacks", () => {
    expect(sfxAsset(manifest, "boom", 0, 0)).toBeNull();
  });

  it("picks riser drop-ins by the built-in variant's length", () => {
    const risers: FxAssetManifest = {
      ...manifest,
      sfx: [
        { id: "riser-a", sound: "riser", variant: 0, url: url(20), duration: 1.5, gain_db: 0 },
        { id: "riser-b", sound: "riser", variant: 1, url: url(21), duration: 2.5, gain_db: 0 },
        { id: "riser-1s5-x", sound: "riser", variant: 0, url: url(22), duration: 1.5, gain_db: 0, custom: true },
        { id: "riser-1s6-y", sound: "riser", variant: 1, url: url(23), duration: 1.62, gain_db: 0, custom: true },
        { id: "riser-2s5-z", sound: "riser", variant: 2, url: url(24), duration: 2.5, gain_db: 0, custom: true },
        { id: "riser-3s-w", sound: "riser", variant: 3, url: url(25), duration: 3.0, gain_db: 0, custom: true },
      ],
    };
    // variant 0 (1.5 s): the 1.5 and 1.62 s drop-ins, by seed
    expect([0, 1, 2, 3].map((seed) => sfxAsset(risers, "riser", 0, seed)?.id))
      .toEqual(["riser-1s5-x", "riser-1s6-y", "riser-1s5-x", "riser-1s6-y"]);
    // variant 1 (2.5 s): only the 2.5 s drop-in (3.0 s is 0.5 s farther than the closest)
    expect([0, 1, 7].map((seed) => sfxAsset(risers, "riser", 1, seed)?.id)).toEqual(["riser-2s5-z", "riser-2s5-z", "riser-2s5-z"]);
    // an out-of-range variant uses variant 0's length
    expect(sfxAsset(risers, "riser", 9, 1)?.id).toBe("riser-1s6-y");
  });
});

describe("bedAsset mirrors mix_bed_file", () => {
  it("prefers an explicit drop-in id", () => {
    expect(bedAsset(manifest, "chill-yours", "hype", 0)).toMatchObject({ id: "chill-yours", gainDb: -1, custom: true, loopSeconds: 30 });
  });

  it("uses the mood's drop-ins by seed before the built-in", () => {
    expect(bedAsset(manifest, "chill-unwind", "chill", 0)?.id).toBe("chill-mine");
    expect(bedAsset(manifest, "chill-unwind", "chill", 3)?.id).toBe("chill-yours");
  });

  it("falls back to the built-in id, then the mood's first built-in, then any built-in", () => {
    expect(bedAsset(manifest, "hype-tribal-bass", "hype", 0)).toMatchObject({
      id: "hype-tribal-bass", gainDb: 0, loopStart: 0.1, loopSeconds: 44.305896, custom: false,
    });
    expect(bedAsset(manifest, "gone-bed", "hype", 0)?.id).toBe("hype-tent");
    expect(bedAsset(manifest, "gone-bed", "dark", 0)?.id).toBe("hype-tent");
    expect(bedAsset({ ...manifest, music: [] }, "hype", "hype", 0)).toBeNull();
  });

  it("plays a retired one-per-mood bed id as the first built-in of its mood, whatever the seed", () => {
    // documents saved before the curated beds name "hype", "chill", ...; aggressive ones borrowed "hype"
    for (const seed of [0, 1, 7]) expect(bedAsset(manifest, "hype", "hype", seed)?.id).toBe("hype-tent");
    const noDropIns = { ...manifest, music: manifest.music.filter((e) => !e.custom) };
    expect(bedAsset(noDropIns, "chill", "chill", 5)).toMatchObject({ id: "chill-unwind", loopStart: 0.1 });
  });

  it("gives built-ins their loop start and drop-ins none", () => {
    expect(bedAsset(manifest, "chill-mine", "chill", 0)).toMatchObject({ loopStart: 0, loopSeconds: 40, custom: true });
    const old = { ...manifest, music: [{ id: "hype-old", mood: "hype" as const, url: url(14, "mp3"), duration: 30, loop_seconds: 30 }] };
    expect(bedAsset(old, "hype-old", "hype", 0)?.loopStart).toBe(0);   // a manifest without loop_start
  });
});

describe("loadFxManifest", () => {
  it("fetches once and shares the result", async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify(manifest)));
    const [a, b] = await Promise.all([loadFxManifest({ fetcher }), loadFxManifest({ fetcher })]);
    expect(a).toEqual(manifest);
    expect(b).toBe(a);
    expect(fetcher).toHaveBeenCalledOnce();
    expect(fetcher).toHaveBeenCalledWith("/api/fx/assets", { credentials: "same-origin" });
  });

  it("does not cache a failure", async () => {
    const fetcher = vi.fn()
      .mockResolvedValueOnce(new Response("nope", { status: 503 }))
      .mockResolvedValueOnce(new Response(JSON.stringify(manifest)));
    await expect(loadFxManifest({ fetcher })).rejects.toThrow("503");
    await expect(loadFxManifest({ fetcher })).resolves.toEqual(manifest);
  });

  it("rejects an unexpected body", async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: "x" })));
    await expect(loadFxManifest({ fetcher })).rejects.toThrow("unexpected shape");
  });
});

describe("asset bytes and decoding", () => {
  it("fetches each immutable asset once through the proxy", async () => {
    const fetcher = vi.fn().mockImplementation(async () => new Response(new Uint8Array([1, 2, 3])));
    const [a, b] = await Promise.all([fetchFxAsset(url(1), fetcher), fetchFxAsset(`/api${url(1)}`, fetcher)]);
    expect(a).toBe(b);
    expect(fetcher).toHaveBeenCalledOnce();
    expect(fetcher).toHaveBeenCalledWith(`/api${url(1)}`, { credentials: "same-origin" });
  });

  it("decodes once per context from a copy of the bytes", async () => {
    const fetcher = vi.fn().mockImplementation(async () => new Response(new Uint8Array([1, 2, 3, 4])));
    const decodeAudioData = vi.fn(async (data: ArrayBuffer) => ({ length: data.byteLength }) as unknown as AudioBuffer);
    const context = { decodeAudioData } as unknown as BaseAudioContext;
    const other = { decodeAudioData } as unknown as BaseAudioContext;
    const first = await decodeFxAudio(context, url(4), fetcher);
    expect(await decodeFxAudio(context, url(4), fetcher)).toBe(first);
    await decodeFxAudio(other, url(4), fetcher);
    expect(decodeAudioData).toHaveBeenCalledTimes(2);
    expect(fetcher).toHaveBeenCalledOnce();
    const shared = await fetchFxAsset(url(4), fetcher);
    expect(decodeAudioData.mock.calls[0][0]).not.toBe(shared);
    expect(shared.byteLength).toBe(4);
  });
});
