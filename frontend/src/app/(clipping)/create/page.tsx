"use client";

import { useState, useRef, useEffect, useCallback } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Progress } from "@/components/ui/progress";
import { Separator } from "@/components/ui/separator";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Card, CardContent } from "@/components/ui/card";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Slider } from "@/components/ui/slider";
import { formatSupportMessage, parseApiError } from "@/lib/api-error";
import { buildFontOptionsPayload, FONT_SIZE_OPTIONS, FONT_TEMPLATE_DEFAULT_VALUE } from "@/lib/font-options";
import { DEFAULT_HOOK_STYLE, hookStylePayload, type HookAnimation, type HookPosition, type HookStyle } from "@/lib/hook-style";
import { splitHookIntoHighlightSpans } from "@/lib/hook-highlight";
import { TemplatePicker, type TemplateInfo } from "@/components/template-picker";
import { PresetPromptDialog } from "@/components/batch/preset-prompt-dialog";
import { takePendingFile } from "@/lib/pending-file-transfer";
import {
  DEFAULT_BROLL_SETTINGS,
  DEFAULT_SOCIAL_OVERLAY,
  brollSettingsPayload,
  socialOverlayPayload,
  type BrollSettings,
  type SocialOverlay,
  type TargetDuration,
} from "@/lib/retention-settings";
import {
  deletePreset,
  listPresets,
  loadLastSettings,
  savePreset,
  saveLastSettings,
  type TaskGenerationSettings,
  type TaskPreset,
} from "@/lib/task-presets";
import Link from "next/link";
import {
  ArrowLeft,
  Youtube,
  CheckCircle,
  AlertCircle,
  Loader2,
  Palette,
  Type,
  Sparkles,
  Upload,
  Monitor,
  Settings,
  Scissors,
  Film,
  Music,
} from "lucide-react";

interface FontOption {
  name: string;
  display_name: string;
  format?: string;
}

type OutputFormat = "vertical" | "vertical_pan" | "vertical_split" | "original";
type Tab = "source" | "style" | "hook" | "retention" | "cleanup" | "output";

const MAX_VIDEO_UPLOAD_BYTES = 12_000_000_000;
const FONT_SEARCH_THRESHOLD = 8;

type DirectUploadAuthorization = { directUpload: true; uploadUrl: string; headers: Record<string, string> };
type ProxyUploadAuthorization = { directUpload: false; reason: "signed_backend_auth_required" };
type UploadAuthorization = DirectUploadAuthorization | ProxyUploadAuthorization;

const extractYouTubeVideoId = (value: string): string | null => {
  const input = value.trim();
  if (!input) return null;
  try {
    const parsed = new URL(input);
    const host = parsed.hostname.replace(/^www\./, "");
    if (host === "youtu.be") {
      const id = parsed.pathname.split("/").filter(Boolean)[0];
      return id && id.length === 11 ? id : null;
    }
    if (host === "youtube.com" || host === "m.youtube.com" || host === "music.youtube.com") {
      const fromSearch = parsed.searchParams.get("v");
      if (fromSearch && fromSearch.length === 11) return fromSearch;
      const pathParts = parsed.pathname.split("/").filter(Boolean);
      const embedId = pathParts[0] === "embed" ? pathParts[1] : null;
      if (embedId && embedId.length === 11) return embedId;
    }
  } catch {
    return null;
  }
  return null;
};

const getYouTubeThumbnailUrl = (value: string): string | null => {
  const videoId = extractYouTubeVideoId(value);
  return videoId ? `https://i.ytimg.com/vi/${videoId}/hqdefault.jpg` : null;
};

async function requestUploadAuthorization(): Promise<UploadAuthorization> {
  const response = await fetch("/api/upload/authorization", { method: "POST", cache: "no-store" });
  if (!response.ok) {
    const uploadError = await parseApiError(response, `Upload authorization error: ${response.status}`);
    throw new Error(formatSupportMessage(uploadError));
  }
  return response.json() as Promise<UploadAuthorization>;
}

async function uploadVideoFileViaProxy(file: File): Promise<string> {
  const formData = new FormData();
  formData.append("video", file);
  const uploadResponse = await fetch("/api/upload", { method: "POST", body: formData });
  if (!uploadResponse.ok) {
    const fallbackMessage =
      uploadResponse.status === 413
        ? "Uploaded file is too large. Please upload a video under 12 GB."
        : `Upload error: ${uploadResponse.status}`;
    const uploadError = await parseApiError(uploadResponse, fallbackMessage);
    throw new Error(formatSupportMessage(uploadError));
  }
  const uploadResult = await uploadResponse.json();
  if (typeof uploadResult.video_path !== "string" || !uploadResult.video_path) {
    throw new Error("Upload finished without a video path. Please try again.");
  }
  return uploadResult.video_path;
}

async function uploadVideoFile(file: File): Promise<string> {
  if (file.size > MAX_VIDEO_UPLOAD_BYTES) {
    throw new Error("Uploaded file is too large. Please upload a video under 12 GB.");
  }
  const uploadAuthorization = await requestUploadAuthorization();
  if (!uploadAuthorization.directUpload) {
    return uploadVideoFileViaProxy(file);
  }
  const formData = new FormData();
  formData.append("video", file);
  const uploadResponse = await fetch(uploadAuthorization.uploadUrl, {
    method: "POST",
    headers: uploadAuthorization.headers,
    body: formData,
  });
  if (!uploadResponse.ok) {
    const fallbackMessage =
      uploadResponse.status === 413
        ? "Uploaded file is too large. Please upload a video under 12 GB."
        : `Upload error: ${uploadResponse.status}`;
    const uploadError = await parseApiError(uploadResponse, fallbackMessage);
    throw new Error(formatSupportMessage(uploadError));
  }
  const uploadResult = await uploadResponse.json();
  if (typeof uploadResult.video_path !== "string" || !uploadResult.video_path) {
    throw new Error("Upload finished without a video path. Please try again.");
  }
  return uploadResult.video_path;
}

const TABS: { id: Tab; label: string; icon: typeof Youtube }[] = [
  { id: "source", label: "Source", icon: Youtube },
  { id: "style", label: "Style", icon: Sparkles },
  { id: "hook", label: "Hook", icon: Type },
  { id: "retention", label: "Retention", icon: Film },
  { id: "cleanup", label: "Cleanup", icon: Scissors },
  { id: "output", label: "Output", icon: Monitor },
];

