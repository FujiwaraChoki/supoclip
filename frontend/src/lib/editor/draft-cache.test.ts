import { afterEach, describe, expect, it, vi } from "vitest";
import { draft } from "./document.test-fixture";
import {
  CACHED_HISTORY_STEPS,
  SAVED_ENTRIES_KEPT,
  compactDrafts,
  pruneDrafts,
  draftCacheKey,
  readDraft,
  removeTaskDrafts,
  serializeDraft,
  summarizeDrafts,
  writeDraft,
} from "./draft-cache";

/** localStorage with a quota counted in characters, like browsers do. */
class QuotaStorage implements Storage {
  data = new Map<string, string>();
  constructor(private quota = Infinity) {}
  get length() { return this.data.size; }
  key(index: number) { return [...this.data.keys()][index] ?? null; }
  getItem(key: string) { return this.data.get(key) ?? null; }
  removeItem(key: string) { this.data.delete(key); }
  clear() { this.data.clear(); }
  setItem(key: string, value: string) {
    const used = [...this.data].reduce((sum, [k, v]) => sum + (k === key ? 0 : k.length + v.length), 0);
    if (used + key.length + value.length > this.quota) throw new DOMException("full", "QuotaExceededError");
    this.data.set(key, value);
  }
}

function install(store: Storage) {
  vi.stubGlobal("localStorage", store);
  return store;
}

const edited = (volume: number) => ({ ...draft, volume });
const longHistory = { past: Array.from({ length: 50 }, (_, i) => edited(i / 100)), present: edited(0.9), future: [] };

/** An entry as releases before this one wrote it: the full history, forever. */
function legacy(unsaved: boolean) {
  return JSON.stringify({
    basis: "clip.mp4",
    revision: 3,
    saved: JSON.stringify(unsaved ? draft : longHistory.present),
    history: longHistory,
  });
}

afterEach(() => vi.unstubAllGlobals());

