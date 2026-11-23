"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import { intlLocale } from "@/i18n/config";
import { ANNOUNCEMENTS_UPDATED_EVENT, type ManagedAnnouncement } from "@/lib/announcements";

type Editor = Omit<ManagedAnnouncement, "id">;

const NO_ANNOUNCEMENTS: ManagedAnnouncement[] = [];
export const managedAnnouncementsKey = ["admin", "announcements"] as const;

const newestFirst = (a: ManagedAnnouncement, b: ManagedAnnouncement) =>
  new Date(b.publishedAt).getTime() - new Date(a.publishedAt).getTime();

function localDate(iso: string) {
  const date = new Date(iso);
  return new Date(date.getTime() - date.getTimezoneOffset() * 60_000).toISOString().slice(0, 16);
}

function emptyEditor(action: string): Editor {
  return { title: "", description: "", href: "", action, isPublished: false, publishedAt: localDate(new Date().toISOString()) };
}

export function AnnouncementManager() {
  const t = useTranslations("admin");
  const dateLocale = intlLocale(useLocale());
  const queryClient = useQueryClient();
  const [saveError, setSaveError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [editor, setEditor] = useState<Editor | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);

  const list = useQuery({
    queryKey: managedAnnouncementsKey,
    queryFn: async () => {
      const response = await fetch("/api/admin/announcements", { cache: "no-store" });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || t("announcements.loadFailed"));
      return data.announcements as ManagedAnnouncement[];
    },
  });
  const items = list.data ?? NO_ANNOUNCEMENTS;
  // A retry after a failed load shows the loading line again, as before.
  const loading = !list.data && (list.isPending || list.isFetching);
  const loadError = list.isError && !list.isFetching
    ? list.error.message || t("announcements.loadFailed")
    : null;
  const error = saveError ?? loadError;
  const load = () => {
    setSaveError(null);
    void list.refetch();
  };

  const saveMutation = useMutation({
    mutationFn: async ({ data, id }: { data: Editor; id: string | null }) => {
      const response = await fetch(id ? `/api/admin/announcements/${encodeURIComponent(id)}` : "/api/admin/announcements", {
        method: id ? "PATCH" : "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(data),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || t("announcements.saveFailed"));
      return id ? { ...data, id } : (result.announcement as ManagedAnnouncement);
    },
    onMutate: () => {
      setSaveError(null);
      setMessage(null);
    },
    onSuccess: (saved) => {
      queryClient.setQueryData<ManagedAnnouncement[]>(managedAnnouncementsKey, (current = NO_ANNOUNCEMENTS) =>
        [saved, ...current.filter(item => item.id !== saved.id)].sort(newestFirst));
      setEditor(null);
      setEditingId(null);
      setMessage(saved.isPublished
        ? new Date(saved.publishedAt).getTime() > Date.now() ? t("announcements.scheduled") : t("announcements.published")
        : t("announcements.draftSaved"));
      // The public banner (useAnnouncements) refetches on this event.
      window.dispatchEvent(new Event(ANNOUNCEMENTS_UPDATED_EVENT));
    },
    onError: (err) => setSaveError(err instanceof Error ? err.message : t("announcements.saveFailed")),
  });
  const saving = saveMutation.isPending;
  const save = (data: Editor, id: string | null) => saveMutation.mutate({ data, id });

  const badge = (item: ManagedAnnouncement) => {
    if (!item.isPublished) return t("announcements.draft");
    if (new Date(item.publishedAt).getTime() > Date.now()) return t("announcements.scheduledBadge");
    return t("announcements.publishedBadge");
  };

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between gap-3">
        <p className="text-sm text-muted-foreground">{t("announcements.intro")}</p>
        <Button disabled={saving || loading} onClick={() => { setEditingId(null); setEditor(emptyEditor(t("announcements.defaultAction"))); setMessage(null); }}>{t("announcements.new")}</Button>
      </div>
      {error && <div role="alert" className="flex items-center gap-3 text-sm text-red-600">{error}{!editor && <Button variant="outline" size="sm" onClick={load}>{t("announcements.retry")}</Button>}</div>}
      {message && <p role="status" className="text-sm text-muted-foreground">{message}</p>}
      {editor && (
        <form className="space-y-4 rounded-2xl border bg-background p-5" onSubmit={event => {
          event.preventDefault();
          void save({ ...editor, publishedAt: new Date(editor.publishedAt).toISOString() }, editingId);
        }}>
          <h2 className="font-semibold">{editingId ? t("announcements.edit") : t("announcements.new")}</h2>
          <label className="block space-y-1.5 text-sm font-medium">{t("announcements.titleLabel")}<Input required maxLength={120} value={editor.title} onChange={event => setEditor({ ...editor, title: event.target.value })} disabled={saving} /></label>
          <label className="block space-y-1.5 text-sm font-medium">{t("announcements.descriptionLabel")}<Textarea required maxLength={2000} value={editor.description} onChange={event => setEditor({ ...editor, description: event.target.value })} disabled={saving} /></label>
          <div className="grid gap-4 sm:grid-cols-2">
            <label className="block space-y-1.5 text-sm font-medium">{t("announcements.destination")}<Input required placeholder="/affiliate" maxLength={500} value={editor.href} onChange={event => setEditor({ ...editor, href: event.target.value })} disabled={saving} /></label>
            <label className="block space-y-1.5 text-sm font-medium">{t("announcements.linkLabel")}<Input required maxLength={80} value={editor.action} onChange={event => setEditor({ ...editor, action: event.target.value })} disabled={saving} /></label>
          </div>
          <label className="block space-y-1.5 text-sm font-medium">{t("announcements.publicationDate")}<Input type="datetime-local" required value={editor.publishedAt} onChange={event => setEditor({ ...editor, publishedAt: event.target.value })} disabled={saving} /><span className="block text-xs font-normal text-muted-foreground">{t("announcements.publicationHint")}</span></label>
          <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={editor.isPublished} onChange={event => setEditor({ ...editor, isPublished: event.target.checked })} disabled={saving} />{t("announcements.publishOnSite")}</label>
          <div className="flex gap-2">
            <Button type="submit" disabled={saving}>{saving ? t("announcements.saving") : editor.isPublished ? t("announcements.saveAndPublish") : t("announcements.saveDraft")}</Button>
            <Button type="button" variant="outline" disabled={saving} onClick={() => { setEditor(null); setEditingId(null); }}>{t("cancel")}</Button>
          </div>
        </form>
      )}
      {loading ? <p className="text-sm text-muted-foreground">{t("announcements.loadingList")}</p> : !items.length ? <p className="rounded-2xl border p-8 text-center text-sm text-muted-foreground">{t("announcements.empty")}</p> : (
        <div className="divide-y overflow-hidden rounded-2xl border bg-background">
          {items.map(item => (
            <article key={item.id} className="space-y-3 p-5">
              <div className="flex flex-wrap items-center gap-2">
                <h2 className="font-semibold">{item.title}</h2>
                <Badge variant={item.isPublished ? "default" : "outline"}>{badge(item)}</Badge>
              </div>
              <p className="whitespace-pre-wrap text-sm text-muted-foreground">{item.description}</p>
              <p className="text-xs text-muted-foreground">{new Date(item.publishedAt).toLocaleString(dateLocale)} · {item.href}</p>
              <div className="flex gap-2">
                <Button variant="outline" size="sm" disabled={saving} onClick={() => { setEditingId(item.id); setEditor({ ...item, publishedAt: localDate(item.publishedAt) }); setMessage(null); }}>{t("announcements.editAction")}</Button>
                <Button variant="outline" size="sm" disabled={saving} onClick={() => void save({ ...item, isPublished: !item.isPublished }, item.id)}>{item.isPublished ? t("announcements.hide") : t("announcements.publish")}</Button>
              </div>
            </article>
          ))}
        </div>
      )}
    </div>
  );
}
