"use client";

import { useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { AlertCircle, Copy, Loader2, RefreshCw, Scissors, Wrench } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Alert, AlertDescription } from "@/components/ui/alert";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { useCopyText } from "@/lib/copy-text";
import { packageToKeepRanges } from "@/lib/editor/package-trims";

export type ClipStatus = "VIRAL_READY" | "RESCUE_REQUIRED" | "REJECTED";

export interface ClipPackage {
  clip_status: ClipStatus;
  scoring: {
    raw_virality_score: number;
    projected_score_after_rescue: number;
    primary_flaw_fixed: string;
  };
  surgical_edits: {
    new_start: string;
    new_end: string;
    cold_open_tease_timestamp: string | null;
    internal_cuts: { cut_start: string; cut_end: string; reason: string }[];
    total_seconds_shaved: number;
    first_spoken_words_after_trim: string;
    last_spoken_words_after_trim: string;
  };
  on_screen_text_hooks: {
    curiosity_gap: string;
    polarizing_debate: string;
    high_stakes_context: string;
  };
  seo_packaging: {
    titles: { search_curiosity_hybrid: string; shock_browse_fyp: string; high_stakes_value: string };
    description: { engagement_hook_line: string; seo_keyword_block: string; full_copy_paste_caption: string };
    hashtags: { entity_specific: string[]; niche_category: string[]; platform_broad: string[] };
    pinned_comment_bait: string;
    target_search_keywords: string[];
  };
}

interface PackageClip {
  id: string;
  clip_order: number;
  package?: ClipPackage | null;
  duration?: number;
}

interface ClipPackageButtonProps {
  taskId: string;
  clip: PackageClip;
  onChanged?: () => void | Promise<void>;
}

function canApply(p: ClipPackage, duration?: number) {
  const e = p.surgical_edits;
  if (p.clip_status === "REJECTED" && !e.internal_cuts.length && !e.total_seconds_shaved) return false;
  // Clip length is unknown here (the studio re-checks): trust a usable window.
  return packageToKeepRanges(e, duration ?? 24 * 3600).length > 0;
}

const STATUS: Record<ClipStatus, { key: string; className: string }> = {
  VIRAL_READY: { key: "statusViralReady", className: "bg-green-100 text-green-800" },
  RESCUE_REQUIRED: { key: "statusRescueRequired", className: "bg-amber-100 text-amber-800" },
  REJECTED: { key: "statusRejected", className: "bg-red-100 text-red-800" },
};

