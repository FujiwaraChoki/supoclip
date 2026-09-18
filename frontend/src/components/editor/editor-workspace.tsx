"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import {
  AlertCircle,
  ArrowLeft,
  Check,
  CloudOff,
  Download,
  Film,
  Keyboard,
  Layers,
  Loader2,
  Redo2,
  RotateCcw,
  Undo2,
} from "lucide-react";
import { toast } from "sonner";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Separator } from "@/components/ui/separator";
import { downloadBlob } from "@/lib/clip-actions";
import {
  clamp,
  splitSegment,
  timecode,
  type CaptionStyle,
} from "@/lib/editor/document";
import { cn } from "@/lib/utils";
import { editorRequest, useEditor } from "./use-editor";
import { Preview } from "./preview";
import { Timeline } from "./timeline";
import { Inspector, type InspectorTab } from "./inspector";
import { ExportPanel, readyEditor, type ClipSummary } from "./export-panel";
import { Keys, Panel, PanelHeader, ToolButton } from "./studio-ui";

const SHORTCUTS: [string[], string][] = [
  [["Space"], "Play / pause"],
  [["←", "→"], "Previous / next frame"],
  [["Shift", "←", "→"], "Jump one second"],
  [["I"], "Set in point"],
  [["O"], "Set out point"],
  [["S"], "Split at playhead"],
  [["Ctrl/⌘", "Z"], "Undo"],
  [["Ctrl/⌘", "Shift", "Z"], "Redo"],
  [["Ctrl/⌘", "S"], "Save draft"],
];

// Widgets that own these keys natively; the editor shortcuts must not steal them.
const WIDGET_KEYS: Record<string, string[]> = {
  "[role=tab],[role=radio]": ["arrowleft", "arrowright", " "],
  "[role=switch],[role=checkbox]": [" "],
};

