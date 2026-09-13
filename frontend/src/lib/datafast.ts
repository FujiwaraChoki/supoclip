"use client";

// Local-first build: analytics are hard-disabled (no-op), not env-gated —
// there's no telemetry, full stop. Call sites keep working unchanged.

export type DataFastMetadata = Record<
  string,
  string | number | boolean | readonly (string | number | boolean)[] | null | undefined
>;

export function track(_goalName: string, _metadata?: DataFastMetadata) {
  return;
}

export function identify(_metadata: DataFastMetadata & { user_id: string }) {
  return;
}