export default function VideoProcessingPage() {
  const [activeTab, setActiveTab] = useState<Tab>("source");
  const [url, setUrl] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [progress, setProgress] = useState(0);
  const [statusMessage, setStatusMessage] = useState("");
  const [currentStep, setCurrentStep] = useState("");
  const [sourceType, setSourceType] = useState<"youtube" | "upload">("youtube");
  const [fileName, setFileName] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sourceTitle, setSourceTitle] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const fileRef = useRef<File | null>(null);
  const [isDraggingFile, setIsDraggingFile] = useState(false);
  // Batch queue: when more than one file is selected/dropped, all of them
  // process sequentially (one upload+create at a time) instead of only the
  // first file being kept.
  const [queuedFiles, setQueuedFiles] = useState<File[]>([]);
  type BatchItemStatus = "pending" | "uploading" | "creating" | "done" | "error";
  const [batchStatuses, setBatchStatuses] = useState<
    Array<{ name: string; status: BatchItemStatus; taskId?: string; error?: string }>
  >([]);
  const [isBatchProcessing, setIsBatchProcessing] = useState(false);
  const [presetDialogOpen, setPresetDialogOpen] = useState(false);
  const [autoExportToSource, setAutoExportToSource] = useState(false);

  const [fontFamily, setFontFamily] = useState<string | null>(null);
  const [fontSize, setFontSize] = useState<number | null>(null);
  const [fontColor, setFontColor] = useState<string | null>(null);
  const [availableFonts, setAvailableFonts] = useState<FontOption[]>([]);
  const [fontSearch, setFontSearch] = useState("");
  const [fontLoadError, setFontLoadError] = useState<string | null>(null);

  const [captionTemplate, setCaptionTemplate] = useState("default");
  const [availableTemplates, setAvailableTemplates] = useState<TemplateInfo[]>([]);
  const [outputFormat, setOutputFormat] = useState<OutputFormat>("vertical");
  const [addSubtitles, setAddSubtitles] = useState(true);
  const [cutLongPauses, setCutLongPauses] = useState(false);
  const [pauseThresholdMs, setPauseThresholdMs] = useState("900");
  const [removeFillerWords, setRemoveFillerWords] = useState(false);
  const [filteredWords, setFilteredWords] = useState("");
  // null = use the manual toggles/threshold above unchanged (default, fully
  // backward compatible). Once the user touches the slider it takes over as
  // the single knob for both pause and filler-word aggressiveness.
  const [cleanupSensitivity, setCleanupSensitivity] = useState<number | null>(null);

  const [hookStyle, setHookStyle] = useState<HookStyle>(DEFAULT_HOOK_STYLE);
  const updateHookStyle = useCallback(<K extends keyof HookStyle>(key: K, value: HookStyle[K]) => {
    setHookStyle((current) => ({ ...current, [key]: value }));
  }, []);
  const [sfxOptions, setSfxOptions] = useState<Array<{ name: string; display_name: string }>>([]);

  const [socialOverlay, setSocialOverlay] = useState<SocialOverlay>(DEFAULT_SOCIAL_OVERLAY);
  const updateSocialOverlay = useCallback(<K extends keyof SocialOverlay>(key: K, value: SocialOverlay[K]) => {
    setSocialOverlay((current) => ({ ...current, [key]: value }));
  }, []);
  const [brollSettings, setBrollSettings] = useState<BrollSettings>(DEFAULT_BROLL_SETTINGS);
  const updateBrollSettings = useCallback(<K extends keyof BrollSettings>(key: K, value: BrollSettings[K]) => {
    setBrollSettings((current) => ({ ...current, [key]: value }));
  }, []);
  const [targetDuration, setTargetDuration] = useState<TargetDuration>(null);
  const [clipCount, setClipCount] = useState<number | null>(null);

  const [presets, setPresets] = useState<TaskPreset[]>([]);
  const [presetName, setPresetName] = useState("");
  const [selectedPreset, setSelectedPreset] = useState("");
  const hasRestoredSettings = useRef(false);

  const applyGenerationSettings = useCallback((settings: TaskGenerationSettings) => {
    setFontFamily(settings.fontFamily);
    setFontSize(settings.fontSize);
    setFontColor(settings.fontColor);
    setCaptionTemplate(settings.captionTemplate);
    setOutputFormat(settings.outputFormat as OutputFormat);
    setAddSubtitles(settings.addSubtitles);
    setCutLongPauses(settings.cutLongPauses);
    setPauseThresholdMs(settings.pauseThresholdMs);
    setRemoveFillerWords(settings.removeFillerWords);
    setFilteredWords(settings.filteredWords);
    setCleanupSensitivity(settings.cleanupSensitivity ?? null);
    setHookStyle({ ...DEFAULT_HOOK_STYLE, ...settings.hookStyle });
    setSocialOverlay({ ...DEFAULT_SOCIAL_OVERLAY, ...settings.socialOverlay });
    setBrollSettings({ ...DEFAULT_BROLL_SETTINGS, ...settings.brollSettings });
    setTargetDuration(settings.targetDuration ?? null);
    setClipCount(settings.clipCount ?? null);
  }, []);

  const currentGenerationSettings = useCallback(
    (): TaskGenerationSettings => ({
      fontFamily,
      fontSize,
      fontColor,
      captionTemplate,
      outputFormat,
      addSubtitles,
      cutLongPauses,
      pauseThresholdMs,
      removeFillerWords,
      filteredWords,
      cleanupSensitivity,
      hookStyle,
      socialOverlay,
      brollSettings,
      targetDuration,
      clipCount,
    }),
    [
      fontFamily, fontSize, fontColor, captionTemplate, outputFormat, addSubtitles,
      cutLongPauses, pauseThresholdMs, removeFillerWords, filteredWords, cleanupSensitivity,
      hookStyle, socialOverlay, brollSettings, targetDuration, clipCount,
    ],
  );

  useEffect(() => {
    if (hasRestoredSettings.current) return;
    hasRestoredSettings.current = true;
    const last = loadLastSettings();
    if (last) applyGenerationSettings(last);
    setPresets(listPresets());
  }, [applyGenerationSettings]);

  // Picked up once on mount: a file handed off by the home screen's
  // drag-and-drop or "Import Video" CTA (see lib/pending-file-transfer.ts).
  // A direct visit to /create has nothing pending, so this is a no-op.
  useEffect(() => {
    const file = takePendingFile();
    if (!file) return;
    setSourceType("upload");
    handleFileSelected(file);
  }, []);

  const apiUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
  const youtubeThumbnailUrl = sourceType === "youtube" ? getYouTubeThumbnailUrl(url) : null;

  const refreshFonts = useCallback(async () => {
    try {
      setFontLoadError(null);
      const response = await fetch("/api/fonts", { cache: "no-store" });
      if (!response.ok) throw new Error(`Failed to load fonts (${response.status})`);
      const data = await response.json();
      const fonts: FontOption[] = data.fonts || [];
      setAvailableFonts(fonts);

      const fontFaceStyles = fonts
        .map((font) => {
          const format = font.format === "otf" ? "opentype" : "truetype";
          return `@font-face { font-family: '${font.name}'; src: url('/api/fonts/${font.name}') format('${format}'); font-weight: normal; font-style: normal; }`;
        })
        .join("\n");
      const styleElement = document.createElement("style");
      styleElement.id = "custom-fonts";
      styleElement.innerHTML = fontFaceStyles;
      const existingStyle = document.getElementById("custom-fonts");
      if (existingStyle) existingStyle.remove();
      document.head.appendChild(styleElement);
    } catch (err) {
      console.error("Failed to load fonts:", err);
      setFontLoadError("Could not load fonts right now.");
    }
  }, []);

  useEffect(() => {
    void refreshFonts();
  }, [refreshFonts]);

  useEffect(() => {
    const loadTemplates = async () => {
      try {
        const response = await fetch(`${apiUrl}/caption-templates`);
        if (response.ok) {
          const data = await response.json();
          setAvailableTemplates(data.templates || []);
        }
      } catch (err) {
        console.error("Failed to load caption templates:", err);
      }
    };
    loadTemplates();
  }, [apiUrl]);

  useEffect(() => {
    const loadSfx = async () => {
      try {
        const response = await fetch(`${apiUrl}/sfx`, { cache: "no-store" });
        if (response.ok) {
          const data = await response.json();
          setSfxOptions(data.sfx || []);
        }
      } catch (err) {
        console.error("Failed to load SFX library:", err);
      }
    };
    loadSfx();
  }, [apiUrl]);

  const handleFileSelected = (file: File | null) => {
    fileRef.current = file;
    setFileName(file ? file.name : null);
  };
  const handleFilesSelected = (files: File[]) => {
    if (files.length > 1) {
      setQueuedFiles(files);
      handleFileSelected(null);
      setFileName(`${files.length} videos queued`);
    } else {
      setQueuedFiles([]);
      handleFileSelected(files[0] || null);
    }
  };
  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) =>
    handleFilesSelected(Array.from(e.target.files || []));
  const handleDragOver = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    if (isLoading) return;
    setIsDraggingFile(true);
  };
  const handleDragLeave = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setIsDraggingFile(false);
  };
  const handleDrop = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setIsDraggingFile(false);
    if (isLoading) return;
    handleFilesSelected(Array.from(e.dataTransfer.files || []));
  };

  const filteredFonts = availableFonts.filter((font) => {
    const keyword = fontSearch.toLowerCase().trim();
    if (!keyword) return true;
    return font.display_name.toLowerCase().includes(keyword) || font.name.toLowerCase().includes(keyword);
  });

  const selectedTemplate = availableTemplates.find((t) => t.id === captionTemplate);
  const previewFontFamily = fontFamily ?? selectedTemplate?.font_family ?? "TikTokSans-Regular";
  const previewFontSize = fontSize ?? selectedTemplate?.font_size ?? 24;
  const previewFontColor = fontColor ?? selectedTemplate?.font_color ?? "#FFFFFF";

  const getStepIcon = (step: string) => {
    const iconMap: Record<string, React.ReactElement> = {
      validation: <Loader2 className="w-4 h-4 animate-spin text-secondary" />,
      source_analysis: <Loader2 className="w-4 h-4 animate-spin text-secondary" />,
      youtube_info: <Youtube className="w-4 h-4 text-foreground" />,
      download: <Loader2 className="w-4 h-4 animate-spin text-primary" />,
      transcript: <Loader2 className="w-4 h-4 animate-spin text-secondary" />,
      ai_analysis: <Loader2 className="w-4 h-4 animate-spin text-secondary" />,
      clip_generation: <Loader2 className="w-4 h-4 animate-spin text-primary" />,
      complete: <CheckCircle className="w-4 h-4 text-primary" />,
    };
    return iconMap[step] || <Loader2 className="w-4 h-4 animate-spin text-muted-foreground" />;
  };

  const buildTaskCreationPayload = (videoUrl: string) => {
    const normalizedPauseThreshold = Number.isFinite(Number(pauseThresholdMs))
      ? Math.max(250, Math.min(3000, Math.round(Number(pauseThresholdMs))))
      : 900;
    const normalizedFilteredWords = filteredWords.split(",").map((w) => w.trim().toLowerCase()).filter(Boolean);
    return {
      source: { url: videoUrl, title: null },
      font_options: buildFontOptionsPayload(fontFamily, fontSize, fontColor),
      caption_template: captionTemplate,
      processing_mode: "fast",
      output_format: outputFormat,
      add_subtitles: addSubtitles,
      cut_long_pauses: cutLongPauses,
      pause_threshold_ms: normalizedPauseThreshold,
      remove_filler_words: removeFillerWords,
      filtered_words: normalizedFilteredWords,
      ...(cleanupSensitivity !== null ? { sensitivity: cleanupSensitivity } : {}),
      hook_style: hookStylePayload(hookStyle),
      social_overlay: socialOverlayPayload(socialOverlay),
      broll_settings: brollSettingsPayload(brollSettings),
      target_duration_seconds: targetDuration,
      max_clips: clipCount,
      include_broll: brollSettings.enabled,
    };
  };

  const runBatchSubmit = async (templateId: string | null) => {
    if (queuedFiles.length === 0) return;

    setIsBatchProcessing(true);
    setError(null);
    setBatchStatuses(queuedFiles.map((file) => ({ name: file.name, status: "pending" })));

    // Sequential by design: uploads run one at a time here (a batch drop of
    // several large files shouldn't saturate the upload endpoint at once).
    // Once every file is uploaded, a single POST creates the whole
    // DB-backed batch queue row, which the ARQ worker then walks item by
    // item — the actual processing sequencing lives server-side now, not
    // in this loop (which previously created+enqueued one task per file
    // itself, with no queue row, no pause/resume, and no restart-survival).
    const items: { source_filename: string; source_path: string }[] = [];
    for (let i = 0; i < queuedFiles.length; i++) {
      const file = queuedFiles[i];
      try {
        setBatchStatuses((current) =>
          current.map((item, idx) => (idx === i ? { ...item, status: "uploading" } : item)),
        );
        const videoUrl = await uploadVideoFile(file);
        items.push({ source_filename: file.name, source_path: videoUrl });
        setBatchStatuses((current) =>
          current.map((item, idx) => (idx === i ? { ...item, status: "done" } : item)),
        );
      } catch (err) {
        setBatchStatuses((current) =>
          current.map((item, idx) =>
            idx === i
              ? { ...item, status: "error", error: err instanceof Error ? err.message : "Failed" }
              : item,
          ),
        );
      }
    }

    if (items.length === 0) {
      setIsBatchProcessing(false);
      return;
    }

    try {
      const response = await fetch("/api/batch-queue", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          items,
          template_id: templateId,
          auto_export_to_source: autoExportToSource,
        }),
      });
      if (!response.ok) {
        throw new Error(await formatSupportMessage(await parseApiError(response, `API error: ${response.status}`)));
      }
      const result = await response.json();
      saveLastSettings(currentGenerationSettings());
      window.location.href = `/batch/${result.batch_queue.id}`;
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to start batch");
      setIsBatchProcessing(false);
    }
  };

  const handleBatchSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (queuedFiles.length === 0) return;
    setPresetDialogOpen(true);
  };

  const handleSubmit = async (e: React.FormEvent) => {
    if (queuedFiles.length > 1) return handleBatchSubmit(e);
    e.preventDefault();
    if (sourceType === "upload" && !fileRef.current) return;
    if (sourceType === "youtube" && !url.trim()) return;

    setIsLoading(true);
    setProgress(0);
    setError(null);
    setStatusMessage("");
    setCurrentStep("");
    setSourceTitle(null);

    const fontOptions = buildFontOptionsPayload(fontFamily, fontSize, fontColor);

    try {
      let videoUrl = url;
      const normalizedPauseThreshold = Number.isFinite(Number(pauseThresholdMs))
        ? Math.max(250, Math.min(3000, Math.round(Number(pauseThresholdMs))))
        : 900;
      const normalizedFilteredWords = filteredWords.split(",").map((w) => w.trim().toLowerCase()).filter(Boolean);

      if (sourceType === "upload" && fileRef.current) {
        setStatusMessage("Uploading video file...");
        setProgress(5);
        videoUrl = await uploadVideoFile(fileRef.current);
      }

      const startResponse = await fetch("/api/tasks/create", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          source: { url: videoUrl, title: null },
          font_options: fontOptions,
          caption_template: captionTemplate,
          processing_mode: "fast",
          output_format: outputFormat,
          add_subtitles: addSubtitles,
          cut_long_pauses: cutLongPauses,
          pause_threshold_ms: normalizedPauseThreshold,
          remove_filler_words: removeFillerWords,
          filtered_words: normalizedFilteredWords,
          ...(cleanupSensitivity !== null ? { sensitivity: cleanupSensitivity } : {}),
          hook_style: hookStylePayload(hookStyle),
          social_overlay: socialOverlayPayload(socialOverlay),
          broll_settings: brollSettingsPayload(brollSettings),
          target_duration_seconds: targetDuration,
          max_clips: clipCount,
          include_broll: brollSettings.enabled,
        }),
      });

      if (!startResponse.ok) {
        const startError = await parseApiError(startResponse, `API error: ${startResponse.status}`);
        throw new Error(formatSupportMessage(startError));
      }

      const startResult = await startResponse.json();
      saveLastSettings(currentGenerationSettings());
      window.location.href = `/tasks/${startResult.task_id}`;
    } catch (err) {
      console.error("Error processing video:", err);
      setError(err instanceof Error ? err.message : "Failed to process video. Please try again.");
    } finally {
      setIsLoading(false);
      setProgress(0);
      setStatusMessage("");
      setCurrentStep("");
      setFileName(null);
      fileRef.current = null;
      setUrl("");
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  };

  const canSubmit =
    (sourceType === "youtube" ? url.trim().length > 0 : Boolean(fileRef.current || fileName)) &&
    !isLoading &&
    !isBatchProcessing;

  return (
    <div className="min-h-screen bg-background">
      <div className="border-b border-border bg-background">
        <div className="max-w-7xl mx-auto px-4 py-4 flex items-center justify-between">
          <Link href="/">
            <Button variant="ghost" size="sm">
              <ArrowLeft className="w-4 h-4" />
              Home
            </Button>
          </Link>
          <h1 className="text-lg font-bold text-foreground">New Video</h1>
          <div className="w-16" />
        </div>
      </div>

      <div className="max-w-6xl mx-auto px-4 py-8">
        <form onSubmit={handleSubmit} className="flex flex-col lg:flex-row gap-8 items-start">
          {/* Sticky preview column — never scrolls away */}
          <div className="w-full lg:w-[300px] flex-shrink-0 lg:sticky lg:top-8">
            <div className="flex items-center gap-2 mb-3 text-xs text-muted-foreground">
              <Monitor className="w-3.5 h-3.5" />
              <span>Preview (approximate)</span>
            </div>
            <div className="mx-auto" style={{ maxWidth: "280px" }}>
              <div className="relative overflow-hidden bg-foreground border border-border" style={{ aspectRatio: "9/16" }}>
                {youtubeThumbnailUrl ? (
                  <div
                    className="absolute inset-0 bg-cover bg-center scale-105 blur-sm"
                    style={{ backgroundImage: `url(${youtubeThumbnailUrl})` }}
                  />
                ) : (
                  <div className="absolute inset-0 bg-foreground" />
                )}
                <div className="absolute inset-0 bg-foreground/20" />

                {/* Hook title overlay — text only, composited directly over the frame (not the
                    boxed HookTitlePreview, which has its own opaque background) */}
                <div
                  className="absolute inset-x-0 z-10 flex px-4"
                  style={{
                    top: hookStyle.hook_position === "top" || !hookStyle.hook_position ? "6%" : undefined,
                    bottom: hookStyle.hook_position === "bottom" ? "42%" : undefined,
                    ...(hookStyle.hook_position === "center"
                      ? { top: "50%", transform: "translateY(-50%)" }
                      : {}),
                    justifyContent: "center",
                  }}
                >
                  <span
                    className="text-center font-bold"
                    style={{
                      fontFamily: `'${hookStyle.hook_font_family ?? previewFontFamily}', system-ui, sans-serif`,
                      fontSize: `${Math.round((hookStyle.hook_font_size_scale ?? 0.82) * 17)}px`,
                      color: hookStyle.hook_font_color ?? "#FFFFFF",
                      backgroundColor: hookStyle.hook_background_color ?? "transparent",
                      padding: hookStyle.hook_background_color ? "0.3em 0.3em" : 0,
                      WebkitTextStroke: `1px ${hookStyle.hook_stroke_color ?? "#000000"}`,
                      textShadow: (hookStyle.hook_shadow ?? true) ? "0 2px 4px rgba(0,0,0,0.7)" : "none",
                    }}
                  >
                    {splitHookIntoHighlightSpans("This Changes Everything").map((span, index) => (
                      <span
                        key={index}
                        style={span.highlighted ? { color: hookStyle.hook_highlight_color ?? "#FFE000" } : undefined}
                      >
                        {index > 0 ? " " : ""}
                        {span.text}
                      </span>
                    ))}
                  </span>
                </div>

                {/* Caption preview */}
                <div className="absolute left-0 right-0 z-10" style={{ bottom: "26%" }}>
                  <div className="mx-3">
                    <p
                      style={{
                        color: previewFontColor,
                        fontSize: `${Math.max(Math.min(previewFontSize * 0.55, 20), 10)}px`,
                        fontFamily: `'${previewFontFamily}', system-ui, sans-serif`,
                        textAlign: "center",
                        lineHeight: "1.5",
                        textShadow: "0 2px 8px rgba(0,0,0,0.8)",
                      }}
                      className="font-bold"
                    >
                      Your subtitle will look like this
                    </p>
                  </div>
                </div>

                {/* Social overlay preview */}
                {socialOverlay.enabled && (
                  <div className="absolute left-3 z-10 max-w-[75%]" style={{ bottom: "10%" }}>
                    <p className="text-background text-xs font-bold mb-1 flex items-center gap-1">
                      @{socialOverlay.username.trim() || "yourhandle"}
                      {socialOverlay.verified && <span className="text-secondary">✓</span>}
                    </p>
                    <p className="text-background/80 text-[10px]">
                      {(socialOverlay.likes.trim() || "24.5K")} likes · {(socialOverlay.comments.trim() || "482")} comments
                      {socialOverlay.followers.trim() ? ` · ${socialOverlay.followers.trim()} followers` : ""}
                    </p>
                  </div>
                )}
              </div>
            </div>
          </div>

          {/* Tabbed controls */}
          <div className="flex-1 min-w-0 space-y-4">
            {error && (
              <Alert className="border-border bg-background">
                <AlertCircle className="h-4 w-4 text-foreground" />
                <AlertDescription className="text-sm text-foreground font-bold">{error}</AlertDescription>
              </Alert>
            )}

            {/* Presets bar */}
            <Card className="border-border">
              <CardContent className="px-4 py-3 space-y-2">
                <div className="flex items-center gap-2 text-sm font-medium text-foreground">
                  <Settings className="w-4 h-4" />
                  Presets
                </div>
                <div className="flex items-center gap-2">
                  <Select
                    value={selectedPreset || "__none__"}
                    onValueChange={(value) => {
                      setSelectedPreset(value === "__none__" ? "" : value);
                      const preset = presets.find((p) => p.name === value);
                      if (preset) applyGenerationSettings(preset.settings);
                    }}
                    disabled={presets.length === 0}
                  >
                    <SelectTrigger className="flex-1">
                      <SelectValue placeholder={presets.length === 0 ? "No saved presets" : "Load a preset"} />
                    </SelectTrigger>
                    <SelectContent>
                      {presets.map((preset) => (
                        <SelectItem key={preset.name} value={preset.name}>
                          {preset.name}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    disabled={!selectedPreset}
                    onClick={() => {
                      deletePreset(selectedPreset);
                      setPresets(listPresets());
                      setSelectedPreset("");
                    }}
                  >
                    Delete
                  </Button>
                </div>
                <div className="flex items-center gap-2">
                  <Input
                    value={presetName}
                    onChange={(e) => setPresetName(e.target.value)}
                    placeholder="Preset name"
                    className="flex-1 h-8 text-xs"
                  />
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    disabled={!presetName.trim()}
                    onClick={() => {
                      savePreset(presetName.trim(), currentGenerationSettings());
                      setPresets(listPresets());
                      setSelectedPreset(presetName.trim());
                      setPresetName("");
                    }}
                  >
                    Save current as...
                  </Button>
                </div>
              </CardContent>
            </Card>

            {/* Tab bar */}
            <div className="flex gap-1 border-b border-border overflow-x-auto">
              {TABS.map((tab) => {
                const Icon = tab.icon;
                return (
                  <button
                    key={tab.id}
                    type="button"
                    onClick={() => setActiveTab(tab.id)}
                    className={`flex items-center gap-1.5 px-3 py-2 text-sm font-medium border-b-2 whitespace-nowrap transition-colors ${
                      activeTab === tab.id
                        ? "border-foreground text-foreground"
                        : "border-transparent text-muted-foreground hover:text-foreground"
                    }`}
                  >
                    <Icon className="w-4 h-4" />
                    {tab.label}
                  </button>
                );
              })}
            </div>

            {/* Source tab */}
            {activeTab === "source" && (
              <div className="space-y-3">
                <div className="flex gap-2">
                  <button
                    type="button"
                    onClick={() => {
                      setSourceType("youtube");
                      setFileName(null);
                      fileRef.current = null;
                      if (fileInputRef.current) fileInputRef.current.value = "";
                    }}
                    disabled={isLoading}
                    className={`flex items-center gap-2 px-4 py-2 text-sm font-medium transition-all border ${
                      sourceType === "youtube" ? "bg-foreground text-background border-foreground" : "bg-background text-muted-foreground border-border hover:text-foreground"
                    }`}
                  >
                    <Youtube className="w-4 h-4" />
                    YouTube URL
                  </button>
                  <button
                    type="button"
                    onClick={() => setSourceType("upload")}
                    disabled={isLoading}
                    className={`flex items-center gap-2 px-4 py-2 text-sm font-medium transition-all border ${
                      sourceType === "upload" ? "bg-foreground text-background border-foreground" : "bg-background text-muted-foreground border-border hover:text-foreground"
                    }`}
                  >
                    <Upload className="w-4 h-4" />
                    Upload Video
                  </button>
                </div>

                {sourceType === "youtube" ? (
                  <div className="relative">
                    <Youtube className="absolute left-4 top-1/2 -translate-y-1/2 w-5 h-5 text-muted-foreground" />
                    <Input
                      type="url"
                      placeholder="https://www.youtube.com/watch?v=..."
                      value={url}
                      onChange={(e) => setUrl(e.target.value)}
                      disabled={isLoading}
                      className="h-14 pl-12 text-base border-border focus:border-foreground placeholder:text-muted-foreground"
                    />
                  </div>
                ) : (
                  <div
                    className={`relative border-2 border-dashed p-8 text-center transition-colors cursor-pointer ${
                      isDraggingFile ? "border-foreground bg-background" : "border-border hover:border-foreground"
                    }`}
                    onClick={() => !isLoading && fileInputRef.current?.click()}
                    onDragOver={handleDragOver}
                    onDragLeave={handleDragLeave}
                    onDrop={handleDrop}
                  >
                    <input
                      type="file"
                      accept="video/*"
                      multiple
                      ref={fileInputRef}
                      onChange={handleFileChange}
                      disabled={isLoading}
                      className="hidden"
                    />
                    <Upload className="w-8 h-8 text-muted-foreground mx-auto mb-3" />
                    {fileName ? (
                      <p className="text-sm font-medium text-foreground">{fileName}</p>
                    ) : (
                      <>
                        <p className="text-sm font-medium text-foreground">Drop video files here or click to browse</p>
                        <p className="text-xs text-muted-foreground mt-1">MP4, MOV, AVI up to 500MB · drop multiple to batch-process</p>
                      </>
                    )}
                  </div>
                )}

                {queuedFiles.length > 1 && (
                  <div className="border border-border divide-y divide-border">
                    {queuedFiles.map((file, idx) => {
                      const status = batchStatuses[idx]?.status;
                      return (
                        <div key={`${file.name}-${idx}`} className="flex items-center justify-between px-3 py-2 text-xs">
                          <span className="truncate text-foreground">{file.name}</span>
                          <span
                            className={`ml-2 flex-shrink-0 font-medium ${
                              status === "done"
                                ? "text-primary"
                                : status === "error"
                                  ? "text-foreground font-bold"
                                  : status === "uploading" || status === "creating"
                                    ? "text-foreground"
                                    : "text-muted-foreground"
                            }`}
                          >
                            {status === "done"
                              ? "Queued for processing"
                              : status === "error"
                                ? batchStatuses[idx]?.error || "Failed"
                                : status === "uploading"
                                  ? "Uploading…"
                                  : status === "creating"
                                    ? "Creating task…"
                                    : "Waiting…"}
                          </span>
                        </div>
                      );
                    })}
                  </div>
                )}
                {queuedFiles.length > 1 && (
                  <div className="flex items-center justify-between px-1 py-2">
                    <label htmlFor="auto-export-toggle" className="text-xs text-muted-foreground">
                      Auto-export clips into each source video&apos;s directory
                    </label>
                    <Switch
                      id="auto-export-toggle"
                      checked={autoExportToSource}
                      onCheckedChange={setAutoExportToSource}
                    />
                  </div>
                )}
              </div>
            )}

            {/* Style tab */}
            {activeTab === "style" && (
              <div className="space-y-4">
                <div className="space-y-2">
                  <label className="text-sm text-muted-foreground">Caption Style</label>
                  <TemplatePicker
                    templates={availableTemplates}
                    selectedId={captionTemplate}
                    onSelect={setCaptionTemplate}
                    disabled={isLoading}
                  />
                </div>

                <div className="space-y-2">
                  <label className="text-sm text-muted-foreground flex items-center gap-2">
                    <Type className="w-3.5 h-3.5" />
                    Font Family
                  </label>
                  {availableFonts.length > FONT_SEARCH_THRESHOLD && (
                    <Input
                      type="text"
                      value={fontSearch}
                      onChange={(e) => setFontSearch(e.target.value)}
                      placeholder="Search fonts"
                      disabled={isLoading}
                    />
                  )}
                  <Select
                    value={fontFamily ?? FONT_TEMPLATE_DEFAULT_VALUE}
                    onValueChange={(value) => setFontFamily(value === FONT_TEMPLATE_DEFAULT_VALUE ? null : value)}
                    disabled={isLoading}
                  >
                    <SelectTrigger className="w-full">
                      <SelectValue placeholder="Template default" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value={FONT_TEMPLATE_DEFAULT_VALUE}>Template default</SelectItem>
                      {filteredFonts.map((font) => (
                        <SelectItem key={font.name} value={font.name}>
                          <span style={{ fontFamily: `'${font.name}', system-ui, sans-serif` }}>{font.display_name}</span>
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  {fontLoadError && <p className="text-xs text-foreground font-bold">{fontLoadError}</p>}
                </div>

                <div className="space-y-2">
                  <label className="text-sm text-muted-foreground">Size</label>
                  <div className="grid grid-cols-4 gap-1.5">
                    {FONT_SIZE_OPTIONS.map((option) => (
                      <button
                        key={option.label}
                        type="button"
                        onClick={() => setFontSize(option.value)}
                        disabled={isLoading}
                        className={`px-2 py-1.5 text-xs font-medium border transition-colors ${
                          fontSize === option.value ? "bg-foreground text-background border-foreground" : "bg-background text-muted-foreground border-border hover:text-foreground"
                        }`}
                      >
                        {option.label}
                      </button>
                    ))}
                  </div>
                </div>

                <div className="space-y-2">
                  <label className="text-sm text-muted-foreground flex items-center gap-1.5">
                    <Palette className="w-3.5 h-3.5" />
                    Color
                  </label>
                  <div className="flex items-center gap-2">
                    <input
                      type="color"
                      value={fontColor ?? "#FFFFFF"}
                      onChange={(e) => setFontColor(e.target.value)}
                      disabled={isLoading}
                      className="w-10 h-8 border border-border cursor-pointer"
                    />
                    <button type="button" className="text-xs text-muted-foreground hover:text-foreground" onClick={() => setFontColor(null)}>
                      Template default
                    </button>
                  </div>
                </div>

                <div className="flex items-center justify-between p-3 border border-border bg-background">
                  <div className="flex items-center gap-3">
                    <Type className="w-4 h-4 text-primary" />
                    <div>
                      <h3 className="text-sm font-medium text-foreground">Add subtitles</h3>
                      <p className="text-xs text-muted-foreground">Burn captions onto clips</p>
                    </div>
                  </div>
                  <Switch checked={addSubtitles} onCheckedChange={setAddSubtitles} disabled={isLoading} />
                </div>
              </div>
            )}

            {/* Hook tab */}
            {activeTab === "hook" && (
              <div className="space-y-5">
                <div className="space-y-2">
                  <label className="text-sm text-muted-foreground flex items-center gap-2">
                    <Type className="w-3.5 h-3.5" />
                    Font Family
                  </label>
                  <Select
                    value={hookStyle.hook_font_family ?? FONT_TEMPLATE_DEFAULT_VALUE}
                    onValueChange={(value) => updateHookStyle("hook_font_family", value === FONT_TEMPLATE_DEFAULT_VALUE ? null : value)}
                    disabled={isLoading}
                  >
                    <SelectTrigger className="w-full">
                      <SelectValue placeholder="Template default" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value={FONT_TEMPLATE_DEFAULT_VALUE}>Template default</SelectItem>
                      {availableFonts.map((font) => (
                        <SelectItem key={font.name} value={font.name}>
                          <span style={{ fontFamily: `'${font.name}', system-ui, sans-serif` }}>{font.display_name}</span>
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>

                <div className="space-y-2">
                  <label className="text-sm text-muted-foreground">Size</label>
                  <div className="grid grid-cols-4 gap-1.5">
                    {[
                      { label: "Small", value: 0.65 },
                      { label: "Default", value: null },
                      { label: "Large", value: 1.0 },
                      { label: "XL", value: 1.3 },
                    ].map((option) => (
                      <button
                        key={option.label}
                        type="button"
                        onClick={() => updateHookStyle("hook_font_size_scale", option.value)}
                        disabled={isLoading}
                        className={`px-2 py-1.5 text-xs font-medium border transition-colors ${
                          hookStyle.hook_font_size_scale === option.value ? "bg-foreground text-background border-foreground" : "bg-background text-muted-foreground border-border hover:text-foreground"
                        }`}
                      >
                        {option.label}
                      </button>
                    ))}
                  </div>
                </div>

                <div className="grid grid-cols-2 gap-3">
                  <div className="space-y-2">
                    <label className="text-sm text-muted-foreground flex items-center gap-1.5">
                      <Palette className="w-3.5 h-3.5" />
                      Text color
                    </label>
                    <div className="flex items-center gap-2">
                      <input
                        type="color"
                        value={hookStyle.hook_font_color ?? "#FFFFFF"}
                        onChange={(e) => updateHookStyle("hook_font_color", e.target.value)}
                        disabled={isLoading}
                        className="w-10 h-8 border border-border cursor-pointer"
                      />
                      <button type="button" className="text-xs text-muted-foreground hover:text-foreground" onClick={() => updateHookStyle("hook_font_color", null)}>
                        Reset
                      </button>
                    </div>
                  </div>
                  <div className="space-y-2">
                    <label className="text-sm text-muted-foreground flex items-center justify-between">
                      <span>Yellow keyword</span>
                    </label>
                    <div className="flex items-center gap-2">
                      <input
                        type="color"
                        value={hookStyle.hook_highlight_color ?? "#FFE000"}
                        onChange={(e) => updateHookStyle("hook_highlight_color", e.target.value)}
                        disabled={isLoading}
                        className="w-10 h-8 border border-border cursor-pointer"
                      />
                      <button type="button" className="text-xs text-muted-foreground hover:text-foreground" onClick={() => updateHookStyle("hook_highlight_color", null)}>
                        Reset
                      </button>
                    </div>
                  </div>
                </div>

                <div className="space-y-2">
                  <label className="text-sm text-muted-foreground flex items-center justify-between">
                    <span>Background box</span>
                    <Switch
                      checked={hookStyle.hook_background_color !== null}
                      onCheckedChange={(checked) =>
                        updateHookStyle("hook_background_color", checked ? (hookStyle.hook_background_color ?? "#00000099") : null)
                      }
                      disabled={isLoading}
                    />
                  </label>
                  {hookStyle.hook_background_color !== null && (
                    <div className="flex items-center gap-2">
                      <input
                        type="color"
                        value={hookStyle.hook_background_color.slice(0, 7)}
                        onChange={(e) => updateHookStyle("hook_background_color", `${e.target.value}99`)}
                        disabled={isLoading}
                        className="w-10 h-8 border border-border cursor-pointer"
                      />
                      <span className="text-xs text-muted-foreground">Box color</span>
                    </div>
                  )}
                </div>

                <div className="space-y-2">
                  <label className="text-sm text-muted-foreground">Outline</label>
                  <div className="flex items-center gap-2">
                    <input
                      type="color"
                      value={hookStyle.hook_stroke_color ?? "#000000"}
                      onChange={(e) => updateHookStyle("hook_stroke_color", e.target.value)}
                      disabled={isLoading}
                      className="w-10 h-8 border border-border cursor-pointer"
                    />
                    <button type="button" className="text-xs text-muted-foreground hover:text-foreground" onClick={() => updateHookStyle("hook_stroke_color", null)}>
                      Reset
                    </button>
                  </div>
                </div>

                <div className="space-y-2">
                  <label className="text-sm text-muted-foreground">Position</label>
                  <div className="grid grid-cols-3 gap-1.5">
                    {(["top", "center", "bottom"] as HookPosition[]).map((position) => (
                      <button
                        key={position}
                        type="button"
                        onClick={() => updateHookStyle("hook_position", position)}
                        disabled={isLoading}
                        className={`px-2 py-1.5 text-xs font-medium border capitalize transition-colors ${
                          (hookStyle.hook_position ?? "top") === position ? "bg-foreground text-background border-foreground" : "bg-background text-muted-foreground border-border hover:text-foreground"
                        }`}
                      >
                        {position}
                      </button>
                    ))}
                  </div>
                </div>

                <div className="space-y-2">
                  <label className="text-sm text-muted-foreground flex items-center justify-between">
                    <span>Duration</span>
                    <span className="text-muted-foreground">{(hookStyle.hook_duration_seconds ?? 4).toFixed(1)}s</span>
                  </label>
                  <input
                    type="range"
                    min={1.5}
                    max={8}
                    step={0.5}
                    value={hookStyle.hook_duration_seconds ?? 4}
                    onChange={(e) => updateHookStyle("hook_duration_seconds", Number(e.target.value))}
                    disabled={isLoading}
                    className="w-full"
                  />
                </div>

                <div className="space-y-2">
                  <label className="text-sm text-muted-foreground">Animation</label>
                  <Select
                    value={hookStyle.hook_animation ?? "fade_pop"}
                    onValueChange={(value) => updateHookStyle("hook_animation", value as HookAnimation)}
                    disabled={isLoading}
                  >
                    <SelectTrigger className="w-full">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="fade_pop">Fade + Pop</SelectItem>
                      <SelectItem value="fade">Fade</SelectItem>
                      <SelectItem value="slide_down">Slide Down</SelectItem>
                      <SelectItem value="zoom_punch">Zoom Punch-in</SelectItem>
                      <SelectItem value="none">None</SelectItem>
                    </SelectContent>
                  </Select>
                </div>

                <div className="flex items-center justify-between">
                  <label className="text-sm text-muted-foreground">Drop shadow</label>
                  <Switch checked={hookStyle.hook_shadow ?? true} onCheckedChange={(checked) => updateHookStyle("hook_shadow", checked)} disabled={isLoading} />
                </div>

                <div className="space-y-2">
                  <label className="text-sm text-muted-foreground flex items-center gap-2">
                    <Music className="w-3.5 h-3.5" />
                    Whoosh/riser SFX
                  </label>
                  <Select
                    value={hookStyle.hook_sfx ?? "__none__"}
                    onValueChange={(value) => updateHookStyle("hook_sfx", value === "__none__" ? null : value)}
                    disabled={isLoading}
                  >
                    <SelectTrigger className="w-full">
                      <SelectValue placeholder={sfxOptions.length === 0 ? "No SFX in library" : "None"} />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="__none__">None</SelectItem>
                      {sfxOptions.map((sfx) => (
                        <SelectItem key={sfx.name} value={sfx.name}>
                          {sfx.display_name}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  {sfxOptions.length === 0 && (
                    <p className="text-xs text-muted-foreground">
                      Add sound files to the SFX library in{" "}
                      <Link href="/settings" className="underline">
                        Settings
                      </Link>
                      .
                    </p>
                  )}
                </div>

                <button type="button" className="text-xs text-muted-foreground hover:text-foreground underline" onClick={() => setHookStyle(DEFAULT_HOOK_STYLE)}>
                  Reset all hook styling to template default
                </button>
              </div>
            )}

            {/* Retention tab */}
            {activeTab === "retention" && (
              <div className="space-y-5">
                <div className="border border-border bg-background p-3 space-y-3">
                  <div className="flex items-center justify-between">
                    <div>
                      <h3 className="text-sm font-medium text-foreground">Fake social overlay</h3>
                      <p className="text-xs text-muted-foreground">Username, verified badge, like/comment/follower counts — cosmetic only.</p>
                    </div>
                    <Switch checked={socialOverlay.enabled} onCheckedChange={(checked) => updateSocialOverlay("enabled", checked)} disabled={isLoading} />
                  </div>
                  {socialOverlay.enabled && (
                    <div className="space-y-2">
                      <Input
                        placeholder="Username (e.g. yourhandle)"
                        value={socialOverlay.username}
                        onChange={(e) => updateSocialOverlay("username", e.target.value)}
                        disabled={isLoading}
                      />
                      <div className="grid grid-cols-3 gap-2">
                        <Input placeholder="Likes (24.5K)" value={socialOverlay.likes} onChange={(e) => updateSocialOverlay("likes", e.target.value)} disabled={isLoading} />
                        <Input placeholder="Comments (482)" value={socialOverlay.comments} onChange={(e) => updateSocialOverlay("comments", e.target.value)} disabled={isLoading} />
                        <Input placeholder="Followers" value={socialOverlay.followers} onChange={(e) => updateSocialOverlay("followers", e.target.value)} disabled={isLoading} />
                      </div>
                      <label className="flex items-center gap-2 text-sm text-foreground">
                        <input
                          type="checkbox"
                          checked={socialOverlay.verified}
                          onChange={(e) => updateSocialOverlay("verified", e.target.checked)}
                          disabled={isLoading}
                          className="rounded"
                        />
                        Show verified badge
                      </label>
                    </div>
                  )}
                </div>

                <div className="border border-border bg-background p-3 space-y-3">
                  <div className="flex items-center justify-between">
                    <div>
                      <h3 className="text-sm font-medium text-foreground">B-roll cuts</h3>
                      <p className="text-xs text-muted-foreground">Let the AI suggest B-roll insertion points from stock footage.</p>
                    </div>
                    <Switch checked={brollSettings.enabled} onCheckedChange={(checked) => updateBrollSettings("enabled", checked)} disabled={isLoading} />
                  </div>
                  {brollSettings.enabled && (
                    <div className="space-y-3">
                      <div className="space-y-1.5">
                        <label className="text-xs font-medium text-muted-foreground flex items-center justify-between">
                          <span>Max insertions per clip</span>
                          <span>{brollSettings.maxInsertions}</span>
                        </label>
                        <input
                          type="range"
                          min={1}
                          max={6}
                          step={1}
                          value={brollSettings.maxInsertions}
                          onChange={(e) => updateBrollSettings("maxInsertions", Number(e.target.value))}
                          disabled={isLoading}
                          className="w-full"
                        />
                      </div>
                      <div className="space-y-1.5">
                        <label className="text-xs font-medium text-muted-foreground flex items-center justify-between">
                          <span>Minimum gap between insertions</span>
                          <span>{brollSettings.minGapSeconds}s</span>
                        </label>
                        <input
                          type="range"
                          min={2}
                          max={30}
                          step={1}
                          value={brollSettings.minGapSeconds}
                          onChange={(e) => updateBrollSettings("minGapSeconds", Number(e.target.value))}
                          disabled={isLoading}
                          className="w-full"
                        />
                      </div>
                    </div>
                  )}
                </div>

                <div className="space-y-2">
                  <label className="text-sm text-muted-foreground">Target clip length</label>
                  <div className="grid grid-cols-4 gap-1.5">
                    {[15, 30, 60, null].map((preset) => (
                      <button
                        key={preset ?? "auto"}
                        type="button"
                        onClick={() => setTargetDuration(preset as TargetDuration)}
                        disabled={isLoading}
                        className={`px-2 py-1.5 text-xs font-medium border transition-colors ${
                          targetDuration === preset ? "bg-foreground text-background border-foreground" : "bg-background text-muted-foreground border-border hover:text-foreground"
                        }`}
                      >
                        {preset ? `${preset}s` : "Auto"}
                      </button>
                    ))}
                  </div>
                  <p className="text-xs text-muted-foreground">The AI aims for clips around this length, and clips longer than it get trimmed down.</p>
                </div>

                <div className="space-y-2">
                  <label className="text-sm text-muted-foreground">Clip count</label>
                  <div className="grid grid-cols-5 gap-1.5">
                    {[3, 5, 7, 10, null].map((preset) => (
                      <button
                        key={preset ?? "auto"}
                        type="button"
                        onClick={() => setClipCount(preset)}
                        disabled={isLoading}
                        className={`px-2 py-1.5 text-xs font-medium border transition-colors ${
                          clipCount === preset ? "bg-foreground text-background border-foreground" : "bg-background text-muted-foreground border-border hover:text-foreground"
                        }`}
                      >
                        {preset ?? "Auto"}
                      </button>
                    ))}
                  </div>
                  <p className="text-xs text-muted-foreground">How many clips the AI aims to produce from this video (quality still gates each pick).</p>
                </div>
              </div>
            )}

            {/* Cleanup tab */}
            {activeTab === "cleanup" && (
              <div className="space-y-4">
                <div className="space-y-1.5">
                  <div className="flex items-center justify-between">
                    <label className="text-sm font-medium text-foreground">Cleanup sensitivity</label>
                    <span className="text-xs text-muted-foreground tabular-nums">
                      {cleanupSensitivity === null ? "Off" : `${cleanupSensitivity}/100`}
                    </span>
                  </div>
                  <Slider
                    value={[cleanupSensitivity ?? 0]}
                    min={0}
                    max={100}
                    step={1}
                    disabled={isLoading}
                    onValueChange={([value]) => setCleanupSensitivity(value)}
                  />
                  <p className="text-xs text-muted-foreground">
                    0 turns cleanup off. Higher values cut shorter pauses and more filler words at once
                    &mdash; this overrides the manual controls below. Meaning-changing cuts (punchlines,
                    sentence-ending words, emphatic delivery) are always protected regardless of sensitivity.
                  </p>
                  {cleanupSensitivity !== null && (
                    <button
                      type="button"
                      onClick={() => setCleanupSensitivity(null)}
                      disabled={isLoading}
                      className="text-xs text-muted-foreground underline hover:text-foreground"
                    >
                      Reset to manual controls
                    </button>
                  )}
                </div>
                <Separator />
                <div className="flex items-center justify-between">
                  <div>
                    <div className="text-sm font-medium text-foreground">Cut long pauses</div>
                    <div className="text-xs text-muted-foreground">Split out silence gaps longer than your threshold.</div>
                  </div>
                  <Switch checked={cutLongPauses} onCheckedChange={setCutLongPauses} disabled={isLoading || cleanupSensitivity !== null} />
                </div>
                <div className="space-y-1.5">
                  <label className="text-xs font-medium text-muted-foreground">Pause threshold (ms)</label>
                  <Input
                    type="number"
                    min={250}
                    max={3000}
                    step={50}
                    value={pauseThresholdMs}
                    onChange={(e) => setPauseThresholdMs(e.target.value)}
                    disabled={isLoading || !cutLongPauses || cleanupSensitivity !== null}
                    placeholder="900"
                  />
                </div>
                <div className="flex items-center justify-between">
                  <div>
                    <div className="text-sm font-medium text-foreground">Remove filler words</div>
                    <div className="text-xs text-muted-foreground">Uses a safe default list like &quot;um&quot;, &quot;uh&quot;, and &quot;you know&quot;.</div>
                  </div>
                  <Switch checked={removeFillerWords} onCheckedChange={setRemoveFillerWords} disabled={isLoading || cleanupSensitivity !== null} />
                </div>
                <div className="space-y-1.5">
                  <label className="text-xs font-medium text-muted-foreground">Extra filtered words or phrases</label>
                  <Input
                    value={filteredWords}
                    onChange={(e) => setFilteredWords(e.target.value)}
                    disabled={isLoading}
                    placeholder="basically, literally, to be honest"
                  />
                </div>
              </div>
            )}

            {/* Output tab */}
            {activeTab === "output" && (
              <div className="space-y-4">
                <div className="flex items-center justify-between gap-4 p-3 border rounded-lg bg-background">
                  <div className="flex min-w-0 items-center gap-3">
                    <Monitor className="w-4 h-4 text-secondary" />
                    <div>
                      <h3 className="text-sm font-medium text-foreground">Framing</h3>
                      <p className="text-xs text-muted-foreground">Choose how clips are reframed for social video</p>
                    </div>
                  </div>
                  <Select value={outputFormat} onValueChange={(value) => setOutputFormat(value as OutputFormat)} disabled={isLoading}>
                    <SelectTrigger className="w-[180px] bg-background">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="vertical">Auto 9:16</SelectItem>
                      <SelectItem value="vertical_pan">Speaker pan</SelectItem>
                      <SelectItem value="vertical_split">Split-screen</SelectItem>
                      <SelectItem value="original">Original</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
              </div>
            )}

            {isLoading && (
              <div className="space-y-2 pt-2">
                <div className="flex justify-between text-sm">
                  <span className="text-muted-foreground">Processing</span>
                  <span className="text-foreground font-medium">{progress}%</span>
                </div>
                <Progress value={progress} className="h-2" />
                {currentStep && statusMessage && (
                  <div className="flex items-center gap-3 bg-background p-3 border border-border">
                    {getStepIcon(currentStep)}
                    <div>
                      <p className="text-sm font-medium text-foreground">{statusMessage}</p>
                      {sourceTitle && <p className="text-xs text-muted-foreground mt-1">Processing: {sourceTitle}</p>}
                    </div>
                  </div>
                )}
              </div>
            )}

            <Separator />

            <Button type="submit" className="w-full h-12 text-base rounded-xl" disabled={!canSubmit}>
              {isBatchProcessing
                ? `Queuing ${batchStatuses.filter((s) => s.status === "done" || s.status === "error").length}/${queuedFiles.length}…`
                : isLoading
                  ? "Processing..."
                  : queuedFiles.length > 1
                    ? `Process ${queuedFiles.length} Videos`
                    : "Process Video"}
            </Button>
          </div>
        </form>
      </div>
      <PresetPromptDialog
        open={presetDialogOpen}
        onOpenChange={setPresetDialogOpen}
        onSelect={(templateId) => void runBatchSubmit(templateId)}
      />
    </div>
  );
}
