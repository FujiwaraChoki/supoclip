"use client";

import { useRef, useState } from "react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";
import { Copy, Download, Loader2, MessageSquare, Send, Upload } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { triggerFileDownload } from "@/lib/clip-export";

/** A real answer is a few kilobytes; this only keeps a wrong file from filling the box. */
export const MAX_ANSWER_BYTES = 500_000;

type Pending = "download" | "copy" | "submit" | null;

function readFileText(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result ?? ""));
    reader.onerror = () => reject(reader.error ?? new Error("Could not read the file"));
    reader.readAsText(file);
  });
}

/** The backend's reason (a string `detail`), or null when the body has none. */
async function failureReason(response: Response): Promise<string | null> {
  try {
    const body = (await response.json()) as { detail?: unknown };
    return typeof body.detail === "string" ? body.detail : null;
  } catch {
    return null;
  }
}

/**
 * LLM=manual: the task waits for the user's own AI chat. Get the prompt (download
 * or copy), run it in a chat, then paste or upload the JSON answer.
 */
export function ManualAnalysisPanel({
  taskId,
  readOnly,
  onSubmitted,
}: {
  taskId: string;
  readOnly: boolean;
  onSubmitted: () => unknown;
}) {
  const t = useTranslations("task.manualAnalysis");
  const [pending, setPending] = useState<Pending>(null);
  const [answer, setAnswer] = useState("");
  const [fileName, setFileName] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const base = `/api/tasks/${encodeURIComponent(taskId)}`;

  async function loadPrompt(): Promise<string> {
    const response = await fetch(`${base}/analysis-prompt`, { cache: "no-store" });
    if (!response.ok) throw new Error((await failureReason(response)) ?? t("promptError"));
    return response.text();
  }

  async function downloadPrompt() {
    setPending("download");
    try {
      const prompt = await loadPrompt();
      const url = URL.createObjectURL(new Blob([prompt], { type: "text/markdown;charset=utf-8" }));
      triggerFileDownload(url, `supoclip-prompt-${taskId.slice(0, 8)}.md`);
      // The download has started by now; release the blob on the next tick.
      setTimeout(() => URL.revokeObjectURL(url), 0);
      toast.success(t("downloaded"));
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("promptError"));
    } finally {
      setPending(null);
    }
  }

  async function copyPrompt() {
    setPending("copy");
    try {
      const prompt = await loadPrompt();
      try {
        await navigator.clipboard.writeText(prompt);
      } catch {
        toast.error(t("copyError"));
        return;
      }
      toast.success(t("copied"));
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("promptError"));
    } finally {
      setPending(null);
    }
  }

  async function chooseFile(file: File | undefined) {
    if (!file) return;
    setError(null);
    if (file.size > MAX_ANSWER_BYTES) {
      setError(t("fileTooLarge"));
      return;
    }
    try {
      setAnswer(await readFileText(file));
      setFileName(file.name);
    } catch {
      setError(t("fileReadError"));
    }
  }

  async function submit() {
    if (!answer.trim() || pending) return;
    setPending("submit");
    setError(null);
    try {
      const response = await fetch(`${base}/analysis`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ response: answer }),
      });
      if (response.status === 409) {
        // Another tab already answered, or the task moved on: show where it is now.
        await onSubmitted();
        return;
      }
      if (!response.ok) {
        // 422 carries the reason (not JSON, cut off, wrong shape): the user can fix it and resend.
        setError((await failureReason(response)) ?? t("submitError"));
        return;
      }
      toast.success(t("submitted"));
      await onSubmitted();
    } catch {
      setError(t("submitError"));
    } finally {
      setPending(null);
    }
  }

  return (
    <section aria-labelledby="manual-analysis-title" className="mx-auto max-w-2xl rounded-2xl border bg-card p-6 sm:p-8">
      <div className="flex items-start gap-4">
        <span className="flex size-11 shrink-0 items-center justify-center rounded-xl bg-muted text-muted-foreground">
          <MessageSquare className="size-5" />
        </span>
        <div>
          <h2 id="manual-analysis-title" className="text-lg font-semibold">{t("title")}</h2>
          <p className="mt-1 text-sm text-muted-foreground">{t("body")}</p>
        </div>
      </div>

      {readOnly ? (
        <p role="status" className="mt-6 text-sm text-muted-foreground">{t("readOnly")}</p>
      ) : (
        <div className="mt-6 space-y-8">
          <div>
            <h3 className="text-sm font-semibold">{t("step1Title")}</h3>
            <p className="mt-1 text-sm text-muted-foreground">{t("step1Body")}</p>
            <div className="mt-3 flex flex-wrap gap-2">
              <Button variant="outline" onClick={() => void downloadPrompt()} disabled={pending !== null}>
                {pending === "download" ? <Loader2 className="size-4 animate-spin" /> : <Download className="size-4" />}
                {t("download")}
              </Button>
              <Button variant="outline" onClick={() => void copyPrompt()} disabled={pending !== null}>
                {pending === "copy" ? <Loader2 className="size-4 animate-spin" /> : <Copy className="size-4" />}
                {t("copy")}
              </Button>
            </div>
            <p className="mt-2 text-xs text-muted-foreground">{t("step1Hint")}</p>
          </div>

          <div>
            <h3 className="text-sm font-semibold">{t("step2Title")}</h3>
            <p className="mt-1 text-sm text-muted-foreground">{t("step2Body")}</p>
            <Textarea
              aria-label={t("pasteLabel")}
              placeholder={t("pastePlaceholder")}
              value={answer}
              onChange={(event) => { setAnswer(event.target.value); setFileName(null); setError(null); }}
              disabled={pending === "submit"}
              rows={8}
              spellCheck={false}
              className="mt-3 font-mono text-xs"
            />
            {error && <p role="alert" className="mt-2 text-sm text-destructive">{error}</p>}
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <input
                ref={fileInput}
                type="file"
                accept=".json,.txt,.md,application/json,text/plain"
                className="sr-only"
                tabIndex={-1}
                aria-hidden="true"
                data-testid="manual-analysis-file"
                onChange={(event) => {
                  void chooseFile(event.target.files?.[0]);
                  event.target.value = "";
                }}
              />
              <Button variant="outline" onClick={() => fileInput.current?.click()} disabled={pending === "submit"}>
                <Upload className="size-4" />{t("upload")}
              </Button>
              <Button onClick={() => void submit()} disabled={!answer.trim() || pending !== null}>
                {pending === "submit" ? <Loader2 className="size-4 animate-spin" /> : <Send className="size-4" />}
                {pending === "submit" ? t("submitting") : t("submit")}
              </Button>
              <span className="text-xs text-muted-foreground">
                {fileName ? t("fileLoaded", { name: fileName }) : answer ? t("chars", { count: answer.length }) : null}
              </span>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
