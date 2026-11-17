export interface SingleFlightOptions {
  /** Return at once when a run is in flight, instead of waiting to run again. */
  skipIfBusy?: boolean;
}

/**
 * Runs one load at a time. A poll tick is dropped while the previous load is
 * still running, so a slow backend never sees refreshes pile up. An explicit
 * refresh (after starting or cancelling an export) waits for the running load,
 * then loads again, so it always sees its own change.
 */
export function singleFlight() {
  let running: Promise<void> | null = null;
  return async function run(
    load: () => Promise<void>,
    { skipIfBusy = false }: SingleFlightOptions = {},
  ): Promise<void> {
    if (running && skipIfBusy) return;
    while (running) await running.catch(() => {});
    const current: Promise<void> = load().finally(() => {
      if (running === current) running = null;
    });
    running = current;
    return current;
  };
}
