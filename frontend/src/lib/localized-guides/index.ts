import { enClipExamplesChrome, enEditorFigure, enGta6Copy, enShellExtras } from "./en";
import { esGuideBundle } from "./es";
import { idGuideBundle } from "./id";
import { ptGuideBundle } from "./pt";
import {
  GUIDE_LOCALES,
  LOCALIZED_GUIDE_SLUGS,
  resolveGuideLocale,
  type ArticleShellExtras,
  type ClipExamplesChrome,
  type Gta6ArticleCopy,
  type LocalizedBlogPostCopy,
  type LocalizedCreatorGuideContent,
  type LocalizedCreatorGuideSlug,
  type LocalizedGuideSlug,
  type TranslatedGuideBundle,
  type TranslatedGuideLocale,
} from "./types";

export * from "./types";

const TRANSLATED_BUNDLES: Record<TranslatedGuideLocale, TranslatedGuideBundle> = {
  es: esGuideBundle,
  pt: ptGuideBundle,
  id: idGuideBundle,
};

export function isLocalizedGuideSlug(slug: string): slug is LocalizedGuideSlug {
  return (LOCALIZED_GUIDE_SLUGS as readonly string[]).includes(slug);
}

function isLocalizedCreatorGuideSlug(slug: string): slug is LocalizedCreatorGuideSlug {
  return slug !== "how-to-clip-gta-6" && isLocalizedGuideSlug(slug);
}

export function getTranslatedGuideBundle(locale: string | null | undefined): TranslatedGuideBundle | null {
  const resolved = resolveGuideLocale(locale);
  if (resolved === "en") return null;
  return TRANSLATED_BUNDLES[resolved];
}

export function getLocalizedBlogPostCopy(
  slug: string,
  locale: string | null | undefined,
): LocalizedBlogPostCopy | null {
  const bundle = getTranslatedGuideBundle(locale);
  if (!bundle) return null;
  if (slug === "how-to-clip-gta-6") return bundle.gta6.post;
  if (isLocalizedCreatorGuideSlug(slug)) return bundle.creatorGuides[slug].post;
  return null;
}

export function getLocalizedCreatorGuideContent(
  slug: string,
  locale: string | null | undefined,
): LocalizedCreatorGuideContent | null {
  const bundle = getTranslatedGuideBundle(locale);
  if (!bundle || !isLocalizedCreatorGuideSlug(slug)) return null;
  return bundle.creatorGuides[slug];
}

export function getGta6Copy(locale: string | null | undefined): Gta6ArticleCopy {
  const bundle = getTranslatedGuideBundle(locale);
  return bundle ? bundle.gta6 : enGta6Copy;
}

export function getArticleShellExtras(locale: string | null | undefined): ArticleShellExtras {
  const bundle = getTranslatedGuideBundle(locale);
  return bundle ? bundle.shell : enShellExtras;
}

export function getClipExamplesChrome(locale: string | null | undefined): ClipExamplesChrome {
  const bundle = getTranslatedGuideBundle(locale);
  return bundle ? bundle.clipExamplesChrome : enClipExamplesChrome;
}

export function getEditorFigureCopy(slug: string, locale: string | null | undefined) {
  const content = getLocalizedCreatorGuideContent(slug, locale);
  return content?.editorFigure ?? enEditorFigure;
}

export { GUIDE_LOCALES, LOCALIZED_GUIDE_SLUGS };
