/**
 * The agent home screen's contract with the backend (`backend/src/api/routes/agent.py`).
 * Card shapes mirror `backend/src/agent/tools.py`.
 */
import type { SocialPost, SocialProviderId } from "@/lib/social";

export type HomeMode = "agent" | "manual";

export interface AgentStatus {
  available: boolean;
  /** Why not: switched off, not set up, a plan without the agent, or Pro after its free messages. */
  reason: null | "disabled" | "not_configured" | "plan" | "trial_used";
  /** `full` on Creator/Team/self-host/admins, `trial` for Pro while it has free messages. */
  access?: "full" | "trial" | null;
  trial?: { limit: number; used: number } | null;
  plan?: string | null;
  home_mode: HomeMode | null;
  /** Ask "How do you want to work?" (before a choice; new accounts and Creators). */
  offer_choice?: boolean;
  model: string | null;
  daily_limit: number;
}

/** The agent is part of the product for this account (it may still need Creator to use). */
export function agentOffered(status: AgentStatus | null | undefined): boolean {
  return Boolean(status && status.reason !== "disabled" && status.reason !== "not_configured");
}

export function trialLeft(status: AgentStatus | null | undefined): number | null {
  if (!status?.trial) return null;
  return Math.max(0, status.trial.limit - status.trial.used);
}

export interface AgentConversationSummary {
  id: string;
  title: string | null;
  created_at: string | null;
  updated_at: string | null;
  status: string | null;
}

export type OptionIcon =
  | "sparkles" | "list" | "target" | "recap" | "trailer" | "calendar" | "send" | "scissors" | "wand" | "captions"
  | "chart" | "youtube" | "tiktok" | "instagram" | "clock" | "check" | "x" | "upload" | "link" | "film" | "heart";

export interface AgentOption {
  id: string;
  label: string;
  reply: string;
  description?: string;
  icon?: OptionIcon;
  recommended?: boolean;
  project_id?: string;
  clip_id?: string;
  score?: number;
  when?: string;
}

export interface CardPost extends Pick<SocialPost,
  "id" | "provider" | "status" | "scheduled_for" | "published_at" | "created_at" | "title" | "caption" |
  "task_id" | "clip_id" | "external_url" | "error_message" | "metrics"> {
  account: { display_name: string | null; username: string | null };
}

export interface AgentJobCard {
  id: string;
  kind: "clip_edit" | "followup";
  status: "pending" | "running" | "completed" | "failed" | "cancelled";
  step: string | null;
  project_id: string | null;
  clip_id: string | null;
  clip_title?: string | null;
  instruction?: string | null;
  summary?: string | null;
  export_url?: string | null;
  error?: string | null;
}

export type AgentCard =
  | { type: "options"; style: "chips" | "cards" | "clips" | "slots"; question: string | null; multiple: boolean; options: AgentOption[] }
  | { type: "projects"; items: { id: string; title: string; status: string; clips_count: number; cover_clip_id: string | null; created_at: string | null; workflow: string }[] }
  | { type: "project"; project: { id: string; title: string; status: string; progress: number | null; clips: { id: string; title: string | null; score: number | null; duration: number | null; order: number | null }[] } }
  | { type: "clips"; items: { project_id: string; clip_id: string; title: string | null; score: number | null; duration: number | null; project_title: string | null }[] }
  | { type: "project_started"; project: { id: string; title: string | null; workflow: string } }
  | { type: "project_restyled"; project_id: string; title: string | null }
  | { type: "clip"; project_id: string; clip_id: string; title: string | null; note: string }
  | { type: "posts"; items: CardPost[]; range: { start: string; end: string } }
  | { type: "post"; post: CardPost; action: "scheduled" | "publishing" | "updated" | "cancelled" | "retried" }
  | { type: "edit"; job: AgentJobCard }
  | { type: "followup"; job_id: string; project_id: string; project_title: string | null; instruction: string }
  | { type: "slots"; items: string[]; platform: SocialProviderId | null; basis: string }
  | { type: "performance"; summary: Record<string, unknown> }
  | { type: "defaults"; changed: Record<string, unknown> }
  | { type: "memory"; note: string };

export interface ApprovalItem {
  id: string;
  tool: string;
  input: { project_id?: string; clip_id?: string; account_id?: string; platform?: SocialProviderId; title?: string; caption?: string; hashtags?: string[]; privacy?: string };
}

export type ToolStatus = "running" | "done" | "error" | "declined" | "awaiting_approval" | "interrupted";

export type AgentItem =
  | { type: "user"; id: string; text: string; attachments: AttachmentPreview[]; created_at: string | null }
  | { type: "notice"; id: string; notice: { kind: string; project_id?: string; clip_id?: string; title?: string | null; status?: string }; created_at: string | null }
  | { type: "text"; id: string; text: string; run_id?: string | null }
  | { type: "tool"; id: string; name: string; input: Record<string, string | number | boolean>; status: ToolStatus; card?: AgentCard | null; error?: string | null; approval?: ApprovalItem };

