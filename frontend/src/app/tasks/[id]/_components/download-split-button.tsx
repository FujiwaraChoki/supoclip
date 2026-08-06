"use client";

import { Download, Loader2 } from "lucide-react";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { EXPORT_PRESETS } from "./clip-format";

interface DownloadSplitButtonProps {
  preset: string;
  onPresetChange: (preset: string) => void;
  onDownload: () => void;
  isPending?: boolean;
  /** Human name of the thing being downloaded, used for screen-reader labels. */
  targetLabel: string;
  className?: string;
}

export function DownloadSplitButton({
  preset,
  onPresetChange,
  onDownload,
  isPending = false,
  targetLabel,
  className = "",
}: DownloadSplitButtonProps) {
  const presetLabel = EXPORT_PRESETS.find((option) => option.value === preset)?.label ?? preset;

  return (
    <div
      className={`inline-flex items-stretch h-8 rounded-md border border-input bg-background shadow-xs overflow-hidden ${className}`}
    >
      <button
        type="button"
        onClick={onDownload}
        disabled={isPending}
        aria-label={`Download ${targetLabel} (${presetLabel})`}
        className="inline-flex items-center gap-1.5 px-3 text-sm font-medium hover:bg-accent transition-colors focus-visible:outline-none focus-visible:bg-accent disabled:opacity-60 disabled:cursor-not-allowed"
      >
        {isPending ? (
          <Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" />
        ) : (
          <Download className="w-4 h-4" aria-hidden="true" />
        )}
        {isPending ? "Preparing…" : "Download"}
      </button>
      <Select value={preset} onValueChange={onPresetChange}>
        <SelectTrigger
          size="sm"
          aria-label={`Download format for ${targetLabel}`}
          className="h-8 min-w-[112px] rounded-none border-0 border-l border-input shadow-none focus-visible:ring-0 focus-visible:border-input bg-transparent"
        >
          <SelectValue />
        </SelectTrigger>
        <SelectContent align="end">
          {EXPORT_PRESETS.map((option) => (
            <SelectItem key={option.value} value={option.value}>
              {option.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}
