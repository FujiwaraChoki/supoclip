// Browser client for dynamic-edit assets (backend `GET /fx/assets`, proxied at `/api/fx/assets`).
//
// The manifest lists the built-in SFX, music beds and emoji atlas plus the API machine's drop-ins (`custom: true`).
// `sfxAsset` / `bedAsset` mirror `edit_fx/assets.py` `mix_sfx_file` / `mix_bed_file` exactly, so the studio preview and
// the browser export hear the same file and normalisation gain as the server mix. Asset URLs are content-addressed and
// immutable, so fetched bytes are cached for the page's lifetime; decoded AudioBuffers are cached per AudioContext.
import { loadEmojiAtlas } from "./browser-host";
import type { EmojiAtlas } from "./core/host";
import type { Mood, SoundId } from "./core/types";

export interface FxSfxAsset {
  id: string;
  sound: SoundId;
  variant: number;
  url: string;
  duration: number;
  gain_db: number;
  custom?: boolean;
}

export interface FxMusicAsset {
  id: string;
  mood: Mood;
  url: string;
  duration: number;
  loop_seconds: number;
  /** Built-ins: seconds of padding before the loop (the file holds a little of the loop on each side of it). */
  loop_start?: number;
  /** Present on drop-ins (normalisation to the built-ins' level); built-ins are 0 dB. */
  gain_db?: number;
  custom?: boolean;
}

export interface FxAssetManifest {
  version: number;
  sfx: FxSfxAsset[];
  music: FxMusicAsset[];
  emoji: { atlas: string; map: string; cell: number };
}

/** One file to play at mix time: its same-origin URL and the gain (dB) that normalises it. */
export interface FxMixAsset {
  id: string;
  url: string;
  gainDb: number;
  duration: number;
  /** Loop length for music beds (== duration for drop-ins); undefined for SFX. */
  loopSeconds?: number;
  /** Where the loop starts in the file (built-in beds); 0 for drop-ins. */
  loopStart?: number;
  custom: boolean;
}

export const FX_ASSETS_ENDPOINT = "/api/fx/assets";
const BACKEND_PREFIX = "/fx/assets/";

type Fetcher = (input: string, init?: RequestInit) => Promise<Response>;

/** `/fx/assets/<sha>.<ext>` (backend) → `/api/fx/assets/<sha>.<ext>` (same-origin proxy). Other URLs are kept. */
export function proxiedAssetUrl(url: string): string {
  return url.startsWith(BACKEND_PREFIX) ? `/api${url}` : url;
}

function index(seed: number, length: number): number {
  const s = Math.trunc(Number.isFinite(seed) ? seed : 0);
  return ((s % length) + length) % length;
}

function finite(value: unknown, fallback = 0): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function isManifest(value: unknown): value is FxAssetManifest {
  if (!value || typeof value !== "object") return false;
  const m = value as Partial<FxAssetManifest>;
  return Array.isArray(m.sfx) && Array.isArray(m.music) && !!m.emoji && typeof m.emoji.atlas === "string"
    && typeof m.emoji.map === "string";
}

let manifestPromise: Promise<FxAssetManifest> | null = null;

/**
 * The asset manifest, fetched once per page. A failed fetch is not cached, so the next caller retries.
 * `refresh` re-fetches (e.g. after a self-hoster adds drop-ins).
 */
export function loadFxManifest(options: { refresh?: boolean; fetcher?: Fetcher } = {}): Promise<FxAssetManifest> {
  if (!manifestPromise || options.refresh) {
    const fetcher = options.fetcher ?? ((input, init) => fetch(input, init));
    const pending = (async () => {
      const response = await fetcher(FX_ASSETS_ENDPOINT, { credentials: "same-origin" });
      if (!response.ok) throw new Error(`fx assets manifest: ${response.status}`);
      const body: unknown = await response.json();
      if (!isManifest(body)) throw new Error("fx assets manifest: unexpected shape");
      return body;
    })();
    manifestPromise = pending;
    pending.catch(() => {
      if (manifestPromise === pending) manifestPromise = null;
    });
  }
  return manifestPromise;
}

/** Sounds whose drop-ins are picked by length (`assets.LENGTH_MATCHED_SOUNDS`): a 2.5 s riser stays 2.5 s. */
const LENGTH_MATCHED_SOUNDS: ReadonlySet<SoundId> = new Set<SoundId>(["riser"]);
const LENGTH_MATCH_SLACK = 0.25;

/** Mirrors `assets.length_matched`: the entries closest in length to `target` (within the slack), in order. */
export function lengthMatched<T extends { duration: number }>(entries: readonly T[], target: number): T[] {
  if (!entries.length) return [];
  const gaps = entries.map((e) => Math.abs(finite(e.duration) - target));
  const limit = Math.min(...gaps) + LENGTH_MATCH_SLACK + 1e-6;
  return entries.filter((_e, i) => gaps[i] <= limit);
}

/**
 * Mirrors `mix_sfx_file(sound, variant, seed)`: drop-ins for the sound replace the built-ins (`custom[seed % len]`, in
 * the manifest's mix order; risers only among the drop-ins closest in length to the built-in variant); otherwise the
 * built-in variant, falling back to variant 0. Null for an unknown sound.
 */
