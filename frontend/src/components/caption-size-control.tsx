"use client";

import { useTranslations } from "next-intl";

import { FONT_SIZE_OPTIONS } from "@/lib/font-options";
import { cn } from "@/lib/utils";

export function CaptionSizeControl({ value, onChange, disabled = false }: {
  value: number | null; onChange: (value: number | null) => void; disabled?: boolean;
}) {
  const t = useTranslations("home");
  const labelFor = (option: number | null) => {
    if (option === null) return t("captionSize.templateDefault");
    if (option === 18) return t("captionSize.small");
    if (option === 28) return t("captionSize.medium");
    if (option === 40) return t("captionSize.large");
    return String(option);
  };
  return <fieldset disabled={disabled} className="space-y-2">
    <legend className="text-sm text-muted-foreground">{t("captionSize.size")}</legend>
    <div className="grid grid-cols-4 gap-1.5">
      {FONT_SIZE_OPTIONS.map((option) => <button key={option.label} type="button"
        aria-pressed={value === option.value} onClick={() => onChange(option.value)}
        className={cn("rounded-md border px-2 py-1.5 text-xs font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring", value === option.value ? "border-primary bg-primary text-primary-foreground" : "border-input bg-background text-muted-foreground hover:bg-accent")}>
        {labelFor(option.value)}
      </button>)}
    </div>
  </fieldset>;
}
