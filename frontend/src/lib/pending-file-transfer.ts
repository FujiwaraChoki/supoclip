/**
 * Hands a single `File` from one client-side route to the next without a
 * full page reload — a `File` object can't survive a URL query param or
 * `localStorage`, but it does survive a Next.js App Router client
 * navigation (the JS runtime stays alive), so a module-level variable is
 * enough. Used by the home screen's drag-and-drop and "Import Video" CTA to
 * hand a dropped/selected file to /create.
 */
let pendingFile: File | null = null;

export function setPendingFile(file: File) {
  pendingFile = file;
}

/** Consumes the pending file, if any — a second call returns null. */
export function takePendingFile(): File | null {
  const file = pendingFile;
  pendingFile = null;
  return file;
}
