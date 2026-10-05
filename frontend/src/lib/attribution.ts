/**
 * First-touch campaign attribution.
 *
 * The browser keeps the first visit that carried a campaign signal (UTM tags,
 * a ?ref= code, or an external referrer) for 90 days. After sign-up it is
 * copied once to `user_acquisition`, which also records the user's first clip
 * export so campaigns can be measured on activation, not just sign-ups.
 */
export const ATTRIBUTION_FIELDS = [
  "utm_source",
  "utm_medium",
  "utm_campaign",
  "utm_content",
  "utm_term",
  "ref",
  "referrer_host",
  "landing_path",
] as const;

export type AttributionField = (typeof ATTRIBUTION_FIELDS)[number];

export type Attribution = Partial<Record<AttributionField, string>> & {
  /** ISO timestamp of the visit that produced this attribution. */
  captured_at: string;
};

export const ATTRIBUTION_STORAGE_KEY = "supoclip:attribution:v1";
export const ATTRIBUTION_TTL_MS = 90 * 24 * 60 * 60 * 1000;

const MAX_FIELD_LENGTH: Record<AttributionField, number> = {
  utm_source: 100,
  utm_medium: 100,
  utm_campaign: 100,
  utm_content: 100,
  utm_term: 100,
  ref: 100,
  referrer_host: 255,
  landing_path: 512,
};

/** Fields that identify a traffic source; landing_path alone is "direct". */
const SIGNAL_FIELDS: AttributionField[] = ["utm_source", "utm_medium", "utm_campaign", "ref", "referrer_host"];

function clean(field: AttributionField, value: unknown) {
  if (typeof value !== "string") return undefined;
  const trimmed = value.trim();
  if (!trimmed) return undefined;
  // Campaign values are grouped case-insensitively; paths keep their case.
  const normalized = field === "landing_path" ? trimmed : trimmed.toLowerCase();
  return normalized.slice(0, MAX_FIELD_LENGTH[field]);
}

function normalizeHost(host: string) {
  return host.toLowerCase().replace(/^www\./, "");
}

/** Validates untrusted input (request bodies, localStorage) into an Attribution. */
export function sanitizeAttribution(input: unknown): Attribution | null {
  if (!input || typeof input !== "object") return null;
  const record = input as Record<string, unknown>;
  const capturedAt = typeof record.captured_at === "string" ? new Date(record.captured_at) : null;
  if (!capturedAt || Number.isNaN(capturedAt.getTime())) return null;

  const attribution: Attribution = { captured_at: capturedAt.toISOString() };
  for (const field of ATTRIBUTION_FIELDS) {
    const value = clean(field, record[field]);
    if (value) attribution[field] = value;
  }
  return attribution;
}

export function parseAttribution(url: URL, referrer: string, now: Date): Attribution {
  const params = url.searchParams;
  let referrerHost: string | undefined;
  try {
    const host = referrer ? normalizeHost(new URL(referrer).hostname) : "";
    if (host && host !== normalizeHost(url.hostname)) referrerHost = host;
  } catch {
    // Malformed referrers are treated as direct traffic.
  }

  return sanitizeAttribution({
    utm_source: params.get("utm_source"),
    utm_medium: params.get("utm_medium"),
    utm_campaign: params.get("utm_campaign"),
    utm_content: params.get("utm_content"),
    utm_term: params.get("utm_term"),
    ref: params.get("ref") ?? params.get("via"),
    referrer_host: referrerHost,
    landing_path: url.pathname,
    captured_at: now.toISOString(),
  })!;
}

export function hasCampaignSignal(attribution: Attribution | null | undefined) {
  return Boolean(attribution && SIGNAL_FIELDS.some((field) => attribution[field]));
}

export function isAttributionExpired(attribution: Attribution, now: Date) {
  return now.getTime() - new Date(attribution.captured_at).getTime() > ATTRIBUTION_TTL_MS;
}

/** First touch wins, except that a real source replaces an earlier direct visit. */
export function mergeAttribution(existing: Attribution | null, incoming: Attribution, now: Date): Attribution {
  if (!existing || isAttributionExpired(existing, now)) return incoming;
  if (!hasCampaignSignal(existing) && hasCampaignSignal(incoming)) return incoming;
  return existing;
}

/** Compact metadata for analytics goals (DataFast keeps at most 10 keys). */
export function attributionMetadata(attribution: Partial<Record<AttributionField, string | null>> | null | undefined) {
  const metadata: Record<string, string> = {};
  if (!attribution) return metadata;
  for (const field of ["utm_source", "utm_medium", "utm_campaign", "utm_content", "ref", "referrer_host"] as const) {
    const value = attribution[field];
    if (value) metadata[field] = value;
  }
  return metadata;
}

function readStorage(): Attribution | null {
  try {
    return sanitizeAttribution(JSON.parse(window.localStorage.getItem(ATTRIBUTION_STORAGE_KEY) ?? "null"));
  } catch {
    return null;
  }
}

/** Records the current page visit and returns the attribution to keep. */
export function captureAttribution(): Attribution | null {
  if (typeof window === "undefined") return null;
  const now = new Date();
  const next = mergeAttribution(readStorage(), parseAttribution(new URL(window.location.href), document.referrer, now), now);
  try {
    window.localStorage.setItem(ATTRIBUTION_STORAGE_KEY, JSON.stringify(next));
  } catch {
    // Storage can be unavailable (private mode, quotas); attribution is best effort.
  }
  return next;
}

export function getStoredAttribution(): Attribution | null {
  if (typeof window === "undefined") return null;
  const stored = readStorage();
  return stored && !isAttributionExpired(stored, new Date()) ? stored : null;
}
