import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CLIP_JOB_MAX_FAILED_POLLS, CLIP_JOB_POLL_MS, followClipJob, type ClipJobEnd } from "./clip-job";

const URL = "/api/tasks/task-1/clip-jobs/job-1";

function follow(responses: (() => Response | Promise<Response>)[]) {
  const fetchMock = vi.fn(async () => {
    const next = responses.shift();
    if (!next) throw new Error("no more responses");
    return next();
  });
  vi.stubGlobal("fetch", fetchMock);
  const progress: number[] = [];
  const ends: ClipJobEnd[] = [];
  const stop = followClipJob(URL, { onProgress: (value) => progress.push(value), onEnd: (end) => ends.push(end) });
  return { fetchMock, progress, ends, stop };
}

const state = (body: unknown) => () => Response.json(body);
const status = (code: number) => () => new Response(null, { status: code });
const offline = () => { throw new TypeError("Failed to fetch"); };
const ticks = (count: number) => vi.advanceTimersByTimeAsync(count * CLIP_JOB_POLL_MS);

beforeEach(() => { vi.useFakeTimers(); });
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

describe("followClipJob", () => {
  it("reports progress, then done, and stops polling", async () => {
    const { fetchMock, progress, ends } = follow([state({ status: "running", progress: 0.4 }), state({ status: "done", progress: 1 })]);
    await ticks(5);
    expect(progress).toEqual([0.4]);
    expect(ends).toEqual([{ status: "done" }]);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("keeps polling a job that stays queued", async () => {
    const { fetchMock, ends } = follow(Array.from({ length: 40 }, () => state({ status: "queued", progress: 0 })));
    await ticks(30);
    expect(fetchMock).toHaveBeenCalledTimes(30);
    expect(ends).toEqual([]);
  });

  it("ends a failed job with the server's code and message", async () => {
    const { ends } = follow([state({ status: "failed", error: { code: "WORKER_STOPPED", message: "The worker stopped." } })]);
    await ticks(1);
    expect(ends).toEqual([{ status: "failed", code: "WORKER_STOPPED", message: "The worker stopped." }]);
  });

  it.each([[401], [403]])("treats %i as the end", async (code) => {
    const { fetchMock, ends } = follow([status(code), state({ status: "done" })]);
    await ticks(3);
    expect(ends).toEqual([{ status: "denied" }]);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("treats a missing job as gone", async () => {
    const { ends } = follow([status(404)]);
    await ticks(1);
    expect(ends).toEqual([{ status: "gone" }]);
  });

  it("gives up after five failed polls in a row", async () => {
    const { fetchMock, ends } = follow([offline, status(503), offline, status(500), offline, state({ status: "done" })]);
    await ticks(CLIP_JOB_MAX_FAILED_POLLS - 1);
    expect(ends).toEqual([]);
    await ticks(3);
    expect(ends).toEqual([{ status: "unreachable" }]);
    expect(fetchMock).toHaveBeenCalledTimes(CLIP_JOB_MAX_FAILED_POLLS);
  });

  it("starts counting again after a poll that worked", async () => {
    const { ends } = follow([offline, offline, offline, offline, state({ status: "running", progress: 0.1 }), offline, offline, offline, offline, state({ status: "done" })]);
    await ticks(10);
    expect(ends).toEqual([{ status: "done" }]);
  });

  it("stops when asked", async () => {
    const { fetchMock, ends, stop } = follow([state({ status: "running" }), state({ status: "done" })]);
    await ticks(1);
    stop();
    await ticks(3);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(ends).toEqual([]);
  });
});
