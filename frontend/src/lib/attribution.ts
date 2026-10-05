/**
 * First-touch campaign attribution.
 *
 * While nobody is signed in, the browser keeps the first visit that carried a
 * campaign signal (UTM tags, a ?ref= code, or an external referrer) for 90
 * days. The first account to sign in claims it synchronously into that
 * account's pending slot (so no other account can), syncs it to
 * `user_acquisition`, and stores the server's confirmed value for the account.
 * Unconfirmed empty answers are retried on later page loads.
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
export const accountAttributionKey = (userId: string) => `supoclip:attribution:account:${userId}`;
export const pendingAttributionKey = (userId: string) => `supoclip:attribution:pending:${userId}`;
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

/** Routes whose segment is a secret or a record ID, never a marketing page. */
const DYNAMIC_ROUTES: Array<[prefix: string, placeholder: string]> = [
  ["share", ":token"],
  ["tasks", ":id"],
];

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const MULTI_WORD_SLUG = /^[a-z0-9]+(?:-[a-z0-9]+)+$/;

function maskSegment(segment: string) {
  if (UUID.test(segment) || /^\d+$/.test(segment)) return ":id";
  // Tokens (e.g. token_urlsafe) are long, URL-safe, and mix digits or case; slugs are lowercase words.
  const tokenLike =
    segment.length >= 16 &&
    /^[A-Za-z0-9_-]+$/.test(segment) &&
    !MULTI_WORD_SLUG.test(segment) &&
    (/\d/.test(segment) || (/[a-z]/.test(segment) && /[A-Z]/.test(segment)));
  return tokenLike ? ":token" : segment;
}

/**
 * Reduces a landing path to its route so share tokens and record IDs never
 * enter attribution: `/share/<token>` → `/share/:token`, `/tasks/<id>/edit` →
 * `/tasks/:id/edit`. Query strings and fragments are dropped.
 */
export function normalizeLandingPath(path: string) {
  const pathname = path.split(/[?#]/, 1)[0];
  if (!pathname.startsWith("/")) return undefined;
  const segments = pathname.split("/").slice(1);
  const route = DYNAMIC_ROUTES.find(([prefix]) => segments[0] === prefix);
  return (
    "/" +
    segments
      .map((segment, index) => (route && index === 1 && segment ? route[1] : maskSegment(segment)))
      .join("/")
  );
}

function clean(field: AttributionField, value: unknown) {
  if (typeof value !== "string") return undefined;
  const trimmed = value.trim();
  if (!trimmed) return undefined;
  // Campaign values are grouped case-insensitively; paths keep their case.
  const normalized = field === "landing_path" ? normalizeLandingPath(trimmed) : trimmed.toLowerCase();
  return normalized?.slice(0, MAX_FIELD_LENGTH[field]);
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

function readJson(key: string): unknown {
  try {
    return JSON.parse(window.localStorage.getItem(key) ?? "null");
  } catch {
    return null;
  }
}

function writeJson(key: string, value: unknown) {
  try {
    window.localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // Storage can be unavailable (private mode, quotas); attribution is best effort.
  }
}

/**
 * Records a visit made while signed out. Callers must only pass visits where
 * the session is known to be anonymous; signed-in visits are never captured.
 */
export function captureAnonymousAttribution(href: string, referrer: string): Attribution | null {
  if (typeof window === "undefined") return null;
  const now = new Date();
  const next = mergeAttribution(sanitizeAttribution(readJson(ATTRIBUTION_STORAGE_KEY)), parseAttribution(new URL(href), referrer, now), now);
  writeJson(ATTRIBUTION_STORAGE_KEY, next);
  return next;
}

/** The unclaimed, signed-out record on this browser, if it hasn't expired. */
export function getAnonymousAttribution(): Attribution | null {
  if (typeof window === "undefined") return null;
  const stored = sanitizeAttribution(readJson(ATTRIBUTION_STORAGE_KEY));
  return stored && !isAttributionExpired(stored, new Date()) ? stored : null;
}

/** Canonical identity of one recorded visit; equal keys mean the exact same visit. */
export function attributionVisitKey(attribution: Attribution) {
  return JSON.stringify([attribution.captured_at, ...ATTRIBUTION_FIELDS.map((field) => attribution[field] ?? null)]);
}

function removeKey(key: string) {
  try {
    window.localStorage.removeItem(key);
  } catch {
    // Best effort, like every other storage access here.
  }
}

/** The confirmed record for this account, or undefined when the server hasn't confirmed one yet. */
function readConfirmed(userId: string): { attribution: Attribution | null } | undefined {
  const stored = readJson(accountAttributionKey(userId));
  if (!stored || typeof stored !== "object" || !("attribution" in stored)) return undefined;
  return { attribution: sanitizeAttribution((stored as { attribution: unknown }).attribution) };
}

/** Whether the server has confirmed this account's attribution on this browser (no more syncing needed). */
export function isAccountAttributionConfirmed(userId: string) {
  return typeof window !== "undefined" && readConfirmed(userId) !== undefined;
}

/** A visit this account claimed but the server hasn't confirmed yet. */
export function getPendingAttribution(userId: string): Attribution | null {
  if (typeof window === "undefined") return null;
  return sanitizeAttribution(readJson(pendingAttributionKey(userId)));
}

/**
 * Synchronously moves the anonymous visit into this account's pending slot, so
 * no other account (or later sign-in) on this browser can claim it while the
 * sync request is in flight. Returns the visit to sync: an earlier unconfirmed
 * claim takes precedence. A confirmed account still consumes the anonymous
 * visit (discarding it) so the next account to sign in can't inherit it.
 */
export function claimAnonymousAttribution(userId: string): Attribution | null {
  if (typeof window === "undefined") return null;
  if (isAccountAttributionConfirmed(userId)) {
    removeKey(ATTRIBUTION_STORAGE_KEY);
    return null;
  }
  const pending = getPendingAttribution(userId);
  if (pending) return pending;
  const anonymous = getAnonymousAttribution();
  removeKey(ATTRIBUTION_STORAGE_KEY);
  if (!anonymous) return null;
  writeJson(pendingAttributionKey(userId), anonymous);
  return anonymous;
}

/**
 * Attribution for this account's analytics: the server-confirmed value, or the
 * claimed visit while confirmation is pending. Never another account's data.
 */
export function getAccountAttribution(userId: string): Attribution | null {
  if (typeof window === "undefined") return null;
  const confirmed = readConfirmed(userId);
  return confirmed ? confirmed.attribution : getPendingAttribution(userId);
}

/**
 * Stores the server's confirmed value for this account and consumes the
 * pending claim only if it is still the exact visit that was sent, so a late
 * response can't erase a newer journey. The anonymous slot is never touched.
 */
export function confirmAccountAttribution(userId: string, attribution: Attribution | null, sentVisit: Attribution | null) {
  if (typeof window === "undefined") return;
  writeJson(accountAttributionKey(userId), { attribution });
  const pending = getPendingAttribution(userId);
  if (sentVisit && pending && attributionVisitKey(pending) === attributionVisitKey(sentVisit)) {
    removeKey(pendingAttributionKey(userId));
  }
}
