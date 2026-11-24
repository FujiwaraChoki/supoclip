"use client";

import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useTranslations } from "next-intl";
import { AnimatePresence, motion } from "motion/react";
import { ArrowUp, FileText, FileVideo, Loader2, Paperclip, Square, X } from "lucide-react";
import { toast } from "sonner";

import { AgentMark } from "@/components/agent/agent-mark";
import { VideoSourceIcon } from "@/components/video-source-icon";
import { firstLink, type Attachment } from "@/lib/agent";
import {
  AttachmentError, MAX_ATTACHMENTS, MAX_INLINE_BYTES, attachmentKind, formatSize, inlineBytes, prepareAttachment,
  type AttachmentKind,
} from "@/lib/agent-attachments";
import { validateVideoSourceUrl } from "@/lib/video-upload";
import { cn } from "@/lib/utils";

type Pending = {
  id: string;
  name: string;
  kind: AttachmentKind;
  size: number;
  status: "working" | "ready";
  /** Shown before the file is ready (images), then the shrunk preview. */
  localPreview?: string;
  attachment?: Attachment;
};

const ACCEPT = "video/*,image/*,application/pdf,.pdf,text/*,.txt,.md,.markdown,.csv,.tsv,.json,.srt,.vtt,.html,.xml,.yaml,.yml";

function hasFiles(event: DragEvent | React.DragEvent) {
  return Array.from(event.dataTransfer?.types ?? []).includes("Files");
}

/** A full-page target while files are dragged over the window. */
function DropOverlay({ visible }: { visible: boolean }) {
  const t = useTranslations("agent");
  if (typeof document === "undefined") return null;
  return createPortal(
    <AnimatePresence>
      {visible && (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.15 }}
          className="pointer-events-none fixed inset-0 z-[80] flex items-center justify-center bg-canvas/70 p-6 backdrop-blur-sm"
        >
          <motion.div
            initial={{ scale: 0.96, y: 8 }}
            animate={{ scale: 1, y: 0 }}
            exit={{ scale: 0.98 }}
            className="flex h-full max-h-[28rem] w-full max-w-2xl flex-col items-center justify-center gap-4 rounded-[2rem] border-2 border-dashed border-foreground/25 bg-background/80 text-center shadow-2xl"
          >
            <AgentMark size="lg" mood="listening" />
            <p className="font-display text-xl font-bold tracking-tight">{t("composer.dropTitle")}</p>
            <p className="max-w-sm text-sm text-muted-foreground">{t("composer.dropBody")}</p>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>,
    document.body,
  );
}

