export type ApiErrorInfo = {
  message: string;
  traceId: string | null;
};

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
    payload = (await response.json()) as ErrorPayload;
  } catch {
    // Some upstream responses are plain text
  }

  const traceFromBody =
    typeof payload.trace_id === "string" && payload.trace_id.trim()
      ? payload.trace_id
      : null;

  return {
    message: normalizeMessage(payload, fallbackMessage),
    traceId: traceHeader || traceFromBody,
  };
}

export function formatSupportMessage({ message, traceId }: ApiErrorInfo): string {
  if (!traceId) {
    return message;
  }

  return `${message} (Trace ID: ${traceId})`;
}
