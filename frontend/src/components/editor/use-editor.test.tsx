import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { useEditor } from "./use-editor";
import { draft } from "@/lib/editor/document.test-fixture";

afterEach(() => vi.unstubAllGlobals());
it("serializes overlapping saves and drains newer changes without losing a revision", async () => {
  const storage = new Map<string, string>();
  vi.stubGlobal("localStorage", {
    getItem: (key: string) => storage.get(key) ?? null,
    setItem: (key: string, value: string) => storage.set(key, value),
  });
  let release!: () => void;
  const gate = new Promise<void>((r) => (release = r));
  const requests: { revision: number; document: typeof draft }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (_url: string, init: RequestInit) => {
      if (init.method === "GET")
        return new Response(
          JSON.stringify({
            status: "ready",
            basis: "clip.mp4",
            draft: { revision: 0, document: draft },
            original: draft,
          }),
        );
      const body = JSON.parse(init.body as string);
      requests.push(body);
      if (requests.length === 1) await gate;
      return new Response(JSON.stringify({ revision: body.revision + 1 }));
    }),
  );
  const { result } = renderHook(() => useEditor("/test/editor"));
  await waitFor(() => expect(result.current.document).not.toBeNull());
  act(() => result.current.update((d) => ({ ...d, volume: 0.5 })));
  let first!: Promise<void>, second!: Promise<void>, third!: Promise<void>;
  act(() => {
    first = result.current.save();
  });
  act(() => result.current.update((d) => ({ ...d, volume: 0.25 })));
  act(() => {
    second = result.current.save();
    third = result.current.save();
  });
  await act(async () => {
    release();
    await Promise.all([first, second, third]);
  });
  expect(requests.map((r) => r.revision)).toEqual([0, 1]);
  expect(requests.map((r) => r.document.volume)).toEqual([0.5, 0.25]);
  expect(result.current.status).toBe("All changes saved");
  expect(result.current.document?.volume).toBe(0.25);
});

const generated = [{
  id: "generated", start: 1, end: 2, offset: 0, query: "city",
  footage: {
    provider: "pexels" as const, id: 42, width: 360, height: 640, duration: 4,
    fileUrl: "https://videos.pexels.com/42.mp4", pageUrl: "", thumbnail: "", credit: "",
  },
}];

function cachedEditor(present: typeof draft, revision = 0, serverRevision = 0, serverChanges: Partial<typeof draft> = {}) {
  const server = { ...draft, broll: generated, ...serverChanges };
  const local = {
    basis: "clip.mp4", revision, saved: JSON.stringify(draft),
    history: { past: [draft], present, future: [draft] },
  };
  const storage = new Map([["supoclip-editor:/test/editor", JSON.stringify(local)]]);
  vi.stubGlobal("localStorage", {
    getItem: (key: string) => storage.get(key) ?? null,
    setItem: (key: string, value: string) => storage.set(key, value),
  });
  const requests: { document: typeof draft }[] = [];
  vi.stubGlobal("fetch", vi.fn(async (_url: string, init: RequestInit) => {
    if (init.method === "GET") return new Response(JSON.stringify({
      status: "ready", basis: "clip.mp4", original: server,
      draft: { revision: serverRevision, document: server },
    }));
    const body = JSON.parse(init.body as string);
    requests.push(body);
    return new Response(JSON.stringify({ revision: body.revision + 1 }));
  }));
  return { requests, storage };
}

it("restores original placements when undoing a cached edit after reload", async () => {
  const transformed = { ...draft, textTransforms: { hook: { x: .1, y: .2, scale: 1.5 } }, captionTransforms: { '["s","a"]': { x: -.1, y: -.2, scale: 1.4 } } };
  cachedEditor(transformed, 0, 0, transformed);
  const { result } = renderHook(() => useEditor("/test/editor"));
  await waitFor(() => expect(result.current.document?.captionTransforms?.['["s","a"]']?.scale).toBe(1.4));
  act(() => result.current.undo());
  expect(result.current.document?.captionTransforms).toEqual({});
  expect(result.current.document?.textTransforms).toEqual({});
  act(() => result.current.redo());
  expect(result.current.document?.captionTransforms?.['["s","a"]']?.scale).toBe(1.4);
  expect(result.current.document?.textTransforms?.hook?.scale).toBe(1.5);
});

