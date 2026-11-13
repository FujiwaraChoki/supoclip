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
    { id: "hype", mood: "hype", url: url(7, "m4a"), duration: 61.9, loop_seconds: 61.9355 },
    { id: "chill", mood: "chill", url: url(8, "m4a"), duration: 93.6, loop_seconds: 93.6585 },
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
});

describe("bedAsset mirrors mix_bed_file", () => {
  it("prefers an explicit drop-in id", () => {
    expect(bedAsset(manifest, "chill-yours", "hype", 0)).toMatchObject({ id: "chill-yours", gainDb: -1, custom: true, loopSeconds: 30 });
  });

  it("uses the mood's drop-ins by seed before the built-in", () => {
    expect(bedAsset(manifest, "chill", "chill", 0)?.id).toBe("chill-mine");
    expect(bedAsset(manifest, "chill", "chill", 3)?.id).toBe("chill-yours");
  });

  it("falls back to the built-in id, then the mood's built-in, then any built-in", () => {
    expect(bedAsset(manifest, "hype", "hype", 0)).toMatchObject({ id: "hype", gainDb: 0, loopSeconds: 61.9355, custom: false });
    expect(bedAsset(manifest, "gone-bed", "hype", 0)?.id).toBe("hype");
    expect(bedAsset(manifest, "gone-bed", "dark", 0)?.id).toBe("hype");
    expect(bedAsset({ ...manifest, music: [] }, "hype", "hype", 0)).toBeNull();
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
