"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslations } from "next-intl";
import { Loader2, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Kbd } from "@/components/ui/kbd";
import { editDuration, timecode, type EditDocument } from "@/lib/editor/document";
import { cn } from "@/lib/utils";
import { editorRequest } from "./use-editor";

interface AIResult {
  status: "queued" | "running" | "completed" | "failed";
  summary?: string;
  notes?: string[];
  document?: EditDocument;
  changed?: boolean;
  length?: number;
  error?: string;
}

interface Props {
  endpoint: string;
  basis: string;
  doc: EditDocument;
  /** Playhead on the source timeline, so "here" means something. */
  playhead: number;
  update: (change: (doc: EditDocument) => EditDocument, group?: string) => void;
  undo: () => void;
}

const POLL_MS = 900;
const GIVE_UP_MS = 180_000;

/** Command field for AI edits, docked under the viewer. Edits arrive as one undoable change. */
export function AIPrompt({ endpoint, basis, doc, playhead, update, undo }: Props) {
  const t = useTranslations("editor");
  const [text, setText] = useState("");
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<(AIResult & { instruction: string; before: number }) | null>(null);
  const [pending, setPending] = useState<EditDocument | null>(null);
  const [applied, setApplied] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const docRef = useRef(doc);
  docRef.current = doc;
  const cancelled = useRef(false);
  const active = useRef<AbortController | null>(null);

  useEffect(() => {
    const focus = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "j") {
        event.preventDefault();
        input.current?.focus();
      }
    };
    window.addEventListener("keydown", focus);
    return () => window.removeEventListener("keydown", focus);
  }, []);
  useEffect(() => {
    // Reset on mount: React's dev mode mounts, unmounts and mounts again.
    cancelled.current = false;
    return () => {
      cancelled.current = true;
      active.current?.abort();
    };
  }, []);
  // Undo is only offered while the AI's change is still the latest one.
  const canUndo = applied !== null && JSON.stringify(doc) === applied;

  const apply = useCallback(
    (next: EditDocument, expected?: string) => {
      let accepted = false;
      update((current) => {
        if (expected !== undefined && JSON.stringify(current) !== expected) return current;
        accepted = true;
        return next;
      });
      if (accepted) {
        setApplied(JSON.stringify(next));
        setPending(null);
      } else setPending(next);
    },
    [update],
  );

  const send = async () => {
    const instruction = text.trim();
    if (!instruction || active.current) return;
    const controller = new AbortController();
    active.current = controller;
    let timedOut = false;
    const deadline = setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, GIVE_UP_MS);
    const sent = JSON.stringify(docRef.current);
    const before = editDuration(docRef.current);
    setResult(null);
    setPending(null);
    setApplied(null);
    setRunning(true);
    try {
      const { id } = await editorRequest<{ id: string }>(`${endpoint}/ai`, "POST", {
        basis,
        document: docRef.current,
        instruction,
        playhead,
      }, controller.signal);
      controller.signal.throwIfAborted();
      setText("");
      for (;;) {
        await new Promise((resolve) => setTimeout(resolve, POLL_MS));
        controller.signal.throwIfAborted();
        const next = await editorRequest<AIResult>(`${endpoint}/ai/${id}`, "GET", undefined, controller.signal);
        controller.signal.throwIfAborted();
        if (next.status === "completed" || next.status === "failed") {
          setResult({ ...next, instruction, before });
          if (next.status === "completed" && next.changed && next.document) {
            // Edits made while the AI worked are never silently overwritten.
            apply(next.document, sent);
          }
          break;
        }
      }
    } catch (e) {
      if (!cancelled.current) setResult({
        status: "failed",
        error: timedOut ? t("ai.timeout") : e instanceof Error ? e.message : t("ai.failed"),
        instruction,
        before,
      });
    } finally {
      clearTimeout(deadline);
      if (active.current === controller) active.current = null;
      if (!cancelled.current) setRunning(false);
    }
  };

  const lengthChanged =
    result?.status === "completed" &&
    result.changed &&
    typeof result.length === "number" &&
    Math.abs(result.length - result.before) > 0.05;

  return (
    <div className="px-3 pb-3 sm:px-4">
      <form
        className="flex items-center gap-2 rounded-xl bg-stone-100 p-1 pl-3.5 transition-shadow focus-within:ring-2 focus-within:ring-ring/25 dark:bg-stone-900"
        onSubmit={(e) => {
          e.preventDefault();
          void send();
        }}
      >
        <input
          ref={input}
          value={text}
          disabled={running}
          maxLength={1000}
          aria-label={t("ai.label")}
          placeholder={running ? t("ai.working") : t("ai.placeholder")}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Escape") e.currentTarget.blur();
          }}
          className="h-8 min-w-0 flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground disabled:cursor-wait"
        />
        {!text && !running && (
          <Kbd className="max-sm:hidden">⌘J</Kbd>
        )}
        <Button type="submit" size="sm" className="h-8 rounded-lg px-3" disabled={!text.trim() || running}>
          {running && <Loader2 className="animate-spin" />}
          {t("ai.send")}
        </Button>
      </form>
      {result && (
        <div role="status" className="mt-2 flex max-h-32 items-start gap-2 overflow-y-auto px-1 text-xs [overflow-wrap:anywhere]">
          <div className="min-w-0 flex-1 space-y-0.5">
            <p className={cn(result.status === "failed" && "text-destructive")}>
              <span className="text-muted-foreground">“{result.instruction}” — </span>
              {result.status === "failed" ? result.error : result.summary}
              {lengthChanged && (
                <span className="text-muted-foreground tabular-nums">
                  {" "}
                  {t("ai.length", { duration: timecode(result.length!), was: timecode(result.before) })}
                </span>
              )}
            </p>
            {!!result.notes?.length && (
              <p className="text-muted-foreground">{result.notes.join(" · ")}</p>
            )}
            {pending && <p className="text-muted-foreground">{t("ai.editedMeanwhile")}</p>}
          </div>
          <div className="flex shrink-0 items-center gap-1">
            {pending && (
              <Button size="sm" variant="outline" className="h-6 px-2 text-xs" onClick={() => apply(pending)}>
                {t("ai.apply")}
              </Button>
            )}
            {canUndo && (
              <Button
                size="sm"
                variant="outline"
                className="h-6 px-2 text-xs"
                onClick={() => {
                  undo();
                  setApplied(null);
                }}
              >
                {t("ai.undo")}
              </Button>
            )}
            <Button
              size="icon-sm"
              variant="ghost"
              className="size-6"
              aria-label={t("ai.dismiss")}
              onClick={() => {
                setResult(null);
                setPending(null);
              }}
            >
              <X />
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
