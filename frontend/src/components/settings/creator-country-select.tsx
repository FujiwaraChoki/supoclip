"use client";

import { useMemo } from "react";
import { useLocale, useTranslations } from "next-intl";

import { Select, SelectContent, SelectItem, SelectSeparator, SelectTrigger, SelectValue } from "@/components/ui/select";
import { PAYOUT_COUNTRIES } from "@/lib/creator-commission";

/** "ZZ": the creator lives outside the countries Stripe can pay commission to. */
export const ELSEWHERE = "ZZ";

export function useCountryName() {
  const locale = useLocale();
  const t = useTranslations("settings.creator");
  return useMemo(() => {
    const names = new Intl.DisplayNames([locale], { type: "region" });
    return (code: string) => (code === ELSEWHERE ? t("countryElsewhere") : names.of(code) ?? code);
  }, [locale, t]);
}

/** Where a creator lives: the payout countries, then "Somewhere else". */
export function CreatorCountrySelect({
  value,
  onChange,
  id,
}: {
  value: string;
  onChange: (value: string) => void;
  id?: string;
}) {
  const t = useTranslations("settings.creator");
  const locale = useLocale();
  const countryName = useCountryName();
  const countries = useMemo(
    () => [...PAYOUT_COUNTRIES].map((code) => ({ code, name: countryName(code) })).sort((a, b) => a.name.localeCompare(b.name, locale)),
    [countryName, locale],
  );

  return (
    <Select value={value} onValueChange={onChange}>
      <SelectTrigger id={id} className="mt-1.5 w-full" aria-label={t("countryLabel")}>
        <SelectValue placeholder={t("countryPlaceholder")} />
      </SelectTrigger>
      <SelectContent>
        {countries.map(({ code, name }) => (
          <SelectItem key={code} value={code}>{name}</SelectItem>
        ))}
        <SelectSeparator />
        <SelectItem value={ELSEWHERE}>{t("countryElsewhere")}</SelectItem>
      </SelectContent>
    </Select>
  );
}
