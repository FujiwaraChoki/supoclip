/**
 * The clip studio's browser copy of drafts.
 *
 * Drafts are saved to the server. The browser copy covers changes the server
 * has not confirmed yet (a closed tab, a lost connection) and keeps a short
 * undo history across reloads. Earlier releases kept every opened clip's
 * full 50-step history forever, which filled the browser's storage for
 * SupoClip. Now each copy is bounded, and copies the server already has are
 * kept only for the few most recently edited clips. A copy with unsaved
 * changes is never removed to make room.
 */
import type { EditDocument } from "./document";

export const DRAFT_CACHE_PREFIX = "supoclip-editor:";
/** Undo steps kept in the browser; the in-memory history is longer. */
export const CACHED_HISTORY_STEPS = 10;
/** A copy larger than this keeps fewer undo steps (characters of JSON). */
export const CACHED_ENTRY_BUDGET = 250_000;
/** Saved clips whose undo history survives a reload. */
export const SAVED_ENTRIES_KEPT = 3;

export interface CachedHistory {
  past: EditDocument[];
  present: EditDocument;
  future: EditDocument[];
}

/** A cached draft as the editor reads it, whichever release wrote it. */
export interface CachedDraft {
  basis: string;
  revision: number;
  history: CachedHistory;
  /** The server has not confirmed `history.present`. */
  unsaved: boolean;
  /** Last write, for keeping the most recent saved copies. 0 for old entries. */
  at: number;
  /** Written by a release that kept the full history. */
  legacy: boolean;
}

interface StoredV2 {
  v: 2;
  basis: string;
  revision: number;
  unsaved: boolean;
  at: number;
  history: CachedHistory;
}

interface StoredLegacy {
  basis?: string;
  revision?: number;
  saved?: string;
  history?: CachedHistory;
}

export const draftCacheKey = (endpoint: string) => `${DRAFT_CACHE_PREFIX}${endpoint}`;

function storage(): Storage | null {
  try {
    return typeof window === "undefined" ? null : window.localStorage;
  } catch {
    return null;
  }
}

function keys(store: Storage): string[] {
  const found: string[] = [];
  for (let index = 0; index < (store.length ?? 0); index++) {
    const key = store.key(index);
    if (key?.startsWith(DRAFT_CACHE_PREFIX)) found.push(key);
  }
  return found;
}

function parse(raw: string | null): CachedDraft | null {
  if (!raw) return null;
  try {
    const value = JSON.parse(raw) as StoredV2 | StoredLegacy;
    if ("v" in value && value.v === 2) {
      if (!value.history?.present) return null;
      return {
        basis: value.basis, revision: value.revision, history: value.history,
        unsaved: value.unsaved !== false, at: value.at ?? 0, legacy: false,
      };
    }
    const legacy = value as StoredLegacy;
    if (!legacy.history?.present || typeof legacy.basis !== "string") return null;
    return {
      basis: legacy.basis,
      revision: legacy.revision ?? 0,
      history: legacy.history,
      unsaved: JSON.stringify(legacy.history.present) !== legacy.saved,
      at: 0,
      legacy: true,
    };
  } catch {
    return null;
  }
}

export function readDraft(key: string): CachedDraft | null {
  const store = storage();
  if (!store) return null;
  try {
    return parse(store.getItem(key));
  } catch {
    return null;
  }
}

/** The smallest form of the draft within the budget: fewer undo steps first. */
export function serializeDraft(
  basis: string, revision: number, history: CachedHistory,
  { unsaved = true, at = Date.now(), steps = CACHED_HISTORY_STEPS }: { unsaved?: boolean; at?: number; steps?: number } = {},
): string {
  for (let keep = steps; ; keep = Math.floor(keep / 2)) {
    const payload: StoredV2 = {
      v: 2, basis, revision, unsaved, at,
      history: { past: keep ? history.past.slice(-keep) : [], present: history.present, future: history.future.slice(0, keep) },
    };
    const text = JSON.stringify(payload);
    if (text.length <= CACHED_ENTRY_BUDGET || keep === 0) return text;
  }
}

export function removeDraft(key: string) {
  try {
    storage()?.removeItem(key);
  } catch {
    /* Nothing to free when storage is unavailable. */
  }
}

/**
 * Store a clip's draft and short history. Returns false only when even the
 * bare draft cannot be stored. On a full quota it first removes copies the
 * server already has (never another clip's unsaved changes), then retries
 * without undo history.
 */
