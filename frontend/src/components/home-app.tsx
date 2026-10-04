"use client";

import { useState, useRef, useEffect, useCallback } from "react";
import Link from "next/link";
import {
  AlertCircle, ArrowRight, ArrowUp, Captions, CaptionsOff, Check, ChevronDown, Crop, FileVideo,
  Loader2, Paintbrush, Paperclip, Upload, Wand2, X, Youtube,
} from "lucide-react";
import { CaptionSizeControl } from "@/components/caption-size-control";
import { FONT_SEARCH_THRESHOLD, getYouTubeThumbnailUrl, uploadVideoFile } from "@/lib/video-upload";
import { AppShell } from "@/components/app/app-shell";
import { GenerationCard } from "@/components/app/generation-card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { SubscriptionCancelBanner } from "@/components/subscription-cancel-banner";
import { UpgradeDialog, UpgradeNudge, getUpgradeState, upgradeReasonForError, type UpgradeReason } from "@/components/billing/upgrade-prompt";
import { getPublicBillingPlans } from "@/lib/billing-plans";
import { Skeleton } from "@/components/ui/skeleton";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Kbd } from "@/components/ui/kbd";
import { useSession } from "@/lib/auth-client";
import { useBillingSummary } from "@/hooks/use-billing-summary";
import { formatBillingPlanName, isPaidBillingPlan } from "@/lib/billing-plans";
import { track } from "@/lib/datafast";
import { formatSupportMessage, parseApiError } from "@/lib/api-error";
import { buildFontOptionsPayload, FONT_TEMPLATE_DEFAULT_VALUE } from "@/lib/font-options";
import { fetchGenerations, greeting, type GenerationSummary } from "@/lib/generations";
import { cn } from "@/lib/utils";

interface FontOption {
  name: string;
  display_name: string;
  format?: string;
}

interface CaptionTemplate {
  id: string;
  name: string;
  description: string;
  animation: string;
  font_family?: string;
  font_size?: number;
  font_color?: string;
}

type OutputFormat = "vertical" | "vertical_pan" | "vertical_split" | "original";

const FRAMINGS: { id: OutputFormat; label: string; hint: string }[] = [
  { id: "vertical", label: "Auto 9:16", hint: "Face-tracked vertical crop" },
  { id: "vertical_pan", label: "Speaker pan", hint: "Follows whoever is talking" },
  { id: "vertical_split", label: "Split-screen", hint: "Two speakers stacked" },
  { id: "original", label: "Original", hint: "Keep the source aspect ratio" },
];

const COLOR_SWATCHES = ["#FFFFFF", "#000000", "#FFD700", "#FF6B6B", "#4ECDC4", "#45B7D1"];

function formatBytes(bytes: number) {
  if (bytes > 1e9) return `${(bytes / 1e9).toFixed(1)} GB`;
  return `${Math.max(1, Math.round(bytes / 1e6))} MB`;
}

