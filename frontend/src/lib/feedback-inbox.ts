/** Admin feedback inbox: the backend's shapes (backend/src/api/routes/admin_feedback.py). */

export const FEEDBACK_CATEGORIES = ["bug", "feature", "general", "sales", "cancellation"] as const;
export const FEEDBACK_STATUSES = ["open", "in_progress", "resolved"] as const;
export const WEBHOOK_KINDS = ["discord", "json"] as const;

export type FeedbackCategory = (typeof FEEDBACK_CATEGORIES)[number];
export type FeedbackStatus = (typeof FEEDBACK_STATUSES)[number];
/** Admins create "discord" and "json"; "hermes" is configured on the server and read-only here. */
export type WebhookKind = (typeof WEBHOOK_KINDS)[number] | "hermes";
export type DeliveryStatus = "pending" | "delivered" | "failed" | "cancelled";

export type FeedbackListItem = {
  id: string;
  user_id: string | null;
  email: string | null;
  user_name: string | null;
  category: FeedbackCategory;
  message: string;
  status: FeedbackStatus;
  platform: "web" | "ios";
  created_at: string;
  note_count: number;
  failed_deliveries: number;
};

export type FeedbackList = {
  items: FeedbackListItem[];
  total: number;
  page: number;
  page_size: number;
  status_counts: Record<FeedbackStatus, number>;
};

export type FeedbackNote = { id: string; body: string; created_at: string; author_id: string | null; author_name?: string | null };

export type SubmissionDelivery = {
  id: string;
  webhook_id: string;
  webhook_name: string;
  webhook_kind: WebhookKind;
  status: DeliveryStatus;
  attempts: number;
  next_attempt_at: string | null;
  last_status_code: number | null;
  last_error: string | null;
  delivered_at: string | null;
  created_at: string;
};

export type FeedbackSubmission = {
  id: string;
  user_id: string | null;
  email: string | null;
  category: FeedbackCategory;
  message: string;
  page_url: string | null;
  platform: "web" | "ios";
  user_agent: string | null;
  app_version: string | null;
  locale: string | null;
  status: FeedbackStatus;
  resolved_at: string | null;
  resolved_by: string | null;
  resolved_by_name: string | null;
  created_at: string;
  updated_at: string;
  user_name: string | null;
  user_current_email: string | null;
  user_plan: string | null;
  user_created_at: string | null;
  notes: FeedbackNote[];
  deliveries: SubmissionDelivery[];
};

export type FeedbackWebhook = {
  id: string;
  name: string;
  kind: WebhookKind;
  url_hint: string;
  categories: FeedbackCategory[];
  enabled: boolean;
  env_source: string | null;
  has_secret: boolean;
  created_at: string;
  updated_at: string;
  pending: number;
  failed: number;
  last_delivered_at: string | null;
};

export type EnvWebhookStatus = {
  env_key: string;
  name: string;
  categories: FeedbackCategory[];
  configured: boolean;
  imported_at: string | null;
};

export type DeliveryAttempt = {
  attempt: number;
  status_code: number | null;
  error: string | null;
  duration_ms: number | null;
  attempted_at: string;
};

export type WebhookDelivery = {
  id: string;
  submission_id: string | null;
  is_test: boolean;
  status: DeliveryStatus;
  attempts: number;
  next_attempt_at: string | null;
  last_status_code: number | null;
  last_error: string | null;
  delivered_at: string | null;
  created_at: string;
  category: FeedbackCategory | null;
  attempt_log: DeliveryAttempt[];
};

/** `from`/`to` date inputs (YYYY-MM-DD, UTC days) as the backend's inclusive-exclusive range. */
export function dateRangeQuery(from?: string, to?: string): Record<string, string> {
  const range: Record<string, string> = {};
  const day = /^\d{4}-\d{2}-\d{2}$/;
  if (from && day.test(from)) range.from = `${from}T00:00:00Z`;
  if (to && day.test(to)) {
    const end = new Date(`${to}T00:00:00Z`);
    end.setUTCDate(end.getUTCDate() + 1);
    range.to = end.toISOString();
  }
  return range;
}

/** Readable error from a failed admin API call: a known code, the backend's message, or null. */
export async function apiError(response: Response): Promise<{ code: string | null; message: string | null }> {
  const data = await response.json().catch(() => null);
  const detail = data?.detail ?? data?.error;
  if (detail && typeof detail === "object" && !Array.isArray(detail)) {
    return { code: typeof detail.code === "string" ? detail.code : null, message: detail.message ?? null };
  }
  return { code: null, message: typeof detail === "string" ? detail : null };
}
