"use client";

import { useState, useRef, useEffect, useCallback, type ReactNode } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Skeleton } from "@/components/ui/skeleton";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { useSession } from "@/lib/auth-client";
import { isPaidBillingPlan } from "@/lib/billing-plans";
import { track } from "@/lib/datafast";
import { formatSupportMessage, parseApiError } from "@/lib/api-error";
import { buildFontOptionsPayload, FONT_SIZE_OPTIONS, FONT_TEMPLATE_DEFAULT_VALUE } from "@/lib/font-options";
import { cn } from "@/lib/utils";
import { ClipPhonePreview, type FontOption } from "@/components/clip-phone-preview";
import { CollapsibleSection } from "@/components/collapsible-section";
import { AppShell } from "@/components/app-shell";
import Link from "next/link";
import { ArrowRight, Youtube, CheckCircle, AlertCircle, Loader2, Film, Upload, Link2, Smartphone, User, Columns2, Monitor } from "lucide-react";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";

interface LatestTask {
  id: string;
  source_title: string;
  source_type: string;
  status: string;
  clips_count: number;
  created_at: string;
}

interface BillingSummary {
  monetization_enabled: boolean;
  plan: string;
  subscription_status: string;
  usage_count: number;
  usage_limit: number | null;
  remaining: number | null;
  can_create_task: boolean;
  upgrade_required: boolean;
  reason: string | null;
}

type SourceType = "youtube" | "external" | "upload";

type OutputFormat = "vertical" | "vertical_pan" | "vertical_split" | "original";

const MAX_VIDEO_UPLOAD_BYTES = 1_000_000_000;

// Only surface the font search box once the list is long enough to need it.
const FONT_SEARCH_THRESHOLD = 8;

// Mirrors backend/src/generation_preferences.py, which clamps rather than rejects —
// validating here keeps the user's numbers from being silently rewritten.
const MIN_CLIP_COUNT = 1;
const MAX_CLIP_COUNT = 10;
const MIN_CLIP_SECONDS = 15;
const MAX_CLIP_SECONDS = 60;

// Plain seconds, MM:SS or HH:MM:SS — the three shapes the backend parser accepts.
const TIMEFRAME_PATTERN = /^(\d+|\d{1,3}:[0-5]\d|\d{1,3}:[0-5]\d:[0-5]\d)$/;

const SOURCE_TABS: Array<{ id: SourceType; label: string; icon: typeof Youtube }> = [
  { id: "youtube", label: "YouTube URL", icon: Youtube },
  { id: "external", label: "Other source", icon: Link2 },
  { id: "upload", label: "Upload Video", icon: Upload },
];

const COLOR_SWATCHES: Array<{ value: string; label: string }> = [
  { value: "#FFFFFF", label: "White" },
  { value: "#000000", label: "Black" },
  { value: "#FFD700", label: "Gold" },
  { value: "#FF6B6B", label: "Coral red" },
  { value: "#4ECDC4", label: "Turquoise" },
  { value: "#45B7D1", label: "Sky blue" },
];

type DirectUploadAuthorization = {
  directUpload: true;
  uploadUrl: string;
  headers: Record<string, string>;
};

type ProxyUploadAuthorization = {
  directUpload: false;
  reason: "signed_backend_auth_required";
};

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
      if (fromSearch && fromSearch.length === 11) {
        return fromSearch;
      }

      const pathParts = parsed.pathname.split("/").filter(Boolean);
      const embedId = pathParts[0] === "embed" ? pathParts[1] : null;
      if (embedId && embedId.length === 11) {
        return embedId;
      }
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

const isVideoFile = (file: File): boolean =>
  file.type.startsWith("video/") || /\.(mp4|mov|avi|mkv|webm|m4v)$/i.test(file.name);

const isValidTimeframe = (value: string): boolean => {
  const trimmed = value.trim();
  return !trimmed || TIMEFRAME_PATTERN.test(trimmed);
};

async function requestUploadAuthorization(): Promise<UploadAuthorization> {
  const response = await fetch("/api/upload/authorization", {
    method: "POST",
    cache: "no-store",
  });

  if (!response.ok) {
    const uploadError = await parseApiError(
      response,
      `Upload authorization error: ${response.status}`,
    );
    throw new Error(formatSupportMessage(uploadError));
  }

  return response.json() as Promise<UploadAuthorization>;
}

