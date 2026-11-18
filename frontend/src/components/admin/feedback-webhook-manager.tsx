"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useLocale, useTranslations } from "next-intl";

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { FeedbackStatusBadge } from "@/components/admin/feedback-badges";
import { intlLocale } from "@/i18n/config";
import { useCopyText } from "@/lib/copy-text";
import {
  FEEDBACK_CATEGORIES,
  WEBHOOK_KINDS,
  apiError,
  type EnvWebhookStatus,
  type FeedbackCategory,
  type FeedbackWebhook,
  type WebhookDelivery,
} from "@/lib/feedback-inbox";

const API = "/api/admin/feedback-webhooks";
const KNOWN_ERRORS = new Set([
  "url_required", "url_too_long", "url_invalid", "url_not_https", "url_has_credentials", "url_private_address",
  "url_unresolvable", "url_not_discord", "categories_required", "invalid_name", "webhook_managed",
]);

type Editor = { name: string; kind: (typeof WEBHOOK_KINDS)[number]; url: string; categories: FeedbackCategory[]; enabled: boolean };
const emptyEditor = (): Editor => ({ name: "", kind: "discord", url: "", categories: [...FEEDBACK_CATEGORIES], enabled: true });

/** Webhook destinations for new feedback (/admin/feedback/webhooks). URLs and secrets are write-only. */
export function FeedbackWebhookManager() {
  const t = useTranslations("admin");
  const dateLocale = intlLocale(useLocale());
  const copy = useCopyText();
  const [webhooks, setWebhooks] = useState<FeedbackWebhook[]>([]);
  const [environment, setEnvironment] = useState<EnvWebhookStatus[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [editor, setEditor] = useState<Editor | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [secret, setSecret] = useState<{ name: string; value: string } | null>(null);
  const [deleting, setDeleting] = useState<FeedbackWebhook | null>(null);
  const [logFor, setLogFor] = useState<string | null>(null);
  const [log, setLog] = useState<WebhookDelivery[] | null>(null);
  const secretRef = useRef<HTMLElement>(null);
  const when = (iso: string) => new Date(iso).toLocaleString(dateLocale, { dateStyle: "medium", timeStyle: "short" });

  const describe = useCallback(async (response: Response, fallback: string) => {
    const { code, message: detail } = await apiError(response);
    if (code && KNOWN_ERRORS.has(code)) return t(`feedback.webhooks.errors.${code}`);
    return detail || fallback;
  }, [t]);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await fetch(API, { cache: "no-store" });
      if (!response.ok) throw new Error(await describe(response, t("feedback.webhooks.loadFailed")));
      const data = (await response.json()) as { webhooks: FeedbackWebhook[]; environment: EnvWebhookStatus[] };
      setWebhooks(data.webhooks);
      setEnvironment(data.environment);
    } catch (err) {
      setError(err instanceof Error ? err.message : t("feedback.webhooks.loadFailed"));
    } finally {
      setLoading(false);
    }
  }, [describe, t]);

  useEffect(() => { void load(); }, [load]);

  const loadLog = useCallback(async (id: string) => {
    setLog(null);
    const response = await fetch(`${API}/${encodeURIComponent(id)}/deliveries`, { cache: "no-store" });
    if (response.ok) setLog(((await response.json()) as { deliveries: WebhookDelivery[] }).deliveries);
    else setLog([]);
  }, []);

  const run = async (key: string, work: () => Promise<void>) => {
    setBusy(key);
    setError(null);
    setMessage(null);
    try {
      await work();
    } catch (err) {
      setError(err instanceof Error ? err.message : t("feedback.webhooks.saveFailed"));
    } finally {
      setBusy(null);
    }
  };

  const save = (event: React.FormEvent) => {
    event.preventDefault();
    if (!editor) return;
    void run("save", async () => {
      const body = editingId
        ? { name: editor.name, categories: editor.categories, enabled: editor.enabled, ...(editor.url.trim() ? { url: editor.url.trim() } : {}) }
        : { ...editor, url: editor.url.trim() };
      const response = await fetch(editingId ? `${API}/${encodeURIComponent(editingId)}` : API, {
        method: editingId ? "PATCH" : "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!response.ok) throw new Error(await describe(response, t("feedback.webhooks.saveFailed")));
      const data = (await response.json()) as { webhook: FeedbackWebhook; secret?: string | null };
      if (data.secret) setSecret({ name: data.webhook.name, value: data.secret });
      setEditor(null);
      setEditingId(null);
      setMessage(editingId ? t("feedback.webhooks.updated") : t("feedback.webhooks.created"));
      await load();
    });
  };

  const toggle = (webhook: FeedbackWebhook, enabled: boolean) => void run(`toggle-${webhook.id}`, async () => {
    const response = await fetch(`${API}/${encodeURIComponent(webhook.id)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled }),
    });
    if (!response.ok) throw new Error(await describe(response, t("feedback.webhooks.saveFailed")));
    setMessage(enabled ? t("feedback.webhooks.enabledMessage", { name: webhook.name }) : t("feedback.webhooks.disabledMessage", { name: webhook.name }));
    await load();
  });

  const test = (webhook: FeedbackWebhook) => void run(`test-${webhook.id}`, async () => {
    const response = await fetch(`${API}/${encodeURIComponent(webhook.id)}/test`, { method: "POST" });
    if (!response.ok) throw new Error(await describe(response, t("feedback.webhooks.testFailed")));
    const { delivery } = (await response.json()) as { delivery: { status: string; status_code: number | null; error: string | null } };
    if (delivery.status === "delivered") setMessage(t("feedback.webhooks.testDelivered", { name: webhook.name, code: delivery.status_code ?? "" }));
    else setError(t("feedback.webhooks.testRejected", { name: webhook.name, reason: delivery.error || "—" }));
    if (logFor === webhook.id) await loadLog(webhook.id);
  });

  const rotate = (webhook: FeedbackWebhook) => void run(`rotate-${webhook.id}`, async () => {
    const response = await fetch(`${API}/${encodeURIComponent(webhook.id)}/rotate-secret`, { method: "POST" });
    if (!response.ok) throw new Error(await describe(response, t("feedback.webhooks.saveFailed")));
    setSecret({ name: webhook.name, value: ((await response.json()) as { secret: string }).secret });
  });

  const remove = (webhook: FeedbackWebhook) => void run(`delete-${webhook.id}`, async () => {
    const response = await fetch(`${API}/${encodeURIComponent(webhook.id)}`, { method: "DELETE" });
    if (!response.ok) throw new Error(await describe(response, t("feedback.webhooks.saveFailed")));
    setDeleting(null);
    setMessage(t("feedback.webhooks.deleted", { name: webhook.name }));
    await load();
  });

  const retry = (deliveryId: string, webhookId: string) => void run(`retry-${deliveryId}`, async () => {
    const response = await fetch(`/api/admin/feedback/deliveries/${encodeURIComponent(deliveryId)}/retry`, { method: "POST" });
    if (!response.ok) throw new Error(await describe(response, t("feedback.triage.retryFailed")));
    setMessage(t("feedback.triage.retryQueued"));
    await loadLog(webhookId);
  });

  const toggleLog = (id: string) => {
    if (logFor === id) {
      setLogFor(null);
      return;
    }
    setLogFor(id);
    void loadLog(id);
  };

  const categoryLabel = (category: FeedbackCategory) => t(`feedback.category.${category}`);
  const pendingEnv = environment.filter((item) => item.configured && !item.imported_at);
  const importedEnv = environment.filter((item) => item.imported_at);

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="max-w-2xl text-sm text-muted-foreground">{t("feedback.webhooks.intro")}</p>
        <Button disabled={busy !== null || loading} onClick={() => { setEditingId(null); setEditor(emptyEditor()); setMessage(null); }}>
          {t("feedback.webhooks.add")}
        </Button>
      </div>

      {(importedEnv.length > 0 || pendingEnv.length > 0) && (
        <div className="rounded-2xl border border-sky-200 bg-sky-50 p-4 text-sm text-sky-950 dark:border-sky-900/60 dark:bg-sky-950/40 dark:text-sky-100">
          {importedEnv.map((item) => (
            <p key={item.env_key}>{t("feedback.webhooks.envImported", { name: item.name, env: item.env_key, date: when(item.imported_at!) })}</p>
          ))}
          {pendingEnv.map((item) => (
            <p key={item.env_key}>{t("feedback.webhooks.envPending", { env: item.env_key })}</p>
          ))}
        </div>
      )}

      <div aria-live="polite" className="text-sm">
        {error && <p role="alert" className="text-red-600">{error}</p>}
        {message && <p role="status" className="text-muted-foreground">{message}</p>}
      </div>

      {secret && (
        <section aria-labelledby="webhook-secret" className="space-y-2 rounded-2xl border border-amber-300 bg-amber-50 p-4 text-sm dark:border-amber-900/60 dark:bg-amber-950/40">
          <h2 id="webhook-secret" className="font-semibold">{t("feedback.webhooks.secretTitle", { name: secret.name })}</h2>
          <p>{t("feedback.webhooks.secretHint")}</p>
          <div className="flex flex-wrap items-center gap-2">
            <code ref={secretRef} className="break-all rounded bg-background px-2 py-1 font-mono text-xs">{secret.value}</code>
            <Button size="sm" variant="outline" onClick={() => void copy(secret.value, secretRef.current)}>{t("feedback.webhooks.copySecret")}</Button>
            <Button size="sm" variant="ghost" onClick={() => setSecret(null)}>{t("feedback.webhooks.secretDone")}</Button>
          </div>
        </section>
      )}

      {editor && (
        <form onSubmit={save} className="space-y-4 rounded-2xl border bg-background p-5" aria-labelledby="webhook-form-title">
          <h2 id="webhook-form-title" className="font-semibold">{editingId ? t("feedback.webhooks.editTitle") : t("feedback.webhooks.addTitle")}</h2>
          <label className="block space-y-1.5 text-sm font-medium">
            {t("feedback.webhooks.name")}
            <Input required maxLength={80} value={editor.name} onChange={(event) => setEditor({ ...editor, name: event.target.value })} disabled={busy === "save"} />
          </label>
          <fieldset className="space-y-1.5" disabled={Boolean(editingId) || busy === "save"}>
            <legend className="text-sm font-medium">{t("feedback.webhooks.type")}</legend>
            <div className="flex flex-wrap gap-4">
              {WEBHOOK_KINDS.map((kind) => (
                <label key={kind} className="flex items-start gap-2 text-sm">
                  <input type="radio" name="webhook-kind" value={kind} checked={editor.kind === kind} onChange={() => setEditor({ ...editor, kind })} className="mt-1" />
                  <span>
                    <span className="font-medium">{t(`feedback.webhooks.kind.${kind}`)}</span>
                    <span className="block text-xs text-muted-foreground">{t(`feedback.webhooks.kindHint.${kind}`)}</span>
                  </span>
                </label>
              ))}
            </div>
          </fieldset>
          <label className="block space-y-1.5 text-sm font-medium">
            {t("feedback.webhooks.url")}
            <Input
              type="url"
              inputMode="url"
              autoComplete="off"
              spellCheck={false}
              required={!editingId}
              maxLength={2048}
              placeholder={editingId ? t("feedback.webhooks.urlKeep") : editor.kind === "discord" ? "https://discord.com/api/webhooks/…" : "https://"}
              value={editor.url}
              onChange={(event) => setEditor({ ...editor, url: event.target.value })}
              disabled={busy === "save"}
            />
            <span className="block text-xs font-normal text-muted-foreground">{t("feedback.webhooks.urlHint")}</span>
          </label>
          <fieldset className="space-y-1.5" disabled={busy === "save"}>
            <legend className="text-sm font-medium">{t("feedback.webhooks.categories")}</legend>
            <div className="flex flex-wrap gap-x-4 gap-y-2">
              {FEEDBACK_CATEGORIES.map((category) => (
                <label key={category} className="flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={editor.categories.includes(category)}
                    onChange={(event) => setEditor({
                      ...editor,
                      categories: event.target.checked
                        ? FEEDBACK_CATEGORIES.filter((value) => value === category || editor.categories.includes(value))
                        : editor.categories.filter((value) => value !== category),
                    })}
                  />
                  {categoryLabel(category)}
                </label>
              ))}
            </div>
          </fieldset>
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={editor.enabled} onChange={(event) => setEditor({ ...editor, enabled: event.target.checked })} disabled={busy === "save"} />
            {t("feedback.webhooks.enabled")}
          </label>
          <div className="flex gap-2">
            <Button type="submit" disabled={busy === "save" || !editor.categories.length}>{busy === "save" ? t("savingDots") : t("feedback.webhooks.save")}</Button>
            <Button type="button" variant="outline" disabled={busy === "save"} onClick={() => { setEditor(null); setEditingId(null); }}>{t("cancel")}</Button>
          </div>
        </form>
      )}

      {loading ? (
        <p className="text-sm text-muted-foreground">{t("feedback.webhooks.loading")}</p>
      ) : !webhooks.length ? (
        <p className="rounded-2xl border p-8 text-center text-sm text-muted-foreground">{t("feedback.webhooks.empty")}</p>
      ) : (
        <ul className="divide-y overflow-hidden rounded-2xl border bg-background">
          {webhooks.map((webhook) => {
            // The Hermes assistant comes from the server's environment: on/off, test and log only.
            const managed = webhook.kind === "hermes";
            return (
              <li key={webhook.id} className="space-y-3 p-5">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <h2 className="font-semibold">{webhook.name}</h2>
                      <Badge variant="outline">{t(`feedback.webhooks.kind.${webhook.kind}`)}</Badge>
                      {!webhook.enabled && <Badge variant="secondary">{t("feedback.webhooks.off")}</Badge>}
                      {webhook.failed > 0 && <Badge variant="destructive">{t("feedback.failedDeliveries", { count: webhook.failed })}</Badge>}
                    </div>
                    {managed
                      ? <p className="mt-1 text-xs text-muted-foreground">{t("feedback.webhooks.serverConfigured")}</p>
                      : <p className="mt-1 font-mono text-xs text-muted-foreground">{webhook.url_hint}</p>}
                  </div>
                  <label className="flex items-center gap-2 text-sm">
                    <Switch
                      checked={webhook.enabled}
                      disabled={busy !== null}
                      onCheckedChange={(value) => toggle(webhook, value)}
                      aria-label={t("feedback.webhooks.enableNamed", { name: webhook.name })}
                    />
                    <span aria-hidden>{webhook.enabled ? t("feedback.webhooks.on") : t("feedback.webhooks.off")}</span>
                  </label>
                </div>
                <div className="flex flex-wrap gap-1.5" aria-label={t("feedback.webhooks.categories")}>
                  {webhook.categories.map((category) => <Badge key={category} variant="secondary">{categoryLabel(category)}</Badge>)}
                </div>
                <p className="text-xs text-muted-foreground">
                  {webhook.last_delivered_at ? t("feedback.webhooks.lastDelivered", { date: when(webhook.last_delivered_at) }) : t("feedback.webhooks.neverDelivered")}
                  {webhook.pending > 0 && ` · ${t("feedback.webhooks.pendingCount", { count: webhook.pending })}`}
                  {webhook.env_source && !managed && ` · ${t("feedback.webhooks.fromEnv", { env: webhook.env_source })}`}
                </p>
                <div className="flex flex-wrap gap-2">
                  <Button size="sm" variant="outline" disabled={busy !== null} onClick={() => test(webhook)}>
                    {busy === `test-${webhook.id}` ? t("feedback.webhooks.testing") : t("feedback.webhooks.test")}
                  </Button>
                  {!managed && (
                    <Button size="sm" variant="outline" disabled={busy !== null} onClick={() => {
                      setEditingId(webhook.id);
                      setEditor({ name: webhook.name, kind: webhook.kind === "json" ? "json" : "discord", url: "", categories: webhook.categories, enabled: webhook.enabled });
                      setMessage(null);
                    }}>{t("feedback.webhooks.edit")}</Button>
                  )}
                  <Button size="sm" variant="outline" aria-expanded={logFor === webhook.id} aria-controls={`log-${webhook.id}`} onClick={() => toggleLog(webhook.id)}>
                    {logFor === webhook.id ? t("feedback.webhooks.hideLog") : t("feedback.webhooks.showLog")}
                  </Button>
                  {webhook.kind === "json" && (
                    <Button size="sm" variant="outline" disabled={busy !== null} onClick={() => rotate(webhook)}>{t("feedback.webhooks.rotate")}</Button>
                  )}
                  {!managed && (
                    <Button size="sm" variant="ghost" className="text-red-600" disabled={busy !== null} onClick={() => setDeleting(webhook)}>{t("feedback.webhooks.delete")}</Button>
                  )}
                </div>
                {logFor === webhook.id && (
                  <div id={`log-${webhook.id}`} className="rounded-xl border">
                    {log === null ? (
                      <p className="p-4 text-sm text-muted-foreground">{t("feedback.webhooks.loading")}</p>
                    ) : !log.length ? (
                      <p className="p-4 text-sm text-muted-foreground">{t("feedback.webhooks.noDeliveries")}</p>
                    ) : (
                      <ul className="divide-y text-sm">
                        {log.map((item) => (
                          <li key={item.id} className="flex flex-wrap items-center justify-between gap-3 px-4 py-2.5">
                            <div>
                              <p>
                                {item.is_test ? t("feedback.webhooks.testDelivery") : item.submission_id ? (
                                  <a className="font-medium hover:underline" href={`/admin/feedback/${item.submission_id}`}>
                                    {item.category ? categoryLabel(item.category) : item.submission_id.slice(0, 8)}
                                  </a>
                                ) : "—"}
                                <span className="text-muted-foreground"> · <time dateTime={item.created_at}>{when(item.created_at)}</time></span>
                              </p>
                              <p className="text-xs text-muted-foreground">
                                {item.attempt_log.map((attempt) => attempt.error || (attempt.status_code ? `HTTP ${attempt.status_code}` : null)).filter(Boolean).join(" → ") || t("feedback.webhooks.attempts", { count: item.attempts })}
                                {item.status === "pending" && item.next_attempt_at && ` · ${t("feedback.webhooks.nextAttempt", { date: when(item.next_attempt_at) })}`}
                              </p>
                            </div>
                            <div className="flex items-center gap-2">
                              <FeedbackStatusBadge status={item.status} label={t(`feedback.webhooks.deliveryStatus.${item.status}`)} />
                              {!item.is_test && (item.status === "failed" || item.status === "cancelled") && (
                                <Button size="sm" variant="outline" disabled={busy !== null} onClick={() => retry(item.id, webhook.id)}>{t("feedback.triage.retry")}</Button>
                              )}
                            </div>
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}

      <AlertDialog open={deleting !== null} onOpenChange={(open) => { if (!open) setDeleting(null); }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t("feedback.webhooks.deleteTitle", { name: deleting?.name ?? "" })}</AlertDialogTitle>
            <AlertDialogDescription>{t("feedback.webhooks.deleteBody")}</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={busy !== null}>{t("cancel")}</AlertDialogCancel>
            <AlertDialogAction
              disabled={busy !== null}
              onClick={(event) => { event.preventDefault(); if (deleting) remove(deleting); }}
            >
              {t("feedback.webhooks.delete")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
