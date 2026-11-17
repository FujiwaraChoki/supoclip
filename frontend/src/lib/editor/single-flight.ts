export interface SingleFlightOptions {
  /** Return at once when a run is in flight, instead of waiting to run again. */
  skipIfBusy?: boolean;
}

export type SingleFlightLoad = (signal: AbortSignal) => Promise<void>;

/**
 * Runs one load at a time. A poll tick is dropped while the previous load is
 * still running, so a slow backend never sees refreshes pile up. An explicit
 * refresh (after starting or cancelling an export) waits for the running load,
 * then loads again, so it always sees its own change.
 *
 * ``reset()`` aborts the load in flight and drops every queued run; a load
 * must not touch state once its signal is aborted. Runs started afterwards
 * work as before.
 */
export function singleFlight() {
  let running: Promise<void> | null = null;
  let controller = new AbortController();
  async function run(
    load: SingleFlightLoad,
    { skipIfBusy = false }: SingleFlightOptions = {},
  ): Promise<void> {
    if (running && skipIfBusy) return;
    const { signal } = controller;
    while (running) {
      await running.catch(() => {});
      if (signal.aborted) return;
    }
    const current: Promise<void> = load(signal).finally(() => {
      if (running === current) running = null;
    });
    running = current;
    return current;
  }
  function reset() {
    controller.abort();
    controller = new AbortController();
    running = null;
  }
  return Object.assign(run, { reset });
}