export interface AgentConversation {
  conversation: { id: string; title: string | null; created_at: string | null; updated_at: string | null };
  items: AgentItem[];
  run: { id: string; status: "queued" | "running" | "awaiting_approval"; pending?: { items?: ApprovalItem[] } | null } | null;
  jobs: AgentJobCard[];
  last_event_id: string;
  /** The reply being written while nobody watched, and the stream id it reached. */
  partial?: { text: string; event_id: string | null } | null;
}

/** Something sent along with a message: a video to clip, or an image, PDF or text file to read. */
export type Attachment =
  | { kind: "video"; url: string; name: string }
  | { kind: "image"; name: string; media_type: string; data: string; preview: string; size: number }
  | { kind: "pdf"; name: string; data: string; size: number }
  | { kind: "text"; name: string; text: string; size: number };

/** What the chat shows for an attachment once sent. */
export interface AttachmentPreview {
  name: string | null;
  kind: "video" | "image" | "pdf" | "text";
  preview?: string | null;
  size?: number | null;
}

export const agentKeys = {
  status: ["agent", "status"] as const,
  conversations: ["agent", "conversations"] as const,
  conversation: (id: string) => ["agent", "conversation", id] as const,
};

async function json<T>(response: Response): Promise<T> {
  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    let code: string | undefined;
    try {
      const body = await response.json();
      const detail = body?.detail ?? body;
      if (typeof detail === "string") message = detail;
      else if (detail?.message) { message = detail.message; code = detail.code; }
    } catch { /* keep the status message */ }
    throw Object.assign(new Error(message), { status: response.status, code });
  }
  return (await response.json()) as T;
}

export async function fetchAgentStatus(): Promise<AgentStatus | null> {
  const response = await fetch("/api/agent/status", { cache: "no-store" });
  if (response.status === 401) return null;
  return json<AgentStatus>(response);
}

export async function fetchConversations(): Promise<AgentConversationSummary[]> {
  const response = await fetch("/api/agent/conversations?limit=20", { cache: "no-store" });
  return (await json<{ conversations: AgentConversationSummary[] }>(response)).conversations;
}

export async function fetchConversation(id: string): Promise<AgentConversation> {
  return json<AgentConversation>(await fetch(`/api/agent/conversations/${id}`, { cache: "no-store" }));
}

export function messageContext() {
  return {
    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
    page: typeof window === "undefined" ? undefined : window.location.pathname,
  };
}

export async function startConversation(message: string, attachments: Attachment[], locale: string) {
  return json<{ conversation: { id: string }; run: { id: string } }>(
    await fetch("/api/agent/conversations", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, attachments, locale, ...messageContext() }),
    }),
  );
}

export async function sendMessage(conversationId: string, message: string, attachments: Attachment[], locale: string) {
  return json<{ run: { id: string } }>(
    await fetch(`/api/agent/conversations/${conversationId}/messages`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, attachments, locale, ...messageContext() }),
    }),
  );
}

export async function answerApproval(conversationId: string, approved: boolean, runId?: string) {
  return json<{ ok: boolean }>(
    await fetch(`/api/agent/conversations/${conversationId}/approval`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ approved, run_id: runId, timezone: messageContext().timezone }),
    }),
  );
}

export async function stopConversation(conversationId: string) {
  return json<{ stopped: boolean }>(await fetch(`/api/agent/conversations/${conversationId}/stop`, { method: "POST" }));
}

export async function deleteConversation(conversationId: string) {
  return json<{ ok: boolean }>(await fetch(`/api/agent/conversations/${conversationId}`, { method: "DELETE" }));
}

export async function renameConversation(conversationId: string, title: string) {
  return json<{ ok: boolean }>(
    await fetch(`/api/agent/conversations/${conversationId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title }),
    }),
  );
}

export async function saveHomeMode(homeMode: HomeMode) {
  return json<{ homeMode: HomeMode | null }>(
    await fetch("/api/preferences", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ homeMode }),
    }),
  );
}

export function clipFileUrl(projectId: string, clipId: string) {
  return `/api/tasks/${projectId}/clips/${clipId}/file`;
}

/** The first http(s) link in a message, so the composer can show it as a video. */
export function firstLink(text: string): string | null {
  return text.match(/https?:\/\/[^\s<>"']+/)?.[0] ?? null;
}

/** Whether a later message already answered an options card (so it shows as answered). */
export function answeredReply(items: AgentItem[], index: number): string | null {
  for (let next = index + 1; next < items.length; next += 1) {
    const item = items[next];
    if (item.type === "user") return item.text;
  }
  return null;
}
