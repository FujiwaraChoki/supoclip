import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { isChunkLoadError, reloadOnceForChunkError } from "./chunk-reload";

function chunkError(message = "Loading chunk 123 failed.") {
  const error = new Error(message);
  error.name = "ChunkLoadError";
  return error;
}

describe("isChunkLoadError", () => {
  it("recognises chunk and CSS chunk load failures", () => {
    expect(isChunkLoadError(chunkError())).toBe(true);
    expect(isChunkLoadError(new Error("Loading chunk 42 failed.\n(timeout: /_next/static/chunks/42.js)"))).toBe(true);
    expect(isChunkLoadError(new Error("Loading CSS chunk 7 failed."))).toBe(true);
  });

  it("ignores other errors", () => {
    expect(isChunkLoadError(new Error("boom"))).toBe(false);
    expect(isChunkLoadError(new TypeError("Failed to fetch"))).toBe(false);
    expect(isChunkLoadError(null)).toBe(false);
    expect(isChunkLoadError("Loading chunk 1 failed")).toBe(false);
  });
});

describe("reloadOnceForChunkError", () => {
  const original = window.location;
  const reload = vi.fn();

  beforeEach(() => {
    Object.defineProperty(window, "location", { configurable: true, value: { ...original, reload } });
  });

  afterEach(() => {
    Object.defineProperty(window, "location", { configurable: true, value: original });
    reload.mockReset();
    window.sessionStorage.clear();
    vi.restoreAllMocks();
  });

  it("reloads once for a chunk error", () => {
    expect(reloadOnceForChunkError(chunkError(), 1_000_000)).toBe(true);
    expect(reload).toHaveBeenCalledTimes(1);
  });

  it("does not reload again within 60 seconds", () => {
    reloadOnceForChunkError(chunkError(), 1_000_000);
    expect(reloadOnceForChunkError(chunkError(), 1_000_000 + 59_999)).toBe(false);
    expect(reload).toHaveBeenCalledTimes(1);
  });

  it("may reload again once 60 seconds have passed", () => {
    reloadOnceForChunkError(chunkError(), 1_000_000);
    expect(reloadOnceForChunkError(chunkError(), 1_000_000 + 60_000)).toBe(true);
    expect(reload).toHaveBeenCalledTimes(2);
  });

  it("does not reload for other errors", () => {
    expect(reloadOnceForChunkError(new Error("boom"), 1_000_000)).toBe(false);
    expect(reload).not.toHaveBeenCalled();
  });

  it("does not reload or throw when sessionStorage is unavailable", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new DOMException("denied", "SecurityError");
    });
    expect(() => reloadOnceForChunkError(chunkError(), 1_000_000)).not.toThrow();
    expect(reload).not.toHaveBeenCalled();
  });

  it("does not reload when the attempt can't be recorded", () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("full", "QuotaExceededError");
    });
    expect(reloadOnceForChunkError(chunkError(), 1_000_000)).toBe(false);
    expect(reload).not.toHaveBeenCalled();
  });
});
