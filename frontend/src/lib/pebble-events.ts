/**
 * Messages between the create screen / task page and Agent's onboarding guide, which lives in the
 * app shell. Plain window events, so neither side has to know whether the other is on screen.
 */
export type PebbleEvents = {
  /** A recognised video link in the create composer (debounced). */
  "pebble:source": { url: string };
  /** A video file picked in the create composer, once its length is read. */
  "pebble:file": { durationSeconds: number };
  /** The free preview project was created. */
  "pebble:preview-started": { taskId: string };
  /** The free preview couldn't start (too long, limits). */
  "pebble:preview-error": { code: string | null; message: string };
  /** Back from checkout on a preview project; the page is unlocking it. */
  "pebble:paid": { taskId: string };
};

export function emitPebble<K extends keyof PebbleEvents>(name: K, detail: PebbleEvents[K]) {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new CustomEvent(name, { detail }));
}

export function onPebble<K extends keyof PebbleEvents>(name: K, handler: (detail: PebbleEvents[K]) => void) {
  const listener = (event: Event) => handler((event as CustomEvent<PebbleEvents[K]>).detail);
  window.addEventListener(name, listener);
  return () => window.removeEventListener(name, listener);
}