it("keeps a long pointer gesture in one undo step and separates the next gesture", async () => {
  cachedEditor(draft);
  const { result } = renderHook(() => useEditor("/test/editor"));
  await waitFor(() => expect(result.current.document).not.toBeNull());
  const now = vi.spyOn(Date, "now").mockReturnValue(1000);
  try {
    act(() => result.current.update(d => ({ ...d, volume: 0.8 }), "gesture:first"));
    now.mockReturnValue(4000);
    act(() => result.current.update(d => ({ ...d, volume: 0.6 }), "gesture:first"));
    act(() => result.current.undo());
    expect(result.current.document?.volume).toBe(1);
    act(() => result.current.redo());
    expect(result.current.document?.volume).toBe(0.6);
    act(() => result.current.update(d => ({ ...d, volume: 0.4 }), "gesture:second"));
    act(() => result.current.undo());
    expect(result.current.document?.volume).toBe(0.6);
  } finally { now.mockRestore(); }
});

it("migrates legacy cached drafts and their undo/redo history before saving", async () => {
  const edited = { ...draft, volume: 0.25 };
  const { requests } = cachedEditor(edited);
  const { result } = renderHook(() => useEditor("/test/editor"));
  await waitFor(() => expect(result.current.document?.volume).toBe(0.25));
  expect(result.current.document?.broll).toEqual(generated);
  act(() => result.current.undo());
  expect(result.current.document?.broll).toEqual(generated);
  act(() => result.current.redo());
  expect(result.current.document?.volume).toBe(0.25);
  expect(result.current.document?.broll).toEqual(generated);
  await act(async () => result.current.save());
  expect(requests.at(-1)?.document.broll).toEqual(generated);
});

it("opens an unchanged legacy cache without dropping generated B-roll or autosaving", async () => {
  const { requests } = cachedEditor(draft);
  const { result } = renderHook(() => useEditor("/test/editor"));
  await waitFor(() => expect(result.current.document?.broll).toEqual(generated));
  expect(result.current.status).toBe("All changes saved");
  await act(async () => result.current.save());
  expect(requests).toEqual([]);
});

it("does not mistake a saved legacy cache for local changes after a server revision", async () => {
  cachedEditor(draft, 0, 1);
  const { result } = renderHook(() => useEditor("/test/editor"));
  await waitFor(() => expect(result.current.document?.broll).toEqual(generated));
  expect(result.current.error).toBeNull();
  expect(result.current.status).toBe("All changes saved");
});

it("retains conflict protection when a legacy cache has unsaved changes", async () => {
  cachedEditor({ ...draft, volume: 0.25 }, 0, 1);
  const { result } = renderHook(() => useEditor("/test/editor"));
  await waitFor(() => expect(result.current.error).toContain("newer draft"));
  expect(result.current.document?.volume).toBe(0.25);
  expect(result.current.document?.broll).toEqual(generated);
});

it("preserves an explicit local removal of all B-roll", async () => {
  const { requests } = cachedEditor({ ...draft, broll: [] });
  const { result } = renderHook(() => useEditor("/test/editor"));
  await waitFor(() => expect(result.current.document).not.toBeNull());
  await act(async () => result.current.save());
  expect(requests.at(-1)?.document.broll).toEqual([]);
});

function serverEditor(storage: Map<string, string>, gate?: Promise<void>, initial: typeof draft = draft) {
  vi.stubGlobal("localStorage", {
    getItem: (key: string) => storage.get(key) ?? null,
    setItem: (key: string, value: string) => storage.set(key, value),
    removeItem: (key: string) => storage.delete(key),
  });
  let revision = 0;
  let document = initial;
  const calls: { url: string; method: string; body?: unknown }[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit) => {
    const body = init.body ? JSON.parse(init.body as string) : undefined;
    calls.push({ url, method: init.method ?? "GET", body });
    if (init.method === "GET") return new Response(JSON.stringify({
      status: "ready", basis: "clip.mp4", original: draft, duration: 10,
      draft: { revision, document },
    }));
    if (url.endsWith("/rebuild")) {
      revision += 1;
      document = { ...draft, volume: 0.75 };
      return new Response(JSON.stringify({ status: "preparing" }));
    }
    if (gate) await gate;
    revision += 1;
    document = body.document;
    return new Response(JSON.stringify({ revision }));
  }));
  return calls;
}

