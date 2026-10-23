"use client";
import type { ComponentProps, ReactNode } from "react";
import { Button } from "@/components/ui/button";
import { Kbd, KbdGroup } from "@/components/ui/kbd";
import { Label } from "@/components/ui/label";
import { Slider } from "@/components/ui/slider";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

/** Bordered surface matching the app's Card styling, without Card's padding rhythm. */
export function Panel({ className, ...props }: ComponentProps<"section">) {
  return (
    <section
      className={cn(
        "min-w-0 overflow-hidden rounded-xl border bg-card text-card-foreground shadow-sm",
        className,
      )}
      {...props}
    />
  );
}

export function PanelHeader({
  title,
  meta,
  children,
  className,
}: {
  title: ReactNode;
  meta?: ReactNode;
  children?: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex flex-wrap items-center justify-between gap-3 border-b px-4 py-3",
        className,
      )}
    >
      <div className="flex min-w-0 items-baseline gap-2">
        <h2 className="text-sm font-semibold">{title}</h2>
        {meta && (
          <span className="truncate text-xs text-muted-foreground tabular-nums">
            {meta}
          </span>
        )}
      </div>
      {children && <div className="flex items-center gap-2">{children}</div>}
    </div>
  );
}

export function Section({
  title,
  description,
  action,
  children,
  className,
}: {
  title: ReactNode;
  description?: ReactNode;
  action?: ReactNode;
  children?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("space-y-4", className)}>
      <div className="flex items-start justify-between gap-3">
        <div className="space-y-1">
          <h3 className="text-sm font-semibold leading-none">{title}</h3>
          {description && (
            <p className="text-xs leading-relaxed text-muted-foreground">
              {description}
            </p>
          )}
        </div>
        {action}
      </div>
      {children}
    </div>
  );
}

export function Field({
  label,
  htmlFor,
  children,
  className,
}: {
  label: ReactNode;
  htmlFor?: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("space-y-2", className)}>
      <Label htmlFor={htmlFor} className="text-xs text-muted-foreground">
        {label}
      </Label>
      {children}
    </div>
  );
}

export function Range({
  label,
  value,
  min,
  max,
  step = 1,
  unit = "",
  onChange,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  step?: number;
  unit?: string;
  onChange: (value: number) => void;
}) {
  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between text-xs">
        <span className="text-muted-foreground">{label}</span>
        <span className="rounded-md bg-muted px-1.5 py-0.5 font-medium tabular-nums">
          {Number(value.toFixed(2))}
          {unit}
        </span>
      </div>
      <Slider
        aria-label={label}
        value={[value]}
        min={min}
        max={max}
        step={step}
        onValueChange={(v) => onChange(v[0])}
      />
    </div>
  );
}

/** Keyboard shortcut label; accepts one key or a sequence rendered as a group. */
export function Keys({
  keys,
  className,
}: {
  keys: string | string[];
  className?: string;
}) {
  const list = Array.isArray(keys) ? keys : [keys];
  return (
    <KbdGroup className={className}>
      {list.map((key) => (
        <Kbd key={key}>{key}</Kbd>
      ))}
    </KbdGroup>
  );
}

/** Icon button with a tooltip that doubles as shortcut documentation. */
export function ToolButton({
  label,
  shortcut,
  children,
  ...props
}: ComponentProps<typeof Button> & {
  label: string;
  shortcut?: string | string[];
}) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button aria-label={label} size="icon" variant="ghost" {...props}>
          {children}
        </Button>
      </TooltipTrigger>
      <TooltipContent className="flex items-center gap-2">
        {label}
        {shortcut && <Keys keys={shortcut} />}
      </TooltipContent>
    </Tooltip>
  );
}

type Translate = (key: string) => string;

/** English strings produced outside these components, shown through `error.message` or editor status. */
const EDITOR_STATUS_KEYS: Record<string, string> = {
  "Preparing preview…": "status.preparing",
  "All changes saved": "status.saved",
  "Unsaved changes": "status.unsaved",
  "Saving…": "status.saving",
  "Save failed · draft kept locally": "status.saveFailed",
  "Recovered local draft": "status.recovered",
};

const EDITOR_ERROR_KEYS: Record<string, string> = {
  "Could not save draft": "errors.saveDraft",
  "Preview preparation failed": "errors.previewFailed",
  "A newer draft exists on the server. Your local changes are retained. Download a draft backup, then reload the saved version to resolve the conflict.":
    "errors.conflict",
  "Could not load editor": "errors.load",
  "Preview is not ready": "errors.notReady",
  "Combine cancelled": "errors.combineCancelled",
  "The combine is still running in the export queue. Refresh your clips when it completes.":
    "errors.combineTimeout",
  "Source preparation is taking longer than expected. Reopen the editor to check its status.":
    "errors.prepareTimeout",
  "Could not prepare clip": "errors.prepareFailed",
  "Export failed": "errors.exportFailed",
  "Edit this long transcript one word at a time.": "errors.transcriptTooLong",
  "This browser cannot encode this video. Choose background export to render it on the server.":
    "errors.cannotEncode",
  "Could not load the source video.": "errors.cannotLoadSource",
  "Canvas is unavailable. Use background export.": "errors.canvasUnavailable",
  "This browser cannot decode the source. Use background export.": "errors.cannotDecode",
  "Audio encoding is unavailable. Use background export.":
    "errors.audioEncodeUnavailable",
  "Export produced no video.": "errors.noVideo",
};

export function editorStatus(t: Translate, status: string) {
  const key = EDITOR_STATUS_KEYS[status];
  return key ? t(key) : status;
}

export function editorMessage(t: Translate, message: string) {
  const key = EDITOR_ERROR_KEYS[message];
  return key ? t(key) : message;
}