export default function HomeApp() {
  const [url, setUrl] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [statusMessage, setStatusMessage] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [isDragging, setIsDragging] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const urlInputRef = useRef<HTMLInputElement | null>(null);
  const { data: session, isPending } = useSession();
  const sourceType: "youtube" | "upload" = file ? "upload" : "youtube";

  // Font customization states — null means "use the caption template's own value"
  const [fontFamily, setFontFamily] = useState<string | null>(null);
  const [fontSize, setFontSize] = useState<number | null>(null);
  const [fontColor, setFontColor] = useState<string | null>(null);
  const [availableFonts, setAvailableFonts] = useState<FontOption[]>([]);
  const [fontSearch, setFontSearch] = useState("");
  const [fontLoadError, setFontLoadError] = useState<string | null>(null);
  const [isUploadingFont, setIsUploadingFont] = useState(false);
  const fontUploadInputRef = useRef<HTMLInputElement | null>(null);

  const [captionTemplate, setCaptionTemplate] = useState("default");
  const [availableTemplates, setAvailableTemplates] = useState<CaptionTemplate[]>([]);
  const [outputFormat, setOutputFormat] = useState<OutputFormat>("vertical");
  const [addSubtitles, setAddSubtitles] = useState(true);
  const [cutLongPauses, setCutLongPauses] = useState(false);
  const [pauseThresholdMs, setPauseThresholdMs] = useState("900");
  const [removeFillerWords, setRemoveFillerWords] = useState(false);
  const [filteredWords, setFilteredWords] = useState("");

  const [recent, setRecent] = useState<GenerationSummary[] | null>(null);
  const [billingSummary, updateBillingSummary] = useBillingSummary(Boolean(session?.user?.id));
  const [upgradeReason, setUpgradeReason] = useState<UpgradeReason | null>(null);
  const [lastUpgradeReason, setLastUpgradeReason] = useState<UpgradeReason>({ kind: "start" });
  const openUpgrade = useCallback((reason: UpgradeReason) => {
    setLastUpgradeReason(reason);
    setUpgradeReason(reason);
  }, []);
  const youtubeThumbnailUrl = sourceType === "youtube" ? getYouTubeThumbnailUrl(url) : null;

  const refreshFonts = useCallback(async () => {
    try {
      setFontLoadError(null);
      const response = await fetch("/api/fonts", { cache: "no-store" });
      if (!response.ok) throw new Error(`Failed to load fonts (${response.status})`);
      const data = await response.json();
      const fonts: FontOption[] = data.fonts || [];
      setAvailableFonts(fonts);

      const styleElement = document.createElement("style");
      styleElement.id = "custom-fonts";
      styleElement.innerHTML = fonts.map((font) => `
        @font-face {
          font-family: '${font.name}';
          src: url('/api/fonts/${font.name}') format('${font.format === "otf" ? "opentype" : "truetype"}');
          font-weight: normal;
          font-style: normal;
        }`).join("\n");
      document.getElementById("custom-fonts")?.remove();
      document.head.appendChild(styleElement);
    } catch (error) {
      console.error("Failed to load fonts:", error);
      setFontLoadError("Could not load fonts right now.");
    }
  }, []);

  useEffect(() => { void refreshFonts(); }, [refreshFonts]);

  useEffect(() => {
    fetch("/api/caption-templates")
      .then((response) => (response.ok ? response.json() : null))
      .then((data) => { if (data) setAvailableTemplates(data.templates || []); })
      .catch((error) => console.error("Failed to load caption templates:", error));
  }, []);

  useEffect(() => {
    if (!session?.user?.id) return;
    fetchGenerations()
      .then((tasks) => setRecent(tasks.slice(0, 8)))
      .catch((error) => { console.error("Failed to load recent generations:", error); setRecent([]); });
  }, [session?.user?.id]);

  const attachFile = (next: File | null) => {
    if (next && !next.type.startsWith("video/") && !/\.(mp4|mov|avi|mkv|webm|m4v)$/i.test(next.name)) {
      setError("That doesn't look like a video file. Try MP4, MOV, MKV or WebM.");
      return;
    }
    setError(null);
    setFile(next);
    if (!next && fileInputRef.current) fileInputRef.current.value = "";
    if (!next) requestAnimationFrame(() => urlInputRef.current?.focus());
  };

  const handleFontUpload = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const fontFile = event.target.files?.[0];
    event.target.value = "";
    if (!fontFile) return;

    const lower = fontFile.name.toLowerCase();
    if (!lower.endsWith(".ttf") && !lower.endsWith(".otf")) {
      setError("Only .ttf and .otf files are supported for custom fonts.");
      return;
    }

    try {
      setIsUploadingFont(true);
      setError(null);
      const formData = new FormData();
      formData.append("file", fontFile);
      const response = await fetch("/api/fonts/upload", { method: "POST", body: formData });
      if (!response.ok) {
        setError(formatSupportMessage(await parseApiError(response, "Failed to upload font")));
        return;
      }
      const data = await response.json();
      if (data?.font?.name) setFontFamily(data.font.name);
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
    return !keyword || font.display_name.toLowerCase().includes(keyword) || font.name.toLowerCase().includes(keyword);
  });

  const canUploadCustomFonts =
    !billingSummary?.monetization_enabled ||
    (isPaidBillingPlan(billingSummary.plan) && ["active", "trialing"].includes(billingSummary.subscription_status));

  // Effective values for the live preview only — the submitted payload keeps nulls.
  const selectedTemplate = availableTemplates.find((template) => template.id === captionTemplate);
  const previewFontFamily = fontFamily ?? selectedTemplate?.font_family ?? "TikTokSans-Regular";
  const previewFontSize = fontSize ?? selectedTemplate?.font_size ?? 24;
  const previewFontColor = fontColor ?? selectedTemplate?.font_color ?? "#FFFFFF";
  const generationRequiresUpgrade = Boolean(billingSummary?.monetization_enabled && !billingSummary.can_create_task);
  const upgradeState = billingSummary?.monetization_enabled ? getUpgradeState(billingSummary) : null;
  // Users can explore every option; the plan prompt only appears when they hit Generate.
  const controlsDisabled = isLoading;
  const hasSource = sourceType === "upload" ? Boolean(file) : Boolean(url.trim());
  const cleanupCount = [cutLongPauses, removeFillerWords, filteredWords.trim().length > 0].filter(Boolean).length;
  const customized = fontFamily !== null || fontSize !== null || fontColor !== null;

  const handleSubmit = async (e?: React.FormEvent) => {
    e?.preventDefault();
    if (!hasSource || isLoading || !session?.user?.id) return;
    if (generationRequiresUpgrade) {
      openUpgrade({ kind: upgradeState?.isPaid ? "limit" : "start" });
      return;
    }

    setIsLoading(true);
    setError(null);
    setStatusMessage("");

    const fontOptions = buildFontOptionsPayload(fontFamily, fontSize, fontColor);

    try {
      let videoUrl = url.trim();
      const normalizedPauseThreshold = Number.isFinite(Number(pauseThresholdMs))
        ? Math.max(250, Math.min(3000, Math.round(Number(pauseThresholdMs))))
        : 900;
      const normalizedFilteredWords = filteredWords.split(",").map((word) => word.trim().toLowerCase()).filter(Boolean);

      if (sourceType === "upload" && file) {
        setStatusMessage("Uploading video…");
        videoUrl = await uploadVideoFile(file);
      }

      setStatusMessage(sourceType === "youtube" ? "Checking video length…" : "Starting generation…");
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
        }),
      });

      if (!startResponse.ok) {
        const info = await parseApiError(startResponse, `API error: ${startResponse.status}`);
        const upgradeForError = upgradeReasonForError(info.code, info.detail, upgradeState?.nextPlan?.youtubeMaxMinutes);
        if (upgradeForError) {
          openUpgrade(upgradeForError);
          setIsLoading(false);
          setStatusMessage("");
          return;
        }
        throw new Error(formatSupportMessage(info));
      }

      const startResult = await startResponse.json();
      track("task_created", {
        source_type: sourceType,
        caption_template: captionTemplate,
        output_format: outputFormat,
        add_subtitles: addSubtitles,
        cut_long_pauses: cutLongPauses,
        pause_threshold_ms: normalizedPauseThreshold,
        remove_filler_words: removeFillerWords,
        filtered_words: normalizedFilteredWords,
        processing_mode: "fast",
      });
      window.location.href = `/tasks/${startResult.task_id}`;
    } catch (error) {
      console.error("Error processing video:", error);
      setError(error instanceof Error ? error.message : "Failed to process video. Please try again.");
      setIsLoading(false);
      setStatusMessage("");
    }
  };

  if (isPending || !session?.user) {
    return (
      <div className="flex min-h-screen items-center justify-center p-4">
        <div className="w-full max-w-2xl space-y-4">
          <Skeleton className="mx-auto h-9 w-64" />
          <Skeleton className="h-36 w-full rounded-2xl" />
        </div>
      </div>
    );
  }

  const limitNote = billingSummary?.max_youtube_duration_seconds
    ? `${formatBillingPlanName(billingSummary.plan)}: YouTube videos up to ${billingSummary.max_youtube_duration_seconds / 60} minutes. We check the length before using a generation.`
    : null;

  return (
    <AppShell>
      <main className="mx-auto w-full max-w-5xl px-4 pb-20 pt-10 sm:px-8 md:pt-[12vh]">
        {billingSummary?.cancel_at && billingSummary.subscription_provider === "stripe" && (
          <SubscriptionCancelBanner
            cancelAt={billingSummary.cancel_at}
            onRestarted={() => updateBillingSummary({ cancel_at: null })}
            className="mb-8"
          />
        )}

        <div className="mx-auto max-w-3xl animate-rise">
          <h1 className="text-center font-display text-3xl font-bold tracking-tight sm:text-[2.6rem] sm:leading-tight">
            {greeting(session.user.name)}
          </h1>
          <p className="mt-2 text-center text-muted-foreground">
            Drop in a long video. Get back captioned, ready-to-post clips.
          </p>

          {upgradeState && <PlanStatusNudge state={upgradeState} onUpgrade={openUpgrade} />}

          <form
            onSubmit={handleSubmit}
            onDragOver={(event) => { event.preventDefault(); if (!controlsDisabled) setIsDragging(true); }}
            onDragLeave={(event) => { if (!event.currentTarget.contains(event.relatedTarget as Node)) setIsDragging(false); }}
            onDrop={(event) => {
              event.preventDefault();
              setIsDragging(false);
              if (!controlsDisabled) attachFile(event.dataTransfer.files?.[0] ?? null);
            }}
            className={cn(
              "relative mt-8 rounded-2xl border bg-background shadow-[0_1px_2px_rgb(0_0_0/0.04),0_12px_40px_-12px_rgb(0_0_0/0.12)] transition-all focus-within:border-foreground/25 focus-within:shadow-[0_1px_2px_rgb(0_0_0/0.04),0_16px_48px_-12px_rgb(0_0_0/0.18)]",
              isDragging && "border-brand ring-4 ring-brand/15",
            )}
          >
            <input
              id="video-upload"
              type="file"
              accept="video/*"
              ref={fileInputRef}
              onChange={(event) => attachFile(event.target.files?.[0] ?? null)}
              disabled={controlsDisabled}
              className="hidden"
            />

            {isDragging && (
              <div className="pointer-events-none absolute inset-0 z-10 flex flex-col items-center justify-center gap-2 rounded-2xl bg-background/95 text-sm font-medium">
                <Upload className="size-6 text-brand" />Drop to upload your video
              </div>
            )}

            <div className="flex items-start gap-3 p-3 pb-2 sm:p-4 sm:pb-2">
              {file ? (
                <div className="flex min-w-0 flex-1 items-center gap-3 rounded-xl bg-muted/60 p-2 pr-3">
                  <span className="flex size-11 shrink-0 items-center justify-center rounded-lg bg-foreground text-background"><FileVideo className="size-5" /></span>
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium">{file.name}</p>
                    <p className="text-xs text-muted-foreground">{formatBytes(file.size)} · ready to upload</p>
                  </div>
                  <button type="button" onClick={() => attachFile(null)} disabled={isLoading} aria-label="Remove file" className="rounded-md p-1 text-muted-foreground hover:bg-background hover:text-foreground">
                    <X className="size-4" />
                  </button>
                </div>
              ) : (
                <>
                  {youtubeThumbnailUrl ? (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img src={youtubeThumbnailUrl} alt="" className="mt-0.5 hidden h-11 w-[78px] shrink-0 rounded-lg object-cover sm:block" />
                  ) : (
                    <span className="mt-0.5 flex size-11 shrink-0 items-center justify-center rounded-xl bg-muted text-muted-foreground"><Youtube className="size-5" /></span>
                  )}
                  <div className="min-w-0 flex-1">
                    <label htmlFor="youtube-url" className="sr-only">YouTube URL</label>
                    <input
                      id="youtube-url"
                      ref={urlInputRef}
                      type="url"
                      autoFocus
                      autoComplete="off"
                      placeholder="Paste a YouTube link, or drop a video file"
                      aria-describedby={limitNote ? "youtube-duration-limit" : undefined}
                      value={url}
                      onChange={(e) => setUrl(e.target.value)}
                      onKeyDown={(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) void handleSubmit(); }}
                      disabled={controlsDisabled}
                      className="h-11 w-full bg-transparent text-base outline-none placeholder:text-muted-foreground/80 disabled:cursor-not-allowed"
                    />
                  </div>
                </>
              )}
            </div>

            <div className="flex flex-wrap items-center gap-1.5 px-3 pb-3 sm:px-4">
              {!file && (
                <Chip onClick={() => fileInputRef.current?.click()} disabled={controlsDisabled} aria-label="Upload a video file">
                  <Paperclip className="size-3.5" /><span className="hidden sm:inline">Upload</span>
                </Chip>
              )}

              <Popover>
                <PopoverTrigger asChild>
                  <Chip disabled={controlsDisabled || !addSubtitles} active={customized}>
                    <Paintbrush className="size-3.5" />{selectedTemplate?.name || "Default"}<ChevronDown className="size-3 opacity-60" />
                  </Chip>
                </PopoverTrigger>
                <PopoverContent align="start" className="w-[min(92vw,560px)] rounded-2xl p-0">
                  <div className="grid gap-0 sm:grid-cols-[minmax(0,1fr)_180px]">
                    <div className="max-h-[420px] min-w-0 space-y-4 overflow-y-auto p-4">
                      <div>
                        <p className="mb-2 text-xs font-medium uppercase tracking-wider text-muted-foreground">Caption style</p>
                        <div className="flex flex-col gap-1" role="radiogroup" aria-label="Caption style">
                          {(availableTemplates.length ? availableTemplates : [{ id: "default", name: "Default", description: "Word-by-word captions", animation: "" } as CaptionTemplate]).map((template) => (
                            <button
                              key={template.id}
                              type="button"
                              role="radio"
                              aria-checked={captionTemplate === template.id}
                              onClick={() => setCaptionTemplate(template.id)}
                              className={cn("flex w-full min-w-0 items-center gap-3 rounded-lg px-2.5 py-2 text-left transition-colors hover:bg-accent", captionTemplate === template.id && "bg-accent")}
                            >
                              <span className="size-3 shrink-0 rounded-full border" style={{ backgroundColor: template.font_color || "#fff" }} />
                              <span className="min-w-0 flex-1">
                                <span className="block text-sm font-medium">{template.name}</span>
                                <span className="block truncate text-xs text-muted-foreground">{template.description}</span>
                              </span>
                              {captionTemplate === template.id && <Check className="size-4" />}
                            </button>
                          ))}
                        </div>
                      </div>

                      <div className="space-y-3 border-t pt-4">
                        <div className="flex items-center justify-between">
                          <p className="text-xs font-medium uppercase tracking-wider text-muted-foreground">Customize</p>
                          {customized && (
                            <button type="button" className="text-xs text-muted-foreground underline-offset-2 hover:underline" onClick={() => { setFontFamily(null); setFontSize(null); setFontColor(null); }}>
                              Reset to template
                            </button>
                          )}
                        </div>
                        <div className="space-y-2">
                          <div className="flex items-center justify-between gap-2">
                            <label className="text-sm">Font</label>
                            <input ref={fontUploadInputRef} type="file" accept=".ttf,.otf" onChange={handleFontUpload} className="hidden" />
                            <Button type="button" variant="ghost" size="sm" className="h-7 text-xs" disabled={controlsDisabled || isUploadingFont} onClick={() => canUploadCustomFonts ? fontUploadInputRef.current?.click() : openUpgrade({ kind: "custom_fonts" })}>
                              <Upload className="size-3" />{isUploadingFont ? "Uploading…" : "Upload font"}
                            </Button>
                          </div>
                          {!canUploadCustomFonts && (
                            <button type="button" onClick={() => openUpgrade({ kind: "custom_fonts" })} className="text-xs text-muted-foreground underline underline-offset-2 hover:text-foreground">
                              Use your own fonts with a paid plan
                            </button>
                          )}
                          {availableFonts.length > FONT_SEARCH_THRESHOLD && (
                            <Input value={fontSearch} onChange={(e) => setFontSearch(e.target.value)} placeholder="Search fonts" className="h-8" />
                          )}
                          <Select value={fontFamily ?? FONT_TEMPLATE_DEFAULT_VALUE} onValueChange={(value) => setFontFamily(value === FONT_TEMPLATE_DEFAULT_VALUE ? null : value)}>
                            <SelectTrigger className="w-full"><SelectValue placeholder="Template default" /></SelectTrigger>
                            <SelectContent>
                              <SelectItem value={FONT_TEMPLATE_DEFAULT_VALUE}>Template default</SelectItem>
                              {filteredFonts.map((font) => (
                                <SelectItem key={font.name} value={font.name}>
                                  <span style={{ fontFamily: `'${font.name}', system-ui, sans-serif` }}>{font.display_name}</span>
                                </SelectItem>
                              ))}
                              {availableFonts.length > 0 && filteredFonts.length === 0 && <SelectItem value="__no_match__" disabled>No fonts match your search</SelectItem>}
                            </SelectContent>
                          </Select>
                          {fontLoadError && <p className="text-xs text-amber-700">{fontLoadError}</p>}
                        </div>
                        <CaptionSizeControl value={fontSize} onChange={setFontSize} disabled={controlsDisabled} />
                        <div className="space-y-2">
                          <p className="text-sm">Color</p>
                          <div className="flex flex-wrap items-center gap-1.5">
                            <button type="button" onClick={() => setFontColor(null)} className={cn("h-7 rounded-full border px-2.5 text-xs", fontColor === null ? "border-foreground bg-foreground text-background" : "hover:bg-accent")}>Template</button>
                            {COLOR_SWATCHES.map((color) => (
                              <button key={color} type="button" onClick={() => setFontColor(color)} title={color} aria-label={`Caption color ${color}`}
                                className={cn("size-7 rounded-full border-2 transition-transform hover:scale-110", fontColor === color ? "border-foreground" : "border-border")}
                                style={{ backgroundColor: color }} />
                            ))}
                            <label className="relative size-7 cursor-pointer overflow-hidden rounded-full border-2 border-dashed border-border" title="Custom color">
                              <input type="color" value={fontColor ?? "#FFFFFF"} onChange={(e) => setFontColor(e.target.value)} className="absolute inset-0 cursor-pointer opacity-0" aria-label="Custom caption color" />
                              <span className="absolute inset-1 rounded-full bg-[conic-gradient(red,yellow,lime,cyan,blue,magenta,red)]" />
                            </label>
                          </div>
                        </div>
                      </div>
                    </div>

                    <div className="hidden border-l bg-muted/40 p-4 sm:block">
                      <CaptionPreview
                        thumbnail={youtubeThumbnailUrl}
                        fontFamily={previewFontFamily}
                        fontSize={previewFontSize}
                        color={previewFontColor}
                      />
                      <p className="mt-3 text-center text-[11px] text-muted-foreground">Live preview</p>
                    </div>
                  </div>
                </PopoverContent>
              </Popover>

              <Popover>
                <PopoverTrigger asChild>
                  <Chip disabled={controlsDisabled}>
                    <Crop className="size-3.5" />{FRAMINGS.find((f) => f.id === outputFormat)?.label}<ChevronDown className="size-3 opacity-60" />
                  </Chip>
                </PopoverTrigger>
                <PopoverContent align="start" className="w-[min(92vw,420px)] rounded-2xl p-3">
                  <p className="mb-2 px-1 text-xs font-medium uppercase tracking-wider text-muted-foreground">Framing</p>
                  <div className="grid grid-cols-2 gap-2" role="radiogroup" aria-label="Framing">
                    {FRAMINGS.map((framing) => (
                      <button
                        key={framing.id}
                        type="button"
                        role="radio"
                        aria-checked={outputFormat === framing.id}
                        onClick={() => setOutputFormat(framing.id)}
                        className={cn("rounded-xl border p-3 text-left transition-colors hover:bg-accent", outputFormat === framing.id && "border-foreground ring-1 ring-foreground")}
                      >
                        <FramingGlyph kind={framing.id} />
                        <span className="mt-2 block text-sm font-medium">{framing.label}</span>
                        <span className="block text-xs text-muted-foreground">{framing.hint}</span>
                      </button>
                    ))}
                  </div>
                </PopoverContent>
              </Popover>

              <Chip onClick={() => setAddSubtitles((value) => !value)} disabled={controlsDisabled} aria-pressed={addSubtitles} active={!addSubtitles}>
                {addSubtitles ? <Captions className="size-3.5" /> : <CaptionsOff className="size-3.5" />}
                {addSubtitles ? "Captions" : "No captions"}
              </Chip>

              <Popover>
                <PopoverTrigger asChild>
                  <Chip disabled={controlsDisabled} active={cleanupCount > 0}>
                    <Wand2 className="size-3.5" />Cleanup{cleanupCount > 0 && <span className="rounded-full bg-foreground px-1.5 text-[10px] text-background">{cleanupCount}</span>}
                    <ChevronDown className="size-3 opacity-60" />
                  </Chip>
                </PopoverTrigger>
                <PopoverContent align="start" className="w-[min(92vw,340px)] space-y-4 rounded-2xl">
                  <div>
                    <p className="text-sm font-medium">Clip cleanup</p>
                    <p className="text-xs text-muted-foreground">Remove dead air and common filler phrases while rendering.</p>
                  </div>
                  <div className="flex items-center justify-between gap-4">
                    <div>
                      <label htmlFor="cut-pauses" className="text-sm font-medium">Cut long pauses</label>
                      <p className="text-xs text-muted-foreground">Split out silence gaps longer than</p>
                    </div>
                    <Switch id="cut-pauses" checked={cutLongPauses} onCheckedChange={setCutLongPauses} />
                  </div>
                  <div className="flex items-center gap-2">
                    <Input type="number" min={250} max={3000} step={50} value={pauseThresholdMs} onChange={(e) => setPauseThresholdMs(e.target.value)} disabled={!cutLongPauses} aria-label="Pause threshold (ms)" className="h-8 w-24" />
                    <span className="text-xs text-muted-foreground">milliseconds</span>
                  </div>
                  <div className="flex items-center justify-between gap-4">
                    <div>
                      <label htmlFor="filler-words" className="text-sm font-medium">Remove filler words</label>
                      <p className="text-xs text-muted-foreground">“um”, “uh”, “you know” and friends</p>
                    </div>
                    <Switch id="filler-words" checked={removeFillerWords} onCheckedChange={setRemoveFillerWords} />
                  </div>
                  <div className="space-y-1.5">
                    <label htmlFor="filtered-words" className="text-xs font-medium text-muted-foreground">Extra words or phrases to cut</label>
                    <Input id="filtered-words" value={filteredWords} onChange={(e) => setFilteredWords(e.target.value)} placeholder="basically, literally, to be honest" className="h-8" />
                  </div>
                </PopoverContent>
              </Popover>

              <div className="ml-auto flex items-center gap-2">
                <span className="hidden text-xs text-muted-foreground sm:inline-flex sm:items-center sm:gap-1">
                  {isLoading ? <span role="status">{statusMessage || "Working…"}</span> : hasSource ? <><Kbd>⌘</Kbd><Kbd>↵</Kbd></> : null}
                </span>
                <Button
                  type="submit"
                  size="icon"
                  aria-label="Generate clips"
                  disabled={!hasSource || isLoading}
                  className="size-9 rounded-full bg-brand text-brand-foreground shadow-sm hover:bg-brand/90 disabled:bg-muted disabled:text-muted-foreground disabled:opacity-100"
                >
                  {isLoading ? <Loader2 className="size-4 animate-spin" /> : <ArrowUp className="size-4" />}
                </Button>
              </div>
            </div>
          </form>

          {isLoading && statusMessage && <p role="status" className="mt-3 text-center text-sm text-muted-foreground sm:hidden">{statusMessage}</p>}

          {error && (
            <Alert className="mt-4 border-red-200 bg-red-50">
              <AlertCircle className="h-4 w-4 text-red-500" />
              <AlertDescription className="text-sm text-red-700">{error}</AlertDescription>
            </Alert>
          )}

          <p id="youtube-duration-limit" className="mt-4 text-center text-xs text-muted-foreground">
            {limitNote ? `${limitNote} ` : ""}
            Completion emails follow your preference in{" "}
            <Link href="/settings" className="font-medium text-foreground underline-offset-2 hover:underline">Settings</Link>.
          </p>
        </div>

        <section className="mt-16 md:mt-24" aria-labelledby="recent-heading">
          <div className="mb-4 flex items-end justify-between">
            <h2 id="recent-heading" className="font-display text-lg font-bold tracking-tight">Recent</h2>
            {recent && recent.length > 0 && (
              <Link href="/list" className="group flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
                View library<ArrowRight className="size-3.5 transition-transform group-hover:translate-x-0.5" />
              </Link>
            )}
          </div>
          {recent === null ? (
            <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4">
              {[0, 1, 2, 3].map((key) => <Skeleton key={key} className="aspect-[3/4] rounded-xl" />)}
            </div>
          ) : recent.length === 0 ? (
            <div className="rounded-2xl border border-dashed p-10 text-center text-sm text-muted-foreground">
              Your clips will show up here. Paste a link above to make your first batch.
            </div>
          ) : (
            <div className="grid grid-cols-2 gap-x-4 gap-y-6 sm:grid-cols-3 lg:grid-cols-4">
              {recent.map((generation) => <GenerationCard key={generation.id} generation={generation} />)}
            </div>
          )}
        </section>
      </main>
      <UpgradeDialog
        open={upgradeReason !== null}
        onOpenChange={(open) => { if (!open) setUpgradeReason(null); }}
        reason={upgradeReason ?? lastUpgradeReason}
        billing={billingSummary}
        source="home"
      />
    </AppShell>
  );
}

