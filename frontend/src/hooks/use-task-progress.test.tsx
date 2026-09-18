import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useTaskProgress } from "./use-task-progress";

class FakeEventSource extends EventTarget {
  static instances: FakeEventSource[] = [];
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  close = vi.fn();
  constructor(public url: string) { super(); FakeEventSource.instances.push(this); }
  emit(type: string, data: unknown) { this.dispatchEvent(new MessageEvent(type, { data: JSON.stringify(data) })); }
}

describe("generation progress recovery", () => {
  beforeEach(() => { vi.useFakeTimers(); FakeEventSource.instances = []; vi.stubGlobal("EventSource", FakeEventSource); });
  afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

  it("keeps reconnecting after a disconnect and reconciles missed completion", async () => {
    const refresh = vi.fn().mockResolvedValue(true);
    const onClip = vi.fn();
    const onProgress = vi.fn();
    const { result, rerender, unmount } = renderHook(({ active }) => useTaskProgress({ taskId: "one", active, refresh, onClip, onProgress }), { initialProps: { active: true } });
    const source = FakeEventSource.instances[0];
    await act(async () => { source.onerror?.(); });
    expect(source.close).not.toHaveBeenCalled();
    expect(result.current.reconnecting).toBe(true);
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    expect(refresh).toHaveBeenCalledTimes(2);
    act(() => { source.onopen?.(); source.emit("clip_ready", { clip: { id: "clip" } }); });
    expect(onClip).toHaveBeenCalledWith({ id: "clip" });
    expect(result.current.reconnecting).toBe(false);
    await act(async () => { source.emit("progress", { status: "completed", progress: 100 }); });
    expect(refresh).toHaveBeenCalledTimes(3);
    rerender({ active: false });
    expect(source.close).toHaveBeenCalled();
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
    expect(refresh).toHaveBeenCalledTimes(3);
    unmount();
  });

  it("recovers malformed events and never overlaps background requests", async () => {
    let resolve!: () => void;
    const refresh = vi.fn(() => new Promise<void>((done) => { resolve = done; }));
    const { unmount } = renderHook(() => useTaskProgress({ taskId: "one", active: true, refresh, onProgress: vi.fn(), onClip: vi.fn() }));
    const source = FakeEventSource.instances[0];
    act(() => source.dispatchEvent(new MessageEvent("progress", { data: "broken" })));
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
    expect(refresh).toHaveBeenCalledTimes(1);
    await act(async () => resolve());
    unmount();
    expect(source.close).toHaveBeenCalledOnce();
    expect(vi.getTimerCount()).toBe(0);
  });
});
