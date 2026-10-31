"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";
import { AlertCircle, Copy, Loader2, RefreshCw, Sparkles } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { useCopyText } from "@/lib/copy-text";

export type CopyPlatform = "tiktok" | "youtube_shorts" | "instagram_reels";

export interface PlatformCopy {
  title: string;
  description: string;
  hashtags: string[];
}

export interface ClipCopy {
  generated_at: string;
  platforms: Record<CopyPlatform, PlatformCopy>;
}

interface CopyKitClip {
  id: string;
  clip_order: number;
  copy?: ClipCopy | null;
}

interface ClipCopyKitButtonProps {
  taskId: string;
  clip: CopyKitClip;
  onChanged?: () => void | Promise<void>;
}

const PLATFORMS: { id: CopyPlatform; label: string; titleLimit: number }[] = [
  { id: "tiktok", label: "TikTok", titleLimit: 100 },
  { id: "youtube_shorts", label: "YouTube Shorts", titleLimit: 100 },
  { id: "instagram_reels", label: "Instagram Reels", titleLimit: 125 },
];

function allText(p: PlatformCopy): string {
  return `${p.title}\n\n${p.description}\n\n${p.hashtags.join(" ")}`;
}

export function ClipCopyKitButton({ taskId, clip, onChanged }: ClipCopyKitButtonProps) {
  const t = useTranslations("social");
  const copyText = useCopyText();
  const [open, setOpen] = useState(false);
  const [platform, setPlatform] = useState<CopyPlatform>("tiktok");
  const [copy, setCopy] = useState<ClipCopy | null>(clip.copy ?? null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = async (regenerate: boolean) => {
    setLoading(true);
    setError(null);
    try {
      const response = await fetch(`/api/tasks/${taskId}/clips/${clip.id}/copy`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ regenerate }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || !data?.copy) {
        throw new Error(typeof data?.detail === "string" ? data.detail : t("copyKit.error"));
      }
      setCopy(data.copy as ClipCopy);
      await onChanged?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : t("copyKit.error"));
    } finally {
      setLoading(false);
    }
  };

  const meta = PLATFORMS.find((p) => p.id === platform)!;
  const current = copy?.platforms?.[platform];

  const field = (label: string, value: string, key: string, extra?: React.ReactNode) => (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between gap-2">
        <span className="text-sm font-medium">{label}</span>
        <div className="flex items-center gap-2">
          {extra}
          <Button
            type="button"
            size="sm"
            variant="ghost"
            aria-label={t("copyKit.copyField", { field: label })}
            onClick={() => void copyText(value)}
          >
            <Copy className="size-4" />
            {t("copyKit.copy")}
          </Button>
        </div>
      </div>
      <p data-testid={`copykit-${key}`} className="whitespace-pre-wrap rounded-lg border bg-card p-3 text-sm">
        {value}
      </p>
    </div>
  );

  return (
    <>
      <Button size="sm" variant="outline" onClick={() => setOpen(true)}>
        <Sparkles className="w-4 h-4" />
        {t("copyKit.open")}
      </Button>

      <Sheet open={open} onOpenChange={setOpen}>
        <SheetContent className="w-full sm:max-w-lg overflow-y-auto">
          <SheetHeader>
            <SheetTitle>{t("copyKit.title", { order: clip.clip_order })}</SheetTitle>
            <SheetDescription>{t("copyKit.body")}</SheetDescription>
          </SheetHeader>

          <div className="px-4 pb-4 space-y-5">
            {error && (
              <Alert variant="destructive">
                <AlertCircle className="h-4 w-4" />
                <AlertDescription>{error}</AlertDescription>
              </Alert>
            )}

            {!copy ? (
              <Button onClick={() => void run(false)} disabled={loading}>
                {loading ? <Loader2 className="size-4 animate-spin" /> : <Sparkles className="size-4" />}
                {loading ? t("copyKit.generating") : t("copyKit.generate")}
              </Button>
            ) : (
              <>
                <Tabs value={platform} onValueChange={(value) => setPlatform(value as CopyPlatform)}>
                  <TabsList>
                    {PLATFORMS.map((p) => (
                      <TabsTrigger key={p.id} value={p.id}>{p.label}</TabsTrigger>
                    ))}
                  </TabsList>
                </Tabs>

                {current && (
                  <div className="space-y-4">
                    {field(
                      t("copyKit.titleLabel"),
                      current.title,
                      "title",
                      <span
                        data-testid="copykit-counter"
                        className={current.title.length > meta.titleLimit ? "text-xs text-destructive" : "text-xs text-muted-foreground"}
                      >
                        {current.title.length}/{meta.titleLimit}
                      </span>,
                    )}
                    {field(t("copyKit.descriptionLabel"), current.description, "description")}
                    {field(t("copyKit.hashtagsLabel"), current.hashtags.join(" "), "hashtags")}
                  </div>
                )}

                <div className="flex flex-wrap gap-2">
                  <Button type="button" onClick={() => current && void copyText(allText(current))} disabled={!current}>
                    <Copy className="size-4" />
                    {t("copyKit.copyAll")}
                  </Button>
                  <Button type="button" variant="outline" onClick={() => void run(true)} disabled={loading}>
                    {loading ? <Loader2 className="size-4 animate-spin" /> : <RefreshCw className="size-4" />}
                    {loading ? t("copyKit.generating") : t("copyKit.regenerate")}
                  </Button>
                </div>
              </>
            )}
          </div>
        </SheetContent>
      </Sheet>
    </>
  );
}
