"use client";
import { useEffect, useMemo, useRef } from "react";
import { useTranslations } from "next-intl";
import { Scissors } from "lucide-react";
import { Button } from "@/components/ui/button";
import { editDuration, type EditDocument } from "@/lib/editor/document";
import { applyKeepRanges, rescueKeepRanges, type SurgicalEdits } from "@/lib/editor/package-trims";

interface Props {
  pkg?: { clip_status: string; surgical_edits: SurgicalEdits } | null;
  original: EditDocument;
  update: (change: (doc: EditDocument) => EditDocument, group?: string) => void;
  /** Apply once on mount (the ?apply=rescue deep link). */
  autoApply?: boolean;
  onAutoApplied?: () => void;
}

/** Applies a clip's stored Rescue & package trims as one undoable change. Cold-open tease is never applied. */
export function RescueTrims({ pkg, original, update, autoApply, onAutoApplied }: Props) {
  const t = useTranslations("editor");
  const edits = pkg?.surgical_edits;
  const ranges = useMemo(() => rescueKeepRanges(pkg, editDuration(original)), [pkg, original]);
  const usable = ranges.length > 0;
  const done = useRef(false);
  const apply = () => update((d) => applyKeepRanges(d, original, ranges));
  const applyRef = useRef(apply);
  applyRef.current = apply;
  useEffect(() => {
    if (!autoApply || done.current) return;
    done.current = true;
    if (usable) applyRef.current();
    onAutoApplied?.();
  }, [autoApply, usable, onAutoApplied]);
  if (!usable || !edits) return null;
  const shaved = Math.round((edits.total_seconds_shaved ?? 0) * 10) / 10;
  return (
    <div className="flex items-center gap-2 px-3 pb-2 sm:px-4">
      <Button type="button" size="sm" variant="outline" className="h-8 rounded-lg" onClick={apply} data-testid="apply-rescue">
        <Scissors />
        {t("rescue.apply")}
      </Button>
      <p className="min-w-0 text-xs text-muted-foreground">
        {shaved > 0 && <span>{t("rescue.shave", { seconds: shaved })}</span>}
        {edits.cold_open_tease_timestamp && <span>{shaved > 0 ? " · " : ""}{t("rescue.coldOpenNotApplied")}</span>}
      </p>
    </div>
  );
}