export function writeDraft(key: string, basis: string, revision: number, history: CachedHistory, unsaved = true): boolean {
  const store = storage();
  if (!store) return false;
  const attempt = (text: string) => {
    try {
      store.setItem(key, text);
      return true;
    } catch {
      return false;
    }
  };
  let existed = false;
  try {
    existed = store.getItem(key) !== null;
  } catch {
    /* Treat as new. */
  }
  let stored = attempt(serializeDraft(basis, revision, history, { unsaved }));
  if (!stored) {
    compactDrafts({ keep: key });
    stored = attempt(serializeDraft(basis, revision, history, { unsaved }))
      || attempt(serializeDraft(basis, revision, history, { unsaved, steps: 0 }));
  }
  // A clip's first copy may push an older saved one out; later writes only replace it.
  if (stored && !existed) pruneDrafts({ keep: key });
  return stored;
}

function entries(store: Storage) {
  const found: { key: string; raw: string; draft: CachedDraft | null }[] = [];
  for (const key of keys(store)) {
    const raw = store.getItem(key) ?? "";
    found.push({ key, raw, draft: parse(raw) });
  }
  return found;
}

/**
 * Keep the browser copy small without losing work: shrink copies written by
 * earlier releases, and keep saved copies only for the most recently edited
 * clips. Runs whenever the studio opens and after every write.
 */
export function pruneDrafts({ keep }: { keep?: string } = {}) {
  const store = storage();
  if (!store) return;
  try {
    const found = entries(store);
    for (const entry of found) {
      if (!entry.draft?.legacy || entry.key === keep) continue;
      const compact = serializeDraft(entry.draft.basis, entry.draft.revision, entry.draft.history, {
        unsaved: entry.draft.unsaved, at: 0,
      });
      try {
        store.setItem(entry.key, compact);
      } catch {
        /* The larger copy stays; it is still complete. */
      }
    }
    const saved = found
      .filter((entry) => entry.draft && !entry.draft.unsaved && entry.key !== keep)
      .sort((a, b) => b.draft!.at - a.draft!.at);
    const room = SAVED_ENTRIES_KEPT - (keep && readDraft(keep)?.unsaved === false ? 1 : 0);
    for (const entry of saved.slice(Math.max(0, room))) store.removeItem(entry.key);
  } catch {
    /* Storage became unavailable; keep what is there. */
  }
}

export interface DraftCacheSummary {
  /** Clips with a browser copy. */
  entries: number;
  /** Of those, clips with changes the server has not confirmed. */
  unsaved: number;
  /** Approximate bytes used (localStorage stores UTF-16). */
  bytes: number;
}

export function summarizeDrafts(): DraftCacheSummary {
  const store = storage();
  const summary = { entries: 0, unsaved: 0, bytes: 0 };
  if (!store) return summary;
  try {
    for (const entry of entries(store)) {
      summary.entries += 1;
      summary.bytes += (entry.key.length + entry.raw.length) * 2;
      if (entry.draft?.unsaved !== false) summary.unsaved += 1;
    }
  } catch {
    /* Report what was read. */
  }
  return summary;
}

/**
 * Free as much as possible without losing work: remove every copy the
 * server already has (their undo history after a reload goes with them) and
 * shrink unsaved ones. Unreadable entries are left alone. Returns the
 * approximate bytes freed.
 */
export function compactDrafts({ keep }: { keep?: string } = {}): number {
  const store = storage();
  if (!store) return 0;
  let freed = 0;
  try {
    for (const { key, raw, draft } of entries(store)) {
      if (key === keep || !draft) continue;
      if (!draft.unsaved) {
        store.removeItem(key);
        freed += (key.length + raw.length) * 2;
        continue;
      }
      const compact = serializeDraft(draft.basis, draft.revision, draft.history, { at: draft.at });
      if (compact.length < raw.length) {
        try {
          store.setItem(key, compact);
          freed += (raw.length - compact.length) * 2;
        } catch {
          /* The larger copy stays; it is still a complete backup. */
        }
      }
    }
  } catch {
    /* Storage became unavailable; keep what is there. */
  }
  return freed;
}

/** Remove the browser copies of a deleted task's clips, or of one clip. */
export function removeTaskDrafts(taskId: string, clipId?: string) {
  const store = storage();
  if (!store) return;
  const prefix = `${DRAFT_CACHE_PREFIX}/api/tasks/${taskId}/clips/${clipId ? `${clipId}/` : ""}`;
  try {
    for (const key of keys(store)) if (key.startsWith(prefix)) store.removeItem(key);
  } catch {
    /* Nothing to free when storage is unavailable. */
  }
}
