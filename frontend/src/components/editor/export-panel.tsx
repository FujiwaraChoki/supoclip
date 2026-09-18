"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  AlertCircle,
  Cloud,
  Download,
  Loader2,
  Monitor,
  X,
} from "lucide-react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  editDuration,
  outputSize,
  timecode,
  type EditDocument,
  type EditorState,
  type ExportJob,
} from "@/lib/editor/document";
import { exportDraft } from "@/lib/editor/export-draft";
import { editorRequest } from "./use-editor";
import { toast } from "sonner";
import { cn } from "@/lib/utils";
export interface ClipSummary {
  id: string;
  filename: string;
  clip_order: number;
  duration: number;
  text: string;
  video_url: string;
}
interface Job extends ExportJob {
  endpoint: string;
  label: string;
}
interface Props {
  open: boolean;
  setOpen: (open: boolean) => void;
  taskId: string;
  clip: ClipSummary;
  clips: ClipSummary[];
  doc: EditDocument;
  asset: EditorState;
  save: () => Promise<void>;
}
export async function readyEditor(
  endpoint: string,
  signal?: AbortSignal,
): Promise<EditorState> {
  let state = await editorRequest<EditorState>(endpoint);
  if (state.status === "unprepared" || state.status === "failed")
    await editorRequest(`${endpoint}/prepare`, "POST");
  const started = Date.now();
  while (state.status !== "ready") {
    signal?.throwIfAborted();
    if (Date.now() - started > 600_000)
      throw new Error(
        "Source preparation is taking longer than expected. Reopen the editor to check its status.",
      );
    await new Promise((r) => setTimeout(r, 1500));
    state = await editorRequest<EditorState>(endpoint);
    if (state.status === "failed")
      throw new Error(state.error || "Could not prepare clip");
  }
  return state;
}
export function ExportPanel({
  open,
  setOpen,
  taskId,
  clip,
  clips,
  doc,
  asset,
  save,
}: Props) {
  const [preset, setPreset] = useState("tiktok"),
    [mode, setMode] = useState("server"),
    [progress, setProgress] = useState<number | null>(null),
    [busy, setBusy] = useState(false),
    [message, setMessage] = useState("");
  const [browserSupport, setBrowserSupport] = useState<{
    supported: boolean;
    label: string;
  } | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]),
    [error, setError] = useState<string | null>(null);
  const controller = useRef<AbortController | null>(null);
  const endpoint = `/api/tasks/${taskId}/clips/${clip.id}/editor`;
  const size = outputSize(doc, asset.width, asset.height),
    duration = editDuration(doc);
  useEffect(() => {
    if (!open) return;
    let stopped = false;
    setBrowserSupport(null);
    void import("mediabunny")
      .then(async (m) => {
        const video = await m.canEncodeVideo("avc", {
          width: size.width,
          height: size.height,
          bitrate: preset === "reels" ? 12_000_000 : 10_000_000,
        });
        const aac = !asset.hasAudio || (await m.canEncodeAudio("aac"));
        const audio = aac || (await m.canEncodeAudio("opus"));
        if (!stopped)
          setBrowserSupport({
            supported: video && audio,
            label:
              !video || !audio
                ? "Use background export"
                : aac
                  ? "MP4 · keep this editor open"
                  : "MP4 · Opus audio",
          });
      })
      .catch(() => {
        if (!stopped)
          setBrowserSupport({
            supported: false,
            label: "Use background export",
          });
      });
    return () => {
      stopped = true;
    };
  }, [open, size.width, size.height, preset, asset.hasAudio]);
  const refresh = useCallback(async () => {
    const results = await Promise.allSettled(
      clips.map(async (c) => {
        const url = `/api/tasks/${taskId}/clips/${c.id}/editor`;
        const state = await editorRequest<EditorState>(url);
        return (state.jobs || []).map((job) => ({
          ...job,
          endpoint: url,
          label: `Clip ${c.clip_order}`,
        }));
      }),
    );
    const fulfilled = results.filter(
      (r): r is PromiseFulfilledResult<Job[]> => r.status === "fulfilled",
    );
    if (fulfilled.length) setJobs(fulfilled.flatMap((r) => r.value));
  }, [clips, taskId]);
  useEffect(() => {
    if (!open) return;
    void refresh();
    const timer = setInterval(() => void refresh(), 3000);
    return () => clearInterval(timer);
  }, [open, refresh]);
  useEffect(() => () => controller.current?.abort(), []);
  useEffect(() => {
    if (!busy || mode !== "browser") return;
    const warn = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [busy, mode]);
  const start = async (batch: boolean) => {
    setBusy(true);
    setError(null);
    const control = new AbortController();
    controller.current = control;
    try {
      await save();
      if (mode === "browser" && !batch) {
        setProgress(0);
        await exportDraft(
          `${endpoint}/media/clean.mp4`,
          doc,
          asset,
          preset,
          control.signal,
          setProgress,
        );
        toast.success("Video exported");
      } else {
        for (const c of batch ? clips : [clip]) {
          control.signal.throwIfAborted();
          setMessage(`Preparing clip ${c.clip_order}…`);
          const url = `/api/tasks/${taskId}/clips/${c.id}/editor`;
          const state =
            c.id === clip.id ? asset : await readyEditor(url, control.signal);
          control.signal.throwIfAborted();
          await editorRequest(`${url}/exports`, "POST", {
            basis: state.basis,
            preset,
            document: c.id === clip.id ? doc : state.draft.document,
          });
        }
        await refresh();
        toast.success(
          batch
            ? "Clips added to the export queue"
            : "Background export started",
        );
      }
    } catch (e) {
      if (!control.signal.aborted)
        setError(e instanceof Error ? e.message : "Export failed");
      else toast.info("Export cancelled");
    } finally {
      setBusy(false);
      setProgress(null);
      setMessage("");
      controller.current = null;
    }
  };
  const summary = [
    ["Resolution", `${size.width} × ${size.height}`],
    ["Duration", timecode(duration)],
    [
      "Audio",
      !asset.hasAudio
        ? "No audio track"
        : doc.muted
          ? "Muted"
          : `${Math.round(doc.volume * 100)}% volume`,
    ],
    [
      "Estimated size",
      `~${(((preset === "reels" ? 12 : 10) * duration) / 8).toFixed(1)} MB`,
    ],
  ];
  const modes = [
    {
      id: "server",
      Icon: Cloud,
      title: "Background export",
      detail: "Works across browsers",
      disabled: busy,
    },
    {
      id: "browser",
      Icon: Monitor,
      title: "On this device",
      detail: browserSupport?.label || "Checking this browser…",
      disabled: busy || !browserSupport?.supported,
    },
  ];
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogContent className="max-h-[90vh] gap-5 overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle className="font-[var(--font-syne)] text-2xl">
            Ready for the feed
          </DialogTitle>
          <DialogDescription>
            Export your current edit. Background exports keep running when you
            leave.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-2">
          <p className="text-xs font-medium text-muted-foreground">Platform</p>
          <ToggleGroup
            type="single"
            variant="outline"
            aria-label="Export preset"
            className="w-full"
            value={preset}
            disabled={busy}
            onValueChange={(value) => value && setPreset(value)}
          >
            {[
              ["tiktok", "TikTok"],
              ["reels", "Reels"],
              ["shorts", "Shorts"],
            ].map(([id, label]) => (
              <ToggleGroupItem key={id} value={id} className="flex-1">
                {label}
              </ToggleGroupItem>
            ))}
          </ToggleGroup>
        </div>
        <dl className="grid grid-cols-2 gap-x-4 gap-y-3 rounded-lg border bg-muted/40 p-4 text-sm">
          {summary.map(([label, value]) => (
            <div key={label}>
              <dt className="text-xs text-muted-foreground">{label}</dt>
              <dd className="font-medium tabular-nums">{value}</dd>
            </div>
          ))}
        </dl>
        <div className="space-y-2">
          <p className="text-xs font-medium text-muted-foreground">Render on</p>
          <div className="grid grid-cols-2 gap-2">
            {modes.map(({ id, Icon, title, detail, disabled }) => (
              <button
                key={id}
                disabled={disabled}
                aria-pressed={mode === id}
                className={cn(
                  "rounded-lg border p-3 text-left transition-colors outline-none hover:bg-accent focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:pointer-events-none disabled:opacity-50",
                  mode === id && "border-primary ring-1 ring-primary",
                )}
                onClick={() => setMode(id)}
              >
                <Icon className="mb-2 size-4" />
                <span className="block text-sm font-medium">{title}</span>
                <span className="block text-xs text-muted-foreground">
                  {detail}
                </span>
              </button>
            ))}
          </div>
          {mode === "browser" && browserSupport?.label.includes("Opus") && (
            <p className="text-xs text-muted-foreground">
              This browser uses Opus audio in MP4. Background export provides
              wider player compatibility with AAC audio.
            </p>
          )}
        </div>
        {error && (
          <Alert variant="destructive">
            <AlertCircle />
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}
        {busy ? (
          <div className="space-y-2 rounded-lg border p-3">
            <div className="flex items-center justify-between text-sm">
              <span className="flex items-center gap-2">
                <Loader2 className="size-4 animate-spin" />
                {progress !== null
                  ? `Exporting ${progress}%`
                  : message || "Saving draft…"}
              </span>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => controller.current?.abort()}
              >
                Cancel
              </Button>
            </div>
            {progress !== null && (
              <Progress aria-label="Browser export progress" value={progress} />
            )}
          </div>
        ) : (
          <div className="flex flex-col-reverse gap-2 sm:flex-row">
            {clips.length > 1 && (
              <Button variant="outline" onClick={() => void start(true)}>
                Export all {clips.length} clips
              </Button>
            )}
            <Button
              disabled={mode === "browser" && !browserSupport?.supported}
              onClick={() => void start(false)}
              className="sm:flex-1"
            >
              <Download />
              Export clip
            </Button>
          </div>
        )}
        {jobs.length > 0 && (
          <div className="space-y-2 border-t pt-4">
            <h3 className="text-sm font-semibold">Export queue</h3>
            <ul className="divide-y rounded-lg border">
              {jobs.map((job) => (
                <li
                  key={`${job.endpoint}/${job.id}`}
                  className="flex items-center gap-3 p-3 text-sm"
                >
                  <div className="min-w-0 flex-1 space-y-1.5">
                    <p className="flex items-center gap-2 font-medium">
                      {job.label}
                      <span className="font-mono text-xs font-normal text-muted-foreground">
                        {job.id.slice(0, 6)}
                      </span>
                      <Badge
                        variant={
                          job.status === "failed"
                            ? "destructive"
                            : job.status === "completed"
                              ? "default"
                              : "secondary"
                        }
                        className="capitalize"
                      >
                        {job.status === "rendering"
                          ? `${job.progress}%`
                          : job.status}
                      </Badge>
                    </p>
                    {job.error && (
                      <p className="text-xs text-destructive">{job.error}</p>
                    )}
                    {job.status === "rendering" && (
                      <Progress
                        className="h-1.5"
                        value={job.progress}
                        aria-label={`${job.label} export progress`}
                      />
                    )}
                  </div>
                  {job.status === "completed" ? (
                    <Button size="sm" variant="outline" asChild>
                      <a
                        href={`${job.endpoint}/exports/${job.id}/file`}
                        download
                      >
                        <Download />
                        Download
                      </a>
                    </Button>
                  ) : (
                    (job.status === "queued" || job.status === "rendering") && (
                      <Button
                        size="icon-sm"
                        variant="ghost"
                        aria-label={`Cancel ${job.label} export`}
                        onClick={async () => {
                          try {
                            await editorRequest(
                              `${job.endpoint}/exports/${job.id}`,
                              "DELETE",
                            );
                            toast.info("Cancellation requested");
                            await refresh();
                          } catch (e) {
                            setError((e as Error).message);
                          }
                        }}
                      >
                        <X />
                      </Button>
                    )
                  )}
                </li>
              ))}
            </ul>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