function Chip({ active, className, children, ...props }: React.ComponentProps<"button"> & { active?: boolean }) {
  return (
    <button
      type="button"
      className={cn(
        "inline-flex h-8 items-center gap-1.5 rounded-full border px-3 text-xs font-medium text-foreground/80 transition-colors hover:bg-accent hover:text-foreground disabled:pointer-events-none disabled:opacity-50 data-[state=open]:bg-accent",
        active && "border-foreground/30 bg-accent text-foreground",
        className,
      )}
      {...props}
    >
      {children}
    </button>
  );
}

function CaptionPreview({ thumbnail, fontFamily, fontSize, color }: { thumbnail: string | null; fontFamily: string; fontSize: number; color: string }) {
  return (
    <div className="relative mx-auto aspect-[9/16] w-full overflow-hidden rounded-xl bg-stone-800 shadow-inner">
      {thumbnail ? (
        <div className="absolute inset-0 scale-110 bg-cover bg-center blur-[2px]" style={{ backgroundImage: `url(${thumbnail})` }} />
      ) : (
        <div className="absolute inset-0 bg-[radial-gradient(circle_at_50%_30%,oklch(0.55_0.05_50),oklch(0.22_0.01_50))]" />
      )}
      <div className="absolute inset-x-0 top-0 h-1/3 bg-gradient-to-b from-black/40 to-transparent" />
      <p className="absolute inset-x-2 top-[10%] text-center text-[10px] font-bold leading-tight text-white drop-shadow">Your hook title lands here</p>
      <p
        className="absolute inset-x-2 top-[72%] text-center font-bold leading-snug"
        style={{
          color,
          fontFamily: `'${fontFamily}', system-ui, sans-serif`,
          fontSize: `${Math.max(Math.min(fontSize * 0.5, 18), 10)}px`,
          textShadow: "0 2px 6px rgba(0,0,0,0.8), 0 0 2px rgba(0,0,0,0.9)",
        }}
      >
        This is how your captions look
      </p>
    </div>
  );
}

