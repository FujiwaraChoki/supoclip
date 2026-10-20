"use client";

import { Languages } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { useRouter } from "next/navigation";
import { useTransition } from "react";

import { setUserLocale } from "@/i18n/actions";
import { locales, type Locale } from "@/i18n/config";
import { cn } from "@/lib/utils";

const NAMES: Record<Locale, string> = {
  en: "English",
  es: "Español",
  pt: "Português",
  de: "Deutsch",
  zh: "中文",
};

const SHORT: Record<Locale, string> = {
  en: "EN",
  es: "ES",
  pt: "PT",
  de: "DE",
  zh: "中文",
};

export function LanguageSwitcher({
  className,
  compact = false,
  variant = "select",
}: {
  className?: string;
  compact?: boolean;
  variant?: "select" | "inline";
}) {
  const locale = useLocale();
  const t = useTranslations("language");
  const router = useRouter();
  const [pending, startTransition] = useTransition();

  function onChange(next: string) {
    if (next === locale) return;
    startTransition(async () => {
      await setUserLocale(next);
      router.refresh();
    });
  }

  if (variant === "inline") {
    return (
      <div className={cn("flex flex-wrap gap-x-3 gap-y-1", className)}>
        <span className="sr-only">{t("label")}</span>
        {locales.map((code) => (
          <button
            key={code}
            type="button"
            onClick={() => onChange(code)}
            disabled={pending}
            aria-current={code === locale ? "true" : undefined}
            className={cn(
              "transition-colors hover:text-foreground",
              code === locale ? "font-medium text-foreground" : "text-muted-foreground",
            )}
          >
            {NAMES[code]}
          </button>
        ))}
      </div>
    );
  }

  const labels = compact ? SHORT : NAMES;

  return (
    <label className={cn("flex items-center gap-2 text-sm text-muted-foreground", className)}>
      <Languages className={cn("shrink-0", compact ? "size-4" : "size-4")} aria-hidden />
      <span className={compact ? "sr-only" : "shrink-0"}>{t("label")}</span>
      <select
        value={locale}
        onChange={(event) => onChange(event.target.value)}
        disabled={pending}
        aria-label={t("label")}
        className={cn(
          "h-8 rounded-md border border-sidebar-border bg-background text-foreground disabled:opacity-60",
          compact ? "w-14 px-1 text-xs" : "min-w-0 flex-1 px-2 text-sm",
        )}
      >
        {locales.map((code) => (
          <option key={code} value={code}>
            {labels[code]}
          </option>
        ))}
      </select>
    </label>
  );
}
