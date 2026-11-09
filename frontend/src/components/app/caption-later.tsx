"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";

export type CaptionLaterClip = { id: string; hook_title?: string | null; text?: string | null };
export type CaptionLaterTemplate = { id: string; name: string };

/**
 * For tasks made without captions: pick the clips worth captioning, pick a style,
 * and each chosen clip is re-rendered with it. One request per clip, so a failure
 * names the clip and the rest still finish.
 */
export function CaptionLater({
  taskId,
  clips,
  templates,
  onDone,
}: {
  taskId: string;
  clips: CaptionLaterClip[];
  templates: CaptionLaterTemplate[];
  onDone: () => unknown;
}) {
  const t = useTranslations("task");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [template, setTemplate] = useState(templates[0]?.id ?? "default");
  const [progress, setProgress] = useState<{ done: number; total: number } | null>(null);
  const busy = progress !== null;

  const toggle = (id: string) =>
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  async function run() {
    const ids = clips.filter((clip) => selected.has(clip.id)).map((clip) => clip.id);
    if (!ids.length) return;
    let failed = 0;
    setProgress({ done: 0, total: ids.length });
    for (const [index, id] of ids.entries()) {
      try {
        const response = await fetch(`/api/tasks/${taskId}/clips/${id}/captions`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ caption_template: template }),
        });
        if (!response.ok) failed += 1;
      } catch {
        failed += 1;
      }
      setProgress({ done: index + 1, total: ids.length });
    }
    setProgress(null);
    setSelected(new Set());
    await onDone();
    if (failed) toast.error(t("captionLater.failed", { count: failed }));
    else toast.success(t("captionLater.done", { count: ids.length }));
  }

  return (
    <section className="mb-6 rounded-xl border p-4" aria-label={t("captionLater.title")}>
      <h2 className="text-sm font-semibold">{t("captionLater.title")}</h2>
      <p className="mt-0.5 text-xs text-muted-foreground">{t("captionLater.body")}</p>
      <ul className="mt-3 max-h-48 space-y-1 overflow-y-auto">
        {clips.map((clip, index) => (
          <li key={clip.id}>
            <label className="flex cursor-pointer items-center gap-2 rounded-md px-1 py-1 text-sm hover:bg-accent">
              <input
                type="checkbox"
                checked={selected.has(clip.id)}
                disabled={busy}
                onChange={() => toggle(clip.id)}
              />
              <span className="truncate">{clip.hook_title || clip.text || t("captionLater.clip", { number: index + 1 })}</span>
            </label>
          </li>
        ))}
      </ul>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <select
          value={template}
          disabled={busy}
          onChange={(event) => setTemplate(event.target.value)}
          aria-label={t("captionLater.style")}
          className="h-8 rounded-md border bg-background px-2 text-sm"
        >
          {templates.map((item) => (
            <option key={item.id} value={item.id}>{item.name}</option>
          ))}
        </select>
        <Button size="sm" disabled={busy || selected.size === 0} onClick={() => void run()}>
          {progress
            ? t("captionLater.working", { done: progress.done, total: progress.total })
            : t("captionLater.action", { count: selected.size })}
        </Button>
      </div>
    </section>
  );
}
