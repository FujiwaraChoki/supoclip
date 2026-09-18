"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import {
  ArrowLeft,
  AudioLines,
  Clapperboard,
  Download,
  Gauge,
  Layers,
  Palette,
  Scissors,
  SplitSquareVertical,
  Subtitles,
  Volume2,
  VolumeX,
} from "lucide-react";
import { toast } from "sonner";
import { EXPORT_PRESETS, getClipUrl, requestAction } from "@/lib/clip-actions";
import { exportVideo } from "@/lib/editor/export-video";
import { DEFAULT_VIDEO_FX, videoFilter, type VideoFx } from "@/lib/editor/video-effects";
import { StatusBadge } from "@/components/app/status-badge";
import { PageLoading, PageError } from "@/components/app/page-state";

import { formatSupportMessage, parseApiError } from "@/lib/api-error";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Slider } from "@/components/ui/slider";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";

interface TaskDetails {
  id: string;
  source_title: string;
  source_type: string;
  status: string;
  clips_count: number;
  font_size?: number | null;
}

interface Clip {
  id: string;
  filename: string;
  clip_order: number;
  duration: number;
  start_time: string;
  end_time: string;
  text: string;
  video_url: string;
  caption_settings?: { font_size?: number | null; position?: string; position_y?: number; highlight_words?: string[] } | null;
}

const MIN_GAP_SECONDS = 0.25;
const clamp = (value: number, min: number, max: number) => Math.min(Math.max(value, min), max);

