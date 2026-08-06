"use client";

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useRouter } from "next/navigation";
import {
  ArrowLeft,
  Check,
  ChevronLeft,
  ChevronRight,
  Cloud,
  CloudOff,
  Crop,
  Download,
  Library,
  Loader2,
  Maximize2,
  MousePointer2,
  Pause,
  Play,
  Redo2,
  RotateCcw,
  Save,
  Scissors,
  SlidersHorizontal,
  Undo2,
} from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

import {
  deleteEditorAsset,
  EditorVersionConflictError,
  saveEditorProject,
  uploadEditorAsset,
} from "./editor-api";
import { exportProject, downloadBlob } from "./editor-export";
import { EditorInspector } from "./editor-inspector";
import { EditorPreview } from "./editor-preview";
import { EditorTimeline } from "./editor-timeline";
import {
  duplicateItem,
  formatTime,
  normalizeProject,
  projectDuration,
  splitItem,
} from "./editor-utils";
import { MediaPanel } from "./media-panel";
import type {
  EditorAsset,
  EditorProject,
  TimelineItem,
  TimelineItemType,
  TimelineTrack,
} from "./types";
import { useEditorHistory } from "./use-editor-history";

interface EditorShellProps {
  taskId: string;
  initialProject: EditorProject;
  initialVersion: number;
  assets: EditorAsset[];
  transcriptByAssetId: Record<string, string>;
}

type SaveState = "saved" | "dirty" | "saving" | "error";

const DEFAULT_EFFECTS = {
  brightness: 100,
  contrast: 100,
  saturation: 100,
  blur: 0,
  hue: 0,
};

const DEFAULT_CROP = { top: 0, right: 0, bottom: 0, left: 0 };