describe("draft cache", () => {
  it("reads entries written by earlier releases", () => {
    const store = install(new QuotaStorage());
    store.setItem(draftCacheKey("/api/tasks/t/clips/a/editor"), legacy(true));
    store.setItem(draftCacheKey("/api/tasks/t/clips/b/editor"), legacy(false));
    expect(readDraft(draftCacheKey("/api/tasks/t/clips/a/editor"))).toMatchObject({ revision: 3, unsaved: true });
    expect(readDraft(draftCacheKey("/api/tasks/t/clips/b/editor"))?.unsaved).toBe(false);
    expect(readDraft(draftCacheKey("/api/tasks/t/clips/missing/editor"))).toBeNull();
  });

  it("keeps a short history and drops undo steps before the draft itself", () => {
    const stored = JSON.parse(serializeDraft("clip.mp4", 1, longHistory));
    expect(stored.history.past).toHaveLength(CACHED_HISTORY_STEPS);
    expect(stored.history.present.volume).toBe(0.9);
    const bare = JSON.parse(serializeDraft("clip.mp4", 1, longHistory, { steps: 0 }));
    expect(bare.history.past).toEqual([]);
  });

  it("frees copies the server has and shrinks old unsaved ones, without losing changes", () => {
    const store = install(new QuotaStorage());
    const saved = draftCacheKey("/api/tasks/t/clips/saved/editor");
    const unsaved = draftCacheKey("/api/tasks/t/clips/unsaved/editor");
    store.setItem(saved, legacy(false));
    store.setItem(unsaved, legacy(true));
    store.setItem("supoclip-editor:broken", "{not json");
    store.setItem("unrelated", "kept");
    const before = summarizeDrafts();
    expect(before).toMatchObject({ entries: 3, unsaved: 2 });

    expect(compactDrafts()).toBeGreaterThan(0);

    expect(store.getItem(saved)).toBeNull();
    const kept = readDraft(unsaved);
    expect(kept?.unsaved).toBe(true);
    expect(kept?.history.present.volume).toBe(0.9);
    expect(kept?.history.past.length).toBe(CACHED_HISTORY_STEPS);
    expect(store.getItem("supoclip-editor:broken")).toBe("{not json");
    expect(store.getItem("unrelated")).toBe("kept");
    expect(summarizeDrafts().bytes).toBeLessThan(before.bytes);
  });

  it("makes room on a full quota by removing saved copies, never other unsaved work", () => {
    const filler = legacy(false);
    const otherUnsaved = serializeDraft("clip.mp4", 2, { past: [], present: edited(0.3), future: [] });
    const store = install(new QuotaStorage(filler.length + otherUnsaved.length + 200));
    const savedKey = draftCacheKey("/api/tasks/t/clips/saved/editor");
    const otherKey = draftCacheKey("/api/tasks/t/clips/other/editor");
    store.setItem(savedKey, filler);
    store.setItem(otherKey, otherUnsaved);
    const key = draftCacheKey("/api/tasks/t/clips/current/editor");

    expect(writeDraft(key, "clip.mp4", 4, { past: [draft], present: edited(0.7), future: [] })).toBe(true);

    expect(store.getItem(savedKey)).toBeNull();
    expect(store.getItem(otherKey)).toBe(otherUnsaved);
    expect(readDraft(key)?.history.present.volume).toBe(0.7);
  });

  it("reports failure when even the bare draft cannot be stored", () => {
    const otherUnsaved = serializeDraft("clip.mp4", 2, { past: [], present: edited(0.3), future: [] });
    const store = install(new QuotaStorage(otherUnsaved.length + 60));
    const otherKey = draftCacheKey("/api/tasks/t/clips/other/editor");
    store.setItem(otherKey, otherUnsaved);
    expect(writeDraft(draftCacheKey("/api/tasks/t/clips/x/editor"), "clip.mp4", 1, longHistory)).toBe(false);
    expect(store.getItem(otherKey)).toBe(otherUnsaved);
  });

  it("keeps undo history for the last few saved clips and every unsaved one", () => {
    const store = install(new QuotaStorage());
    const savedKeys = Array.from({ length: 5 }, (_, i) => draftCacheKey(`/api/tasks/t/clips/saved${i}/editor`));
    savedKeys.forEach((key, i) =>
      store.setItem(key, serializeDraft("clip.mp4", 1, longHistory, { unsaved: false, at: 1000 + i })),
    );
    const unsavedKey = draftCacheKey("/api/tasks/t/clips/unsaved/editor");
    store.setItem(unsavedKey, serializeDraft("clip.mp4", 1, longHistory, { at: 1 }));
    const legacyKey = draftCacheKey("/api/tasks/t/clips/legacy/editor");
    store.setItem(legacyKey, legacy(true));

    pruneDrafts();

    const left = savedKeys.filter((key) => store.getItem(key) !== null);
    expect(left).toEqual(savedKeys.slice(-SAVED_ENTRIES_KEPT));
    expect(readDraft(unsavedKey)?.unsaved).toBe(true);
    const shrunk = readDraft(legacyKey);
    expect(shrunk).toMatchObject({ unsaved: true, legacy: false });
    expect(shrunk?.history.past).toHaveLength(CACHED_HISTORY_STEPS);
  });

  it("removes a deleted task's or clip's copies only", () => {
    const store = install(new QuotaStorage());
    for (const key of ["/api/tasks/t1/clips/a/editor", "/api/tasks/t1/clips/b/editor", "/api/tasks/t2/clips/a/editor"])
      store.setItem(draftCacheKey(key), legacy(true));
    removeTaskDrafts("t1", "a");
    expect(store.getItem(draftCacheKey("/api/tasks/t1/clips/a/editor"))).toBeNull();
    expect(store.getItem(draftCacheKey("/api/tasks/t1/clips/b/editor"))).not.toBeNull();
    removeTaskDrafts("t1");
    expect(store.getItem(draftCacheKey("/api/tasks/t1/clips/b/editor"))).toBeNull();
    expect(store.getItem(draftCacheKey("/api/tasks/t2/clips/a/editor"))).not.toBeNull();
  });
});
