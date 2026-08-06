"use client";

import { useState } from "react";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Progress } from "@/components/ui/progress";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import {
  ChevronDown,
  Loader2,
  MessageSquare,
  Scissors,
  Share2,
  SplitSquareVertical,
  Star,
  Subtitles,
  TrendingUp,
  Trash2,
  Zap,
} from "lucide-react";
import DynamicVideoPlayer from "@/components/dynamic-video-player";
import { TranscriptPreview } from "@/components/transcript-preview";
import { DownloadSplitButton } from "./download-split-button";
import {
  Clip,
  formatDuration,
  getHookTypeLabel,
  getScoreColor,
  getViralityBgColor,
  getViralityColor,
} from "./clip-format";

interface ClipCardProps {
  clip: Clip;
  src: string;
  outputFormat?: string | null;
  selectMode: boolean;
  isSelected: boolean;
  onToggleSelect: (clipId: string) => void;
  exportPreset: string;
  onExportPresetChange: (clipId: string, preset: string) => void;
  onDownload: (clip: Clip) => void;
  isDownloading: boolean;
  onDelete: (clipId: string) => void;
  onTrim: (clipId: string, startOffset: string, endOffset: string) => Promise<void>;
  onSplit: (clipId: string, splitTime: string) => Promise<void>;
  onUpdateCaptions: (
    clipId: string,
    values: { text: string; position: string; highlightWords: string },
  ) => Promise<void>;
}