export default function TaskEditPage() {
  const params = useParams();
  const searchParams = useSearchParams();
  const requestedClipId = searchParams.get("clip");
  const taskApiUrl = "/api/tasks";

  const [task, setTask] = useState<TaskDetails | null>(null);
  const [clips, setClips] = useState<Clip[]>([]);
  const [selectedClipId, setSelectedClipId] = useState<string | null>(null);
  const [mergeSelection, setMergeSelection] = useState<string[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [trimRange, setTrimRange] = useState<[number, number]>([0, 1]);
  const [splitTime, setSplitTime] = useState(1);
  const [captionText, setCaptionText] = useState("");
  const [captionPosition, setCaptionPosition] = useState("bottom");
  const [highlightWords, setHighlightWords] = useState<string[]>([]);
  const [subtitleSize, setSubtitleSize] = useState<number | null>(null);
  const [captionsDirty, setCaptionsDirty] = useState(false);
  const [previewWidth, setPreviewWidth] = useState(292.5);
  const previewRef = useRef<HTMLDivElement>(null);
  const [subtitleY, setSubtitleY] = useState(78);

  const [volume, setVolume] = useState(100);
  const [isMuted, setIsMuted] = useState(false);
  const [playbackRate, setPlaybackRate] = useState(1);
  const [videoFx, setVideoFx] = useState<VideoFx>(DEFAULT_VIDEO_FX);
  const [currentTime, setCurrentTime] = useState(0);
  const [isPlaying, setIsPlaying] = useState(false);

  const [exportPreset, setExportPreset] = useState("tiktok");
  const [exportProgress, setExportProgress] = useState<number | null>(null);

  const videoRef = useRef<HTMLVideoElement | null>(null);

  const selectedClip = useMemo(
    () => clips.find((clip) => clip.id === selectedClipId) ?? null,
    [clips, selectedClipId]
  );

  const videoStyle = useMemo(
    () => ({
      filter: videoFilter(videoFx, previewWidth / 1080),
      transform: `scale(${videoFx.zoom})`,
      transformOrigin: "center center",
    }),
    [videoFx, previewWidth]
  );

  const subtitleWords = useMemo(
    () => captionText.split(/\s+/).map((word) => word.trim()).filter(Boolean),
    [captionText]
  );

  const formatDuration = (seconds: number) => {
    const mins = Math.floor(seconds / 60);
    const secs = Math.floor(seconds % 60);
    return `${mins}:${secs.toString().padStart(2, "0")}`;
  };


  const buildSupportError = useCallback(async (response: Response, fallbackMessage: string) => {
    const parsed = await parseApiError(response, fallbackMessage);
    return formatSupportMessage(parsed);
  }, []);

  const fetchEditorData = useCallback(async () => {
    if (!params.id) return;
    setError(null);

    try {
      const taskResponse = await fetch(`${taskApiUrl}/${params.id}`, { cache: "no-store" });
      if (!taskResponse.ok) {
        throw new Error(await buildSupportError(taskResponse, `Failed to fetch task: ${taskResponse.status}`));
      }

      const taskData = (await taskResponse.json()) as TaskDetails;
      setTask(taskData);

      if (taskData.status !== "completed") {
        setClips([]);
        return;
      }

      const clipsResponse = await fetch(`${taskApiUrl}/${params.id}/clips`, { cache: "no-store" });
      if (!clipsResponse.ok) {
        throw new Error(await buildSupportError(clipsResponse, `Failed to fetch clips: ${clipsResponse.status}`));
      }

      const clipsData = await clipsResponse.json();
      const nextClips = (clipsData.clips || []) as Clip[];
      setClips(nextClips);

      setSelectedClipId((current) => {
        if (current && nextClips.some((clip) => clip.id === current)) return current;
        return nextClips.find((clip) => clip.id === requestedClipId)?.id ?? nextClips[0]?.id ?? null;
      });

      setMergeSelection((current) => current.filter((id) => nextClips.some((clip) => clip.id === id)));
      return true;
    } catch (fetchError) {
      setError(fetchError instanceof Error ? fetchError.message : "Failed to load editor");
      return false;
    }
  }, [buildSupportError, params.id, taskApiUrl, requestedClipId]);

  useEffect(() => {
    const run = async () => {
      setIsLoading(true);
      try {
        await fetchEditorData();
      } finally {
        setIsLoading(false);
      }
    };
    void run();
  }, [fetchEditorData]);

  const resetCaptionDraft = useCallback(() => {
    if (!selectedClip) return;
    const settings = selectedClip.caption_settings;
    setCaptionText(selectedClip.text || "");
    setHighlightWords(settings?.highlight_words || []);
    setSubtitleY((settings?.position_y ?? 0.78) * 100);
    setSubtitleSize(settings?.font_size ?? task?.font_size ?? null);
    setCaptionPosition(settings?.position || "bottom");
    setCaptionsDirty(false);
  }, [selectedClip, task?.font_size]);

  useEffect(() => {
    if (!selectedClip) return;
    const safeDuration = Math.max(selectedClip.duration, MIN_GAP_SECONDS * 2);
    setTrimRange([0, safeDuration]);
    setSplitTime(clamp(safeDuration / 2, MIN_GAP_SECONDS, safeDuration - MIN_GAP_SECONDS));
    setCurrentTime(0);
    setVideoFx(DEFAULT_VIDEO_FX);
    resetCaptionDraft();
  }, [selectedClip, resetCaptionDraft]);

  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    video.volume = clamp(volume / 100, 0, 1);
    video.muted = isMuted;
    video.playbackRate = playbackRate;
  }, [volume, isMuted, playbackRate, selectedClip]);

  useEffect(() => {
    if (!previewRef.current) return;
    const observer = new ResizeObserver(([entry]) => setPreviewWidth(entry.contentRect.width));
    observer.observe(previewRef.current);
    return () => observer.disconnect();
  }, [selectedClipId, isLoading]);

  useEffect(() => {
    if (!captionsDirty) return;
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [captionsDirty]);

  const withSaving = async (action: () => Promise<unknown>, message: string) => {
    if (isSaving) return false;
    setIsSaving(true);
    setError(null);
    try {
      await action();
      if (!await fetchEditorData()) throw new Error("Changes saved, but the preview could not be refreshed. Reload to see the saved clip.");
      toast.success(message);
      return true;
    } catch (err) {
      const message = err instanceof Error ? err.message : "Could not save changes. Please try again.";
      setError(message);
      toast.error(message);
      return false;
    } finally { setIsSaving(false); }
  };

  const clipAction = (suffix: string, method: string, body: unknown, message: string) => {
    if (!selectedClip || !task) return;
    return withSaving(() => requestAction(`${taskApiUrl}/${task.id}/clips/${selectedClip.id}${suffix}`, method, body), message);
  };
  const handleTrim = () => clipAction("", "PATCH", {
    start_offset: Number(trimRange[0].toFixed(2)),
    end_offset: Number(((selectedClip?.duration || 0) - trimRange[1]).toFixed(2)),
  }, "Clip trimmed");
  const handleSplit = () => clipAction("/split", "POST", { split_time: Number(splitTime.toFixed(2)) }, "Clip split");
  const handleUpdateCaptions = () => clipAction("/captions", "PATCH", {
    caption_text: captionText, position: captionPosition, highlight_words: highlightWords,
    font_size: subtitleSize ?? undefined, position_y: subtitleY / 100,
  }, "Captions saved. Preview updated.");
  const handleMerge = async () => {
    if (!task || mergeSelection.length < 2) return;
    const saved = await withSaving(() => requestAction(`${taskApiUrl}/${task.id}/clips/merge`, "POST", { clip_ids: mergeSelection }), "Clips merged");
    if (saved) setMergeSelection([]);
  };
  const handleExport = async () => {
    if (!selectedClip || isSaving || captionsDirty) return;
    setIsSaving(true);
    setError(null);
    setExportProgress(0);
    try {
      await exportVideo({ clip: selectedClip, presetId: exportPreset, trimRange, videoFx, volume,
        muted: isMuted, onProgress: setExportProgress });
      toast.success("Export ready");
    } catch (err) {
      const message = err instanceof Error ? err.message : "Export failed. Please try again.";
      setError(message);
      toast.error(message);
    } finally { setIsSaving(false); setExportProgress(null); }
  };

  const toggleMergeSelection = (clipId: string) => {
    setMergeSelection((current) => (current.includes(clipId) ? current.filter((id) => id !== clipId) : [...current, clipId]));
  };

  const toggleHighlightedWord = (word: string) => {
    const cleaned = word.toLowerCase().replace(/[^a-z0-9']/g, "").trim();
    if (!cleaned) return;
    setCaptionsDirty(true);
    setHighlightWords((current) => (current.includes(cleaned) ? current.filter((value) => value !== cleaned) : [...current, cleaned]));
  };

  const handleTrimRangeChange = (value: number[]) => {
    if (!selectedClip || value.length !== 2) return;
    const min = 0;
    const max = selectedClip.duration;
    let nextStart = clamp(value[0], min, max);
    let nextEnd = clamp(value[1], min, max);

    if (nextEnd - nextStart < MIN_GAP_SECONDS) {
      if (nextStart + MIN_GAP_SECONDS <= max) nextEnd = nextStart + MIN_GAP_SECONDS;
      else {
        nextStart = max - MIN_GAP_SECONDS;
        nextEnd = max;
      }
    }
    setTrimRange([nextStart, nextEnd]);
  };

  const seekTo = (seconds: number) => {
    if (!videoRef.current || !selectedClip) return;
    const target = clamp(seconds, 0, selectedClip.duration);
    videoRef.current.currentTime = target;
    setCurrentTime(target);
  };

  const handleTimeUpdate = () => {
    if (!videoRef.current) return;
    setCurrentTime(videoRef.current.currentTime || 0);
  };

  const setTrimInToPlayhead = () => {
    setTrimRange(([, end]) => {
      const nextStart = Math.min(currentTime, end - MIN_GAP_SECONDS);
      return [clamp(nextStart, 0, Math.max(end - MIN_GAP_SECONDS, 0)), end];
    });
  };

  const setTrimOutToPlayhead = () => {
    if (!selectedClip) return;
    setTrimRange(([start]) => {
      const nextEnd = Math.max(currentTime, start + MIN_GAP_SECONDS);
      return [start, clamp(nextEnd, start + MIN_GAP_SECONDS, selectedClip.duration)];
    });
  };

  const resetPreviewAdjustments = () => {
    setVideoFx(DEFAULT_VIDEO_FX);
    setVolume(100);
    setIsMuted(false);
    setPlaybackRate(1);
  };

  if (isLoading) return <PageLoading />;
  if (!task && error) return <PageError message={error} retry={() => void fetchEditorData()} />;

  return (
    <div className="min-h-screen bg-white">
      <div className="border-b bg-white">
        <div className="max-w-7xl mx-auto px-4 py-5 flex flex-col items-start justify-between gap-4 sm:flex-row sm:items-center">
          <div className="space-y-1">
            <div className="flex items-center gap-3">
              <Link href={`/tasks/${params.id}`}>
                <Button variant="ghost" size="sm">
                  <ArrowLeft className="w-4 h-4" />
                  Back to generation
                </Button>
              </Link>
              <Badge variant="outline">Clip editor</Badge>
            </div>
            <h1 className="font-[var(--font-syne)] text-2xl font-bold text-black">{task?.source_title || "Clip Editor"}</h1>
          </div>
          <Button onClick={handleExport} disabled={!selectedClip || isSaving || captionsDirty}>
            <Download className="w-4 h-4" />
            {exportProgress !== null ? `Exporting ${exportProgress}%` : "Export Selected"}
          </Button>
        </div>
      </div>

      <fieldset disabled={isSaving} className="max-w-7xl mx-auto px-4 py-6 space-y-6">
        {error && (
          <Alert>
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}

        {captionsDirty && <Alert><AlertDescription>Save subtitle changes to update the preview before exporting or editing another clip. <button type="button" className="ml-2 font-medium underline" onClick={resetCaptionDraft} disabled={isSaving}>Discard changes</button></AlertDescription></Alert>}
        {!task ? (
          <Alert>
            <AlertDescription>Generation not found.</AlertDescription>
          </Alert>
        ) : task.status !== "completed" ? (
          <Card>
            <CardContent className="p-8 text-center space-y-3">
              <p className="text-lg font-semibold">This editor is available once processing completes.</p>
              <p className="text-gray-600"><StatusBadge status={task.status} /></p>
              <Link href={`/tasks/${task.id}`}>
                <Button variant="outline">Return to generation</Button>
              </Link>
            </CardContent>
          </Card>
        ) : clips.length === 0 ? (
          <Card>
            <CardContent className="p-8 text-center space-y-3">
              <p className="text-lg font-semibold">No clips to edit yet.</p>
              <Link href={`/tasks/${task.id}`}>
                <Button variant="outline">Return to generation</Button>
              </Link>
            </CardContent>
          </Card>
        ) : (
          <>
            <div className="grid grid-cols-1 xl:grid-cols-12 items-start gap-5">
              <Card className="xl:col-span-7">
                <CardContent className="p-4 lg:p-5 space-y-4">
                  {selectedClip ? (
                    <>
                      <div ref={previewRef} className="mx-auto aspect-[9/16] w-full max-w-[292.5px] rounded-xl bg-black overflow-hidden relative">
                        <video
                          ref={videoRef}
                          key={selectedClip.filename}
                          src={getClipUrl(selectedClip.video_url, selectedClip.filename)}
                          controls
                          onTimeUpdate={handleTimeUpdate}
                          onPlay={() => setIsPlaying(true)}
                          onPause={() => setIsPlaying(false)}
                          className="h-full w-full object-contain"
                          style={videoStyle}
                        />


                      </div>

                      <div className="border rounded-lg p-3 space-y-3">
                        <div className="flex items-center justify-between text-sm text-gray-600">
                          <span>Playhead: {formatDuration(currentTime)} / {formatDuration(selectedClip.duration)}</span>
                          <span>{isPlaying ? "Playing" : "Paused"}</span>
                        </div>

                        <Slider disabled={isSaving}
                          min={0}
                          max={selectedClip.duration}
                          aria-label="Playhead"
                          value={[currentTime]}
                          step={0.01}
                          onValueChange={(value) => seekTo(value[0] || 0)}
                        />

                        <div className="grid grid-cols-2 lg:grid-cols-4 gap-2">
                          <Button variant="outline" size="sm" onClick={() => seekTo(Math.max(0, currentTime - 1))}>-1s</Button>
                          <Button variant="outline" size="sm" onClick={() => seekTo(Math.min(selectedClip.duration, currentTime + 1))}>+1s</Button>
                          <Button variant="outline" size="sm" onClick={setTrimInToPlayhead}>Set In</Button>
                          <Button variant="outline" size="sm" onClick={setTrimOutToPlayhead}>Set Out</Button>
                        </div>
                      </div>

                      <div className="border rounded-lg p-3 space-y-3">
                        <div className="flex items-center justify-between text-sm text-gray-700">
                          <span className="font-medium">Trim Range</span>
                          <span>{formatDuration(trimRange[0])} - {formatDuration(trimRange[1])}</span>
                        </div>
                        <Slider disabled={isSaving}
                          min={0}
                          max={selectedClip.duration}
                          aria-label="Trim range"
                          value={trimRange}
                          step={0.01}
                          onValueChange={handleTrimRangeChange}
                        />
                        <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
                          <Button onClick={handleTrim} disabled={isSaving || captionsDirty}>
                            <Scissors className="w-4 h-4" />
                            Apply Trim
                          </Button>
                          <Button variant="outline" onClick={() => seekTo(trimRange[0])}>Jump In</Button>
                          <Button variant="outline" onClick={() => seekTo(trimRange[1])}>Jump Out</Button>
                        </div>
                      </div>
                    </>
                  ) : (
                    <p className="text-sm text-gray-600">Select a clip to start editing.</p>
                  )}
                </CardContent>
              </Card>

              <div className="xl:col-span-5 space-y-4">
                <Card>
                  <CardHeader className="pb-3">
                    <CardTitle className="text-base flex items-center gap-2">
                      <Layers className="w-4 h-4" />
                      Fine Controls
                    </CardTitle>
                  </CardHeader>
                  <CardContent className="space-y-5">
                    <div className="space-y-3">
                      <div className="flex items-center justify-between text-sm">
                        <span className="flex items-center gap-2"><SplitSquareVertical className="w-4 h-4" />Split</span>
                        <span>{splitTime.toFixed(2)}s</span>
                      </div>
                      <Slider disabled={isSaving}
                        min={MIN_GAP_SECONDS}
                        max={Math.max((selectedClip?.duration || MIN_GAP_SECONDS) - MIN_GAP_SECONDS, MIN_GAP_SECONDS)}
                        aria-label="Split point"
                        value={[splitTime]}
                        step={0.01}
                        onValueChange={(value) => setSplitTime(value[0] || MIN_GAP_SECONDS)}
                      />
                      <div className="grid grid-cols-2 gap-2">
                        <Button variant="outline" onClick={() => setSplitTime(clamp(currentTime, MIN_GAP_SECONDS, (selectedClip?.duration || 1) - MIN_GAP_SECONDS))} disabled={!selectedClip}>Set to Playhead</Button>
                        <Button variant="outline" onClick={() => void handleSplit()} disabled={isSaving || captionsDirty || !selectedClip || selectedClip.duration <= MIN_GAP_SECONDS * 2}>Split Clip</Button>
                      </div>
                    </div>

                    <div className="space-y-3">
                      <div className="text-sm font-medium flex items-center gap-2"><AudioLines className="w-4 h-4" />Audio</div>
                      <div className="space-y-2">
                        <div className="flex items-center justify-between text-xs text-gray-600">
                          <span>Volume</span>
                          <span>{volume}%</span>
                        </div>
                        <Slider disabled={isSaving} aria-label="Volume" min={0} max={100} step={1} value={[volume]} onValueChange={(v) => setVolume(v[0] || 0)} />
                      </div>
                      <div className="space-y-2">
                        <div className="flex items-center justify-between text-xs text-gray-600">
                          <span>Preview playback speed</span>
                          <span>{playbackRate.toFixed(2)}x</span>
                        </div>
                        <Slider disabled={isSaving} aria-label="Preview playback speed" min={0.5} max={2} step={0.05} value={[playbackRate]} onValueChange={(v) => setPlaybackRate(v[0] || 1)} />
                      </div>
                      <Button variant="outline" className="w-full" onClick={() => setIsMuted((m) => !m)}>
                        {isMuted ? <VolumeX className="w-4 h-4" /> : <Volume2 className="w-4 h-4" />}
                        {isMuted ? "Unmute" : "Mute"}
                      </Button>
                    </div>

                    <div className="space-y-3">
                      <div className="text-sm font-medium flex items-center gap-2"><Palette className="w-4 h-4" />Video FX</div>
                      {[
                        ["Brightness", "brightness", 40, 180, 1],
                        ["Contrast", "contrast", 40, 180, 1],
                        ["Saturation", "saturation", 0, 220, 1],
                        ["Blur", "blur", 0, 8, 0.1],
                        ["Hue", "hue", -180, 180, 1],
                        ["Zoom", "zoom", 1, 2, 0.01],
                      ].map(([label, key, min, max, step]) => {
                        const typedKey = key as keyof VideoFx;
                        const currentValue = videoFx[typedKey];
                        return (
                          <div key={key} className="space-y-1.5">
                            <div className="flex items-center justify-between text-xs text-gray-600">
                              <span>{label}</span>
                              <span>{currentValue}</span>
                            </div>
                            <Slider disabled={isSaving}
                              aria-label={String(label)}
                              min={Number(min)}
                              max={Number(max)}
                              step={Number(step)}
                              value={[currentValue]}
                              onValueChange={(value) => setVideoFx((current) => ({ ...current, [typedKey]: value[0] ?? currentValue }))}
                            />
                          </div>
                        );
                      })}
                    </div>

                    <Button variant="outline" className="w-full" onClick={resetPreviewAdjustments}>
                      <Gauge className="w-4 h-4" />
                      Reset Preview Adjustments
                    </Button>
                  </CardContent>
                </Card>

                <Card>
                  <CardHeader className="pb-3">
                    <CardTitle className="text-base flex items-center gap-2">
                      <Subtitles className="w-4 h-4" />
                      Subtitle Control
                    </CardTitle>
                  </CardHeader>
                  <CardContent className="space-y-3">
                    <textarea
                      value={captionText}
                      onChange={(e) => { setCaptionText(e.target.value); setCaptionsDirty(true); }}
                      aria-label="Subtitle text"
                      placeholder="Edit subtitle script"
                      className="w-full min-h-24 rounded-md border border-gray-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-gray-900"
                    />

                    <div className="grid grid-cols-2 gap-2">
                      <Select disabled={isSaving} value={captionPosition} onValueChange={(value) => { setCaptionPosition(value); setSubtitleY(({ top: 18, middle: 52, bottom: 78 })[value as "top" | "middle" | "bottom"]); setCaptionsDirty(true); }}>
                        <SelectTrigger>
                          <SelectValue placeholder="Position" />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="top">Top</SelectItem>
                          <SelectItem value="middle">Middle</SelectItem>
                          <SelectItem value="bottom">Bottom</SelectItem>
                        </SelectContent>
                      </Select>

                      <Select disabled={isSaving} value={exportPreset} onValueChange={setExportPreset}>
                        <SelectTrigger>
                          <SelectValue placeholder="Preset" />
                        </SelectTrigger>
                        <SelectContent>
                          {EXPORT_PRESETS.map((preset) => <SelectItem key={preset.id} value={preset.id}>{preset.label}</SelectItem>)}
                        </SelectContent>
                      </Select>
                    </div>

                    <div className="space-y-2">
                      <div className="flex items-center justify-between text-xs text-gray-600">
                        <span>Subtitle Size</span>
                        <span>{subtitleSize ?? "Template default"}</span>
                      </div>
                      <Slider disabled={isSaving} aria-label="Subtitle size" min={12} max={72} step={1} value={[subtitleSize ?? 24]} onValueChange={(v) => { setSubtitleSize(v[0] || 24); setCaptionsDirty(true); }} />
                    </div>

                    <div className="space-y-2">
                      <div className="flex items-center justify-between text-xs text-gray-600">
                        <span>Vertical Offset</span>
                        <span>{subtitleY}%</span>
                      </div>
                      <Slider disabled={isSaving} min={10} max={85} step={1} value={[subtitleY]} aria-label="Subtitle vertical position" onValueChange={(v) => { setSubtitleY(v[0] || 78); setCaptionsDirty(true); }} />
                    </div>

                    <div className="space-y-2">
                      <div className="text-xs text-gray-600">Highlight words (click to toggle)</div>
                      <div className="max-h-28 overflow-y-auto rounded-md border border-gray-200 p-2 flex flex-wrap gap-1.5">
                        {subtitleWords.length === 0 ? (
                          <span className="text-xs text-gray-500">No words yet.</span>
                        ) : (
                          subtitleWords.map((word, index) => {
                            const cleaned = word.toLowerCase().replace(/[^a-z0-9']/g, "");
                            const highlighted = cleaned ? highlightWords.includes(cleaned) : false;
                            return (
                              <button
                                key={`${word}-${index}`}
                                type="button"
                                onClick={() => toggleHighlightedWord(word)}
                                className={`px-1.5 py-0.5 rounded text-xs border ${
                                  highlighted ? "bg-yellow-100 border-yellow-300 text-yellow-900" : "bg-white border-gray-200 text-gray-700"
                                }`}
                              >
                                {word}
                              </button>
                            );
                          })
                        )}
                      </div>
                    </div>

                    <Button onClick={handleUpdateCaptions} disabled={isSaving || !selectedClip || !captionsDirty} className="w-full">
                      {isSaving ? "Saving…" : "Save subtitle changes"}
                    </Button>
                  </CardContent>
                </Card>
              </div>
            </div>

            <Card>
              <CardHeader>
                <CardTitle className="text-base flex items-center gap-2">
                  <Clapperboard className="w-4 h-4" />
                  Clips
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-3">
                {mergeSelection.length >= 2 && (
                  <div className="flex justify-end">
                    <Button variant="outline" onClick={handleMerge} disabled={isSaving || captionsDirty}>
                      Merge Selected ({mergeSelection.length})
                    </Button>
                  </div>
                )}
                <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
                  {clips.map((clip) => {
                    const isActive = clip.id === selectedClipId;
                    const isSelectedForMerge = mergeSelection.includes(clip.id);
                    return (
                      <div
                        key={clip.id}
                        className={`text-left rounded-lg border p-3 transition ${
                          isActive ? "border-black bg-gray-50" : "border-gray-200 hover:border-gray-400"
                        }`}
                      >
                        <div className="flex items-start justify-between gap-2">
                          <button type="button" className="min-w-0 text-left disabled:opacity-50" disabled={isSaving || captionsDirty} onClick={() => setSelectedClipId(clip.id)} aria-pressed={isActive}>
                            <p className="font-medium text-sm text-black">Clip {clip.clip_order}</p>
                            <p className="text-xs text-gray-500">{clip.start_time} - {clip.end_time}</p>
                            <p className="text-xs text-gray-500">{formatDuration(clip.duration)}</p>
                          </button>
                          <label className="flex items-center gap-1 text-xs text-gray-600" onClick={(e) => e.stopPropagation()}>
                            <input type="checkbox" checked={isSelectedForMerge} onChange={() => toggleMergeSelection(clip.id)} />
                            Merge
                          </label>
                        </div>
                      </div>
                    );
                  })}
                </div>
              </CardContent>
            </Card>
          </>
        )}
      </fieldset>
    </div>
  );
}
