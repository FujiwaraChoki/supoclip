// STUB (WP3b) for the face doodles (§11.1 face_doodles), owned by WP8: replace the body, keep the export.
// Vector doodles around a face box (output px, already picture-transformed) as seeded, boiling polylines.
import type { DrawOp } from "./host";
import type { DrawerContext } from "./registry";
import type { DoodleKind } from "./types";

export function doodleOps(kind: DoodleKind, face: { x: number; y: number; w: number; h: number }, dc: DrawerContext): DrawOp[] {
  void kind; void face; void dc;
  return [];
}
