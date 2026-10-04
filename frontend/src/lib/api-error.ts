export type ApiErrorInfo = {
  message: string;
  traceId: string | null;
  /** Machine-readable `detail.code` from the backend, e.g. SUBSCRIPTION_REQUIRED. */
  code?: string | null;
  /** The full `detail` object, for codes that carry extra fields. */
  detail?: Record<string, unknown> | null;
};

/** Thrown by request helpers so callers can react to specific backend codes. */
export class ApiRequestError extends Error {
  readonly code: string | null;
  readonly detail: Record<string, unknown> | null;

  constructor(info: ApiErrorInfo) {
    super(formatSupportMessage(info));
    this.name = "ApiRequestError";
    this.code = info.code ?? null;
    this.detail = info.detail ?? null;
  }
}

export function isUpgradeErrorCode(code: string | null | undefined): boolean {
  return code === "SUBSCRIPTION_REQUIRED" || code === "VIDEO_TOO_LONG";
}

type ErrorPayload = {
  detail?: unknown;
  error?: unknown;
  message?: unknown;
  trace_id?: unknown;
};

function normalizeMessage(payload: ErrorPayload, fallback: string): string {
  if (typeof payload.message === "string" && payload.message.trim()) {
    return payload.message;
  }

  if (typeof payload.error === "string" && payload.error.trim()) {
    return payload.error;
  }

  if (typeof payload.detail === "string" && payload.detail.trim()) {
    return payload.detail;
  }

  if (
    payload.detail &&
    typeof payload.detail === "object" &&
    "message" in payload.detail &&
    typeof (payload.detail as { message?: unknown }).message === "string"
  ) {
    return (payload.detail as { message: string }).message;
  }

  return fallback;
}

export async function parseApiError(
  response: Response,
  fallbackMessage: string
): Promise<ApiErrorInfo> {
  const traceHeader = response.headers.get("x-trace-id");
  let payload: ErrorPayload = {};

  try {
    const data: unknown = await response.json();
    if (data && typeof data === "object") payload = data as ErrorPayload;
  } catch {
    // Some upstream responses are plain text
  }

  const traceFromBody =
    typeof payload.trace_id === "string" && payload.trace_id.trim()
      ? payload.trace_id
      : null;

  const detail =
    payload.detail && typeof payload.detail === "object"
      ? (payload.detail as Record<string, unknown>)
      : null;

  return {
    message: normalizeMessage(payload, fallbackMessage),
    traceId: traceHeader || traceFromBody,
    code: typeof detail?.code === "string" ? detail.code : null,
    detail,
  };
}

export function formatSupportMessage({ message, traceId }: ApiErrorInfo): string {
  if (!traceId) {
    return message;
  }

  return `${message} (Trace ID: ${traceId})`;
}
