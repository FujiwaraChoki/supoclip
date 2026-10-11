"use client";

import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import { ANNOUNCEMENTS_UPDATED_EVENT, type ManagedAnnouncement } from "@/lib/announcements";

type Editor = Omit<ManagedAnnouncement, "id">;

function localDate(iso: string) {
  const date = new Date(iso);
  return new Date(date.getTime() - date.getTimezoneOffset() * 60_000).toISOString().slice(0, 16);
}

function emptyEditor(): Editor {
  return { title: "", description: "", href: "", action: "Learn more", isPublished: false, publishedAt: localDate(new Date().toISOString()) };
}

export function AnnouncementManager() {
  const [items, setItems] = useState<ManagedAnnouncement[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [editor, setEditor] = useState<Editor | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await fetch("/api/admin/announcements", { cache: "no-store" });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "Unable to load announcements");
      setItems(data.announcements);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to load announcements");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { void load(); }, []);

  const save = async (data: Editor, id: string | null) => {
    setSaving(true);
    setError(null);
    setMessage(null);
    try {
      const response = await fetch(id ? `/api/admin/announcements/${encodeURIComponent(id)}` : "/api/admin/announcements", {
        method: id ? "PATCH" : "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(data),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || "Unable to save announcement");
      const saved: ManagedAnnouncement = id ? { ...data, id } : result.announcement;
      setItems(current => [saved, ...current.filter(item => item.id !== saved.id)]
        .sort((a, b) => new Date(b.publishedAt).getTime() - new Date(a.publishedAt).getTime()));
      setEditor(null);
      setEditingId(null);
      setMessage(saved.isPublished
        ? new Date(saved.publishedAt).getTime() > Date.now() ? "Announcement scheduled." : "Announcement published."
        : "Draft saved. This announcement is hidden from visitors and users.");
      window.dispatchEvent(new Event(ANNOUNCEMENTS_UPDATED_EVENT));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to save announcement");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between gap-3">
        <p className="text-sm text-muted-foreground">Published announcements appear on the landing page and in the app.</p>
        <Button disabled={saving || loading} onClick={() => { setEditingId(null); setEditor(emptyEditor()); setMessage(null); }}>New announcement</Button>
      </div>
      {error && <div role="alert" className="flex items-center gap-3 text-sm text-red-600">{error}{!editor && <Button variant="outline" size="sm" onClick={() => void load()}>Retry</Button>}</div>}
      {message && <p role="status" className="text-sm text-muted-foreground">{message}</p>}
      {editor && (
        <form className="space-y-4 rounded-2xl border bg-background p-5" onSubmit={event => {
          event.preventDefault();
          void save({ ...editor, publishedAt: new Date(editor.publishedAt).toISOString() }, editingId);
        }}>
          <h2 className="font-semibold">{editingId ? "Edit announcement" : "New announcement"}</h2>
          <label className="block space-y-1.5 text-sm font-medium">Title<Input required maxLength={120} value={editor.title} onChange={event => setEditor({ ...editor, title: event.target.value })} disabled={saving} /></label>
          <label className="block space-y-1.5 text-sm font-medium">Description<Textarea required maxLength={2000} value={editor.description} onChange={event => setEditor({ ...editor, description: event.target.value })} disabled={saving} /></label>
          <div className="grid gap-4 sm:grid-cols-2">
            <label className="block space-y-1.5 text-sm font-medium">Destination path<Input required placeholder="/affiliate" maxLength={500} value={editor.href} onChange={event => setEditor({ ...editor, href: event.target.value })} disabled={saving} /></label>
            <label className="block space-y-1.5 text-sm font-medium">Link label<Input required maxLength={80} value={editor.action} onChange={event => setEditor({ ...editor, action: event.target.value })} disabled={saving} /></label>
          </div>
          <label className="block space-y-1.5 text-sm font-medium">Publication date<Input type="datetime-local" required value={editor.publishedAt} onChange={event => setEditor({ ...editor, publishedAt: event.target.value })} disabled={saving} /><span className="block text-xs font-normal text-muted-foreground">Your local time. A future date schedules the announcement.</span></label>
          <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={editor.isPublished} onChange={event => setEditor({ ...editor, isPublished: event.target.checked })} disabled={saving} />Publish on the landing page and in the app</label>
          <div className="flex gap-2">
            <Button type="submit" disabled={saving}>{saving ? "Saving…" : editor.isPublished ? "Save and publish" : "Save draft"}</Button>
            <Button type="button" variant="outline" disabled={saving} onClick={() => { setEditor(null); setEditingId(null); }}>Cancel</Button>
          </div>
        </form>
      )}
      {loading ? <p className="text-sm text-muted-foreground">Loading announcements…</p> : !items.length ? <p className="rounded-2xl border p-8 text-center text-sm text-muted-foreground">No announcements yet. Create a draft to get started.</p> : (
        <div className="divide-y overflow-hidden rounded-2xl border bg-background">
          {items.map(item => (
            <article key={item.id} className="space-y-3 p-5">
              <div className="flex flex-wrap items-center gap-2">
                <h2 className="font-semibold">{item.title}</h2>
                <Badge variant={item.isPublished ? "default" : "outline"}>{!item.isPublished ? "Draft" : new Date(item.publishedAt).getTime() > Date.now() ? "Scheduled" : "Published"}</Badge>
              </div>
              <p className="whitespace-pre-wrap text-sm text-muted-foreground">{item.description}</p>
              <p className="text-xs text-muted-foreground">{new Date(item.publishedAt).toLocaleString()} · {item.href}</p>
              <div className="flex gap-2">
                <Button variant="outline" size="sm" disabled={saving} onClick={() => { setEditingId(item.id); setEditor({ ...item, publishedAt: localDate(item.publishedAt) }); setMessage(null); }}>Edit</Button>
                <Button variant="outline" size="sm" disabled={saving} onClick={() => void save({ ...item, isPublished: !item.isPublished }, item.id)}>{item.isPublished ? "Hide" : "Publish"}</Button>
              </div>
            </article>
          ))}
        </div>
      )}
    </div>
  );
}
