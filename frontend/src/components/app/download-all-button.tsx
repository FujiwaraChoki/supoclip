"use client";

import { useTranslations } from "next-intl";
import { Download, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

/** "Download all" for a generation: one streamed ZIP of the original files. */
export function DownloadAllButton({ label, hint, busy, busyLabel, onClick }: {
  label: string;
  hint: string;
  /** The ZIP's preflight (or the clips' full-quality render) is running. */
  busy: boolean;
  /** What the busy button says instead of "Preparing download…" (full-quality progress). */
  busyLabel?: string;
  onClick: () => void;
}) {
  const t = useTranslations("task");
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button size="sm" variant="outline" className="h-8 bg-background" aria-busy={busy} disabled={busy} onClick={onClick}>
          {busy ? <Loader2 className="size-4 animate-spin" /> : <Download className="size-4" />}
          <span className="max-sm:sr-only">{busy ? busyLabel ?? t("preparingDownload") : label}</span>
        </Button>
      </TooltipTrigger>
      <TooltipContent>{hint}</TooltipContent>
    </Tooltip>
  );
}