export function ClipPackageButton({ taskId, clip, onChanged }: ClipPackageButtonProps) {
  const t = useTranslations("social");
  const copyText = useCopyText();
  const [open, setOpen] = useState(false);
  const [pkg, setPkg] = useState<ClipPackage | null>(clip.package ?? null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = async (regenerate: boolean) => {
    setLoading(true);
    setError(null);
    try {
      const response = await fetch(`/api/tasks/${taskId}/clips/${clip.id}/package`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ regenerate }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || !data?.package) {
        throw new Error(typeof data?.detail === "string" ? data.detail : t("clipPackage.error"));
      }
      setPkg(data.package as ClipPackage);
      await onChanged?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : t("clipPackage.error"));
    } finally {
      setLoading(false);
    }
  };

  const heading = (label: string) => <h3 className="text-sm font-semibold">{label}</h3>;

  const field = (label: string, value: string, key: string) =>
    value ? (
      <div className="space-y-1.5" key={key}>
        <div className="flex items-center justify-between gap-2">
          <span className="text-sm font-medium">{label}</span>
          <Button
            type="button"
            size="sm"
            variant="ghost"
            aria-label={t("clipPackage.copyField", { field: label })}
            onClick={() => void copyText(value)}
          >
            <Copy className="size-4" />
            {t("clipPackage.copy")}
          </Button>
        </div>
        <p data-testid={`package-${key}`} className="whitespace-pre-wrap rounded-lg border bg-card p-3 text-sm">
          {value}
        </p>
      </div>
    ) : null;

  const renderPackage = (p: ClipPackage) => {
    const status = STATUS[p.clip_status] ?? STATUS.REJECTED;
    const edits = p.surgical_edits;
    const seo = p.seo_packaging;
    const hooks = p.on_screen_text_hooks;
    const tags = [
      ...seo.hashtags.entity_specific,
      ...seo.hashtags.niche_category,
      ...seo.hashtags.platform_broad,
    ].join(" ");
    const hasCopy = Boolean(
      hooks.curiosity_gap || seo.titles.shock_browse_fyp || seo.description.full_copy_paste_caption,
    );
    return (
      <div className="space-y-5">
        <div className="flex flex-wrap items-center gap-2">
          <span
            data-testid="package-status"
            className={`rounded-full px-2.5 py-0.5 text-xs font-semibold ${status.className}`}
          >
            {t(`clipPackage.${status.key}`)}
          </span>
          <span data-testid="package-score" className="text-sm text-muted-foreground">
            {t("clipPackage.score", {
              raw: p.scoring.raw_virality_score,
              projected: p.scoring.projected_score_after_rescue,
            })}
          </span>
        </div>

        {field(t("clipPackage.flaw"), p.scoring.primary_flaw_fixed, "flaw")}
        {!hasCopy && p.clip_status === "REJECTED" && (
          <p className="text-sm text-muted-foreground">{t("clipPackage.rejectedNote")}</p>
        )}

        <section className="space-y-3">
          {heading(t("clipPackage.editsHeading"))}
          {field(t("clipPackage.newStart"), edits.new_start, "new-start")}
          {field(t("clipPackage.newEnd"), edits.new_end, "new-end")}
          {field(t("clipPackage.coldOpen"), edits.cold_open_tease_timestamp ?? "", "cold-open")}
          <div className="space-y-1.5">
            <span className="text-sm font-medium">{t("clipPackage.cutsHeading")}</span>
            {edits.internal_cuts.length === 0 ? (
              <p className="text-sm text-muted-foreground">{t("clipPackage.noCuts")}</p>
            ) : (
              <ul data-testid="package-cuts" className="space-y-1 text-sm">
                {edits.internal_cuts.map((cut, i) => (
                  <li key={i} className="flex items-center justify-between gap-2 rounded-lg border bg-card p-2">
                    <span>{`${cut.cut_start} - ${cut.cut_end} · ${cut.reason}`}</span>
                    <Button
                      type="button"
                      size="sm"
                      variant="ghost"
                      aria-label={t("clipPackage.copyField", { field: `${cut.cut_start} - ${cut.cut_end}` })}
                      onClick={() => void copyText(`${cut.cut_start} - ${cut.cut_end}`)}
                    >
                      <Copy className="size-4" />
                    </Button>
                  </li>
                ))}
              </ul>
            )}
          </div>
          {canApply(p, clip.duration) && (
            <Button asChild size="sm" variant="outline">
              <Link href={`/tasks/${taskId}/edit?clip=${encodeURIComponent(clip.id)}&apply=rescue`}>
                <Scissors className="size-4" />
                {t("clipPackage.applyInStudio")}
              </Link>
            </Button>
          )}
          <p data-testid="package-shaved" className="text-sm text-muted-foreground">
            {t("clipPackage.shaved", { seconds: edits.total_seconds_shaved })}
          </p>
          {field(t("clipPackage.firstWords"), edits.first_spoken_words_after_trim, "first-words")}
          {field(t("clipPackage.lastWords"), edits.last_spoken_words_after_trim, "last-words")}
        </section>

        {hasCopy && (
          <>
            <section className="space-y-3">
              {heading(t("clipPackage.hooksHeading"))}
              {field(t("clipPackage.hookCuriosity"), hooks.curiosity_gap, "hook-curiosity")}
              {field(t("clipPackage.hookDebate"), hooks.polarizing_debate, "hook-debate")}
              {field(t("clipPackage.hookStakes"), hooks.high_stakes_context, "hook-stakes")}
            </section>
            <section className="space-y-3">
              {heading(t("clipPackage.titlesHeading"))}
              {field(t("clipPackage.titleSearch"), seo.titles.search_curiosity_hybrid, "title-search")}
              {field(t("clipPackage.titleShock"), seo.titles.shock_browse_fyp, "title-shock")}
              {field(t("clipPackage.titleStakes"), seo.titles.high_stakes_value, "title-stakes")}
            </section>
            <section className="space-y-3">
              {heading(t("clipPackage.descriptionHeading"))}
              {field(t("clipPackage.hookLine"), seo.description.engagement_hook_line, "hook-line")}
              {field(t("clipPackage.keywordBlock"), seo.description.seo_keyword_block, "keyword-block")}
              {field(t("clipPackage.caption"), seo.description.full_copy_paste_caption, "caption")}
              {field(t("clipPackage.hashtagsHeading"), tags, "hashtags")}
              {field(t("clipPackage.pinnedComment"), seo.pinned_comment_bait, "pinned")}
              {field(t("clipPackage.keywords"), seo.target_search_keywords.join(", "), "keywords")}
            </section>
          </>
        )}
      </div>
    );
  };

  return (
    <>
      <Button size="sm" variant="outline" onClick={() => setOpen(true)}>
        <Wrench className="w-4 h-4" />
        {t("clipPackage.open")}
      </Button>

      <Sheet open={open} onOpenChange={setOpen}>
        <SheetContent className="w-full sm:max-w-lg overflow-y-auto">
          <SheetHeader>
            <SheetTitle>{t("clipPackage.title", { order: clip.clip_order })}</SheetTitle>
            <SheetDescription>{t("clipPackage.body")}</SheetDescription>
          </SheetHeader>

          <div className="px-4 pb-4 space-y-5">
            {error && (
              <Alert variant="destructive">
                <AlertCircle className="h-4 w-4" />
                <AlertDescription>{error}</AlertDescription>
              </Alert>
            )}

            {!pkg ? (
              <Button onClick={() => void run(false)} disabled={loading}>
                {loading ? <Loader2 className="size-4 animate-spin" /> : <Wrench className="size-4" />}
                {loading ? t("clipPackage.generating") : t("clipPackage.generate")}
              </Button>
            ) : (
              <>
                {renderPackage(pkg)}
                <Button type="button" variant="outline" onClick={() => void run(true)} disabled={loading}>
                  {loading ? <Loader2 className="size-4 animate-spin" /> : <RefreshCw className="size-4" />}
                  {loading ? t("clipPackage.generating") : t("clipPackage.regenerate")}
                </Button>
              </>
            )}
          </div>
        </SheetContent>
      </Sheet>
    </>
  );
}