async function uploadVideoFile(file: File): Promise<string> {
  if (file.size > MAX_VIDEO_UPLOAD_BYTES) {
    throw new Error("Uploaded file is too large. Please upload a video under 1 GB.");
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
        ? "Uploaded file is too large. Please upload a video under 1 GB."
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

async function uploadVideoFileViaProxy(file: File): Promise<string> {
  const formData = new FormData();
  formData.append("video", file);

  const uploadResponse = await fetch("/api/upload", {
    method: "POST",
    body: formData,
  });

  if (!uploadResponse.ok) {
    const fallbackMessage =
      uploadResponse.status === 413
        ? "Uploaded file is too large. Please upload a video under 1 GB."
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

const CLIP_COUNT_OPTIONS = [3, 4, 5, 7, 10];

// Beyond this many templates the caption chips scroll sideways inside their own
// container rather than growing the card.
const CAPTION_CHIP_SCROLL_THRESHOLD = 8;

const FRAMING_OPTIONS: Array<{ value: OutputFormat; label: string; icon: typeof Monitor }> = [
  { value: "vertical", label: "Auto 9:16", icon: Smartphone },
  { value: "vertical_pan", label: "Speaker pan", icon: User },
  { value: "vertical_split", label: "Split-screen", icon: Columns2 },
  { value: "original", label: "Original", icon: Monitor },
];

// Presets write straight into the min/max state the payload already reads.
const CLIP_LENGTH_PRESETS: Array<{ id: string; label: string; min: string; max: string }> = [
  { id: "auto", label: "Auto", min: "15", max: "60" },
  { id: "under-30", label: "Under 30s", min: "15", max: "30" },
  { id: "30-45", label: "30\u201345s", min: "30", max: "45" },
  { id: "45-60", label: "45\u201360s", min: "45", max: "60" },
];

function chipClassName(selected: boolean, emphasis: "solid" | "outline" = "solid") {
  return cn(
    "inline-flex items-center gap-1.5 whitespace-nowrap rounded-md border px-3 py-1.5 text-xs font-medium transition-colors",
    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-stone-900 focus-visible:ring-offset-2",
    "disabled:cursor-not-allowed disabled:opacity-50",
    selected && emphasis === "solid" && "border-stone-900 bg-stone-900 text-white",
    // Outline keeps a light background so a template's own colors stay readable.
    selected && emphasis === "outline" && "border-stone-900 bg-stone-50 text-stone-900 ring-2 ring-stone-900 ring-offset-1",
    !selected && "border-stone-300 bg-background text-stone-600 hover:bg-stone-50",
  );
}

function SettingsGroup({
  title,
  description,
  children,
}: {
  title: string;
  description: string;
  children: ReactNode;
}) {
  return (
    <section className="min-w-0 space-y-1.5">
      <div className="space-y-0.5">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-stone-900">{title}</h3>
        <p className="text-xs text-muted-foreground">{description}</p>
      </div>
      <div className="min-w-0 rounded-lg border divide-y">{children}</div>
    </section>
  );
}

/**
 * One setting per row. Compact controls (a switch, a short segmented group) sit
 * to the right of the label; anything text-flexible passes `stacked` and drops
 * to a full-width line underneath, which is what keeps rows from forcing the
 * card wider than its container.
 * Pass `htmlFor` when a single control owns the label, `labelId` when the row
 * holds a group of controls that reference it with aria-labelledby.
 */
function SettingsRow({
  label,
  description,
  htmlFor,
  labelId,
  stacked = false,
  children,
}: {
  label: string;
  description?: string;
  htmlFor?: string;
  labelId?: string;
  stacked?: boolean;
  children: ReactNode;
}) {
  const labelNode = htmlFor ? (
    <label htmlFor={htmlFor} className="text-sm font-medium text-stone-900">
      {label}
    </label>
  ) : (
    <span id={labelId} className="block text-sm font-medium text-stone-900">
      {label}
    </span>
  );

  const heading = (
    <div className="min-w-0">
      {labelNode}
      {description && <p className="text-xs text-muted-foreground">{description}</p>}
    </div>
  );

  if (stacked) {
    return (
      <div className="min-w-0 space-y-2 px-3 py-2.5">
        {heading}
        <div className="min-w-0">{children}</div>
      </div>
    );
  }

  return (
    <div className="flex min-w-0 flex-wrap items-center justify-between gap-x-4 gap-y-2 px-3 py-2.5">
      {heading}
      <div className="flex min-w-0 flex-wrap items-center gap-2">{children}</div>
    </div>
  );
}

function SegmentedOption({
  selected,
  onSelect,
  disabled,
  icon,
  children,
}: {
  selected: boolean;
  onSelect: () => void;
  disabled?: boolean;
  icon?: ReactNode;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onSelect}
      disabled={disabled}
      aria-pressed={selected}
      className={chipClassName(selected)}
    >
      {icon}
      {children}
    </button>
  );
}

export default function HomeApp() {
  const [url, setUrl] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [sourceType, setSourceType] = useState<SourceType>("youtube");
  const [fileName, setFileName] = useState<string | null>(null);
  const [isDragActive, setIsDragActive] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const { data: session, isPending } = useSession();

  // Font customization states — null means "use the caption template's own value"
  const [fontFamily, setFontFamily] = useState<string | null>(null);
  const [fontSize, setFontSize] = useState<number | null>(null);
  const [fontColor, setFontColor] = useState<string | null>(null);
  const [availableFonts, setAvailableFonts] = useState<FontOption[]>([]);
  const [showAdvanced, setShowAdvanced] = useState(false);
  // Presentation only: forces the custom min/max inputs open even when the
  // current values happen to match a preset.
  const [useCustomClipLength, setUseCustomClipLength] = useState(false);
  const [fontSearch, setFontSearch] = useState("");
  const [fontLoadError, setFontLoadError] = useState<string | null>(null);
  const [isUploadingFont, setIsUploadingFont] = useState(false);
  const fontUploadInputRef = useRef<HTMLInputElement | null>(null);

  // Caption template state
  const [captionTemplate, setCaptionTemplate] = useState("default");
  const [availableTemplates, setAvailableTemplates] = useState<Array<{ id: string, name: string, description: string, animation: string, font_family?: string, font_size?: number, font_color?: string }>>([]);
  const [outputFormat, setOutputFormat] = useState<OutputFormat>("vertical");
  const [addSubtitles, setAddSubtitles] = useState(true);
  const [cutLongPauses, setCutLongPauses] = useState(false);
  const [pauseThresholdMs, setPauseThresholdMs] = useState("900");
  const [removeFillerWords, setRemoveFillerWords] = useState(false);
  const [filteredWords, setFilteredWords] = useState("");
  const [clipBrief, setClipBrief] = useState("");
  const [clipKeywords, setClipKeywords] = useState("");
  const [clipCount, setClipCount] = useState("4");
  const [clipMinSeconds, setClipMinSeconds] = useState("25");
  const [clipMaxSeconds, setClipMaxSeconds] = useState("50");
  const [timeframeStart, setTimeframeStart] = useState("");
  const [timeframeEnd, setTimeframeEnd] = useState("");
  const [analysisMode, setAnalysisMode] = useState<"transcript" | "multimodal">("transcript");
  const [workspaceId, setWorkspaceId] = useState("personal");
  const [brandKitId, setBrandKitId] = useState("none");
  const [workspaces, setWorkspaces] = useState<Array<{ id: string; name: string }>>([]);
  const [brandKits, setBrandKits] = useState<Array<{ id: string; name: string; is_default: boolean }>>([]);

  // Latest task state
  const [latestTask, setLatestTask] = useState<LatestTask | null>(null);
  const [isLoadingLatest, setIsLoadingLatest] = useState(false);
  const [billingSummary, setBillingSummary] = useState<BillingSummary | null>(null);
  const apiUrl = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';
  const taskApiUrl = "/api/tasks";
  const youtubeThumbnailUrl = sourceType === "youtube" ? getYouTubeThumbnailUrl(url) : null;

  const refreshFonts = useCallback(async () => {
    try {
      setFontLoadError(null);
      const response = await fetch("/api/fonts", {
        cache: "no-store",
      });
      if (!response.ok) {
        throw new Error(`Failed to load fonts (${response.status})`);
      }

      const data = await response.json();
      const fonts: FontOption[] = data.fonts || [];
      setAvailableFonts(fonts);

      const fontFaceStyles = fonts.map((font) => {
        const format = font.format === "otf" ? "opentype" : "truetype";
        return `
          @font-face {
            font-family: '${font.name}';
            src: url('/api/fonts/${font.name}') format('${format}');
            font-weight: normal;
            font-style: normal;
          }
        `;
      }).join("\n");

      const styleElement = document.createElement("style");
      styleElement.id = "custom-fonts";
      styleElement.innerHTML = fontFaceStyles;

      const existingStyle = document.getElementById("custom-fonts");
      if (existingStyle) {
        existingStyle.remove();
      }

      document.head.appendChild(styleElement);
    } catch (error) {
      console.error("Failed to load fonts:", error);
      setFontLoadError("Could not load fonts right now.");
    }
  }, []);

  useEffect(() => {
    void refreshFonts();
  }, [refreshFonts]);

  useEffect(() => {
    if (!session?.user?.id) return;
    Promise.all([
      fetch("/api/workflows/workspaces", { cache: "no-store" }).then((response) => response.ok ? response.json() : { workspaces: [] }),
      fetch("/api/workflows/brand-kits", { cache: "no-store" }).then((response) => response.ok ? response.json() : { brand_kits: [] }),
    ]).then(([workspaceData, kitData]) => {
      setWorkspaces(workspaceData.workspaces || []);
      const kits = kitData.brand_kits || [];
      setBrandKits(kits);
      const defaultKit = kits.find((kit: { is_default: boolean }) => kit.is_default);
      if (defaultKit) setBrandKitId(defaultKit.id);
    }).catch(() => undefined);
  }, [session?.user?.id]);

  // Load caption templates
  useEffect(() => {
    const loadTemplates = async () => {
      try {
        const response = await fetch(`${apiUrl}/caption-templates`);
        if (response.ok) {
          const data = await response.json();
          setAvailableTemplates(data.templates || []);
        }
      } catch (error) {
        console.error('Failed to load caption templates:', error);
      }
    };

    loadTemplates();
  }, [apiUrl]);

  // Load latest task
  useEffect(() => {
    const fetchLatestTask = async () => {
      if (!session?.user?.id) return;

      try {
        setIsLoadingLatest(true);
        const response = await fetch(`${taskApiUrl}/`, {
          cache: "no-store",
        });

        if (response.ok) {
          const data = await response.json();
          if (data.tasks && data.tasks.length > 0) {
            setLatestTask(data.tasks[0]); // Get the first (latest) task
          }
        }
      } catch (error) {
        console.error('Failed to load latest task:', error);
      } finally {
        setIsLoadingLatest(false);
      }
    };

    fetchLatestTask();
  }, [session?.user?.id, taskApiUrl]);

  useEffect(() => {
    const fetchBillingSummary = async () => {
      if (!session?.user?.id) return;

      try {
        const response = await fetch("/api/tasks/billing-summary", {
          cache: "no-store",
        });

        if (!response.ok) {
          return;
        }

        const data: BillingSummary = await response.json();
        setBillingSummary(data);
      } catch (error) {
        console.error("Failed to load billing summary:", error);
      }
    };

    fetchBillingSummary();
  }, [session?.user?.id, apiUrl]);

  // Always treat file input as uncontrolled, and store file in a ref
  const fileRef = useRef<File | null>(null);

  const clearSelectedFile = () => {
    fileRef.current = null;
    setFileName(null);
    if (fileInputRef.current) {
      fileInputRef.current.value = "";
    }
  };

  // Shared by the file picker and the dropzone so both validate identically.
  const acceptSelectedFile = (file: File | null) => {
    if (!file) {
      clearSelectedFile();
      return;
    }

    // A rejected file must also drop whatever was selected before it, or the
    // form stays submittable and would upload the stale file.
    if (!isVideoFile(file)) {
      clearSelectedFile();
      setError("That file isn't a video. Please choose an MP4, MOV, or AVI file.");
      return;
    }

    if (file.size > MAX_VIDEO_UPLOAD_BYTES) {
      clearSelectedFile();
      setError("Uploaded file is too large. Please upload a video under 1 GB.");
      return;
    }

    setError(null);
    fileRef.current = file;
    setFileName(file.name);
  };

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    acceptSelectedFile(e.target.files?.[0] || null);
  };

  const handleSourceTypeChange = (nextSourceType: SourceType) => {
    setSourceType(nextSourceType);
    if (nextSourceType !== "upload") {
      clearSelectedFile();
    }
  };

  const handleTemplateChange = (templateId: string) => {
    setCaptionTemplate(templateId);
    // Font family/size/color are left as-is: null stays null (meaning "use
    // this template's own style"), and any explicit customization the user
    // made carries over to the newly selected template.
  };

  const handleFontUpload = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) {
      return;
    }

    const isSupported = file.name.toLowerCase().endsWith(".ttf") || file.name.toLowerCase().endsWith(".otf");
    if (!isSupported) {
      setError("Only .ttf and .otf files are supported for custom fonts.");
      return;
    }

    try {
      setIsUploadingFont(true);
      setError(null);
      const formData = new FormData();
      formData.append("file", file);

      const response = await fetch("/api/fonts/upload", {
        method: "POST",
        body: formData,
      });

      if (!response.ok) {
        const parsed = await parseApiError(response, "Failed to upload font");
        setError(formatSupportMessage(parsed));
        return;
      }

      const data = await response.json();
      if (data?.font?.name) {
        setFontFamily(data.font.name);
      }
      await refreshFonts();
    } catch (uploadError) {
      console.error("Failed to upload font:", uploadError);
      setError("Failed to upload font. Please try again.");
    } finally {
      setIsUploadingFont(false);
    }
  };

  const filteredFonts = availableFonts.filter((font) => {
    const keyword = fontSearch.toLowerCase().trim();
    if (!keyword) {
      return true;
    }

    return font.display_name.toLowerCase().includes(keyword) || font.name.toLowerCase().includes(keyword);
  });

  const canUploadCustomFonts =
    !billingSummary?.monetization_enabled ||
    (isPaidBillingPlan(billingSummary.plan) && ["active", "trialing"].includes(billingSummary.subscription_status));

  // Effective values for the live preview only — falls back to the selected
  // template's own style (or a sane default) whenever the user hasn't
  // explicitly customized a field. The actual submitted payload keeps nulls.
  const selectedTemplate = availableTemplates.find((template) => template.id === captionTemplate);
  const previewFontFamily = fontFamily ?? selectedTemplate?.font_family ?? "TikTokSans-Regular";
  const previewFontSize = fontSize ?? selectedTemplate?.font_size ?? 24;
  const previewFontColor = fontColor ?? selectedTemplate?.font_color ?? "#FFFFFF";
  const generationRequiresUpgrade =
    Boolean(billingSummary?.monetization_enabled && !billingSummary.can_create_task);
  const generationGateMessage =
    billingSummary?.reason || "Choose a paid plan to process videos.";
  const generationControlsDisabled = isLoading || generationRequiresUpgrade;

  const handleDragOver = (event: React.DragEvent<HTMLElement>) => {
    if (generationControlsDisabled) return;
    event.preventDefault();
    setIsDragActive(true);
  };

  const handleDragLeave = (event: React.DragEvent<HTMLElement>) => {
    event.preventDefault();
    setIsDragActive(false);
  };

  const handleDrop = (event: React.DragEvent<HTMLElement>) => {
    event.preventDefault();
    setIsDragActive(false);
    if (generationControlsDisabled) return;
    acceptSelectedFile(event.dataTransfer.files?.[0] || null);
  };

  // Inline validation for the advanced numbers — the backend clamps silently,
  // so anything out of range would otherwise be rewritten without telling anyone.
  const parsedClipCount = Number(clipCount);
  const parsedClipMinSeconds = Number(clipMinSeconds);
  const parsedClipMaxSeconds = Number(clipMaxSeconds);

  const clipCountError =
    !Number.isInteger(parsedClipCount) || parsedClipCount < MIN_CLIP_COUNT || parsedClipCount > MAX_CLIP_COUNT
      ? `Pick between ${MIN_CLIP_COUNT} and ${MAX_CLIP_COUNT} clips.`
      : null;
  const clipMinSecondsError =
    !Number.isFinite(parsedClipMinSeconds) || parsedClipMinSeconds < MIN_CLIP_SECONDS || parsedClipMinSeconds > MAX_CLIP_SECONDS
      ? `Must be ${MIN_CLIP_SECONDS}–${MAX_CLIP_SECONDS} seconds.`
      : null;
  const clipMaxSecondsError =
    !Number.isFinite(parsedClipMaxSeconds) || parsedClipMaxSeconds < MIN_CLIP_SECONDS || parsedClipMaxSeconds > MAX_CLIP_SECONDS
      ? `Must be ${MIN_CLIP_SECONDS}–${MAX_CLIP_SECONDS} seconds.`
      : !clipMinSecondsError && parsedClipMaxSeconds < parsedClipMinSeconds
        ? "Must be at least the minimum length."
        : null;
  const timeframeStartError = isValidTimeframe(timeframeStart) ? null : "Use MM:SS or HH:MM:SS.";
  const timeframeEndError = isValidTimeframe(timeframeEnd) ? null : "Use MM:SS or HH:MM:SS.";

  const hasAdvancedErrors = Boolean(
    clipCountError || clipMinSecondsError || clipMaxSecondsError || timeframeStartError || timeframeEndError,
  );

  const activeClipLengthPreset = CLIP_LENGTH_PRESETS.find(
    (preset) => preset.min === clipMinSeconds.trim() && preset.max === clipMaxSeconds.trim(),
  );
  // No matching preset means the user is already on custom values.
  const clipLengthIsCustom = useCustomClipLength || !activeClipLengthPreset;

  const hasSource = sourceType === "upload" ? Boolean(fileName) : Boolean(url.trim());

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();

    if (sourceType === "upload" && !fileRef.current) return;
    if (sourceType !== "upload" && !url.trim()) return;
    if (!session?.user?.id) return;
    if (generationRequiresUpgrade) {
      setError(generationGateMessage);
      return;
    }
    if (hasAdvancedErrors) {
      setShowAdvanced(true);
      setError("Some advanced settings are out of range. Fix the highlighted fields and try again.");
      return;
    }

    setIsLoading(true);
    setError(null);

    const fontOptions = buildFontOptionsPayload(fontFamily, fontSize, fontColor);

    try {
      let videoUrl = url;
      const normalizedPauseThreshold = Number.isFinite(Number(pauseThresholdMs))
        ? Math.max(250, Math.min(3000, Math.round(Number(pauseThresholdMs))))
        : 900;
      const normalizedFilteredWords = filteredWords
        .split(",")
        .map((word) => word.trim().toLowerCase())
        .filter(Boolean);
      const generationPreferences = {
        prompt: clipBrief.trim(),
        keywords: clipKeywords
          .split(",")
          .map((keyword) => keyword.trim())
          .filter(Boolean),
        clip_count: Math.max(1, Math.min(10, Number(clipCount) || 4)),
        clip_min_seconds: Math.max(15, Math.min(60, Number(clipMinSeconds) || 25)),
        clip_max_seconds: Math.max(15, Math.min(60, Number(clipMaxSeconds) || 50)),
        timeframe_start: timeframeStart.trim() || null,
        timeframe_end: timeframeEnd.trim() || null,
        analysis_mode: analysisMode,
      };

      // If uploading file, upload it first
      if (sourceType === "upload" && fileRef.current) {
        videoUrl = await uploadVideoFile(fileRef.current);
      }

      // Step 1: Start the task (using new refactored endpoint)
      const startResponse = await fetch("/api/tasks/create", {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          source: {
            url: videoUrl,
            title: null
          },
          font_options: fontOptions,
          caption_template: captionTemplate,
          processing_mode: "fast",
          output_format: outputFormat,
          add_subtitles: addSubtitles,
          cut_long_pauses: cutLongPauses,
          pause_threshold_ms: normalizedPauseThreshold,
          remove_filler_words: removeFillerWords,
          filtered_words: normalizedFilteredWords,
          generation_preferences: generationPreferences,
          workspace_id: workspaceId === "personal" ? null : workspaceId,
          brand_kit_id: brandKitId === "none" ? null : brandKitId,
        }),
      });

      if (!startResponse.ok) {
        const startError = await parseApiError(
          startResponse,
          `API error: ${startResponse.status}`
        );
        throw new Error(formatSupportMessage(startError));
      }

      const startResult = await startResponse.json();
      const taskIdFromStart = startResult.task_id;
      track("task_created", {
        source_type: sourceType,
        caption_template: captionTemplate,
        output_format: outputFormat,
        add_subtitles: addSubtitles,
        cut_long_pauses: cutLongPauses,
        pause_threshold_ms: normalizedPauseThreshold,
        remove_filler_words: removeFillerWords,
        filtered_words: normalizedFilteredWords,
        has_clip_brief: Boolean(generationPreferences.prompt),
        clip_count: generationPreferences.clip_count,
        clip_duration: `${generationPreferences.clip_min_seconds}-${generationPreferences.clip_max_seconds}`,
        processing_mode: "fast",
      });

      // Only clear the source on success — a failed submit keeps what was typed.
      setUrl("");
      clearSelectedFile();

      // Redirect immediately to the task page
      window.location.href = `/tasks/${taskIdFromStart}`;
    } catch (error) {
      console.error('Error processing video:', error);
      setError(error instanceof Error ? error.message : 'Failed to process video. Please try again.');
      setIsLoading(false);
    }
  };

  if (isPending || !session?.user) {
    return (
      <div className="min-h-screen bg-white flex items-center justify-center p-4">
        <div className="space-y-4">
          <Skeleton className="h-4 w-32 mx-auto" />
          <Skeleton className="h-4 w-48 mx-auto" />
          <Skeleton className="h-4 w-24 mx-auto" />
        </div>
      </div>
    );
  }

  return (
    <AppShell billingSummary={billingSummary} className="bg-white">
      {/* Main Content */}
      <div className="max-w-6xl mx-auto px-6 py-10">
        {/* Latest Generation Banner */}
        {latestTask && (
          <Link href={`/tasks/${latestTask.id}`} className="block mb-8">
            <div className="flex items-center justify-between p-4 rounded-xl border border-stone-200 bg-stone-50/50 hover:bg-stone-50 transition-colors group">
              <div className="flex items-center gap-4 min-w-0">
                <div className="flex-shrink-0 w-10 h-10 rounded-lg bg-stone-900 flex items-center justify-center">
                  <Film className="w-5 h-5 text-white" />
                </div>
                <div className="min-w-0">
                  <p className="text-sm font-medium text-stone-900 truncate">
                    {latestTask.source_title}
                  </p>
                  <div className="flex items-center gap-2 text-xs text-stone-500 mt-0.5">
                    <span className="capitalize">{latestTask.source_type}</span>
                    <span>&middot;</span>
                    <span>{new Date(latestTask.created_at).toLocaleDateString()}</span>
                    <span>&middot;</span>
                    <span>{latestTask.clips_count} {latestTask.clips_count === 1 ? "clip" : "clips"}</span>
                  </div>
                </div>
              </div>
              <div className="flex items-center gap-3 flex-shrink-0">
                {latestTask.status === "completed" ? (
                  <Badge className="bg-green-100 text-green-800 text-xs">
                    <CheckCircle className="w-3 h-3 mr-1" />
                    Completed
                  </Badge>
                ) : latestTask.status === "processing" ? (
                  <Badge className="bg-blue-100 text-blue-800 text-xs">
                    <Loader2 className="w-3 h-3 animate-spin" />
                    Processing
                  </Badge>
                ) : (
                  <Badge variant="outline" className="text-xs">{latestTask.status}</Badge>
                )}
                <ArrowRight className="w-4 h-4 text-stone-400 group-hover:text-stone-600 transition-colors" />
              </div>
            </div>
          </Link>
        )}

        {isLoadingLatest && (
          <div className="mb-8 p-4 rounded-xl border border-stone-200">
            <div className="flex items-center gap-4">
              <Skeleton className="w-10 h-10 rounded-lg" />
              <div>
                <Skeleton className="h-4 w-48 mb-1.5" />
                <Skeleton className="h-3 w-32" />
              </div>
            </div>
          </div>
        )}

        {/* Two Column Layout */}
        <div className="flex flex-col lg:flex-row gap-10 items-start">
          {/* Left Column — Form.
              w-full matters: items-start makes this a fit-content flex item in the
              stacked layout, so without it the column sizes to its widest
              nowrap content (chips) and scrolls the whole page sideways. */}
          <div className="w-full min-w-0 flex-1 lg:w-auto">
            <div className="mb-8">
              <h1 className="text-2xl font-bold text-stone-900 mb-2">
                Create New Clip
              </h1>
              <p className="text-muted-foreground">
                {generationRequiresUpgrade
                  ? "Video processing is available on paid plans."
                  : "Paste a YouTube link or upload a video — AI handles the rest."}
              </p>
            </div>

            <form onSubmit={handleSubmit} className="space-y-6">
              {generationRequiresUpgrade && (
                <Alert className="border-amber-200 bg-amber-50">
                  <AlertCircle className="h-4 w-4 text-amber-600" />
                  <AlertDescription className="text-sm text-amber-900">
                    <span className="font-medium">{generationGateMessage}</span>{" "}
                    Free accounts can browse SupoClip, but video generation requires a paid plan.
                    <Link href="/settings" className="ml-1 font-semibold underline underline-offset-2">
                      Upgrade in settings
                    </Link>.
                  </AlertDescription>
                </Alert>
              )}

              {/* Source Type Tabs */}
              <div className="space-y-3">
                <div className="flex flex-wrap gap-2">
                  {SOURCE_TABS.map((tab) => {
                    const TabIcon = tab.icon;
                    return (
                      <button
                        key={tab.id}
                        type="button"
                        onClick={() => handleSourceTypeChange(tab.id)}
                        disabled={generationControlsDisabled}
                        aria-pressed={sourceType === tab.id}
                        className={`flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-all ${
                          sourceType === tab.id
                            ? "bg-stone-900 text-white shadow-sm"
                            : "bg-stone-100 text-stone-600 hover:bg-stone-200"
                        }`}
                      >
                        <TabIcon className="w-4 h-4" />
                        {tab.label}
                      </button>
                    );
                  })}
                </div>

                {/* URL / Upload Input */}
                {sourceType !== "upload" ? (
                  <div className="relative">
                    <label htmlFor="source-url" className="sr-only">
                      {sourceType === "youtube" ? "YouTube video URL" : "Video URL"}
                    </label>
                    {sourceType === "youtube" ? (
                      <Youtube className="absolute left-4 top-1/2 -translate-y-1/2 w-5 h-5 text-stone-400" />
                    ) : (
                      <Link2 className="absolute left-4 top-1/2 -translate-y-1/2 w-5 h-5 text-stone-400" />
                    )}
                    <Input
                      id="source-url"
                      type="url"
                      placeholder={sourceType === "youtube" ? "https://www.youtube.com/watch?v=..." : "Vimeo, Twitch, Drive, Dropbox, Loom, Zoom, or StreamYard URL"}
                      value={url}
                      onChange={(e) => setUrl(e.target.value)}
                      disabled={generationControlsDisabled}
                      className="h-14 pl-12 text-base rounded-xl border-stone-300 focus:border-stone-500 placeholder:text-stone-400"
                    />
                  </div>
                ) : (
                  <div>
                    <input
                      id="video-upload"
                      type="file"
                      accept="video/*"
                      ref={fileInputRef}
                      onChange={handleFileChange}
                      disabled={generationControlsDisabled}
                      className="hidden"
                    />
                    <button
                      type="button"
                      onClick={() => fileInputRef.current?.click()}
                      disabled={generationControlsDisabled}
                      onDragOver={handleDragOver}
                      onDragEnter={handleDragOver}
                      onDragLeave={handleDragLeave}
                      onDrop={handleDrop}
                      className={`relative w-full border-2 border-dashed rounded-xl p-8 text-center transition-colors cursor-pointer disabled:cursor-not-allowed disabled:opacity-60 ${
                        isDragActive
                          ? "border-stone-900 bg-stone-100"
                          : "border-stone-300 hover:border-stone-400"
                      }`}
                    >
                      {/* Children ignore pointer events so dragging over them
                          doesn't fire dragleave on the dropzone itself. */}
                      <span className="block pointer-events-none">
                        <Upload className="w-8 h-8 text-stone-400 mx-auto mb-3" />
                        {fileName ? (
                          <>
                            <span className="block text-sm font-medium text-stone-900">{fileName}</span>
                            <span className="block text-xs text-muted-foreground mt-1">Click or drop another file to replace it</span>
                          </>
                        ) : (
                          <>
                            <span className="block text-sm font-medium text-stone-700">
                              {isDragActive ? "Drop the video to upload" : "Drop a video file here or click to browse"}
                            </span>
                            <span className="block text-xs text-muted-foreground mt-1">MP4, MOV, AVI up to 1 GB</span>
                          </>
                        )}
                      </span>
                    </button>
                  </div>
                )}
              </div>

              {/* Workspace / brand kit — only rendered when the account has any */}
              {(workspaces.length > 0 || brandKits.length > 0) && (
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 rounded-lg border bg-stone-50 p-3">
                  <div className="space-y-1.5">
                    <label htmlFor="workspace-select" className="text-xs font-medium text-muted-foreground">Workspace</label>
                    <Select value={workspaceId} onValueChange={setWorkspaceId} disabled={generationControlsDisabled}>
                      <SelectTrigger id="workspace-select" className="bg-white w-full"><SelectValue /></SelectTrigger>
                      <SelectContent>
                        <SelectItem value="personal">Personal</SelectItem>
                        {workspaces.map((workspace) => <SelectItem key={workspace.id} value={workspace.id}>{workspace.name}</SelectItem>)}
                      </SelectContent>
                    </Select>
                  </div>
                  <div className="space-y-1.5">
                    <label htmlFor="brand-kit-select" className="text-xs font-medium text-muted-foreground">Brand kit</label>
                    <Select value={brandKitId} onValueChange={setBrandKitId} disabled={generationControlsDisabled}>
                      <SelectTrigger id="brand-kit-select" className="bg-white w-full"><SelectValue /></SelectTrigger>
                      <SelectContent>
                        <SelectItem value="none">No brand kit</SelectItem>
                        {brandKits.map((kit) => <SelectItem key={kit.id} value={kit.id}>{kit.name}{kit.is_default ? " · Default" : ""}</SelectItem>)}
                      </SelectContent>
                    </Select>
                  </div>
                </div>
              )}

              {/* Caption style — the one style choice on the primary path */}
              <Card className="border-stone-200">
                <CardContent className="px-4 pt-0 pb-4 space-y-2">
                  <span id="caption-style-label" className="block text-sm font-medium text-stone-900">
                    Caption Style
                  </span>
                  <div
                    role="group"
                    aria-labelledby="caption-style-label"
                    className={cn(
                      "flex gap-1.5",
                      availableTemplates.length > CAPTION_CHIP_SCROLL_THRESHOLD
                        ? "overflow-x-auto pb-1"
                        : "flex-wrap",
                    )}
                  >
                    {availableTemplates.length > 0 ? (
                      availableTemplates.map((template) => {
                        const selected = captionTemplate === template.id;
                        return (
                          <button
                            key={template.id}
                            type="button"
                            onClick={() => handleTemplateChange(template.id)}
                            disabled={generationControlsDisabled}
                            aria-pressed={selected}
                            title={template.description}
                            className={chipClassName(selected, "outline")}
                          >
                            {template.font_color && (
                              <span
                                aria-hidden="true"
                                className="h-2.5 w-2.5 shrink-0 rounded-full border border-stone-300"
                                style={{ backgroundColor: template.font_color }}
                              />
                            )}
                            <span
                              style={{
                                fontFamily: template.font_family
                                  ? `'${template.font_family}', system-ui, sans-serif`
                                  : undefined,
                              }}
                            >
                              {template.name}
                            </span>
                          </button>
                        );
                      })
                    ) : (
                      <button
                        type="button"
                        onClick={() => handleTemplateChange("default")}
                        disabled={generationControlsDisabled}
                        aria-pressed={captionTemplate === "default"}
                        className={chipClassName(captionTemplate === "default", "outline")}
                      >
                        Default
                      </button>
                    )}
                  </div>
                </CardContent>
              </Card>

              {/* Everything else — sensible defaults, so it stays collapsed */}
              <CollapsibleSection
                id="advanced-settings"
                title="Advanced settings"
                summary="Content, clip shape, captions and cleanup — the defaults work well."
                open={showAdvanced}
                onOpenChange={setShowAdvanced}
              >
                {/* More air between groups than between rows, so each group reads as a unit. */}
                <div className="min-w-0 space-y-6">
                  <SettingsGroup title="Content" description="What the AI looks for.">
                    <SettingsRow
                      stacked
                      htmlFor="clip-brief"
                      label="What should we look for?"
                      description="Steers which moments get picked."
                    >
                      <Textarea
                        id="clip-brief"
                        value={clipBrief}
                        onChange={(event) => setClipBrief(event.target.value)}
                        disabled={generationControlsDisabled}
                        maxLength={2000}
                        placeholder="Example: tactical pricing mistakes with a surprising lesson. Skip intros and sponsor reads."
                        className="min-h-20 bg-background"
                      />
                    </SettingsRow>

                    <SettingsRow
                      stacked
                      htmlFor="clip-keywords"
                      label="Must-include topics"
                      description="Comma separated."
                    >
                      <Input
                        id="clip-keywords"
                        value={clipKeywords}
                        onChange={(event) => setClipKeywords(event.target.value)}
                        disabled={generationControlsDisabled}
                        placeholder="pricing, churn, onboarding"
                        className="bg-background"
                      />
                    </SettingsRow>

                    <SettingsRow
                      stacked
                      labelId="analysis-depth-label"
                      label="Analysis depth"
                      description="With visuals also ranks scene changes, motion and reactions."
                    >
                      <div role="group" aria-labelledby="analysis-depth-label" className="flex flex-wrap gap-1.5">
                        <SegmentedOption
                          selected={analysisMode === "transcript"}
                          onSelect={() => setAnalysisMode("transcript")}
                          disabled={generationControlsDisabled}
                        >
                          Transcript only
                        </SegmentedOption>
                        <SegmentedOption
                          selected={analysisMode === "multimodal"}
                          onSelect={() => setAnalysisMode("multimodal")}
                          disabled={generationControlsDisabled}
                        >
                          With visuals
                        </SegmentedOption>
                      </div>
                    </SettingsRow>

                    <SettingsRow
                      stacked
                      labelId="timeframe-label"
                      label="Analyze only part of the video"
                      description="Leave empty for the whole video."
                    >
                      <div className="space-y-1">
                        <div className="flex flex-wrap items-center gap-2" role="group" aria-labelledby="timeframe-label">
                          <label htmlFor="timeframe-start" className="text-xs text-muted-foreground">From</label>
                          <Input
                            id="timeframe-start"
                            value={timeframeStart}
                            onChange={(event) => setTimeframeStart(event.target.value)}
                            disabled={generationControlsDisabled}
                            placeholder="12:30"
                            aria-invalid={Boolean(timeframeStartError)}
                            aria-describedby={timeframeStartError ? "timeframe-start-error" : undefined}
                            className="w-24 bg-background"
                          />
                          <label htmlFor="timeframe-end" className="text-xs text-muted-foreground">to</label>
                          <Input
                            id="timeframe-end"
                            value={timeframeEnd}
                            onChange={(event) => setTimeframeEnd(event.target.value)}
                            disabled={generationControlsDisabled}
                            placeholder="24:00"
                            aria-invalid={Boolean(timeframeEndError)}
                            aria-describedby={timeframeEndError ? "timeframe-end-error" : undefined}
                            className="w-24 bg-background"
                          />
                        </div>
                        {timeframeStartError && <p id="timeframe-start-error" className="text-xs text-red-600">Start: {timeframeStartError}</p>}
                        {timeframeEndError && <p id="timeframe-end-error" className="text-xs text-red-600">End: {timeframeEndError}</p>}
                      </div>
                    </SettingsRow>
                  </SettingsGroup>

                  <SettingsGroup title="Clips" description="How many clips and what shape.">
                    <SettingsRow
                      labelId="clip-count-label"
                      label="Number of clips"
                      description="Up to this many."
                    >
                      <div className="space-y-1">
                        <div role="group" aria-labelledby="clip-count-label" className="flex flex-wrap gap-1.5">
                          {CLIP_COUNT_OPTIONS.map((option) => (
                            <SegmentedOption
                              key={option}
                              selected={Number(clipCount) === option}
                              onSelect={() => setClipCount(String(option))}
                              disabled={generationControlsDisabled}
                            >
                              {option === MAX_CLIP_COUNT ? `${option} max` : option}
                            </SegmentedOption>
                          ))}
                        </div>
                        {clipCountError && <p id="clip-count-error" className="text-xs text-red-600">{clipCountError}</p>}
                      </div>
                    </SettingsRow>

                    <SettingsRow
                      stacked
                      labelId="clip-length-label"
                      label="Clip length"
                      description={`${MIN_CLIP_SECONDS}\u2013${MAX_CLIP_SECONDS} seconds.`}
                    >
                      <div className="space-y-2">
                        <div role="group" aria-labelledby="clip-length-label" className="flex flex-wrap gap-1.5">
                          {CLIP_LENGTH_PRESETS.map((preset) => (
                            <SegmentedOption
                              key={preset.id}
                              selected={!useCustomClipLength && activeClipLengthPreset?.id === preset.id}
                              onSelect={() => {
                                setClipMinSeconds(preset.min);
                                setClipMaxSeconds(preset.max);
                                setUseCustomClipLength(false);
                              }}
                              disabled={generationControlsDisabled}
                            >
                              {preset.label}
                            </SegmentedOption>
                          ))}
                          <SegmentedOption
                            selected={clipLengthIsCustom}
                            onSelect={() => setUseCustomClipLength(true)}
                            disabled={generationControlsDisabled}
                          >
                            Custom
                          </SegmentedOption>
                        </div>

                        {clipLengthIsCustom && (
                          <div className="space-y-1">
                            <div className="flex flex-wrap items-center gap-2">
                              <label htmlFor="clip-min-seconds" className="sr-only">Minimum clip length in seconds</label>
                              <Input
                                id="clip-min-seconds"
                                type="number"
                                min={MIN_CLIP_SECONDS}
                                max={MAX_CLIP_SECONDS}
                                value={clipMinSeconds}
                                onChange={(event) => setClipMinSeconds(event.target.value)}
                                disabled={generationControlsDisabled}
                                aria-invalid={Boolean(clipMinSecondsError)}
                                aria-describedby={clipMinSecondsError ? "clip-min-seconds-error" : undefined}
                                className="w-20 bg-background"
                              />
                              <span aria-hidden="true" className="text-muted-foreground">&ndash;</span>
                              <label htmlFor="clip-max-seconds" className="sr-only">Maximum clip length in seconds</label>
                              <Input
                                id="clip-max-seconds"
                                type="number"
                                min={MIN_CLIP_SECONDS}
                                max={MAX_CLIP_SECONDS}
                                value={clipMaxSeconds}
                                onChange={(event) => setClipMaxSeconds(event.target.value)}
                                disabled={generationControlsDisabled}
                                aria-invalid={Boolean(clipMaxSecondsError)}
                                aria-describedby={clipMaxSecondsError ? "clip-max-seconds-error" : undefined}
                                className="w-20 bg-background"
                              />
                              <span className="text-sm text-muted-foreground">seconds</span>
                            </div>
                            {clipMinSecondsError && <p id="clip-min-seconds-error" className="text-xs text-red-600">Minimum: {clipMinSecondsError}</p>}
                            {clipMaxSecondsError && <p id="clip-max-seconds-error" className="text-xs text-red-600">Maximum: {clipMaxSecondsError}</p>}
                          </div>
                        )}
                      </div>
                    </SettingsRow>

                    <SettingsRow
                      stacked
                      labelId="framing-label"
                      label="Framing"
                      description="How clips are reframed for social video."
                    >
                      <div role="group" aria-labelledby="framing-label" className="flex flex-wrap gap-1.5">
                        {FRAMING_OPTIONS.map((option) => {
                          const FramingIcon = option.icon;
                          return (
                            <SegmentedOption
                              key={option.value}
                              selected={outputFormat === option.value}
                              onSelect={() => setOutputFormat(option.value)}
                              disabled={generationControlsDisabled}
                              icon={<FramingIcon className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />}
                            >
                              {option.label}
                            </SegmentedOption>
                          );
                        })}
                      </div>
                    </SettingsRow>
                  </SettingsGroup>

                  <SettingsGroup title="Captions" description="Burned-in subtitles and how they look.">
                    <SettingsRow
                      htmlFor="add-subtitles"
                      label="Burn in subtitles"
                      description="Style is chosen above in Caption Style."
                    >
                      <Switch
                        id="add-subtitles"
                        checked={addSubtitles}
                        onCheckedChange={setAddSubtitles}
                        disabled={generationControlsDisabled}
                      />
                    </SettingsRow>

                    {addSubtitles && (
                      <>
                        <SettingsRow
                          stacked
                          htmlFor="font-family-select"
                          label="Font"
                          description={`${availableFonts.length} available. Template default follows your caption style.`}
                        >
                          <div className="space-y-2">
                            {availableFonts.length > FONT_SEARCH_THRESHOLD && (
                              <>
                                <label htmlFor="font-search" className="sr-only">Search fonts</label>
                                <Input
                                  id="font-search"
                                  type="text"
                                  value={fontSearch}
                                  onChange={(e) => setFontSearch(e.target.value)}
                                  placeholder="Search fonts"
                                  disabled={generationControlsDisabled}
                                  className="bg-background"
                                />
                              </>
                            )}
                            <div className="flex min-w-0 items-center gap-2">
                              <Select
                                value={fontFamily ?? FONT_TEMPLATE_DEFAULT_VALUE}
                                onValueChange={(value) => setFontFamily(value === FONT_TEMPLATE_DEFAULT_VALUE ? null : value)}
                                disabled={generationControlsDisabled}
                              >
                                <SelectTrigger id="font-family-select" className="min-w-0 flex-1 bg-background">
                                  <SelectValue placeholder="Template default" />
                                </SelectTrigger>
                                <SelectContent>
                                  <SelectItem value={FONT_TEMPLATE_DEFAULT_VALUE}>Template default</SelectItem>
                                  {filteredFonts.map((font) => (
                                    <SelectItem key={font.name} value={font.name}>
                                      <span style={{ fontFamily: `'${font.name}', system-ui, sans-serif` }}>
                                        {font.display_name}
                                      </span>
                                    </SelectItem>
                                  ))}
                                  {availableFonts.length > 0 && filteredFonts.length === 0 && (
                                    <SelectItem value="__no_match__" disabled>
                                      No fonts match your search
                                    </SelectItem>
                                  )}
                                </SelectContent>
                              </Select>
                              <input
                                ref={fontUploadInputRef}
                                type="file"
                                accept=".ttf,.otf"
                                onChange={handleFontUpload}
                                className="hidden"
                                aria-label="Upload a custom font file"
                              />
                              <Button
                                type="button"
                                variant="ghost"
                                size="sm"
                                className="shrink-0"
                                disabled={generationControlsDisabled || isUploadingFont || !canUploadCustomFonts}
                                onClick={() => fontUploadInputRef.current?.click()}
                              >
                                {isUploadingFont ? "Uploading..." : "Upload"}
                              </Button>
                            </div>
                            {!canUploadCustomFonts && (
                              <p className="text-xs text-amber-700">Custom font upload is available on paid plans.</p>
                            )}
                            {fontLoadError && <p className="text-xs text-amber-700">{fontLoadError}</p>}
                          </div>
                        </SettingsRow>

                        <SettingsRow stacked labelId="font-size-label" label="Size" description="Relative to the caption template.">
                          <div role="group" aria-labelledby="font-size-label" className="flex flex-wrap gap-1.5">
                            {FONT_SIZE_OPTIONS.map((option) => (
                              <SegmentedOption
                                key={option.label}
                                selected={fontSize === option.value}
                                onSelect={() => setFontSize(option.value)}
                                disabled={generationControlsDisabled}
                              >
                                {option.label}
                              </SegmentedOption>
                            ))}
                          </div>
                        </SettingsRow>

                        <SettingsRow
                          stacked
                          labelId="font-color-label"
                          label="Color"
                          description="Template default inherits the caption style."
                        >
                          <div className="space-y-2" role="group" aria-labelledby="font-color-label">
                            <div className="flex items-center gap-1.5">
                              <Checkbox
                                id="font-color-template-default"
                                checked={fontColor === null}
                                onCheckedChange={(checked) => setFontColor(checked === true ? null : "#FFFFFF")}
                                disabled={generationControlsDisabled}
                              />
                              <label htmlFor="font-color-template-default" className="text-xs text-muted-foreground cursor-pointer">
                                Use template default
                              </label>
                            </div>
                            {fontColor !== null && (
                              <>
                                <div className="flex min-w-0 items-center gap-2">
                                  <label htmlFor="font-color-picker" className="sr-only">Caption color</label>
                                  <input
                                    id="font-color-picker"
                                    type="color"
                                    value={fontColor}
                                    onChange={(e) => setFontColor(e.target.value)}
                                    disabled={generationControlsDisabled}
                                    className="h-8 w-10 shrink-0 rounded border border-stone-300 cursor-pointer disabled:cursor-not-allowed"
                                  />
                                  <label htmlFor="font-color-hex" className="sr-only">Caption color hex value</label>
                                  <Input
                                    id="font-color-hex"
                                    type="text"
                                    value={fontColor}
                                    onChange={(e) => setFontColor(e.target.value)}
                                    disabled={generationControlsDisabled}
                                    className="h-8 min-w-0 flex-1 text-xs bg-background"
                                    pattern="^#[0-9A-Fa-f]{6}$"
                                  />
                                </div>
                                <div className="flex flex-wrap gap-2">
                                  {COLOR_SWATCHES.map((swatch) => (
                                    <button
                                      key={swatch.value}
                                      type="button"
                                      onClick={() => setFontColor(swatch.value)}
                                      disabled={generationControlsDisabled}
                                      aria-label={`${swatch.label} (${swatch.value})`}
                                      aria-pressed={fontColor?.toUpperCase() === swatch.value}
                                      className={cn(
                                        "h-5 w-5 shrink-0 rounded border-2 border-stone-300 cursor-pointer hover:scale-110 transition-transform disabled:cursor-not-allowed",
                                        fontColor?.toUpperCase() === swatch.value && "ring-2 ring-stone-900 ring-offset-2",
                                      )}
                                      style={{ backgroundColor: swatch.value }}
                                    />
                                  ))}
                                </div>
                              </>
                            )}
                          </div>
                        </SettingsRow>
                      </>
                    )}
                  </SettingsGroup>

                  <SettingsGroup title="Cleanup" description="Tighten the audio while rendering.">
                    <SettingsRow
                      htmlFor="cut-long-pauses"
                      label="Cut long pauses"
                      description="Drops dead air between sentences."
                    >
                      <Switch
                        id="cut-long-pauses"
                        checked={cutLongPauses}
                        onCheckedChange={setCutLongPauses}
                        disabled={generationControlsDisabled}
                      />
                    </SettingsRow>
                    {cutLongPauses && (
                      <div className="flex flex-wrap items-center gap-2 px-3 py-2.5">
                        <label htmlFor="pause-threshold" className="text-sm text-stone-700">Silences longer than</label>
                        <Input
                          id="pause-threshold"
                          type="number"
                          min={250}
                          max={3000}
                          step={50}
                          value={pauseThresholdMs}
                          onChange={(e) => setPauseThresholdMs(e.target.value)}
                          disabled={generationControlsDisabled}
                          placeholder="900"
                          className="w-24 bg-background"
                        />
                        <span className="text-sm text-muted-foreground">ms</span>
                      </div>
                    )}

                    <SettingsRow
                      htmlFor="remove-filler-words"
                      label="Remove filler words"
                      description="Default list: &ldquo;um&rdquo;, &ldquo;uh&rdquo;, &ldquo;you know&rdquo;."
                    >
                      <Switch
                        id="remove-filler-words"
                        checked={removeFillerWords}
                        onCheckedChange={setRemoveFillerWords}
                        disabled={generationControlsDisabled}
                      />
                    </SettingsRow>
                    {removeFillerWords && (
                      <div className="min-w-0 space-y-1.5 px-3 py-2.5">
                        <label htmlFor="filtered-words" className="text-xs text-muted-foreground">Also remove</label>
                        <Input
                          id="filtered-words"
                          value={filteredWords}
                          onChange={(e) => setFilteredWords(e.target.value)}
                          disabled={generationControlsDisabled}
                          placeholder="basically, literally, to be honest"
                          className="bg-background"
                        />
                      </div>
                    )}
                  </SettingsGroup>

                  <p className="text-xs text-muted-foreground">
                    Completion emails use your user preference in{" "}
                    <Link href="/settings" className="font-medium text-stone-700 underline underline-offset-2">
                      Settings
                    </Link>.
                  </p>
                </div>
              </CollapsibleSection>

              {error && (
                <Alert className="border-red-200 bg-red-50">
                  <AlertCircle className="h-4 w-4 text-red-500" />
                  <AlertDescription className="text-sm text-red-700">
                    {error}
                  </AlertDescription>
                </Alert>
              )}

              <Button
                type="submit"
                className="w-full h-12 text-base rounded-xl"
                disabled={!hasSource || generationRequiresUpgrade || isLoading}
              >
                {isLoading ? (
                  <>
                    <Loader2 className="w-4 h-4 animate-spin" />
                    {sourceType === "upload" ? "Uploading video..." : "Processing..."}
                  </>
                ) : generationRequiresUpgrade ? (
                  "Choose a Paid Plan"
                ) : (
                  "Process Video"
                )}
              </Button>
            </form>
          </div>

          {/* Right Column — Phone Preview */}
          <ClipPhonePreview
            collapsed={sourceType === "upload"}
            thumbnailUrl={youtubeThumbnailUrl}
            fontFamily={fontFamily}
            fontSize={fontSize}
            fontColor={fontColor}
            previewFontFamily={previewFontFamily}
            previewFontSize={previewFontSize}
            previewFontColor={previewFontColor}
            availableFonts={availableFonts}
            templateName={selectedTemplate?.name || "Default"}
          />
        </div>
      </div>
    </AppShell>
  );
}
