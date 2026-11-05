"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";
import { Search } from "lucide-react";

import { Input } from "@/components/ui/input";
import type { SupportedLanguageLabel } from "@/lib/supported-languages";

function normalize(value: string) {
  return value.normalize("NFD").replace(/\p{M}/gu, "").toLocaleLowerCase().trim();
}

export function SupportedLanguages({ languages }: { languages: SupportedLanguageLabel[] }) {
  const t = useTranslations("marketing.faqPage");
  const [query, setQuery] = useState("");
  const search = normalize(query);
  const filtered = languages.filter(({ label, nativeName, name, code, tag }) => normalize(`${label} ${nativeName} ${name} ${code} ${tag}`).includes(search));

  return (
    <div className="mt-8 rounded-2xl border bg-background p-5 sm:p-6">
      <label htmlFor="language-search" className="text-sm font-medium">{t("searchLabel")}</label>
      <div className="relative mt-2 max-w-md">
        <Search aria-hidden className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
        <Input id="language-search" type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder={t("searchPlaceholder")} className="pl-9" aria-describedby="language-results" />
      </div>
      <p id="language-results" role="status" className="mt-3 text-xs text-muted-foreground">{t("resultCount", { count: filtered.length, total: languages.length })}</p>
      {filtered.length ? (
        <ul aria-label={t("listLabel")} className="mt-6 grid gap-x-6 gap-y-4 sm:grid-cols-2 lg:grid-cols-3">
          {filtered.map(({ code, label, nativeName, tag }) => (
            <li key={code} className="min-w-0 text-sm">
              <span className="block font-medium">{label}</span>
              {nativeName !== label && <span className="mt-0.5 block text-xs text-muted-foreground"><bdi lang={tag}>{nativeName}</bdi></span>}
            </li>
          ))}
        </ul>
      ) : <p className="mt-6 text-sm text-muted-foreground">{t("noResults")}</p>}
    </div>
  );
}
