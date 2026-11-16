"use client";
import { useState } from "react";
import { useLocale, useTranslations } from "next-intl";
import { AlertTriangle, HardDrive, Loader2, RefreshCw } from "lucide-react";
import { toast } from "sonner";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { downloadBlob } from "@/lib/clip-actions";
import { compactDrafts, summarizeDrafts, type DraftCacheSummary } from "@/lib/editor/draft-cache";
import { editorRequest } from "./use-editor";
import { editorMessage } from "./studio-ui";

export function formatStorageSize(bytes: number, locale: string) {
  const megabytes = bytes >= 1_000_000;
  return new Intl.NumberFormat(locale, {
    style: "unit",
    unit: megabytes ? "megabyte" : "kilobyte",
    maximumFractionDigits: megabytes ? 1 : 0,
  }).format(megabytes ? bytes / 1_000_000 : Math.max(1, Math.round(bytes / 1000)));
}

function seconds(value: number, locale: string) {
  return new Intl.NumberFormat(locale, { style: "unit", unit: "second", maximumFractionDigits: 0 }).format(value);
}

/** Download the draft a rebuild replaced. */
export async function downloadPreviousDraft(endpoint: string, clipId: string) {
  const backup = await editorRequest<unknown>(`${endpoint}/backup`);
  downloadBlob(
    new Blob([JSON.stringify(backup, null, 2)], { type: "application/json" }),
    `supoclip-previous-draft-${clipId}.json`,
  );
}

/** A ready session whose editing copy is shorter than the clip it came from. */
export function SourceIncompleteAlert({
  duration,
  expected,
  onRebuild,
}: {
  duration: number;
  expected: number;
  onRebuild: () => void;
}) {
  const t = useTranslations("editor");
  const locale = useLocale();
  return (
    <Alert className="border-0 bg-amber-500/10 py-2" data-testid="source-incomplete">
      <AlertTriangle />
      <AlertDescription className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <span className="mr-auto">
          {t("recovery.sourceIncomplete", {
            duration: seconds(duration, locale),
            expected: seconds(expected, locale),
          })}
        </span>
        <Button size="sm" variant="outline" onClick={onRebuild}>
          <RefreshCw />
          {t("recovery.rebuild")}
        </Button>
      </AlertDescription>
    </Alert>
  );
}

/** Confirms a rebuild, saying plainly what happens to the current edits. */
export function RebuildDialog({
  open,
  onOpenChange,
  onRebuild,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onRebuild: () => Promise<void>;
}) {
  const t = useTranslations("editor");
  const [busy, setBusy] = useState(false);
  return (
    <Dialog open={open} onOpenChange={(next) => !busy && onOpenChange(next)}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{t("recovery.rebuildTitle")}</DialogTitle>
          <DialogDescription>{t("recovery.rebuildDescription")}</DialogDescription>
        </DialogHeader>
        <Alert className="border-amber-500/30 bg-amber-500/5">
          <AlertTriangle />
          <AlertDescription>{t("recovery.rebuildWarning")}</AlertDescription>
        </Alert>
        <DialogFooter>
          <Button variant="outline" disabled={busy} onClick={() => onOpenChange(false)}>
            {t("workspace.keepEditing")}
          </Button>
          <Button
            disabled={busy}
            onClick={async () => {
              setBusy(true);
              try {
                await onRebuild();
                onOpenChange(false);
                toast.success(t("recovery.rebuildStarted"));
              } catch (e) {
                toast.error(editorMessage(t, (e as Error).message));
              } finally {
                setBusy(false);
              }
            }}
          >
            {busy ? <Loader2 className="animate-spin" /> : <RefreshCw />}
            {t("recovery.rebuildConfirm")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/** What this browser keeps for the studio, and a safe way to free it. */
export function BrowserBackupsDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const t = useTranslations("editor");
  const locale = useLocale();
  const [summary, setSummary] = useState<DraftCacheSummary | null>(null);
  const shown = open ? (summary ?? summarizeDrafts()) : null;
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        setSummary(next ? summarizeDrafts() : null);
        onOpenChange(next);
      }}
    >
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{t("recovery.backupsTitle")}</DialogTitle>
          <DialogDescription>{t("recovery.backupsDescription")}</DialogDescription>
        </DialogHeader>
        {shown && (
          <div className="space-y-1 rounded-lg border bg-muted/40 p-3 text-sm" data-testid="browser-backups-summary">
            <p className="flex items-center gap-2 font-medium">
              <HardDrive className="size-4 text-muted-foreground" />
              {t("recovery.backupsSummary", {
                entries: shown.entries,
                size: formatStorageSize(shown.bytes, locale),
              })}
            </p>
            {shown.entries > 0 && (
              <p className="text-muted-foreground">{t("recovery.backupsUnsaved", { count: shown.unsaved })}</p>
            )}
          </div>
        )}
        <p className="text-xs text-muted-foreground">
          {t("recovery.backupsClearNote")} {t("recovery.backupsBrowserNote")}
        </p>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            {t("recovery.done")}
          </Button>
          <Button
            disabled={!shown || shown.entries === shown.unsaved}
            onClick={() => {
              const freed = compactDrafts();
              setSummary(summarizeDrafts());
              toast.success(
                freed > 0
                  ? t("recovery.backupsCleared", { size: formatStorageSize(freed, locale) })
                  : t("recovery.backupsNothing"),
              );
            }}
          >
            {t("recovery.backupsClear")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
