"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import {
  AlertCircle,
  ArrowLeft,
  Check,
  ChevronDown,
  CloudOff,
  Download,
  Film,
  Keyboard,
  Layers,
  Loader2,
  MoreHorizontal,
  Redo2,
  RotateCcw,
  Undo2,
} from "lucide-react";
import { toast } from "sonner";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { downloadBlob } from "@/lib/clip-actions";
import {
  clamp,
  outputTime,
  splitSegment,
  timecode,
  type CaptionStyle,
} from "@/lib/editor/document";
import { cn } from "@/lib/utils";
import type { TextDraft } from "@/lib/editor/render";
import { editorRequest, useEditor } from "./use-editor";
import { Preview } from "./preview";
import { Timeline } from "./timeline";
import { Inspector, type InspectorTab } from "./inspector";
import { ExportPanel, readyEditor, type ClipSummary } from "./export-panel";
import { AIPrompt } from "./ai-prompt";
import { editorMessage, editorStatus, Keys, Panel, ToolButton } from "./studio-ui";

const SHORTCUTS: [string[], string][] = [
  [["space"], "shortcuts.playPause"],
  [["←", "→"], "shortcuts.frames"],
  [["shift", "←", "→"], "shortcuts.jump"],
  [["I"], "shortcuts.setIn"],
  [["O"], "shortcuts.setOut"],
  [["S"], "shortcuts.split"],
  [["Delete"], "shortcuts.removeBroll"],
  [["mod", "J"], "shortcuts.aiPrompt"],
  [["mod", "Z"], "shortcuts.undo"],
  [["mod", "shift", "Z"], "shortcuts.redo"],
  [["mod", "S"], "shortcuts.save"],
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
  const t = useTranslations("editor");
  const endpoint = `/api/tasks/${taskId}/clips/${clip.id}/editor`;
  const editor = useEditor(endpoint),
    doc = editor.document,
    asset = editor.asset;
  const [tab, setTab] = useState<InspectorTab>("captions"),
    [segmentIndex, setSegmentIndex] = useState(0),
    [time, setTime] = useState(0);
  // Paint timeline typing immediately without adding a history/save entry per key.
  const [timelineTextDraft, setTimelineTextDraft] = useState<TextDraft>();
  const [seekRequest, setSeekRequest] = useState({ time: 0, nonce: 0 });
  const [exportOpen, setExportOpen] = useState(false),
    [helpOpen, setHelpOpen] = useState(false),
    [restoreOpen, setRestoreOpen] = useState(false),
    [reloadOpen, setReloadOpen] = useState(false);
  const [selected, setSelected] = useState<string[]>([]),
    [combining, setCombining] = useState(false);
  const [brollId, setBrollId] = useState<string | null>(null);
  const [clipsOpen, setClipsOpen] = useState(false);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const index = doc ? Math.min(segmentIndex, doc.segments.length - 1) : 0;
  const seek = useCallback((value: number) => {
    setTime(value);
    setSeekRequest({ time: value, nonce: Date.now() });
  }, []);
  // Jump to a source time from B-roll, playing it in the segment that contains it.
  const seekSource = useCallback(
    (value: number) => {
      if (doc && !(doc.segments[index]?.start <= value && value < doc.segments[index]?.end)) {
        const containing = doc.segments.findIndex((s) => s.start <= value && value < s.end);
        if (containing >= 0) setSegmentIndex(containing);
      }
      seek(value);
    },
    [doc, index, seek],
  );
  const selectBroll = useCallback((id: string | null) => {
    setBrollId(id);
    if (id) setTab("broll");
  }, []);
  const switchClip = async (id: string) => {
    if (id === clip.id) return;
    try {
      await editor.save();
    } catch {
      toast.error(t("workspace.keepLocal"));
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
      toast.success(t("workspace.styleApplied"));
    } catch (e) {
      toast.error(editorMessage(t, (e as Error).message));
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
      toast.success(t("workspace.combined"));
    } catch (e) {
      toast.error(editorMessage(t, (e as Error).message));
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
              ? `button[aria-label="${CSS.escape(t("preview.play"))}"]`
              : `button[aria-label="${CSS.escape(t("preview.pause"))}"]`,
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
      if ((key === "delete" || key === "backspace") && brollId) {
        event.preventDefault();
        editor.update((d) => ({ ...d, broll: (d.broll ?? []).filter((b) => b.id !== brollId) }));
        setBrollId(null);
      }
      if (key === "?") setHelpOpen(true);
    };
    window.addEventListener("keydown", handle);
    return () => window.removeEventListener("keydown", handle);
  }, [
    brollId,
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
    t,
  ]);
  const saved = editor.status === "All changes saved",
    saving = editor.status === "Saving…";
  const shortcutKey = (key: string) =>
    key === "space" ? t("keys.space") : key === "shift" ? t("keys.shift") : key === "mod" ? t("keys.mod") : key;
  return (
    // data-editor-studio hides the app's floating feedback button, which would cover the timeline.
    <div data-editor-studio="" className="flex min-h-dvh flex-col bg-stone-100 lg:h-dvh lg:overflow-hidden dark:bg-stone-950">
      <header className="flex shrink-0 flex-wrap items-center gap-x-3 gap-y-2 px-3 py-2 lg:h-14 lg:flex-nowrap lg:py-0">
        <Button variant="ghost" size="icon" className="rounded-full" asChild>
          <Link href={`/tasks/${taskId}`} aria-label={t("editPage.back")}>
            <ArrowLeft />
          </Link>
        </Button>
        <div className="flex min-w-0 flex-1 items-center gap-2">
          <h1 className="min-w-0 truncate text-sm font-semibold">{title}</h1>
          <span aria-hidden className="text-muted-foreground">/</span>
          <Popover open={clipsOpen} onOpenChange={setClipsOpen}>
            <PopoverTrigger asChild>
              <Button variant="ghost" size="sm" className="h-8 shrink-0 gap-1.5 rounded-full bg-background px-3 shadow-[0_1px_2px_rgb(0_0_0/0.05)] ring-1 ring-black/[0.06] hover:bg-background/80 dark:ring-white/10" aria-label={t("workspace.switchClip")}>
                <span
                  aria-hidden
                  className="h-5 w-3 shrink-0 rounded-[2px] bg-muted"
                  style={{
                    backgroundImage: `url(${endpoint}/media/thumbnails.jpg)`,
                    backgroundSize: "auto 100%",
                  }}
                />
                {t("clipLabel", { order: clip.clip_order })}
                <span className="text-muted-foreground tabular-nums">
                  {t("workspace.clipOf", { count: clips.length })}
                </span>
                <ChevronDown className="opacity-60" />
              </Button>
            </PopoverTrigger>
            <PopoverContent align="start" className="w-80 p-0">
              <div className="border-b px-3 py-2 text-xs font-semibold">
                {t("workspace.clips")} <span className="font-normal text-muted-foreground">{clips.length}</span>
              </div>
              <ul aria-label={t("workspace.yourClips")} className="max-h-80 overflow-y-auto p-1">
                {clips.map((c) => {
                  const current = c.id === clip.id,
                    order = selected.indexOf(c.id);
                  return (
                    <li
                      key={c.id}
                      className={cn(
                        "group relative flex items-center gap-2.5 rounded-md px-2 py-1.5 hover:bg-accent",
                        current && "bg-accent",
                      )}
                    >
                      <span
                        aria-hidden
                        className="h-10 w-6 shrink-0 rounded-[3px] bg-muted"
                        style={{
                          backgroundImage: `url(/api/tasks/${taskId}/clips/${c.id}/editor/media/thumbnails.jpg)`,
                          backgroundSize: "auto 100%",
                        }}
                      />
                      <button
                        className="min-w-0 flex-1 text-left outline-none after:absolute after:inset-0 after:rounded-md focus-visible:after:ring-[3px] focus-visible:after:ring-ring/50"
                        onClick={() => {
                          setClipsOpen(false);
                          void switchClip(c.id);
                        }}
                        aria-pressed={current}
                        aria-label={t("workspace.editClip", { order: c.clip_order })}
                      >
                        <span className="flex items-center justify-between gap-2 text-xs">
                          <span className="font-medium">{t("clipLabel", { order: c.clip_order })}</span>
                          <span className="text-muted-foreground tabular-nums">{timecode(c.duration)}</span>
                        </span>
                        <span className="block truncate text-xs text-muted-foreground">
                          {c.text || t("workspace.storyFallback")}
                        </span>
                      </button>
                      <Checkbox
                        aria-label={t("workspace.selectClip", { order: c.clip_order })}
                        title={t("workspace.selectToCombine")}
                        checked={order >= 0}
                        onCheckedChange={(checked) =>
                          setSelected((ids) => (checked ? [...ids, c.id] : ids.filter((id) => id !== c.id)))
                        }
                        className="relative z-10 bg-background"
                      />
                    </li>
                  );
                })}
              </ul>
              <div className="space-y-2 border-t p-3">
                {selected.length >= 2 ? (
                  <Button size="sm" className="w-full" disabled={combining} onClick={() => void combine()}>
                    {combining ? <Loader2 className="animate-spin" /> : <Layers />}
                    {combining ? t("workspace.combining") : t("workspace.combine", { count: selected.length })}
                  </Button>
                ) : null}
                <p className="text-xs text-muted-foreground">
                  {selected.length >= 2
                    ? t("workspace.combineHint")
                    : selected.length === 1
                      ? t("workspace.combineOneMore")
                      : t("workspace.combineEmpty")}
                </p>
              </div>
            </PopoverContent>
          </Popover>
        </div>
        <span
          role="status"
          aria-live="polite"
          className={cn(
            "flex items-center gap-1.5 text-xs text-muted-foreground",
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
          <span className="max-sm:sr-only">{editorStatus(t, editor.status)}</span>
        </span>
        <div className="flex items-center">
          <ToolButton
            label={t("workspace.undo")}
            shortcut={[t("keys.mod"), "Z"]}
            disabled={!editor.canUndo}
            onClick={editor.undo}
          >
            <Undo2 />
          </ToolButton>
          <ToolButton
            label={t("workspace.redo")}
            shortcut={[t("keys.mod"), t("keys.shift"), "Z"]}
            disabled={!editor.canRedo}
            onClick={editor.redo}
          >
            <Redo2 />
          </ToolButton>
          <ToolButton
            label={t("workspace.shortcuts")}
            shortcut="?"
            onClick={() => setHelpOpen(true)}
          >
            <Keyboard />
          </ToolButton>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" size="icon" aria-label={t("workspace.more")}>
                <MoreHorizontal />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-64">
              <DropdownMenuItem disabled={!doc} onSelect={() => setRestoreOpen(true)}>
                <RotateCcw />
                {t("workspace.restore")}
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              <p className="px-2 py-1.5 text-xs text-muted-foreground">{t("workspace.draftNote")}</p>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
        <Button className="rounded-full px-4" disabled={!doc} onClick={() => setExportOpen(true)}>
          <Download />
          {t("workspace.export")}
        </Button>
      </header>
      {(editor.error || editor.storageError) && (
        <div className="shrink-0 space-y-2 border-b px-3 py-2 sm:px-4">
          {editor.error && (
            <Alert variant="destructive" className="border-0 bg-destructive/5 py-2">
              <AlertCircle />
              <AlertDescription className="flex flex-wrap items-center gap-x-4 gap-y-2">
                <span className="mr-auto">{editorMessage(t, editor.error)}</span>
                <span className="flex flex-wrap gap-1">
                  {doc ? (
                    <>
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() => void editor.save().catch(() => {})}
                      >
                        {t("workspace.retrySave")}
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
                        {t("workspace.backup")}
                      </Button>
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => setReloadOpen(true)}
                      >
                        {t("workspace.reloadSaved")}
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
                          toast.error(editorMessage(t, (e as Error).message));
                        }
                      }}
                    >
                      {t("workspace.retryPreparation")}
                    </Button>
                  )}
                </span>
              </AlertDescription>
            </Alert>
          )}
          {editor.storageError && (
            <Alert variant="destructive" className="border-0 bg-destructive/5 py-2">
              <AlertCircle />
              <AlertDescription>{t("workspace.storageFull")}</AlertDescription>
            </Alert>
          )}
        </div>
      )}
      <div className="flex flex-1 flex-col gap-2 px-2 lg:min-h-0 lg:flex-row">
        {doc && asset?.status === "ready" ? (
          <>
            <main className="flex min-w-0 flex-col lg:min-h-0 lg:flex-1">
              <Preview
                doc={doc}
                asset={asset}
                endpoint={endpoint}
                segmentIndex={index}
                select={setSegmentIndex}
                time={time}
                onTime={setTime}
                seekRequest={seekRequest}
                textDraft={timelineTextDraft}
                tab={tab}
                update={editor.update}
                videoRef={videoRef}
                footer={
                  asset.aiEditing ? (
                    <AIPrompt
                      endpoint={endpoint}
                      basis={asset.basis}
                      doc={doc}
                      playhead={time}
                      update={editor.update}
                      undo={editor.undo}
                    />
                  ) : null
                }
              />
            </main>
            <div className="shrink-0 lg:w-[320px] xl:w-[360px] 2xl:w-[400px]">
              <Inspector
                doc={doc}
                asset={asset}
                tab={tab}
                setTab={setTab}
                time={time}
                editTime={outputTime(doc, Math.min(index, doc.segments.length - 1), time)}
                seek={seekSource}
                update={editor.update}
                applyAll={applyAll}
                endpoint={endpoint}
                brollId={brollId}
                selectBroll={selectBroll}
              />
            </div>
          </>
        ) : (
          <Panel className="flex min-h-[420px] flex-1 flex-col items-center justify-center p-8 text-center">
            <div className="mb-5 flex size-14 items-center justify-center rounded-full bg-muted">
              {editor.error ? (
                <AlertCircle className="size-6 text-destructive" />
              ) : (
                <Film className="size-6 text-muted-foreground" />
              )}
            </div>
            <h2 className="font-display text-xl font-bold">
              {editor.error
                ? t("workspace.previewBack")
                : t("workspace.preparingCanvas")}
            </h2>
            <p className="mt-2 max-w-sm text-sm text-muted-foreground">
              {editor.error
                ? t("workspace.previewBackDetail")
                : t("workspace.preparingDetail")}
            </p>
            {!editor.error && (
              <Loader2 className="mt-5 size-5 animate-spin text-muted-foreground" />
            )}
          </Panel>
        )}
      </div>
      {doc && asset?.status === "ready" && (
        <div className={cn("shrink-0 p-2 lg:h-[252px]", doc.hookTitle?.trim() && "lg:h-[284px]")}>
          <Timeline
            pause={() => videoRef.current?.pause()}
            doc={doc}
            asset={asset}
            endpoint={endpoint}
            currentTime={time}
            onTextDraft={setTimelineTextDraft}
            index={index}
            select={setSegmentIndex}
            seek={seek}
            update={editor.update}
            brollId={brollId}
            selectBroll={selectBroll}
            seekBroll={seekSource}
            addBroll={() => setTab("broll")}
          />
        </div>
      )}
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
            <DialogTitle>{t("workspace.shortcuts")}</DialogTitle>
            <DialogDescription>
              {t("workspace.shortcutsDescription")}
            </DialogDescription>
          </DialogHeader>
          <dl className="divide-y rounded-lg border">
            {SHORTCUTS.map(([keys, label]) => (
              <div
                key={label}
                className="flex items-center justify-between gap-3 px-3 py-2 text-sm"
              >
                <dt>{t(label)}</dt>
                <dd>
                  <Keys keys={keys.map(shortcutKey)} />
                </dd>
              </div>
            ))}
          </dl>
        </DialogContent>
      </Dialog>
      <Dialog open={restoreOpen} onOpenChange={setRestoreOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>{t("workspace.restoreTitle")}</DialogTitle>
            <DialogDescription>
              {t("workspace.restoreDescription")}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setRestoreOpen(false)}>
              {t("workspace.keepEditing")}
            </Button>
            <Button
              onClick={() => {
                if (asset) editor.update(() => structuredClone(asset.original));
                setSegmentIndex(0);
                seek(0);
                setRestoreOpen(false);
              }}
            >
              {t("workspace.restore")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <Dialog open={reloadOpen} onOpenChange={setReloadOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>{t("workspace.reloadTitle")}</DialogTitle>
            <DialogDescription>
              {t("workspace.reloadDescription")}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setReloadOpen(false)}>
              {t("workspace.keepLocalDraft")}
            </Button>
            <Button
              onClick={async () => {
                try {
                  await editor.reload();
                  setReloadOpen(false);
                } catch (e) {
                  toast.error(editorMessage(t, (e as Error).message));
                }
              }}
            >
              {t("workspace.loadSaved")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