it("keeps a bounded browser copy and marks it saved once the server confirms", async () => {
  const storage = new Map<string, string>();
  let release!: () => void;
  serverEditor(storage, new Promise<void>((r) => (release = r)));
  const { result } = renderHook(() => useEditor("/test/editor"));
  await waitFor(() => expect(result.current.document).not.toBeNull());
  expect(JSON.parse(storage.get("supoclip-editor:/test/editor")!).unsaved).toBe(false);
  for (let step = 1; step <= 30; step++)
    act(() => result.current.update((d) => ({ ...d, volume: step / 100 })));
  const pending = JSON.parse(storage.get("supoclip-editor:/test/editor")!);
  expect(pending.v).toBe(2);
  expect(pending.history.present.volume).toBe(0.3);
  expect(pending.history.past.length).toBeLessThanOrEqual(10);
  let saving!: Promise<void>;
  act(() => { saving = result.current.save(); });
  await act(async () => { release(); await saving; });
  expect(result.current.status).toBe("All changes saved");
  const confirmed = JSON.parse(storage.get("supoclip-editor:/test/editor")!);
  expect(confirmed.unsaved).toBe(false);
  expect(confirmed.history.past.length).toBeLessThanOrEqual(10);
  expect(result.current.storageError).toBe(false);
});

it("shrinks an old full-history copy once the server has it", async () => {
  // The server always returns B-roll, as GET .../editor upgrades drafts.
  const server = { ...draft, broll: [] };
  const storage = new Map([["supoclip-editor:/test/editor", JSON.stringify({
    basis: "clip.mp4", revision: 0, saved: JSON.stringify(server),
    history: { past: Array.from({ length: 50 }, () => server), present: server, future: [] },
  })]]);
  serverEditor(storage, undefined, server);
  const { result } = renderHook(() => useEditor("/test/editor"));
  await waitFor(() => expect(result.current.document).not.toBeNull());
  const stored = JSON.parse(storage.get("supoclip-editor:/test/editor")!);
  expect(stored).toMatchObject({ v: 2, unsaved: false });
  expect(stored.history.past).toHaveLength(10);
  expect(result.current.canUndo).toBe(true);
});

it("rebuilds the editing copy with the revision it shows and loads the fresh draft", async () => {
  const storage = new Map<string, string>();
  const calls = serverEditor(storage);
  const { result } = renderHook(() => useEditor("/test/editor"));
  await waitFor(() => expect(result.current.document).not.toBeNull());
  await act(async () => result.current.rebuild());
  expect(calls.find((c) => c.url === "/test/editor/rebuild")).toMatchObject({ method: "POST", body: { revision: 0 } });
  await waitFor(() => expect(result.current.document?.volume).toBe(0.75));
  expect(result.current.status).toBe("All changes saved");
  expect(result.current.canUndo).toBe(false);
  expect(JSON.parse(storage.get("supoclip-editor:/test/editor")!).history.present.volume).toBe(0.75);
});

const seededFx = {
  version: "fx-v1" as const,
  style: {
    intensity: "bold" as const, sfx: true, music: true, music_mood: "auto" as const, zooms: true, shake: true,
    transitions: true, speed: true, censor: "bleep_bar" as const, grade: "auto" as const, grain: false,
    progress_bar: false, loop_ending: false, wait_for_it: true, reactions: false,
  },
  seed: 7, mood: "hype" as const, sensitivity: "normal" as const, timing: "word" as const, beats: [],
  cues: [{ id: "b0.flash", kind: "flash" as const, start: 1, end: 1.2, seed: 1, beat: "b0", origin: "auto" as const, color: "#FFFFFF", peak: 0.8 }],
  suppressed: [], disabled: [], music: null, grade: "punchy" as const, grain: 0, progressBar: false,
  progressColor: "#FF7A1A", loopEnding: "none" as const, hookTitleSuppressed: false,
};