function editorItemId() {
  return `item-${globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`}`;
}

function fitAssetTransform(asset: EditorAsset, project: EditorProject, fullFrame: boolean) {
  if (fullFrame || !asset.width || !asset.height) {
    return { x: 50, y: 50, width: fullFrame ? 100 : 68, height: fullFrame ? 100 : 68, rotation: 0 };
  }
  const canvasAspect = project.canvas.width / project.canvas.height;
  const assetAspect = asset.width / asset.height;
  if (assetAspect > canvasAspect) {
    const width = 72;
    const height = Math.max(12, width * (canvasAspect / assetAspect));
    return { x: 50, y: 50, width, height, rotation: 0 };
  }
  const height = 72;
  const width = Math.max(12, height * (assetAspect / canvasAspect));
  return { x: 50, y: 50, width, height, rotation: 0 };
}

function makeBaseItem(
  type: TimelineItemType,
  name: string,
  track: TimelineTrack,
  start: number,
  duration: number,
): TimelineItem {
  return {
    id: editorItemId(),
    type,
    name,
    track,
    start,
    duration,
    trimStart: 0,
    speed: 1,
    volume: 100,
    muted: false,
    hidden: false,
    locked: false,
    opacity: 100,
    blendMode: "normal",
    transform: { x: 50, y: 50, width: 70, height: 24, rotation: 0 },
    crop: { ...DEFAULT_CROP },
    effects: { ...DEFAULT_EFFECTS },
    fadeIn: 0,
    fadeOut: 0,
  };
}

function boundItemToAsset(item: TimelineItem, assets: readonly EditorAsset[]) {
  if (!item.assetId || (item.type !== "video" && item.type !== "audio")) return item;
  const asset = assets.find((candidate) => candidate.id === item.assetId);
  if (!asset || !(asset.duration > 0)) return item;
  const speed = Math.min(4, Math.max(0.25, item.speed));
  const trimStart = Math.min(Math.max(0, item.trimStart), Math.max(0, asset.duration - 0.05));
  const maximumDuration = Math.max(0.05, (asset.duration - trimStart) / speed);
  return {
    ...item,
    speed,
    trimStart,
    duration: Math.min(item.duration, maximumDuration),
  };
}

function isTextEditingTarget(target: EventTarget | null) {
  return target instanceof HTMLInputElement
    || target instanceof HTMLTextAreaElement
    || target instanceof HTMLSelectElement
    || (target instanceof HTMLElement && target.isContentEditable);
}

export function EditorShell({
  taskId,
  initialProject,
  initialVersion,
  assets: initialAssets,
  transcriptByAssetId,
}: EditorShellProps) {
  const router = useRouter();
  const history = useEditorHistory(initialProject);
  const { project } = history;
  const [assets, setAssets] = useState(initialAssets);
  const [selectedItemId, setSelectedItemId] = useState<string | null>(project.items[0]?.id ?? null);
  const [currentTime, setCurrentTime] = useState(0);
  const [isPlaying, setIsPlaying] = useState(false);
  const [saveState, setSaveState] = useState<SaveState>("saved");
  const [saveError, setSaveError] = useState<string | null>(null);
  const [isUploading, setIsUploading] = useState(false);
  const [deletingAssetId, setDeletingAssetId] = useState<string | null>(null);
  const [uploadProgress, setUploadProgress] = useState<number | null>(null);
  const [exportProgress, setExportProgress] = useState<number | null>(null);
  const [isExporting, setIsExporting] = useState(false);
  const [cropMode, setCropMode] = useState(false);
  const [mobilePanel, setMobilePanel] = useState<"media" | "inspector" | null>(null);
  const [isLeaving, setIsLeaving] = useState(false);
  const [isSmallScreen, setIsSmallScreen] = useState(false);
  const serverVersionRef = useRef(initialVersion);
  const currentTimeRef = useRef(0);
  const playbackAnchorRef = useRef({ time: 0, wallTime: 0 });
  const latestProjectRef = useRef(project);
  const savedFingerprintRef = useRef(JSON.stringify(initialProject));
  const saveQueueRef = useRef<Promise<boolean>>(Promise.resolve(true));
  const exportAbortRef = useRef<AbortController | null>(null);
  const firstSaveEffectRef = useRef(true);
  const nativeBackPendingRef = useRef(false);
  const bypassPopstateRef = useRef(false);
  const backFallbackTimerRef = useRef<number | null>(null);

  const selectedItem = useMemo(
    () => project.items.find((item) => item.id === selectedItemId) ?? null,
    [project.items, selectedItemId],
  );

  useEffect(() => {
    const mediaQuery = window.matchMedia("(max-width: 767px)");
    const update = () => setIsSmallScreen(mediaQuery.matches);
    update();
    mediaQuery.addEventListener("change", update);
    return () => mediaQuery.removeEventListener("change", update);
  }, []);

  useEffect(() => {
    latestProjectRef.current = project;
  }, [project]);

  useEffect(() => {
    currentTimeRef.current = currentTime;
  }, [currentTime]);

  useEffect(() => {
    if (selectedItemId && !project.items.some((item) => item.id === selectedItemId)) {
      setSelectedItemId(null);
    }
    const activeSelection = project.items.find((item) => item.id === selectedItemId);
    if (cropMode && activeSelection?.type !== "video" && activeSelection?.type !== "image") {
      setCropMode(false);
    }
    if (currentTime > project.duration) {
      setCurrentTime(project.duration);
      currentTimeRef.current = project.duration;
    }
  }, [cropMode, currentTime, project.duration, project.items, selectedItemId]);

  const persistProject = useCallback((force = false) => {
    saveQueueRef.current = saveQueueRef.current.then(async () => {
      let forceNext = force;
      while (true) {
        const snapshot = latestProjectRef.current;
        const fingerprint = JSON.stringify(snapshot);
        if (!forceNext && fingerprint === savedFingerprintRef.current) {
          setSaveState("saved");
          return true;
        }
        setSaveState("saving");
        setSaveError(null);
        try {
          const result = await saveEditorProject(taskId, snapshot, serverVersionRef.current);
          serverVersionRef.current = result.version;
          savedFingerprintRef.current = fingerprint;
        } catch (error) {
          const message = error instanceof Error ? error.message : "Failed to save the project";
          setSaveState("error");
          setSaveError(message);
          if (error instanceof EditorVersionConflictError) toast.error(message);
          return false;
        }

        if (JSON.stringify(latestProjectRef.current) === fingerprint) {
          setSaveState("saved");
          return true;
        }
        setSaveState("dirty");
        forceNext = false;
      }
    });
    return saveQueueRef.current;
  }, [taskId]);

  useEffect(() => {
    if (firstSaveEffectRef.current) {
      firstSaveEffectRef.current = false;
      return;
    }
    const fingerprint = JSON.stringify(project);
    if (fingerprint === savedFingerprintRef.current) {
      setSaveState("saved");
      return;
    }
    setSaveState("dirty");
    const timeout = window.setTimeout(() => void persistProject(), 900);
    return () => window.clearTimeout(timeout);
  }, [persistProject, project]);

  useEffect(() => {
    if (saveState === "saved") return;
    const warnBeforeLeaving = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", warnBeforeLeaving);
    return () => window.removeEventListener("beforeunload", warnBeforeLeaving);
  }, [saveState]);

  useEffect(() => {
    if (!isPlaying) return;
    let animationFrame = 0;
    const tick = (now: number) => {
      const elapsed = (now - playbackAnchorRef.current.wallTime) / 1000;
      const next = playbackAnchorRef.current.time + elapsed;
      if (next >= latestProjectRef.current.duration) {
        const end = latestProjectRef.current.duration;
        currentTimeRef.current = end;
        setCurrentTime(end);
        setIsPlaying(false);
        return;
      }
      currentTimeRef.current = next;
      setCurrentTime(next);
      animationFrame = requestAnimationFrame(tick);
    };
    animationFrame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(animationFrame);
  }, [isPlaying]);

  const seek = useCallback((time: number) => {
    const bounded = Math.min(latestProjectRef.current.duration, Math.max(0, time));
    currentTimeRef.current = bounded;
    setCurrentTime(bounded);
    if (isPlaying) playbackAnchorRef.current = { time: bounded, wallTime: performance.now() };
  }, [isPlaying]);

  const togglePlay = useCallback(() => {
    setIsPlaying((playing) => {
      if (playing) return false;
      const start = currentTimeRef.current >= latestProjectRef.current.duration
        ? 0
        : currentTimeRef.current;
      currentTimeRef.current = start;
      setCurrentTime(start);
      playbackAnchorRef.current = { time: start, wallTime: performance.now() };
      return true;
    });
  }, []);

  const updateItem = useCallback((itemId: string, updates: Partial<TimelineItem>) => {
    history.updateProject((current) => ({
      ...current,
      items: current.items.map((item) => item.id === itemId ? boundItemToAsset({ ...item, ...updates }, assets) : item),
    }));
  }, [assets, history]);

  const updateProject = useCallback((updates: Partial<EditorProject>) => {
    history.updateProject((current) => ({ ...current, ...updates }));
  }, [history]);

  const addItem = useCallback((item: TimelineItem) => {
    history.updateProject((current) => {
      const next = { ...current, items: [...current.items, item] };
      return { ...next, duration: projectDuration(next) };
    });
    setSelectedItemId(item.id);
    seek(item.start);
  }, [history, seek]);

  const addAssetToTimeline = useCallback((asset: EditorAsset) => {
    const currentProject = latestProjectRef.current;
    const start = Math.min(currentTimeRef.current, currentProject.duration);
    const hasMainAtTime = currentProject.items.some(
      (item) => item.track === "main" && start >= item.start && start < item.start + item.duration,
    );
    const track: TimelineTrack = asset.kind === "audio"
      ? "audio"
      : asset.kind === "video" && !hasMainAtTime
        ? "main"
        : "overlay";
    const fullFrame = track === "main" && asset.kind === "video";
    const duration = asset.kind === "image"
      ? Math.min(5, Math.max(1, currentProject.duration - start || 5))
      : Math.max(0.1, Math.min(asset.duration || 5, Math.max(asset.duration || 5, currentProject.duration - start)));
    const item: TimelineItem = {
      ...makeBaseItem(asset.kind, asset.name, track, start, duration),
      assetId: asset.id,
      transform: fitAssetTransform(asset, currentProject, fullFrame),
    };
    addItem(item);
  }, [addItem]);

  const addText = useCallback((preset: "heading" | "subheading" | "body" | "cta") => {
    const configs = {
      heading: { name: "Heading", content: "Your headline", fontSize: 112, fontWeight: 900, width: 82, height: 18, background: "transparent" },
      subheading: { name: "Subheading", content: "Add a subheading", fontSize: 72, fontWeight: 700, width: 74, height: 14, background: "transparent" },
      body: { name: "Body text", content: "Write something worth watching.", fontSize: 48, fontWeight: 500, width: 68, height: 16, background: "transparent" },
      cta: { name: "Call to action", content: "FOLLOW FOR MORE", fontSize: 52, fontWeight: 900, width: 62, height: 11, background: "#000000" },
    }[preset];
    const start = currentTimeRef.current;
    const item = makeBaseItem("text", configs.name, "text", start, Math.min(5, Math.max(1, project.duration - start || 5)));
    item.transform = { x: 50, y: preset === "cta" ? 82 : 26, width: configs.width, height: configs.height, rotation: 0 };
    item.text = {
      content: configs.content,
      fontFamily: "Inter",
      fontSize: configs.fontSize,
      fontWeight: configs.fontWeight,
      color: "#ffffff",
      backgroundColor: configs.background,
      align: "center",
      letterSpacing: preset === "cta" ? 2 : -1,
      lineHeight: 1.05,
      strokeColor: "#000000",
      strokeWidth: 0,
    };
    addItem(item);
  }, [addItem, project.duration]);

  const addCaption = useCallback(() => {
    const selectedMedia = selectedItem?.assetId ? selectedItem : project.items.find((item) => item.type === "video");
    const content = selectedMedia?.assetId ? transcriptByAssetId[selectedMedia.assetId] : "";
    const start = selectedMedia?.start ?? currentTimeRef.current;
    const duration = selectedMedia?.duration ?? Math.min(8, Math.max(2, project.duration - start || 8));
    const item = makeBaseItem("caption", "Dynamic captions", "text", start, duration);
    item.transform = { x: 50, y: 78, width: 88, height: 14, rotation: 0 };
    item.text = {
      content: content || "Edit your caption text here",
      fontFamily: "Inter",
      fontSize: 72,
      fontWeight: 900,
      color: "#ffffff",
      backgroundColor: "#00000099",
      align: "center",
      letterSpacing: -1,
      lineHeight: 1.05,
      strokeColor: "#000000",
      strokeWidth: 2,
    };
    addItem(item);
  }, [addItem, project.duration, project.items, selectedItem, transcriptByAssetId]);

  const addShape = useCallback((kind: "rectangle" | "circle" | "line") => {
    const start = currentTimeRef.current;
    const item = makeBaseItem("shape", `${kind[0].toUpperCase()}${kind.slice(1)}`, "overlay", start, Math.min(5, Math.max(1, project.duration - start || 5)));
    item.transform = {
      x: 50,
      y: 50,
      width: kind === "line" ? 70 : kind === "circle" ? 28 : 58,
      height: kind === "line" ? 1.2 : kind === "circle" ? 28 : 20,
      rotation: 0,
    };
    item.shape = { kind, fill: "#ffffff", borderRadius: kind === "rectangle" ? 24 : 0 };
    addItem(item);
  }, [addItem, project.duration]);

  const deleteSelected = useCallback(() => {
    if (!selectedItemId) return;
    history.updateProject((current) => ({ ...current, items: current.items.filter((item) => item.id !== selectedItemId) }));
    setSelectedItemId(null);
  }, [history, selectedItemId]);

  const duplicateSelected = useCallback(() => {
    if (!selectedItem) return;
    const copy = duplicateItem(selectedItem, 0.25);
    addItem(copy);
  }, [addItem, selectedItem]);

  const splitSelected = useCallback(() => {
    if (!selectedItem) return;
    try {
      const [left, right] = splitItem(selectedItem, currentTimeRef.current);
      history.updateProject((current) => {
        const index = current.items.findIndex((item) => item.id === selectedItem.id);
        if (index < 0) return current;
        const items = [...current.items];
        items.splice(index, 1, left, right);
        return { ...current, items };
      });
      setSelectedItemId(right.id);
    } catch {
      toast.error("Move the playhead inside the selected layer before splitting.");
    }
  }, [history, selectedItem]);

  const handleUpload = useCallback(async (files: File[]) => {
    setIsUploading(true);
    setUploadProgress(0);
    try {
      for (let index = 0; index < files.length; index += 1) {
        const asset = await uploadEditorAsset(taskId, files[index], (fileProgress) => {
          setUploadProgress(Math.round(((index + fileProgress / 100) / files.length) * 100));
        });
        setAssets((current) => [...current, asset]);
        addAssetToTimeline(asset);
      }
      toast.success(`${files.length} ${files.length === 1 ? "asset" : "assets"} uploaded and added to the timeline.`);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Upload failed");
    } finally {
      setIsUploading(false);
      setUploadProgress(null);
    }
  }, [addAssetToTimeline, taskId]);

  const handleDeleteAsset = useCallback(async (asset: EditorAsset) => {
    if (deletingAssetId) return;
    const used = project.items.some((item) => item.assetId === asset.id);
    if (used && !window.confirm(`Delete “${asset.name}” and every timeline layer using it?`)) return;
    setDeletingAssetId(asset.id);
    try {
      const saved = await persistProject(true);
      if (!saved || savedFingerprintRef.current !== JSON.stringify(latestProjectRef.current)) {
        throw new Error("The project could not be saved, so the asset was not deleted.");
      }

      const result = await deleteEditorAsset(
        taskId,
        asset.id,
        serverVersionRef.current,
      );
      if (!result.project || typeof result.version !== "number") {
        throw new Error("The server returned an incomplete media deletion result. Refresh the editor before continuing.");
      }
      const nextProject = normalizeProject(result.project);
      serverVersionRef.current = result.version;
      latestProjectRef.current = nextProject;
      savedFingerprintRef.current = JSON.stringify(nextProject);
      history.setProject(nextProject);
      setSaveState("saved");
      setAssets((current) => current.filter((candidate) => candidate.id !== asset.id));
      toast.success("Asset deleted.");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Failed to delete asset");
    } finally {
      setDeletingAssetId(null);
    }
  }, [deletingAssetId, history, persistProject, project.items, taskId]);

  const handleExport = useCallback(async () => {
    if (isExporting) {
      exportAbortRef.current?.abort();
      return;
    }
    setIsPlaying(false);
    setIsExporting(true);
    setExportProgress(0);
    const controller = new AbortController();
    exportAbortRef.current = controller;
    try {
      const saved = await persistProject(true);
      if (!saved || savedFingerprintRef.current !== JSON.stringify(latestProjectRef.current)) {
        throw new Error("Save the project successfully before exporting.");
      }
      const blob = await exportProject(latestProjectRef.current, assets, {
        signal: controller.signal,
        onProgress: (progress) => setExportProgress(Math.round(progress * 100)),
      });
      downloadBlob(blob, `${project.name.replace(/[^a-z0-9-_]+/gi, "_") || "supoclip"}.mp4`);
      toast.success("Export ready.");
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") toast.message("Export canceled.");
      else toast.error(error instanceof Error ? error.message : "Export failed");
    } finally {
      exportAbortRef.current = null;
      setIsExporting(false);
      setExportProgress(null);
    }
  }, [assets, isExporting, persistProject, project.name]);

  // Both the header "back" button and the native browser Back button need to leave
  // via the same two-entry history traversal, since mount pushes a synthetic marker
  // entry on top of the editor's own entry (see the navigation-guard effect below).
  // A plain router.push here would stack a new entry instead of consuming those two,
  // leaving the marker in history so a subsequent native Back reopens the editor.
  const navigateBackFromEditor = useCallback(() => {
    bypassPopstateRef.current = true;
    const guardedUrl = window.location.href;
    window.history.go(-2);
    if (backFallbackTimerRef.current !== null) window.clearTimeout(backFallbackTimerRef.current);
    backFallbackTimerRef.current = window.setTimeout(() => {
      if (window.location.href === guardedUrl) router.replace(`/tasks/${taskId}`);
    }, 350);
  }, [router, taskId]);

  const handleBack = useCallback(async () => {
    if (isLeaving) return;
    setIsLeaving(true);
    const saved = await persistProject(true);
    if (saved && savedFingerprintRef.current === JSON.stringify(latestProjectRef.current)) {
      navigateBackFromEditor();
      return;
    }
    setIsLeaving(false);
    toast.error("The project could not be saved. Fix the save error before leaving.");
  }, [isLeaving, navigateBackFromEditor, persistProject]);

  useEffect(() => {
    const markerKey = "__supoEditorNavigationGuard";
    const marker = { ...(window.history.state ?? {}), [markerKey]: taskId };
    if (window.history.state?.[markerKey] !== taskId) {
      window.history.pushState(marker, "", window.location.href);
    }

    const onPopState = () => {
      if (bypassPopstateRef.current) {
        bypassPopstateRef.current = false;
        return;
      }

      window.history.pushState(marker, "", window.location.href);
      if (nativeBackPendingRef.current) return;
      nativeBackPendingRef.current = true;
      setIsLeaving(true);

      void persistProject(true).then((saved) => {
        if (saved && savedFingerprintRef.current === JSON.stringify(latestProjectRef.current)) {
          navigateBackFromEditor();
          return;
        }

        nativeBackPendingRef.current = false;
        setIsLeaving(false);
        toast.error("The project could not be saved, so navigation was canceled.");
      });
    };

    window.addEventListener("popstate", onPopState);
    return () => {
      window.removeEventListener("popstate", onPopState);
      if (backFallbackTimerRef.current !== null) window.clearTimeout(backFallbackTimerRef.current);
    };
  }, [navigateBackFromEditor, persistProject, taskId]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (deletingAssetId) return;
      if (isTextEditingTarget(event.target)) return;
      const command = event.metaKey || event.ctrlKey;
      if (event.code === "Space") {
        event.preventDefault();
        togglePlay();
      } else if (command && event.key.toLowerCase() === "z") {
        event.preventDefault();
        if (event.shiftKey) history.redo();
        else history.undo();
      } else if (command && event.key.toLowerCase() === "y") {
        event.preventDefault();
        history.redo();
      } else if (command && event.key.toLowerCase() === "d") {
        event.preventDefault();
        duplicateSelected();
      } else if (command && event.key.toLowerCase() === "s") {
        event.preventDefault();
        void persistProject(true);
      } else if (!command && event.key.toLowerCase() === "s") {
        event.preventDefault();
        splitSelected();
      } else if (event.key === "Delete" || event.key === "Backspace") {
        event.preventDefault();
        deleteSelected();
      } else if (event.key === "ArrowLeft") {
        event.preventDefault();
        seek(currentTimeRef.current - 1 / project.canvas.fps);
      } else if (event.key === "ArrowRight") {
        event.preventDefault();
        seek(currentTimeRef.current + 1 / project.canvas.fps);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [deleteSelected, deletingAssetId, duplicateSelected, history, persistProject, project.canvas.fps, seek, splitSelected, togglePlay]);

  const saveLabel = saveState === "saving"
    ? "Saving…"
    : saveState === "dirty"
      ? "Unsaved"
      : saveState === "error"
        ? "Save failed"
        : "Saved";

  if (isSmallScreen) {
    return (
      <div className="flex h-dvh min-h-[480px] flex-col items-center justify-center bg-[#0f0f0f] p-6 text-white">
        <div className="w-full max-w-xs rounded-xl border border-white/10 bg-[#141414] p-6 text-center shadow-2xl">
          <div className="mx-auto mb-4 flex size-10 items-center justify-center rounded-lg border border-white/10 bg-white/5 text-zinc-300">
            <Scissors className="size-5" />
          </div>
          <h2 className="text-base font-semibold text-white">The editor needs a larger screen</h2>
          <p className="mt-2 text-sm text-zinc-400">
            Switch to a tablet or desktop to edit this project.
          </p>
          <Button
            type="button"
            className="mt-5 w-full bg-white text-black hover:bg-zinc-200"
            onClick={navigateBackFromEditor}
          >
            <ArrowLeft />
            Back to task
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div className="relative flex h-dvh min-h-[720px] min-w-0 flex-col overflow-hidden bg-[#0f0f0f] text-white">
      <header className="flex h-14 shrink-0 items-center border-b border-white/10 bg-[#141414] px-3">
        <Button type="button" variant="ghost" size="icon-sm" title="Save and return to task" disabled={isLeaving} className="mr-2 text-zinc-400 hover:bg-white/10 hover:text-white" onClick={() => void handleBack()}>
          {isLeaving ? <Loader2 className="animate-spin" /> : <ArrowLeft />}
        </Button>
        <div className="mr-3 flex size-7 items-center justify-center rounded-lg border border-white/10 bg-white/5 text-zinc-300">
          <Scissors className="size-3.5" />
        </div>
        <div className="min-w-0">
          <input
            value={project.name}
            aria-label="Project name"
            className="select-text block w-[150px] truncate border-0 bg-transparent text-sm font-semibold text-white outline-none placeholder:text-zinc-600 sm:w-[220px] xl:w-[260px]"
            onChange={(event) => updateProject({ name: event.target.value })}
          />
          <button
            type="button"
            className={cn(
              "mt-0.5 flex items-center gap-1 text-[9px] font-medium",
              saveState === "error" ? "text-red-400" : saveState === "dirty" ? "text-zinc-400" : "text-zinc-500",
            )}
            title={saveError ?? undefined}
            onClick={() => void persistProject(true)}
          >
            {saveState === "saving" ? <Loader2 className="size-2.5 animate-spin" /> : saveState === "error" ? <CloudOff className="size-2.5" /> : saveState === "saved" ? <Check className="size-2.5" /> : <Cloud className="size-2.5" />}
            {saveLabel}
          </button>
        </div>

        <div className="mx-auto hidden items-center gap-1 rounded-lg border border-white/10 bg-black/20 p-1 sm:flex">
          <Button type="button" variant="ghost" size="icon-sm" title="Undo (⌘Z)" disabled={!history.canUndo} className="size-7 text-zinc-400 hover:bg-white/10 hover:text-white" onClick={history.undo}>
            <Undo2 />
          </Button>
          <Button type="button" variant="ghost" size="icon-sm" title="Redo (⇧⌘Z)" disabled={!history.canRedo} className="size-7 text-zinc-400 hover:bg-white/10 hover:text-white" onClick={history.redo}>
            <Redo2 />
          </Button>
          <div className="mx-0.5 h-4 w-px bg-white/10" />
          <span className="px-2 font-mono text-[10px] text-zinc-500">{project.canvas.width} × {project.canvas.height}</span>
        </div>

        <Button type="button" variant="ghost" size="icon-sm" title="Media library" className="mr-1 text-zinc-400 hover:bg-white/10 hover:text-white min-[1100px]:hidden" onClick={() => setMobilePanel((current) => current === "media" ? null : "media")}>
          <Library />
        </Button>
        <Button type="button" variant="ghost" size="icon-sm" title="Inspector" className="mr-2 text-zinc-400 hover:bg-white/10 hover:text-white min-[1100px]:hidden" onClick={() => setMobilePanel((current) => current === "inspector" ? null : "inspector")}>
          <SlidersHorizontal />
        </Button>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="mr-2 border border-white/10 bg-white/5 text-zinc-300 hover:bg-white/10 hover:text-white"
          onClick={() => void persistProject(true)}
          disabled={saveState === "saving"}
        >
          <Save />
          Save
        </Button>
        <Button
          type="button"
          size="sm"
          className={cn(
            "min-w-28 bg-white text-black hover:bg-zinc-200",
            isExporting && "bg-red-600 text-white hover:bg-red-500",
          )}
          onClick={() => void handleExport()}
        >
          {isExporting ? <Loader2 className="animate-spin" /> : <Download />}
          {isExporting ? (exportProgress === null ? "Preparing" : `${exportProgress}% · Cancel`) : "Export MP4"}
        </Button>
      </header>

      <div className="flex min-h-0 flex-1">
        <MediaPanel
          className={cn(
            "max-[1099px]:fixed max-[1099px]:bottom-[280px] max-[1099px]:left-0 max-[1099px]:top-14 max-[1099px]:z-[120] max-[1099px]:shadow-2xl",
            mobilePanel !== "media" && "max-[1099px]:hidden",
          )}
          assets={assets}
          isUploading={isUploading}
          uploadProgress={uploadProgress}
          onUpload={(files) => void handleUpload(files)}
          onAddAsset={addAssetToTimeline}
          onDeleteAsset={(asset) => void handleDeleteAsset(asset)}
          onAddText={addText}
          onAddCaption={addCaption}
          onAddShape={addShape}
        />

        <main className="flex min-w-0 flex-1 flex-col">
          <div className="flex h-11 shrink-0 items-center gap-1 border-b border-white/10 bg-[#141414] px-3">
            <Button type="button" variant="ghost" size="sm" className="h-7 bg-white/5 text-[11px] text-zinc-300 hover:bg-white/10 hover:text-white">
              <MousePointer2 />
              Select
            </Button>
            <div className="mx-1 h-4 w-px bg-white/10" />
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="h-7 text-[11px] text-zinc-500 hover:bg-white/10 hover:text-white"
              disabled={!selectedItem || (selectedItem.type !== "video" && selectedItem.type !== "image")}
              onClick={() => selectedItem && updateItem(selectedItem.id, { crop: { ...DEFAULT_CROP } })}
            >
              <RotateCcw />
              Reset crop
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className={cn(
                "h-7 text-[11px] hover:bg-white/10 hover:text-white",
                cropMode ? "bg-white/15 text-white" : "text-zinc-500",
              )}
              disabled={!selectedItem || (selectedItem.type !== "video" && selectedItem.type !== "image")}
              onClick={() => setCropMode((current) => !current)}
            >
              <Crop />
              {cropMode ? "Finish crop" : "Crop"}
            </Button>
            <span className="ml-auto flex items-center gap-1.5 text-[10px] text-zinc-600">
              <Maximize2 className="size-3" />
              Drag layers · resize corners
            </span>
          </div>

          <div className="min-h-0 flex-1">
            <EditorPreview
              project={project}
              assets={assets}
              currentTime={currentTime}
              isPlaying={isPlaying}
              selectedItemId={selectedItemId}
              cropMode={cropMode}
              onSelectItem={setSelectedItemId}
              onUpdateItem={updateItem}
              onBeginTransform={history.beginTransaction}
              onEndTransform={history.endTransaction}
            />
          </div>

          <div className="flex h-12 shrink-0 items-center justify-center gap-1 border-t border-white/10 bg-[#141414]">
            <Button type="button" variant="ghost" size="icon-sm" title="Previous frame" className="text-zinc-400 hover:bg-white/10 hover:text-white" onClick={() => seek(currentTime - 1 / project.canvas.fps)}>
              <ChevronLeft />
            </Button>
            <Button type="button" size="icon" title="Play / pause (Space)" className="mx-2 rounded-full bg-white text-black hover:bg-zinc-200" onClick={togglePlay}>
              {isPlaying ? <Pause className="fill-current" /> : <Play className="ml-0.5 fill-current" />}
            </Button>
            <Button type="button" variant="ghost" size="icon-sm" title="Next frame" className="text-zinc-400 hover:bg-white/10 hover:text-white" onClick={() => seek(currentTime + 1 / project.canvas.fps)}>
              <ChevronRight />
            </Button>
            <button
              type="button"
              className="ml-3 rounded-md border border-white/10 bg-black/20 px-2 py-1 font-mono text-[11px] text-zinc-300"
              onClick={() => seek(0)}
            >
              {formatTime(currentTime, true)} <span className="text-zinc-600">/ {formatTime(project.duration, true)}</span>
            </button>
          </div>
        </main>

        <EditorInspector
          className={cn(
            "max-[1099px]:fixed max-[1099px]:bottom-[280px] max-[1099px]:right-0 max-[1099px]:top-14 max-[1099px]:z-[120] max-[1099px]:shadow-2xl",
            mobilePanel !== "inspector" && "max-[1099px]:hidden",
          )}
          project={project}
          selectedItem={selectedItem}
          onUpdateItem={updateItem}
          onUpdateProject={updateProject}
          onBeginTransform={history.beginTransaction}
          onEndTransform={history.endTransaction}
          onDuplicate={duplicateSelected}
          onDelete={deleteSelected}
        />
      </div>

      <EditorTimeline
        project={project}
        currentTime={currentTime}
        selectedItemId={selectedItemId}
        onSeek={seek}
        onSelectItem={setSelectedItemId}
        onUpdateItem={updateItem}
        onBeginTransform={history.beginTransaction}
        onEndTransform={history.endTransaction}
        onSplit={splitSelected}
        onDuplicate={duplicateSelected}
        onDelete={deleteSelected}
      />
      {deletingAssetId ? (
        <div className="absolute inset-0 z-[300] flex items-center justify-center bg-black/55 backdrop-blur-[2px]">
          <div className="flex items-center gap-3 rounded-xl border border-white/10 bg-[#161616] px-4 py-3 text-sm font-medium shadow-2xl">
            <Loader2 className="size-4 animate-spin text-zinc-300" />
            Safely removing media…
          </div>
        </div>
      ) : null}
    </div>
  );
}
