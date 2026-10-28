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