export function sfxAsset(manifest: FxAssetManifest, sound: SoundId, variant: number, seed: number): FxMixAsset | null {
  const builtins = manifest.sfx.filter((e) => !e.custom && e.sound === sound).sort((a, b) => a.variant - b.variant);
  const builtin = builtins.length
    ? (Number.isInteger(variant) && variant >= 0 && variant < builtins.length ? builtins[variant] : builtins[0])
    : null;
  let custom = manifest.sfx.filter((e) => e.custom && e.sound === sound);
  if (custom.length) {
    if (builtin && LENGTH_MATCHED_SOUNDS.has(sound)) custom = lengthMatched(custom, finite(builtin.duration));
    const chosen = custom[index(seed, custom.length)];
    return { id: chosen.id, url: proxiedAssetUrl(chosen.url), gainDb: finite(chosen.gain_db), duration: finite(chosen.duration), custom: true };
  }
  if (!builtin) return null;
  return { id: builtin.id, url: proxiedAssetUrl(builtin.url), gainDb: finite(builtin.gain_db), duration: finite(builtin.duration), custom: false };
}

function bed(entry: FxMusicAsset): FxMixAsset {
  return {
    id: entry.id,
    url: proxiedAssetUrl(entry.url),
    gainDb: entry.custom ? finite(entry.gain_db) : 0,
    duration: finite(entry.duration),
    loopSeconds: finite(entry.loop_seconds, finite(entry.duration)),
    loopStart: entry.custom ? 0 : finite(entry.loop_start),
    custom: !!entry.custom,
  };
}

/**
 * Mirrors `mix_bed_file(bed_id, mood, seed)`: an explicit drop-in id → it; drop-ins of the mood → `custom[seed % len]`;
 * a built-in id → it; anything else → the mood's built-in (then any built-in). Null only for an empty manifest.
 */
export function bedAsset(manifest: FxAssetManifest, bedId: string, mood: Mood, seed: number): FxMixAsset | null {
  const explicit = manifest.music.find((e) => e.custom && e.id === bedId);
  if (explicit) return bed(explicit);
  const custom = manifest.music.filter((e) => e.custom && e.mood === mood);
  if (custom.length) return bed(custom[index(seed, custom.length)]);
  const builtins = manifest.music.filter((e) => !e.custom);
  const chosen = builtins.find((e) => e.id === bedId) ?? builtins.find((e) => e.mood === mood) ?? builtins[0];
  return chosen ? bed(chosen) : null;
}

let atlasPromise: Promise<EmojiAtlas> | null = null;

/** The emoji atlas as an EmojiAtlas for the browser host, loaded once per page (retried after a failure). */
export function loadFxAtlas(manifest?: FxAssetManifest): Promise<EmojiAtlas> {
  if (!atlasPromise) {
    const pending = (async () => {
      const m = manifest ?? await loadFxManifest();
      return loadEmojiAtlas(proxiedAssetUrl(m.emoji.atlas), proxiedAssetUrl(m.emoji.map));
    })();
    atlasPromise = pending;
    pending.catch(() => {
      if (atlasPromise === pending) atlasPromise = null;
    });
  }
  return atlasPromise;
}

const bytes = new Map<string, Promise<ArrayBuffer>>();
const decoded = new WeakMap<BaseAudioContext, Map<string, Promise<AudioBuffer>>>();

/** The raw bytes of one asset (cached: URLs are immutable). Failures are not cached. */
export function fetchFxAsset(url: string, fetcher: Fetcher = (input, init) => fetch(input, init)): Promise<ArrayBuffer> {
  const target = proxiedAssetUrl(url);
  let pending = bytes.get(target);
  if (!pending) {
    const request = (async () => {
      const response = await fetcher(target, { credentials: "same-origin" });
      if (!response.ok) throw new Error(`fx asset ${target}: ${response.status}`);
      return response.arrayBuffer();
    })();
    pending = request;
    bytes.set(target, request);
    request.catch(() => {
      if (bytes.get(target) === request) bytes.delete(target);
    });
  }
  return pending;
}

/**
 * One asset decoded for `context` (AudioContext for the preview, OfflineAudioContext for export). Cached per context
 * and URL; the shared bytes are copied first because decodeAudioData detaches its input.
 */
export function decodeFxAudio(context: BaseAudioContext, url: string, fetcher?: Fetcher): Promise<AudioBuffer> {
  const target = proxiedAssetUrl(url);
  let perContext = decoded.get(context);
  if (!perContext) {
    perContext = new Map();
    decoded.set(context, perContext);
  }
  const cache = perContext;
  let pending = cache.get(target);
  if (!pending) {
    const request = fetchFxAsset(target, fetcher).then((data) => context.decodeAudioData(data.slice(0)));
    pending = request;
    cache.set(target, request);
    request.catch(() => {
      if (cache.get(target) === request) cache.delete(target);
    });
  }
  return pending;
}

/** Drops every cached manifest, atlas and byte buffer (tests; a session that must see new drop-ins). */
export function resetFxAssetsCache(): void {
  manifestPromise = null;
  atlasPromise = null;
  bytes.clear();
}
