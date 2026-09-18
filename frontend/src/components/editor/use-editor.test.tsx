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
