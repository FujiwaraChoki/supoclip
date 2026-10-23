"use client";

import { useLocale, useTranslations } from "next-intl";
import { useRouter } from "next/navigation";
import { useTransition } from "react";

import { setUserLocale } from "@/i18n/actions";
import { locales, type Locale } from "@/i18n/config";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";

const NAMES: Record<Locale, string> = {
  en: "English",
  es: "Español",
  pt: "Português",
  de: "Deutsch",
  zh: "中文",
};

export function LanguageSwitcher() {
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

  return (
    <Select value={locale} onValueChange={onChange} disabled={pending}>
      <SelectTrigger className="w-full max-w-xs" aria-label={t("label")}>
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {locales.map((code) => (
          <SelectItem key={code} value={code}>
            {NAMES[code]}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}