interface Props {
  taskId: string;
  title: string;
  clips: ClipSummary[];
  clip: ClipSummary;
  onSelect: (id: string) => void;
  refreshClips: (id?: string) => Promise<void>;
}
export function EditorWorkspace({
  taskId,
  title,
  clips,
  clip,
  onSelect,
  refreshClips,
}: Props) {
  const endpoint = `/api/tasks/${taskId}/clips/${clip.id}/editor`;
  const editor = useEditor(endpoint),
    doc = editor.document,
    asset = editor.asset;
  const [tab, setTab] = useState<InspectorTab>("captions"),
    [segmentIndex, setSegmentIndex] = useState(0),
    [time, setTime] = useState(0);
  const [seekRequest, setSeekRequest] = useState({ time: 0, nonce: 0 });
  const [exportOpen, setExportOpen] = useState(false),
    [helpOpen, setHelpOpen] = useState(false),
    [restoreOpen, setRestoreOpen] = useState(false),
    [reloadOpen, setReloadOpen] = useState(false);
  const [selected, setSelected] = useState<string[]>([]),
    [combining, setCombining] = useState(false);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const index = doc ? Math.min(segmentIndex, doc.segments.length - 1) : 0;
  const seek = useCallback((value: number) => {
    setTime(value);
    setSeekRequest({ time: value, nonce: Date.now() });
  }, []);
  const switchClip = async (id: string) => {
    if (id === clip.id) return;
    try {
      await editor.save();
    } catch {
      toast.error(
        "Your draft is kept locally. Save it before switching clips.",
      );
      return;
    }
    onSelect(id);
  };
  const applyAll = async (style: CaptionStyle) => {
    try {
      await editor.save();
      for (const c of clips) {
        if (c.id === clip.id) continue;
        const url = `/api/tasks/${taskId}/clips/${c.id}/editor`,
          state = await readyEditor(url);
        await editorRequest(url, "PATCH", {
          basis: state.basis,
          revision: state.draft.revision,
          document: { ...state.draft.document, captions: style },
        });
      }
      toast.success("Caption style applied to all clips");
    } catch (e) {
      toast.error((e as Error).message);
    }
  };
  const combine = async () => {
    setCombining(true);
    try {
      await editor.save();
      for (const id of selected)
        await readyEditor(`/api/tasks/${taskId}/clips/${id}/editor`);
      const result = await editorRequest<{
        id: string;
        anchor_clip_id: string;
      }>(`/api/tasks/${taskId}/editor/combine`, "POST", { clip_ids: selected });
      const started = Date.now();
      let combinedId = "";
      while (!combinedId) {
        const job = await editorRequest<{
          status: string;
          clip_id?: string;
          error?: string;
        }>(
          `/api/tasks/${taskId}/clips/${result.anchor_clip_id}/editor/exports/${result.id}`,
        );
        if (job.status === "failed" || job.status === "cancelled")
          throw new Error(job.error || "Combine cancelled");
        if (job.clip_id) combinedId = job.clip_id;
        else {
          if (Date.now() - started > 600_000)
            throw new Error(
              "The combine is still running in the export queue. Refresh your clips when it completes.",
            );
          await new Promise((r) => setTimeout(r, 1500));
        }
      }
      await refreshClips(combinedId);
      toast.success("Combined draft created. Original clips are unchanged.");
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setCombining(false);
    }
  };
  useEffect(() => {
    const handle = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement;
      const key = event.key.toLowerCase();
      if (
        target.closest(
          "input,textarea,select,[contenteditable=true],[role=dialog],[role=slider],[role=listbox],[role=combobox]",
        ) ||
        Object.entries(WIDGET_KEYS).some(
          ([selector, keys]) => target.closest(selector) && keys.includes(key),
        ) ||
        helpOpen ||
        exportOpen ||
        restoreOpen ||
        reloadOpen ||
        !doc ||
        !asset
      )
        return;
      if (event.metaKey || event.ctrlKey) {
        if (key === "z") {
          event.preventDefault();
          if (event.shiftKey) editor.redo();
          else editor.undo();
        }
        if (key === "y") {
          event.preventDefault();
          editor.redo();
        }
        if (key === "s") {
          event.preventDefault();
          void editor.save().catch(() => {});
        }
        return;
      }
      const segment = doc.segments[index];
      if (event.code === "Space") {
        event.preventDefault();
        document
          .querySelector<HTMLButtonElement>(
            videoRef.current?.paused
              ? 'button[aria-label="Play"]'
              : 'button[aria-label="Pause"]',
          )
          ?.click();
      }
      if (key === "arrowleft" || key === "arrowright") {
        event.preventDefault();
        videoRef.current?.pause();
        seek(
          clamp(
            time +
              (key === "arrowleft" ? -1 : 1) *
                (event.shiftKey ? 1 : 1 / asset.fps),
            0,
            asset.duration,
          ),
        );
      }
      if (key === "i") {
        event.preventDefault();
        editor.update((d) => ({
          ...d,
          segments: d.segments.map((s, i) =>
            i === index
              ? { ...s, start: clamp(time, 0, segment.end - 0.04) }
              : s,
          ),
        }));
      }
      if (key === "o") {
        event.preventDefault();
        editor.update((d) => ({
          ...d,
          segments: d.segments.map((s, i) =>
            i === index
              ? { ...s, end: clamp(time, segment.start + 0.04, asset.duration) }
              : s,
          ),
        }));
      }
      if (key === "s") {
        event.preventDefault();
        editor.update((d) => splitSegment(d, index, time));
      }
      if (key === "?") setHelpOpen(true);
    };
    window.addEventListener("keydown", handle);
    return () => window.removeEventListener("keydown", handle);
  }, [
    asset,
    doc,
    editor,
    index,
    time,
    seek,
    helpOpen,
    exportOpen,
    restoreOpen,
    reloadOpen,
  ]);
  const saved = editor.status === "All changes saved",
    saving = editor.status === "Saving…";
  return (
    <div className="min-h-[calc(100dvh-73px)] bg-muted/40">
      <div className="border-b bg-background">
        <div className="mx-auto flex max-w-[1800px] flex-wrap items-center justify-between gap-x-6 gap-y-3 px-4 py-4 sm:px-6">
          <div className="flex min-w-0 items-center gap-3">
            <Button variant="outline" size="icon" asChild>
              <Link href={`/tasks/${taskId}`} aria-label="Back to generation">
                <ArrowLeft />
              </Link>
            </Button>
            <div className="min-w-0">
              <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
                <Link
                  href={`/tasks/${taskId}`}
                  className="truncate hover:text-foreground"
                >
                  Generation
                </Link>
                <span aria-hidden>/</span>
                <span className="text-foreground">Clip {clip.clip_order}</span>
              </p>
              <h1 className="truncate font-[var(--font-syne)] text-xl font-bold tracking-tight">
                {title}
              </h1>
            </div>
          </div>
          <div className="flex w-full flex-wrap items-center gap-2 sm:w-auto">
            <span
              role="status"
              aria-live="polite"
              className={cn(
                "mr-auto flex items-center gap-1.5 text-xs text-muted-foreground sm:mr-2",
                editor.error && "text-destructive",
              )}
            >
              {saved ? (
                <Check className="size-3.5" />
              ) : saving ? (
                <Loader2 className="size-3.5 animate-spin" />
              ) : editor.error ? (
                <CloudOff className="size-3.5" />
              ) : null}
              {editor.status}
            </span>
            <div className="flex items-center rounded-md border bg-background shadow-xs">
              <ToolButton
                label="Undo"
                shortcut={["Ctrl/⌘", "Z"]}
                className="rounded-r-none"
                disabled={!editor.canUndo}
                onClick={editor.undo}
              >
                <Undo2 />
              </ToolButton>
              <Separator orientation="vertical" className="!h-5" />
              <ToolButton
                label="Redo"
                shortcut={["Ctrl/⌘", "Shift", "Z"]}
                className="rounded-l-none"
                disabled={!editor.canRedo}
                onClick={editor.redo}
              >
                <Redo2 />
              </ToolButton>
            </div>
            <ToolButton
              label="Keyboard shortcuts"
              shortcut="?"
              onClick={() => setHelpOpen(true)}
            >
              <Keyboard />
            </ToolButton>
            <Button disabled={!doc} onClick={() => setExportOpen(true)}>
              <Download />
              Export
            </Button>
          </div>
        </div>
      </div>
      <div className="mx-auto max-w-[1800px] space-y-3 px-4 pt-4 sm:px-6 empty:hidden">
        {editor.error && (
          <Alert variant="destructive">
            <AlertCircle />
            <AlertDescription className="flex flex-wrap items-center gap-x-4 gap-y-2">
              <span className="mr-auto">{editor.error}</span>
              <span className="flex flex-wrap gap-1">
                {doc ? (
                  <>
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => void editor.save().catch(() => {})}
                    >
                      Retry save
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() =>
                        downloadBlob(
                          new Blob([JSON.stringify(doc, null, 2)], {
                            type: "application/json",
                          }),
                          `supoclip-draft-${clip.id}.json`,
                        )
                      }
                    >
                      Back up draft
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => setReloadOpen(true)}
                    >
                      Reload saved version
                    </Button>
                  </>
                ) : (
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={async () => {
                      try {
                        await editorRequest(`${endpoint}/prepare`, "POST");
                        window.location.reload();
                      } catch (e) {
                        toast.error((e as Error).message);
                      }
                    }}
                  >
                    Retry preparation
                  </Button>
                )}
              </span>
            </AlertDescription>
          </Alert>
        )}
        {editor.storageError && (
          <Alert variant="destructive">
            <AlertCircle />
            <AlertDescription>
              Local draft storage is full or unavailable. Keep this tab open
              until the server confirms your changes are saved.
            </AlertDescription>
          </Alert>
        )}
      </div>
      <div className="mx-auto grid max-w-[1800px] grid-cols-1 items-start gap-4 p-4 sm:px-6 md:grid-cols-[minmax(0,1fr)_300px] xl:grid-cols-[232px_minmax(0,1fr)_340px] 2xl:grid-cols-[260px_minmax(0,1fr)_380px]">
        <Panel
          aria-label="Your clips"
          className="md:col-span-2 xl:col-span-1 xl:sticky xl:top-4"
        >
          <PanelHeader title="Clips">
            <Badge variant="secondary" className="tabular-nums">
              {clips.length}
            </Badge>
          </PanelHeader>
          <ul className="flex gap-2 overflow-x-auto p-2 xl:max-h-[calc(100dvh-330px)] xl:flex-col xl:overflow-x-visible xl:overflow-y-auto">
            {clips.map((c) => {
              const active = c.id === clip.id,
                order = selected.indexOf(c.id);
              return (
                <li
                  key={c.id}
                  className={cn(
                    "group relative flex w-60 shrink-0 gap-3 rounded-lg border border-transparent p-2 transition-colors hover:bg-accent xl:w-auto",
                    active && "border-border bg-accent",
                  )}
                >
                  <span
                    aria-hidden
                    className="relative flex h-16 w-10 shrink-0 items-center justify-center overflow-hidden rounded-md bg-muted text-muted-foreground"
                    style={{
                      backgroundImage: `url(/api/tasks/${taskId}/clips/${c.id}/editor/media/thumbnails.jpg)`,
                      backgroundSize: "auto 100%",
                    }}
                  >
                    <Film className="size-4" />
                    {active && (
                      <span className="absolute inset-0 ring-2 ring-primary ring-inset rounded-md" />
                    )}
                  </span>
                  <button
                    className="min-w-0 flex-1 text-left outline-none after:absolute after:inset-0 after:rounded-lg focus-visible:after:ring-[3px] focus-visible:after:ring-ring/50"
                    onClick={() => void switchClip(c.id)}
                    aria-pressed={active}
                    aria-label={`Edit clip ${c.clip_order}`}
                  >
                    <span className="flex items-center justify-between gap-2 text-xs">
                      <span className="font-medium">Clip {c.clip_order}</span>
                      <span className="text-muted-foreground tabular-nums">
                        {timecode(c.duration)}
                      </span>
                    </span>
                    <span className="mt-1 line-clamp-2 text-xs leading-relaxed text-muted-foreground">
                      {c.text || "Your next story starts here"}
                    </span>
                  </button>
                  <span className="relative z-10 flex flex-col items-center justify-between">
                    <Checkbox
                      aria-label={`Select clip ${c.clip_order} for combine`}
                      title="Select to combine"
                      checked={order >= 0}
                      onCheckedChange={(checked) =>
                        setSelected((ids) =>
                          checked
                            ? [...ids, c.id]
                            : ids.filter((id) => id !== c.id),
                        )
                      }
                      className={cn(
                        "bg-background transition-opacity",
                        order < 0 &&
                          "opacity-0 group-hover:opacity-100 focus-visible:opacity-100 [@media(hover:none)]:opacity-100",
                      )}
                    />
                    {order >= 0 && (
                      <span className="text-[10px] font-medium text-muted-foreground tabular-nums">
                        #{order + 1}
                      </span>
                    )}
                  </span>
                </li>
              );
            })}
          </ul>
          <div className="space-y-2 border-t p-3">
            {selected.length >= 2 ? (
              <>
                <Button
                  size="sm"
                  className="w-full"
                  disabled={combining}
                  onClick={() => void combine()}
                >
                  {combining ? (
                    <Loader2 className="animate-spin" />
                  ) : (
                    <Layers />
                  )}
                  {combining ? "Combining…" : `Combine ${selected.length} drafts`}
                </Button>
                <p className="text-xs text-muted-foreground">
                  Uses selection order and the first clip’s style. Originals are
                  kept.
                </p>
              </>
            ) : (
              <p className="text-xs text-muted-foreground">
                {selected.length === 1
                  ? "Select one more clip to combine."
                  : "Tick two or more clips to combine them into one draft."}
              </p>
            )}
            <Separator />
            <Button
              size="sm"
              variant="ghost"
              className="-ml-2"
              disabled={!doc}
              onClick={() => setRestoreOpen(true)}
            >
              <RotateCcw />
              Restore original
            </Button>
            <p className="text-xs text-muted-foreground">
              Generated clips are never overwritten. Every change here is an
              editable draft.
            </p>
          </div>
        </Panel>
        {doc && asset?.status === "ready" ? (
          <>
            <main className="flex min-w-0 flex-col gap-4">
              <Preview
                doc={doc}
                asset={asset}
                endpoint={endpoint}
                segmentIndex={index}
                select={setSegmentIndex}
                time={time}
                onTime={setTime}
                seekRequest={seekRequest}
                tab={tab}
                update={editor.update}
                videoRef={videoRef}
              />
              <Timeline
                doc={doc}
                asset={asset}
                endpoint={endpoint}
                currentTime={time}
                index={index}
                select={setSegmentIndex}
                seek={seek}
                update={editor.update}
              />
            </main>
            <Inspector
              doc={doc}
              asset={asset}
              tab={tab}
              setTab={setTab}
              time={time}
              seek={seek}
              update={editor.update}
              applyAll={applyAll}
            />
          </>
        ) : (
          <Panel className="flex min-h-[420px] flex-col items-center justify-center p-8 text-center md:col-span-2 xl:min-h-[640px]">
            <div className="mb-5 flex size-14 items-center justify-center rounded-full bg-muted">
              {editor.error ? (
                <AlertCircle className="size-6 text-destructive" />
              ) : (
                <Film className="size-6 text-muted-foreground" />
              )}
            </div>
            <h2 className="font-[var(--font-syne)] text-xl font-bold">
              {editor.error
                ? "Let’s get your preview back"
                : "Preparing your editing canvas"}
            </h2>
            <p className="mt-2 max-w-sm text-sm text-muted-foreground">
              {editor.error
                ? "Your original clip is safe. Retry preparation to continue."
                : "Loading the original video, word timings, thumbnails, and audio waveform. This only happens once per clip."}
            </p>
            {!editor.error && (
              <Loader2 className="mt-5 size-5 animate-spin text-muted-foreground" />
            )}
          </Panel>
        )}
      </div>
      <footer className="mx-auto hidden max-w-[1800px] flex-wrap items-center justify-end gap-x-4 gap-y-2 px-6 pb-6 text-xs text-muted-foreground md:flex">
        {(
          [
            ["Space", "Play"],
            ["I", "In"],
            ["O", "Out"],
            ["S", "Split"],
            ["?", "All shortcuts"],
          ] as const
        ).map(([key, label]) => (
          <span key={key} className="flex items-center gap-1.5">
            <Keys keys={key} />
            {label}
          </span>
        ))}
      </footer>
      {doc && asset && (
        <ExportPanel
          open={exportOpen}
          setOpen={setExportOpen}
          taskId={taskId}
          clip={clip}
          clips={clips}
          doc={doc}
          asset={asset}
          save={editor.save}
        />
      )}
      <Dialog open={helpOpen} onOpenChange={setHelpOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Keyboard shortcuts</DialogTitle>
            <DialogDescription>
              Shortcuts work whenever you’re outside a text field.
            </DialogDescription>
          </DialogHeader>
          <dl className="divide-y rounded-lg border">
            {SHORTCUTS.map(([keys, label]) => (
              <div
                key={label}
                className="flex items-center justify-between gap-3 px-3 py-2 text-sm"
              >
                <dt>{label}</dt>
                <dd>
                  <Keys keys={keys} />
                </dd>
              </div>
            ))}
          </dl>
        </DialogContent>
      </Dialog>
      <Dialog open={restoreOpen} onOpenChange={setRestoreOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Restore the original edit?</DialogTitle>
            <DialogDescription>
              This resets cuts, captions, framing, audio, and effects. You can
              undo this action.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setRestoreOpen(false)}>
              Keep editing
            </Button>
            <Button
              onClick={() => {
                if (asset) editor.update(() => structuredClone(asset.original));
                setSegmentIndex(0);
                seek(0);
                setRestoreOpen(false);
              }}
            >
              Restore original
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <Dialog open={reloadOpen} onOpenChange={setReloadOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Load the saved version?</DialogTitle>
            <DialogDescription>
              This replaces local changes and undo history with the server
              draft. Back up your draft first if you want to keep your local
              changes.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setReloadOpen(false)}>
              Keep local draft
            </Button>
            <Button
              onClick={async () => {
                try {
                  await editor.reload();
                  setReloadOpen(false);
                } catch (e) {
                  toast.error((e as Error).message);
                }
              }}
            >
              Load saved version
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
