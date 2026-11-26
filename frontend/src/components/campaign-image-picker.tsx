"use client";

import { useEffect, useRef, useState } from "react";
import { useTranslations } from "next-intl";
import { ImagePlus, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { CAMPAIGN_IMAGE_ACCEPT, campaignImageProblem, type CampaignImageProblem } from "@/lib/campaign-image";

/** Optional picture shown above the video for the whole clip (reward campaigns ask for one). */
export function CampaignImagePicker({
  file,
  onChange,
  disabled,
}: {
  file: File | null;
  onChange: (file: File | null) => void;
  disabled?: boolean;
}) {
  const t = useTranslations("home.campaignImage");
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [problem, setProblem] = useState<CampaignImageProblem | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);

  useEffect(() => {
    if (!file) {
      setPreviewUrl(null);
      return;
    }
    const url = URL.createObjectURL(file);
    setPreviewUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [file]);

  const choose = (event: React.ChangeEvent<HTMLInputElement>) => {
    const picked = event.target.files?.[0] ?? null;
    event.target.value = "";
    if (!picked) return;
    const found = campaignImageProblem(picked);
    setProblem(found);
    if (!found) onChange(picked);
  };

  return (
    <div className="space-y-3">
      <div>
        <p className="text-sm font-medium">{t("title")}</p>
        <p className="mt-1 text-xs text-muted-foreground">{t("description")}</p>
      </div>
      <input ref={inputRef} type="file" accept={CAMPAIGN_IMAGE_ACCEPT} onChange={choose} className="hidden" aria-hidden tabIndex={-1} />
      {file && previewUrl ? (
        <div className="flex items-center gap-3 rounded-lg border p-2">
          {/* eslint-disable-next-line @next/next/no-img-element -- a local object URL, not an optimizable asset */}
          <img src={previewUrl} alt={t("previewAlt")} className="h-12 w-20 shrink-0 rounded object-contain bg-muted" />
          <span className="min-w-0 flex-1 truncate text-sm">{file.name}</span>
          <Button type="button" variant="ghost" size="icon" className="size-7" disabled={disabled} onClick={() => { setProblem(null); onChange(null); }} aria-label={t("remove")}>
            <X className="size-4" />
          </Button>
        </div>
      ) : null}
      <Button type="button" variant="outline" size="sm" className="w-full" disabled={disabled} onClick={() => inputRef.current?.click()}>
        <ImagePlus className="size-4" />
        {file ? t("replace") : t("choose")}
      </Button>
      {problem && <p role="status" className="text-xs text-destructive">{t(`problems.${problem}`)}</p>}
      <p className="text-xs text-muted-foreground">{t("hint")}</p>
    </div>
  );
}
