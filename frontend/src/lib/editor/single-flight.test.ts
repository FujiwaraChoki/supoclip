import { describe, expect, it } from "vitest";
import { singleFlight } from "./single-flight";

function deferred() {
  let resolve!: () => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<void>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

describe("singleFlight", () => {
  it("skips poll ticks while a load is still running", async () => {
    const run = singleFlight();
    const slow = deferred();
    let calls = 0;
    const load = () => {
      calls += 1;
      return slow.promise;
    };
    const first = run(load, { skipIfBusy: true });
    await run(load, { skipIfBusy: true });
    await run(load, { skipIfBusy: true });
    expect(calls).toBe(1);
    slow.resolve();
    await first;
    await run(async () => void (calls += 1), { skipIfBusy: true });
    expect(calls).toBe(2);
  });

  it("runs an explicit refresh after the load in flight, never alongside it", async () => {
    const run = singleFlight();
    const slow = deferred();
    let active = 0;
    let most = 0;
    const order: string[] = [];
    const track = (name: string, wait?: Promise<void>) => async () => {
      active += 1;
      most = Math.max(most, active);
      order.push(name);
      await wait;
      active -= 1;
    };
    const poll = run(track("poll", slow.promise), { skipIfBusy: true });
    const explicit = [run(track("after-export")), run(track("after-cancel"))];
    await Promise.resolve();
    expect(order).toEqual(["poll"]);
    slow.resolve();
    await Promise.all([poll, ...explicit]);
    expect(order).toEqual(["poll", "after-export", "after-cancel"]);
    expect(most).toBe(1);
  });

  it("recovers after a failed load", async () => {
    const run = singleFlight();
    const failing = deferred();
    const first = run(() => failing.promise);
    const next = run(async () => {});
    failing.reject(new Error("offline"));
    await expect(first).rejects.toThrow("offline");
    await expect(next).resolves.toBeUndefined();
    let ran = false;
    await run(async () => void (ran = true), { skipIfBusy: true });
    expect(ran).toBe(true);
  });

  it("reset aborts the load in flight and drops queued runs", async () => {
    const run = singleFlight();
    const slow = deferred();
    let signal: AbortSignal | undefined;
    const first = run((s) => {
      signal = s;
      return slow.promise;
    });
    let queued = false;
    const waiting = run(async () => void (queued = true));
    run.reset();
    expect(signal?.aborted).toBe(true);
    slow.resolve();
    await Promise.all([first, waiting]);
    expect(queued).toBe(false);
    // Later runs start at once and are not aborted.
    let fresh: AbortSignal | undefined;
    await run(async (s) => void (fresh = s), { skipIfBusy: true });
    expect(fresh?.aborted).toBe(false);
  });
});
