export const locales = ["en", "es", "pt", "de", "zh", "id"] as const;

export type Locale = (typeof locales)[number];

export const defaultLocale: Locale = "en";

export const LOCALE_COOKIE = "NEXT_LOCALE";

/** Top-level message files under `frontend/messages/<locale>/`. */
export const NAMESPACES = [
  "common",
  "metadata",
  "language",
  "shell",
  "auth",
  "settings",
  "billing",
  "home",
  "library",
  "task",
  "editor",
  "social",
  "analytics",
  "marketing",
  "seo",
  "blog",
  "changelog",
  "admin",
  "affiliate",
  "status",
  "share",
  "legal",
] as const;

export type Namespace = (typeof NAMESPACES)[number];

export function isLocale(value: string | null | undefined): value is Locale {
  return !!value && (locales as readonly string[]).includes(value);
}

/** BCP 47 tag used for `lang`, dates, and `Intl` formatters. */
export function intlLocale(locale: string): string {
  if (locale === "zh") return "zh-Hans";
  if (locale === "pt") return "pt-BR";
  return locale;
}

/** Flat message-catalog key for a path such as `/blog/self-host`. */
export function catalogKey(href: string): string {
  return href
    .replace(/^\//, "")
    .replace(/[^a-z0-9]+/gi, "_")
    .replace(/^_|_$/g, "")
    .toLowerCase();
}

export function negotiateLocale(acceptLanguage: string | null | undefined): Locale {
  if (!acceptLanguage) return defaultLocale;

  const ranked = acceptLanguage
    .split(",")
    .map((part) => {
      const [tag, ...params] = part.trim().split(";");
      const qualityParam = params.map((param) => param.trim()).find((param) => param.startsWith("q="));
      const quality = qualityParam ? Number(qualityParam.slice(2)) : 1;
      return { tag: tag.toLowerCase(), quality: Number.isFinite(quality) ? quality : 0 };
    })
    .filter((entry) => entry.tag && entry.tag !== "*")
    .sort((a, b) => b.quality - a.quality);

  for (const { tag } of ranked) {
    const base = tag.split("-")[0];
    if (base === "zh") return "zh";
    if (isLocale(base)) return base;
  }

  return defaultLocale;
}
