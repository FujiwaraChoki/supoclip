"use client";

import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { Check, Coffee, Loader2, X } from "lucide-react";

const STAGES = [
  { id: "queued", label: "Queued", hint: "Waiting for a free worker" },
  { id: "downloading", label: "Downloading", hint: "Fetching the source video" },
  { id: "transcribing", label: "Transcribing", hint: "Word-level transcript" },
  { id: "analyzing", label: "Analyzing", hint: "Picking the most viral moments" },
  { id: "rendering", label: "Rendering clips", hint: "Cropping, captioning, encoding" },
] as const;

type StageId = (typeof STAGES)[number]["id"];

/**
 * The worker publishes free-form progress messages ("Downloading video...",
 * "Generating transcript...", "Analyzing content with AI...", "Creating video
 * clips..."), so match on keywords first and fall back to the percentage.
 */
export function inferStage(status: string, progress: number, message: string): StageId {
  const text = message.toLowerCase();
  if (text.includes("download")) return "downloading";
  if (text.includes("transcri")) return "transcribing";
  if (text.includes("analy")) return "analyzing";
  if (text.includes("clip") || text.includes("render") || text.includes("subtitle")) return "rendering";
  if (text.includes("queue") || text.includes("waiting")) return "queued";

  if (status === "queued") return "queued";
  if (progress >= 70) return "rendering";
  if (progress >= 50) return "analyzing";
  if (progress >= 30) return "transcribing";
  if (progress >= 10) return "downloading";
  return "queued";
}

interface PipelineProgressProps {
  status: string;
  progress: number;
  message: string;
  clipsReady: number;
  onCancel: () => void;
  isCancelling: boolean;
}

export function PipelineProgress({
  status,
  progress,
  message,
  clipsReady,
  onCancel,
  isCancelling,
}: PipelineProgressProps) {
  const activeStage = inferStage(status, progress, message);
  const activeIndex = STAGES.findIndex((stage) => stage.id === activeStage);

  return (
    <Card>
      <CardContent className="p-6 sm:p-8">
        <div className="flex items-start justify-between gap-4 mb-6">
          <div>
            <h2 className="text-lg font-semibold text-foreground">
              {status === "queued" ? "Waiting in queue" : "Generating your clips"}
            </h2>
            <p className="text-sm text-muted-foreground mt-1" aria-live="polite">
              {message || (status === "queued" ? "Your video will start shortly" : "Working on it")}
            </p>
          </div>
          <TooltipProvider>
            <Tooltip>
              <TooltipTrigger asChild>
                <span className="text-muted-foreground shrink-0" aria-hidden="true">
                  <Coffee className="w-5 h-5" />
                </span>
              </TooltipTrigger>
              <TooltipContent>Grab a coffee, and come back to ready-to-post clips.</TooltipContent>
            </Tooltip>
          </TooltipProvider>
        </div>

        <div className="space-y-3">
          <Progress value={progress} className="h-1.5" />
          <div className="flex items-center justify-between text-xs text-muted-foreground tabular-nums">
            <span>{progress}%</span>
            {clipsReady > 0 && (
              <span>
                {clipsReady} clip{clipsReady === 1 ? "" : "s"} ready
              </span>
            )}
          </div>
        </div>

        <ol className="mt-6 space-y-2.5">
          {STAGES.map((stage, index) => {
            const isDone = index < activeIndex;
            const isActive = index === activeIndex;
            return (
              <li key={stage.id} className="flex items-center gap-3 text-sm">
                <span className="flex items-center justify-center w-5 h-5 shrink-0">
                  {isDone ? (
                    <Check className="w-4 h-4 text-green-700" aria-hidden="true" />
                  ) : isActive ? (
                    <Loader2 className="w-4 h-4 animate-spin text-foreground" aria-hidden="true" />
                  ) : (
                    <span className="w-2 h-2 rounded-full bg-stone-300" aria-hidden="true" />
                  )}
                </span>
                <span
                  className={
                    isActive
                      ? "font-medium text-foreground"
                      : isDone
                        ? "text-muted-foreground"
                        : "text-stone-400"
                  }
                >
                  {stage.label}
                </span>
                {isActive && <span className="text-xs text-muted-foreground">— {stage.hint}</span>}
                <span className="sr-only">
                  {isDone ? "completed" : isActive ? "in progress" : "pending"}
                </span>
              </li>
            );
          })}
        </ol>

        <div className="mt-6 pt-4 border-t border-border">
          <Button variant="outline" size="sm" onClick={onCancel} disabled={isCancelling}>
            {isCancelling ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <X className="w-4 h-4" />
            )}
            {isCancelling ? "Cancelling…" : "Cancel generation"}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