export function ClipCard({
  clip,
  src,
  outputFormat,
  selectMode,
  isSelected,
  onToggleSelect,
  exportPreset,
  onExportPresetChange,
  onDownload,
  isDownloading,
  onDelete,
  onTrim,
  onSplit,
  onUpdateCaptions,
}: ClipCardProps) {
  const [isEditing, setIsEditing] = useState(false);
  const [showBreakdown, setShowBreakdown] = useState(false);
  const [startOffset, setStartOffset] = useState("0");
  const [endOffset, setEndOffset] = useState("0");
  const [splitTime, setSplitTime] = useState("5");
  const [captionText, setCaptionText] = useState(clip.text || "");
  const [captionPosition, setCaptionPosition] = useState("bottom");
  const [highlightWords, setHighlightWords] = useState("");
  const [pendingAction, setPendingAction] = useState<"trim" | "split" | "captions" | null>(null);

  const title = clip.hook_title || `Clip ${clip.clip_order}`;
  const fieldId = (name: string) => `clip-${clip.id}-${name}`;

  const runAction = async (action: "trim" | "split" | "captions", run: () => Promise<void>) => {
    if (pendingAction) return;
    setPendingAction(action);
    try {
      await run();
    } finally {
      setPendingAction(null);
    }
  };

  return (
    <Card className="overflow-hidden py-0 gap-0">
      <CardContent className="p-0">
        <div className="relative bg-black">
          <DynamicVideoPlayer
            src={src}
            poster="/placeholder-video.jpg"
            outputFormat={outputFormat}
            sizing="fill"
            className="rounded-none"
          />
          {selectMode && (
            <div className="absolute top-2 left-2 flex items-center gap-2 rounded-md bg-background/90 px-2 py-1.5 shadow-sm">
              <Checkbox
                id={fieldId("select")}
                checked={isSelected}
                onCheckedChange={() => onToggleSelect(clip.id)}
              />
              <label htmlFor={fieldId("select")} className="text-xs font-medium cursor-pointer">
                Select
              </label>
            </div>
          )}
          {clip.virality_score > 0 && (
            <Badge
              className={`absolute top-2 right-2 ${getViralityBgColor(clip.virality_score)} text-white`}
            >
              <Zap className="w-3 h-3 mr-1" aria-hidden="true" />
              {clip.virality_score}
            </Badge>
          )}
        </div>

        <div className="p-4 space-y-3">
          <div>
            <h3 className="font-semibold text-foreground line-clamp-2" title={title}>
              {title}
            </h3>
            <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
              <span>Clip {clip.clip_order}</span>
              <span aria-hidden="true">•</span>
              <span>{formatDuration(clip.duration)}</span>
              <span aria-hidden="true">•</span>
              <span>
                {clip.start_time} – {clip.end_time}
              </span>
              <Badge variant="outline" className={getScoreColor(clip.relevance_score)}>
                <Star className="w-3 h-3 mr-1" aria-hidden="true" />
                {(clip.relevance_score * 100).toFixed(0)}%
              </Badge>
            </div>
          </div>

          {clip.virality_score > 0 && (
            <div className="rounded-lg border border-border">
              <button
                type="button"
                onClick={() => setShowBreakdown((open) => !open)}
                aria-expanded={showBreakdown}
                className="flex w-full items-center justify-between px-3 py-2 text-sm hover:bg-accent transition-colors rounded-lg"
              >
                <span className="flex items-center gap-2 font-medium">
                  <Zap className="w-4 h-4" aria-hidden="true" />
                  Score breakdown
                </span>
                <span className="flex items-center gap-2">
                  <span className={`font-semibold tabular-nums ${getViralityColor(clip.virality_score)}`}>
                    {clip.virality_score}/100
                  </span>
                  <ChevronDown
                    className={`w-4 h-4 transition-transform ${showBreakdown ? "rotate-180" : ""}`}
                    aria-hidden="true"
                  />
                </span>
              </button>

              {showBreakdown && (
                <div className="border-t border-border p-3 space-y-2.5 text-xs">
                  {[
                    { label: "Hook", value: clip.hook_score, Icon: MessageSquare },
                    { label: "Engagement", value: clip.engagement_score, Icon: TrendingUp },
                    { label: "Value", value: clip.value_score, Icon: Star },
                    { label: "Shareability", value: clip.shareability_score, Icon: Share2 },
                  ].map(({ label, value, Icon }) => (
                    <div key={label} className="space-y-1">
                      <div className="flex items-center justify-between">
                        <span className="flex items-center gap-1 text-muted-foreground">
                          <Icon className="w-3 h-3" aria-hidden="true" />
                          {label}
                        </span>
                        <span className="font-medium tabular-nums">{value}/25</span>
                      </div>
                      <Progress value={(value / 25) * 100} className="h-1.5" />
                    </div>
                  ))}

                  {clip.hook_type && clip.hook_type !== "none" && (
                    <div className="pt-1">
                      <Badge variant="outline">{getHookTypeLabel(clip.hook_type)}</Badge>
                    </div>
                  )}
                </div>
              )}
            </div>
          )}

          {clip.text && <TranscriptPreview text={clip.text} clipTitle={title} />}

          <div className="flex items-center gap-2 flex-wrap">
            <DownloadSplitButton
              preset={exportPreset}
              onPresetChange={(preset) => onExportPresetChange(clip.id, preset)}
              onDownload={() => onDownload(clip)}
              isPending={isDownloading}
              targetLabel={title}
            />

            <Button
              size="sm"
              variant="outline"
              aria-expanded={isEditing}
              aria-label={`Edit ${title}`}
              onClick={() => setIsEditing((open) => !open)}
            >
              <Scissors className="w-4 h-4" aria-hidden="true" />
              Edit
            </Button>

            <Button
              size="sm"
              variant="ghost"
              aria-label={`Delete ${title}`}
              className="ml-auto text-red-700 hover:text-red-800 hover:bg-red-50"
              onClick={() => onDelete(clip.id)}
            >
              <Trash2 className="w-4 h-4" aria-hidden="true" />
            </Button>
          </div>

          {isEditing && (
            <div className="rounded-lg border border-border bg-muted/40 p-3 space-y-4">
              <fieldset className="space-y-2">
                <legend className="text-xs font-medium text-muted-foreground">Trim</legend>
                <div className="grid grid-cols-1 sm:grid-cols-[1fr_1fr_auto] gap-2 sm:items-end">
                  <div className="space-y-1">
                    <label htmlFor={fieldId("start")} className="text-xs text-muted-foreground">
                      Start trim (sec)
                    </label>
                    <Input
                      id={fieldId("start")}
                      inputMode="decimal"
                      value={startOffset}
                      onChange={(e) => setStartOffset(e.target.value)}
                    />
                  </div>
                  <div className="space-y-1">
                    <label htmlFor={fieldId("end")} className="text-xs text-muted-foreground">
                      End trim (sec)
                    </label>
                    <Input
                      id={fieldId("end")}
                      inputMode="decimal"
                      value={endOffset}
                      onChange={(e) => setEndOffset(e.target.value)}
                    />
                  </div>
                  <Button
                    size="sm"
                    disabled={pendingAction !== null}
                    onClick={() => runAction("trim", () => onTrim(clip.id, startOffset, endOffset))}
                  >
                    {pendingAction === "trim" ? (
                      <Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" />
                    ) : (
                      <Scissors className="w-4 h-4" aria-hidden="true" />
                    )}
                    {pendingAction === "trim" ? "Trimming…" : "Trim"}
                  </Button>
                </div>
              </fieldset>

              <fieldset className="space-y-2">
                <legend className="text-xs font-medium text-muted-foreground">Split</legend>
                <div className="grid grid-cols-1 sm:grid-cols-[1fr_auto] gap-2 sm:items-end">
                  <div className="space-y-1">
                    <label htmlFor={fieldId("split")} className="text-xs text-muted-foreground">
                      Split at (sec)
                    </label>
                    <Input
                      id={fieldId("split")}
                      inputMode="decimal"
                      value={splitTime}
                      onChange={(e) => setSplitTime(e.target.value)}
                    />
                  </div>
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={pendingAction !== null}
                    onClick={() => runAction("split", () => onSplit(clip.id, splitTime))}
                  >
                    {pendingAction === "split" ? (
                      <Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" />
                    ) : (
                      <SplitSquareVertical className="w-4 h-4" aria-hidden="true" />
                    )}
                    {pendingAction === "split" ? "Splitting…" : "Split"}
                  </Button>
                </div>
              </fieldset>

              <fieldset className="space-y-2">
                <legend className="text-xs font-medium text-muted-foreground">Captions</legend>
                <div className="space-y-1">
                  <label htmlFor={fieldId("caption-text")} className="text-xs text-muted-foreground">
                    Caption text
                  </label>
                  <Input
                    id={fieldId("caption-text")}
                    value={captionText}
                    onChange={(e) => setCaptionText(e.target.value)}
                  />
                </div>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                  <div className="space-y-1">
                    <label htmlFor={fieldId("caption-position")} className="text-xs text-muted-foreground">
                      Position
                    </label>
                    <Select value={captionPosition} onValueChange={setCaptionPosition}>
                      <SelectTrigger id={fieldId("caption-position")} className="w-full">
                        <SelectValue placeholder="Caption position" />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="top">Top</SelectItem>
                        <SelectItem value="middle">Middle</SelectItem>
                        <SelectItem value="bottom">Bottom</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                  <div className="space-y-1">
                    <label htmlFor={fieldId("highlights")} className="text-xs text-muted-foreground">
                      Highlighted words
                    </label>
                    <Input
                      id={fieldId("highlights")}
                      value={highlightWords}
                      onChange={(e) => setHighlightWords(e.target.value)}
                      placeholder="word1, word2"
                    />
                  </div>
                </div>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={pendingAction !== null}
                  onClick={() =>
                    runAction("captions", () =>
                      onUpdateCaptions(clip.id, {
                        text: captionText,
                        position: captionPosition,
                        highlightWords,
                      }),
                    )
                  }
                >
                  {pendingAction === "captions" ? (
                    <Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" />
                  ) : (
                    <Subtitles className="w-4 h-4" aria-hidden="true" />
                  )}
                  {pendingAction === "captions" ? "Updating…" : "Update captions"}
                </Button>
              </fieldset>
            </div>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