/** What the person types to the agent: text, video links, videos, images, PDFs and text files. Enter sends. */
export function AgentComposer({
  onSend,
  onStop,
  running,
  disabled,
  autoFocus,
  placeholder,
  size = "default",
  seed,
  pageDrop = true,
}: {
  onSend: (message: string, attachments: Attachment[]) => Promise<boolean> | boolean;
  onStop?: () => void;
  running?: boolean;
  disabled?: boolean;
  autoFocus?: boolean;
  placeholder?: string;
  size?: "default" | "large";
  /** Text put in the box from outside (a suggestion), with a counter so the same text can be set twice. */
  seed?: { text: string; n: number } | null;
  /** Files dropped anywhere on the page land here. */
  pageDrop?: boolean;
}) {
  const t = useTranslations("agent");
  const [text, setText] = useState("");
  const [pending, setPending] = useState<Pending[]>([]);
  const [sending, setSending] = useState(false);
  const [dragging, setDragging] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const fileRef = useRef<HTMLInputElement | null>(null);
  const pendingRef = useRef(pending);
  pendingRef.current = pending;

  useEffect(() => {
    if (!seed) return;
    setText(seed.text);
    requestAnimationFrame(() => {
      const element = textareaRef.current;
      if (!element) return;
      element.focus();
      element.setSelectionRange(seed.text.length, seed.text.length);
    });
  }, [seed]);

  useEffect(() => {
    const element = textareaRef.current;
    if (!element) return;
    element.style.height = "0px";
    element.style.height = `${Math.min(element.scrollHeight, 240)}px`;
  }, [text]);

  const addFiles = (files: FileList | File[] | null) => {
    const list = Array.from(files ?? []);
    if (!list.length || disabled) return;
    const room = MAX_ATTACHMENTS - pendingRef.current.length;
    if (room <= 0) {
      toast.error(t("composer.tooMany", { count: MAX_ATTACHMENTS }));
      return;
    }
    if (list.length > room) toast.error(t("composer.tooMany", { count: MAX_ATTACHMENTS }));
    for (const file of list.slice(0, room)) {
      const kind = attachmentKind(file);
      if (!kind) {
        toast.error(t("composer.unsupported", { name: file.name }));
        continue;
      }
      const id = `${Date.now()}-${Math.random().toString(36).slice(2)}-${file.name}`;
      const localPreview = kind === "image" ? URL.createObjectURL(file) : undefined;
      setPending((current) => [...current, { id, name: file.name || t(`composer.kinds.${kind}`), kind, size: file.size, status: "working", localPreview }]);
      prepareAttachment(file)
        .then((attachment) => {
          const inline = pendingRef.current.reduce((sum, p) => sum + (p.attachment ? inlineBytes(p.attachment) : 0), 0) + inlineBytes(attachment);
          if (inline > MAX_INLINE_BYTES) throw new AttachmentError("size", "total");
          setPending((current) => current.map((p) => (p.id === id ? { ...p, status: "ready", attachment } : p)));
        })
        .catch((error: unknown) => {
          setPending((current) => current.filter((p) => p.id !== id));
          if (localPreview) URL.revokeObjectURL(localPreview);
          const reason = error instanceof AttachmentError ? error.reason : null;
          toast.error(
            reason === "size" ? t(error instanceof AttachmentError && error.message === "total" ? "composer.tooBigTotal" : "composer.tooBig", { name: file.name })
              : reason === "type" ? t("composer.unsupported", { name: file.name })
                : error instanceof Error && kind === "video" ? error.message
                  : t("composer.readFailed", { name: file.name }),
          );
        });
    }
  };

  // Files dragged anywhere on the page come here; a counter survives dragleave from child elements.
  useEffect(() => {
    if (!pageDrop || disabled) return;
    let depth = 0;
    const enter = (event: DragEvent) => {
      if (!hasFiles(event)) return;
      event.preventDefault();
      depth += 1;
      setDragging(true);
    };
    const over = (event: DragEvent) => {
      if (!hasFiles(event)) return;
      event.preventDefault();
      if (event.dataTransfer) event.dataTransfer.dropEffect = "copy";
    };
    const leave = (event: DragEvent) => {
      if (!hasFiles(event)) return;
      depth = Math.max(0, depth - 1);
      if (depth === 0) setDragging(false);
    };
    const drop = (event: DragEvent) => {
      if (!hasFiles(event)) return;
      event.preventDefault();
      depth = 0;
      setDragging(false);
      addFiles(event.dataTransfer?.files ?? null);
      textareaRef.current?.focus();
    };
    window.addEventListener("dragenter", enter);
    window.addEventListener("dragover", over);
    window.addEventListener("dragleave", leave);
    window.addEventListener("drop", drop);
    return () => {
      window.removeEventListener("dragenter", enter);
      window.removeEventListener("dragover", over);
      window.removeEventListener("dragleave", leave);
      window.removeEventListener("drop", drop);
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pageDrop, disabled]);

  const remove = (id: string) => {
    setPending((current) => {
      const gone = current.find((p) => p.id === id);
      if (gone?.localPreview) URL.revokeObjectURL(gone.localPreview);
      return current.filter((p) => p.id !== id);
    });
  };

  const link = firstLink(text);
  const linkSource = link ? validateVideoSourceUrl(link).source ?? null : null;
  const working = pending.some((p) => p.status === "working");
  const ready = pending.filter((p) => p.status === "ready" && p.attachment);
  const canSend = !disabled && !sending && !running && !working && (text.trim().length > 0 || ready.length > 0);

  const send = async () => {
    if (!canSend) return;
    const attachments = ready.map((p) => p.attachment!);
    const onlyVideos = attachments.every((a) => a.kind === "video");
    const message = text.trim() || (onlyVideos ? t("composer.clipThis") : t("composer.lookAtThis"));
    setSending(true);
    try {
      const ok = await onSend(message, attachments);
      if (ok) {
        setText("");
        pending.forEach((p) => p.localPreview && URL.revokeObjectURL(p.localPreview));
        setPending([]);
      }
    } finally {
      setSending(false);
    }
  };

  return (
    <>
      {pageDrop && <DropOverlay visible={dragging} />}
      <div
        className={cn(
          "relative rounded-[1.35rem] border bg-background shadow-[0_1px_2px_rgb(0_0_0/0.04),0_8px_24px_-12px_rgb(0_0_0/0.12)] transition-shadow focus-within:border-foreground/25 focus-within:shadow-[0_1px_2px_rgb(0_0_0/0.04),0_12px_32px_-12px_rgb(0_0_0/0.18)]",
          disabled && "opacity-60",
        )}
        onDragOver={(event) => { if (!pageDrop && hasFiles(event)) event.preventDefault(); }}
        onDrop={(event) => { if (!pageDrop && hasFiles(event)) { event.preventDefault(); addFiles(event.dataTransfer.files); } }}
      >
        {(pending.length > 0 || linkSource) && (
          <div className="flex flex-wrap items-end gap-2 px-3 pt-3">
            {linkSource && (
              <span className="inline-flex h-8 max-w-full items-center gap-1.5 rounded-lg bg-muted px-2 text-xs">
                <VideoSourceIcon source={linkSource} className="size-3.5" />
                <span className="truncate">{t("composer.videoLink")}</span>
              </span>
            )}
            <AnimatePresence initial={false}>
              {pending.map((item) => (
                <motion.span
                  key={item.id}
                  layout
                  initial={{ opacity: 0, scale: 0.9 }}
                  animate={{ opacity: 1, scale: 1 }}
                  exit={{ opacity: 0, scale: 0.9 }}
                  transition={{ duration: 0.18 }}
                  className="group relative"
                >
                  {item.kind === "image" ? (
                    <span className="relative block size-16 overflow-hidden rounded-xl border bg-muted">
                      {(item.attachment?.kind === "image" ? item.attachment.preview : item.localPreview) && (
                        // eslint-disable-next-line @next/next/no-img-element
                        <img src={item.attachment?.kind === "image" ? item.attachment.preview : item.localPreview} alt="" className="size-full object-cover" />
                      )}
                      {item.status === "working" && (
                        <span className="absolute inset-0 flex items-center justify-center bg-background/60"><Loader2 className="size-4 animate-spin" /></span>
                      )}
                    </span>
                  ) : (
                    <span className="flex h-12 max-w-56 items-center gap-2 rounded-xl border bg-muted/50 pl-2 pr-3">
                      <span className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-background">
                        {item.status === "working" ? <Loader2 className="size-3.5 animate-spin" /> : item.kind === "video" ? <FileVideo className="size-3.5" /> : <FileText className="size-3.5" />}
                      </span>
                      <span className="min-w-0 text-left">
                        <span className="block truncate text-xs font-medium">{item.name}</span>
                        <span className="block text-[10px] uppercase tracking-wide text-muted-foreground">
                          {t(`composer.kinds.${item.kind}`)} · {item.status === "working" && item.kind === "video" ? t("composer.uploading") : formatSize(item.size)}
                        </span>
                      </span>
                    </span>
                  )}
                  <button
                    type="button"
                    aria-label={t("composer.remove")}
                    onClick={() => remove(item.id)}
                    className="absolute -right-1.5 -top-1.5 flex size-5 items-center justify-center rounded-full border bg-background text-muted-foreground shadow-xs transition-colors hover:text-foreground"
                  >
                    <X className="size-3" />
                  </button>
                </motion.span>
              ))}
            </AnimatePresence>
          </div>
        )}
        <textarea
          ref={textareaRef}
          value={text}
          autoFocus={autoFocus}
          rows={1}
          disabled={disabled}
          onChange={(event) => setText(event.target.value)}
          onPaste={(event) => {
            const files = Array.from(event.clipboardData.files);
            if (files.length) {
              event.preventDefault();
              addFiles(files);
            }
          }}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
              event.preventDefault();
              void send();
            }
          }}
          placeholder={placeholder ?? t("composer.placeholder")}
          aria-label={t("composer.label")}
          className={cn(
            "block w-full resize-none bg-transparent px-4 text-[0.9375rem] outline-none placeholder:text-muted-foreground/80",
            size === "large" ? "min-h-[3.75rem] pt-4" : "min-h-[2.75rem] pt-3",
          )}
        />
        <div className="flex items-center justify-between gap-2 px-2.5 pb-2.5 pt-1">
          <div className="flex items-center gap-1">
            <input ref={fileRef} type="file" accept={ACCEPT} multiple hidden onChange={(event) => { addFiles(event.target.files); event.target.value = ""; }} />
            <button
              type="button"
              onClick={() => fileRef.current?.click()}
              disabled={disabled}
              className="inline-flex h-8 items-center gap-1.5 rounded-full px-2.5 text-xs font-medium text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
            >
              <Paperclip className="size-3.5" />{t("composer.attach")}
            </button>
          </div>
          {running ? (
            <button
              type="button"
              onClick={onStop}
              aria-label={t("composer.stop")}
              className="flex size-8 items-center justify-center rounded-full bg-foreground text-background transition-transform hover:scale-105"
            >
              <Square className="size-3 fill-current" />
            </button>
          ) : (
            <button
              type="button"
              onClick={() => void send()}
              disabled={!canSend}
              aria-label={t("composer.send")}
              className="flex size-8 items-center justify-center rounded-full bg-foreground text-background transition-all hover:scale-105 disabled:scale-100 disabled:bg-muted disabled:text-muted-foreground"
            >
              {sending || working ? <Loader2 className="size-4 animate-spin" /> : <ArrowUp className="size-4" />}
            </button>
          )}
        </div>
      </div>
    </>
  );
}