it("gives a cached draft that predates dynamic edits the server's seeded fx, in its undo history too", async () => {
  const { requests } = cachedEditor({ ...draft, volume: 0.25 }, 0, 0, { fx: seededFx });
  const { result } = renderHook(() => useEditor("/test/editor"));
  await waitFor(() => expect(result.current.document?.volume).toBe(0.25));
  expect(result.current.document?.fx).toEqual(seededFx);
  act(() => result.current.undo());
  expect(result.current.document?.fx).toEqual(seededFx);
  act(() => result.current.redo());
  await act(async () => result.current.save());
  expect(requests.at(-1)?.document.fx).toEqual(seededFx);
});

it("reads a cached fx: null like a missing key and never writes null", async () => {
  const { requests } = cachedEditor({ ...draft, volume: 0.25, fx: null });
  const { result } = renderHook(() => useEditor("/test/editor"));
  await waitFor(() => expect(result.current.document?.volume).toBe(0.25));
  expect(result.current.document).not.toHaveProperty("fx");
  await act(async () => result.current.save());
  expect(requests.at(-1)?.document).not.toHaveProperty("fx");

  vi.unstubAllGlobals();
  cachedEditor({ ...draft, volume: 0.5, fx: null }, 0, 0, { fx: seededFx });
  const second = renderHook(() => useEditor("/test/editor"));
  await waitFor(() => expect(second.result.current.document?.volume).toBe(0.5));
  expect(second.result.current.document?.fx).toEqual(seededFx);
});

it("keeps a cached removal of dynamic edits (the inert fx) over the server's", async () => {
  const removed = { ...seededFx, style: { ...seededFx.style, intensity: "off" as const }, cues: [], grade: "none" as const,
    disabled: ["accent", "zoom"] as ("accent" | "zoom")[] };
  cachedEditor({ ...draft, volume: 0.25, fx: removed }, 0, 0, { fx: seededFx });
  const { result } = renderHook(() => useEditor("/test/editor"));
  await waitFor(() => expect(result.current.document?.volume).toBe(0.25));
  expect(result.current.document?.fx).toEqual(removed);
});

it("refreshes the session's face track and matte without touching the draft or its history", async () => {
  const storage = new Map<string, string>();
  vi.stubGlobal("localStorage", {
    getItem: (key: string) => storage.get(key) ?? null,
    setItem: (key: string, value: string) => storage.set(key, value),
    removeItem: (key: string) => storage.delete(key),
  });
  let session: Record<string, unknown> = {};
  let basis = "clip.mp4";
  const fetchMock = vi.fn(async (_url: string, init: RequestInit) => {
    if (init.method === "GET") return new Response(JSON.stringify({
      status: "ready", basis, original: draft,
      draft: { revision: 7, document: { ...draft, volume: 0.1 } }, ...session,
    }));
    const body = JSON.parse(init.body as string);
    return new Response(JSON.stringify({ revision: body.revision + 1 }));
  });
  vi.stubGlobal("fetch", fetchMock);
  const { result } = renderHook(() => useEditor("/test/editor"));
  await waitFor(() => expect(result.current.document).not.toBeNull());
  act(() => result.current.update((d) => ({ ...d, volume: 0.5 })));
  expect(result.current.asset?.faceTrack ?? null).toBeNull();

  const faceTrack = { base: "clean", track: { fps: 6, boxes: [] } };
  session = { faceTrack, matteVersion: "m2" };
  let refreshed = false;
  await act(async () => { refreshed = await result.current.refreshAsset(); });
  expect(refreshed).toBe(true);
  expect(result.current.asset?.faceTrack).toEqual(faceTrack);
  expect(result.current.asset?.matteVersion).toBe("m2");
  // The unsaved edit and its undo step survive; the server's (older) draft is not adopted.
  expect(result.current.document?.volume).toBe(0.5);
  expect(result.current.canUndo).toBe(true);

  // A session rebuilt meanwhile (new basis) is not adopted.
  basis = "rebuilt.mp4";
  session = { faceTrack: null, matteVersion: null };
  await act(async () => { refreshed = await result.current.refreshAsset(); });
  expect(refreshed).toBe(false);
  expect(result.current.asset?.matteVersion).toBe("m2");
});