function FramingGlyph({ kind }: { kind: OutputFormat }) {
  return (
    <span className="flex h-12 items-center justify-center rounded-lg bg-muted" aria-hidden>
      {kind === "original" ? (
        <span className="h-6 w-10 rounded-[3px] border-2 border-foreground/70" />
      ) : kind === "vertical_split" ? (
        <span className="flex h-9 w-5 flex-col gap-0.5 rounded-[3px] border-2 border-foreground/70 p-0.5"><span className="flex-1 rounded-[1px] bg-foreground/30" /><span className="flex-1 rounded-[1px] bg-foreground/30" /></span>
      ) : (
        <span className="relative flex h-9 w-5 items-center justify-center rounded-[3px] border-2 border-foreground/70">
          <span className={cn("size-2 rounded-full bg-brand", kind === "vertical_pan" && "motion-safe:animate-[pulse_1.4s_ease-in-out_infinite]")} />
        </span>
      )}
    </span>
  );
}

function PlanStatusNudge({
  state,
  onUpgrade,
}: {
  state: NonNullable<ReturnType<typeof getUpgradeState>>;
  onUpgrade: (reason: UpgradeReason) => void;
}) {
  const { nextPlan, resetsOn } = state;
  const refreshes = resetsOn ? `Refreshes on ${resetsOn}.` : "Refreshes next billing period.";

  if (!state.isPaid) {
    const entry = getPublicBillingPlans()[0];
    return (
      <UpgradeNudge
        className="mt-6"
        title="Pick a plan to start clipping"
        description={entry ? `From $${entry.priceMonthly}/mo for ${entry.generationLimit} videos. Feel free to set everything up first.` : undefined}
        action="See plans"
        onAction={() => onUpgrade({ kind: "start" })}
      />
    );
  }

  if (state.atLimit) {
    return (
      <UpgradeNudge
        className="mt-6"
        title={`You've clipped all ${state.limit} videos this period`}
        description={nextPlan ? `${refreshes} Or keep going with ${nextPlan.name}: ${nextPlan.generationLimit} a month.` : state.managedByAppStore ? `${refreshes} You can change plans in the SupoClip iOS app.` : refreshes}
        action={nextPlan ? `Get ${nextPlan.name}` : undefined}
        onAction={nextPlan ? () => onUpgrade({ kind: "limit" }) : undefined}
      />
    );
  }

  if (state.nearLimit && state.remaining !== null) {
    return (
      <UpgradeNudge
        className="mt-6"
        title={`${state.remaining} ${state.remaining === 1 ? "video" : "videos"} left this period`}
        description={nextPlan ? `${refreshes} Need more room? ${nextPlan.name} gives you ${nextPlan.generationLimit} a month.` : refreshes}
        action={nextPlan ? "Upgrade" : undefined}
        onAction={nextPlan ? () => onUpgrade({ kind: "explore" }) : undefined}
      />
    );
  }

  return null;
}
