import { act, render, renderHook, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ClipThumbnail, useClipThumbnailStatus, type ThumbnailStatus } from "./clip-thumbnail";

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

const clip = (id: string) => ({ id, filename: `${id}.mp4`, clip_order: 1, duration: 30, text: "", video_url: "/x" });
const clips = [clip("a"), clip("b"), clip("c"), clip("d")];

it("asks for the filmstrip only once the preview is ready", () => {
  const style = (status: ThumbnailStatus) => {
    const { container, unmount } = render(<ClipThumbnail endpoint="/api/tasks/t/clips/a/editor" status={status} />);
    const span = container.firstElementChild as HTMLElement;
    const result = { image: span.style.backgroundImage, pulse: span.classList.contains("animate-pulse") };
    unmount();
    return result;
  };
  expect(style("loading")).toEqual({ image: "", pulse: true });
  expect(style("unprepared")).toEqual({ image: "", pulse: false });
  expect(style("preparing")).toEqual({ image: "", pulse: true });
  // A failed preparation is not shown as still loading.
  expect(style("failed")).toEqual({ image: "", pulse: false });
  expect(style("unknown")).toEqual({ image: "", pulse: false });
  expect(style("ready").image).toContain("/api/tasks/t/clips/a/editor/media/thumbnails.jpg");
});

it("reads each sibling's own preview state and polls one that is still preparing", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  let bPolls = 0;
  const fetch = vi.fn(async (url: string, init: RequestInit) => {
    if (init.method !== "GET") throw new Error(`unexpected ${init.method} ${url}`);
    const id = url.match(/clips\/(\w+)\/editor$/)?.[1];
    if (id === "b") return Response.json({ status: ++bPolls < 3 ? "preparing" : "ready" });
    if (id === "c") return Response.json({ status: "unprepared" });
    if (id === "d") return Response.json({ status: "failed", error: "Source missing" });
    throw new Error(`unexpected ${url}`);
  });
  vi.stubGlobal("fetch", fetch);
  const { result, rerender } = renderHook(({ open }) => useClipThumbnailStatus("t", clips, "a", open), {
    initialProps: { open: false },
  });
  // Nothing is read while the picker is closed.
  expect(fetch).not.toHaveBeenCalled();
  rerender({ open: true });
  await waitFor(() => expect(result.current(clips[3])).toBe("failed"));
  expect(result.current(clips[1])).toBe("preparing");
  expect(result.current(clips[2])).toBe("unprepared");
  // The open clip's state comes from the editor itself, not from here.
  expect(fetch.mock.calls.map(([url]) => url)).not.toContain("/api/tasks/t/clips/a/editor");
  await act(() => vi.advanceTimersByTimeAsync(3000));
  expect(result.current(clips[1])).toBe("preparing");
  await act(() => vi.advanceTimersByTimeAsync(3000));
  expect(result.current(clips[1])).toBe("ready");
  // Never starts a preparation and never asks for media.
  expect(fetch.mock.calls.every(([url, init]) => init.method === "GET" && !url.includes("/media/"))).toBe(true);
  // Once ready it is not read again when the picker reopens; the others are.
  rerender({ open: false });
  fetch.mockClear();
  rerender({ open: true });
  await waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));
  expect(fetch.mock.calls.map(([url]) => url).sort()).toEqual([
    "/api/tasks/t/clips/c/editor",
    "/api/tasks/t/clips/d/editor",
  ]);
});

it("stops polling when the picker closes and shows no image when the state cannot be read", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  const fetch = vi.fn(async (url: string) =>
    url.includes("/b/") ? Response.json({ status: "preparing" }) : new Response("{}", { status: 500 }),
  );
  vi.stubGlobal("fetch", fetch);
  const { result, rerender } = renderHook(({ open }) => useClipThumbnailStatus("t", clips.slice(0, 3), "a", open), {
    initialProps: { open: true },
  });
  await waitFor(() => expect(result.current(clips[2])).toBe("unknown"));
  rerender({ open: false });
  const calls = fetch.mock.calls.length;
  await act(() => vi.advanceTimersByTimeAsync(10_000));
  expect(fetch.mock.calls.length).toBe(calls);
});
