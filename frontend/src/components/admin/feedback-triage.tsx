"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { useLocale, useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { FeedbackStatusBadge } from "@/components/admin/feedback-badges";
import { intlLocale } from "@/i18n/config";
import {
  FEEDBACK_STATUSES,
  apiError,
  type FeedbackNote,
  type FeedbackStatus,
  type FeedbackSubmission,
} from "@/lib/feedback-inbox";

/** Status, notes and webhook deliveries of one submission (admin detail page). */
export function FeedbackTriage({ submission }: { submission: FeedbackSubmission }) {
  const t = useTranslations("admin");
  const dateLocale = intlLocale(useLocale());
  const router = useRouter();
  const [status, setStatus] = useState<FeedbackStatus>(submission.status);
  const [resolvedAt, setResolvedAt] = useState(submission.resolved_at);
  const [notes, setNotes] = useState<FeedbackNote[]>(submission.notes);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const when = (iso: string) => new Date(iso).toLocaleString(dateLocale, { dateStyle: "medium", timeStyle: "short" });
  const base = `/api/admin/feedback/${encodeURIComponent(submission.id)}`;

  const changeStatus = async (next: FeedbackStatus) => {
    if (next === status) return;
    setBusy("status");
    setError(null);
    setMessage(null);
    try {
      const response = await fetch(base, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status: next }),
      });
      if (!response.ok) throw new Error((await apiError(response)).message || t("feedback.triage.saveFailed"));
      const data = (await response.json()) as { submission: FeedbackSubmission };
      setStatus(data.submission.status);
      setResolvedAt(data.submission.resolved_at);
      setMessage(t("feedback.triage.statusSaved", { status: t(`feedback.status.${data.submission.status}`) }));
      router.refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : t("feedback.triage.saveFailed"));
    } finally {
      setBusy(null);
    }
  };

  const addNote = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!draft.trim()) return;
    setBusy("note");
    setError(null);
    setMessage(null);
    try {
      const response = await fetch(`${base}/notes`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ body: draft.trim() }),
      });
      if (!response.ok) throw new Error((await apiError(response)).message || t("feedback.triage.noteFailed"));
      const data = (await response.json()) as { note: FeedbackNote };
      setNotes((current) => [...current, { ...data.note, author_name: t("feedback.triage.you") }]);
      setDraft("");
      setMessage(t("feedback.triage.noteAdded"));
    } catch (err) {
      setError(err instanceof Error ? err.message : t("feedback.triage.noteFailed"));
    } finally {
      setBusy(null);
    }
  };

  const retry = async (deliveryId: string) => {
    setBusy(deliveryId);
    setError(null);
    setMessage(null);
    try {
      const response = await fetch(`/api/admin/feedback/deliveries/${encodeURIComponent(deliveryId)}/retry`, { method: "POST" });
      if (!response.ok) throw new Error((await apiError(response)).message || t("feedback.triage.retryFailed"));
      setMessage(t("feedback.triage.retryQueued"));
      router.refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : t("feedback.triage.retryFailed"));
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="space-y-6">
      <section aria-labelledby="feedback-status" className="rounded-2xl border bg-background p-5">
        <h2 id="feedback-status" className="text-sm font-semibold">{t("feedback.triage.status")}</h2>
        <div role="group" aria-labelledby="feedback-status" className="mt-3 inline-flex flex-wrap gap-1 rounded-lg border p-1">
          {FEEDBACK_STATUSES.map((option) => (
            <Button
              key={option}
              type="button"
              size="sm"
              variant={status === option ? "default" : "ghost"}
              aria-pressed={status === option}
              disabled={busy !== null}
              onClick={() => void changeStatus(option)}
            >
              {t(`feedback.status.${option}`)}
            </Button>
          ))}
        </div>
        {status === "resolved" && resolvedAt && (
          <p className="mt-2 text-xs text-muted-foreground">
            {t("feedback.triage.resolvedAt", { date: when(resolvedAt), name: submission.resolved_by_name || t("feedback.triage.anAdmin") })}
          </p>
        )}
      </section>

      <div aria-live="polite" className="text-sm">
        {error && <p role="alert" className="text-red-600">{error}</p>}
        {message && <p role="status" className="text-muted-foreground">{message}</p>}
      </div>

      <section aria-labelledby="feedback-notes" className="rounded-2xl border bg-background p-5">
        <h2 id="feedback-notes" className="text-sm font-semibold">{t("feedback.triage.notes")}</h2>
        {notes.length ? (
          <ol className="mt-3 space-y-3">
            {notes.map((note) => (
              <li key={note.id} className="rounded-lg bg-muted/40 p-3 text-sm">
                <p className="whitespace-pre-wrap break-words">{note.body}</p>
                <p className="mt-1 text-xs text-muted-foreground">
                  {note.author_name || t("feedback.triage.anAdmin")} · <time dateTime={note.created_at}>{when(note.created_at)}</time>
                </p>
              </li>
            ))}
          </ol>
        ) : (
          <p className="mt-2 text-sm text-muted-foreground">{t("feedback.triage.noNotes")}</p>
        )}
        <form onSubmit={addNote} className="mt-4 space-y-2">
          <label htmlFor="feedback-note" className="text-xs font-medium text-muted-foreground">{t("feedback.triage.addNote")}</label>
          <Textarea id="feedback-note" value={draft} onChange={(event) => setDraft(event.target.value)} maxLength={4000} rows={3} disabled={busy === "note"} />
          <Button type="submit" size="sm" disabled={!draft.trim() || busy !== null}>
            {busy === "note" ? t("savingDots") : t("feedback.triage.saveNote")}
          </Button>
        </form>
      </section>

      <section aria-labelledby="feedback-deliveries" className="rounded-2xl border bg-background p-5">
        <h2 id="feedback-deliveries" className="text-sm font-semibold">{t("feedback.triage.deliveries")}</h2>
        {submission.deliveries.length ? (
          <ul className="mt-3 divide-y">
            {submission.deliveries.map((delivery) => (
              <li key={delivery.id} className="flex flex-wrap items-center justify-between gap-3 py-2.5 text-sm">
                <div>
                  <p className="font-medium">{delivery.webhook_name}</p>
                  <p className="text-xs text-muted-foreground">
                    {t("feedback.webhooks.attempts", { count: delivery.attempts })}
                    {delivery.last_error && ` · ${delivery.last_error}`}
                    {delivery.status === "pending" && delivery.next_attempt_at && ` · ${t("feedback.webhooks.nextAttempt", { date: when(delivery.next_attempt_at) })}`}
                  </p>
                </div>
                <div className="flex items-center gap-2">
                  <FeedbackStatusBadge status={delivery.status} label={t(`feedback.webhooks.deliveryStatus.${delivery.status}`)} />
                  {(delivery.status === "failed" || delivery.status === "cancelled") && (
                    <Button size="sm" variant="outline" disabled={busy !== null} onClick={() => void retry(delivery.id)}
                      aria-label={t("feedback.triage.retryNamed", { name: delivery.webhook_name })}>
                      {t("feedback.triage.retry")}
                    </Button>
                  )}
                </div>
              </li>
            ))}
          </ul>
        ) : (
          <p className="mt-2 text-sm text-muted-foreground">{t("feedback.triage.noDeliveries")}</p>
        )}
      </section>
    </div>
  );
}
