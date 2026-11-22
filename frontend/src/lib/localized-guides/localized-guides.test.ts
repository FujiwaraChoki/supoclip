import { describe, expect, it } from "vitest";

import { getArticleClipGallery } from "@/lib/article-clips";
import { applyBlogCopy, getBlogPost } from "@/lib/blog-posts";
import { getCreatorGuide } from "@/lib/creator-guides";
import { getMarketingLink } from "@/lib/marketing-links";
import {
  GUIDE_LOCALES,
  LOCALIZED_GUIDE_SLUGS,
  contentLangAttr,
  getArticleShellExtras,
  getClipExamplesChrome,
  getEditorFigureCopy,
  getGta6Copy,
  resolveContentLocale,
  resolveGuideLocale,
  type LocalizedCreatorGuideSlug,
  type TranslatedGuideLocale,
} from "./index";

const TRANSLATED_LOCALES: TranslatedGuideLocale[] = ["es", "pt", "id"];
const FALLBACK_LOCALES = ["de", "zh"] as const;
const CREATOR_GUIDE_SLUGS: LocalizedCreatorGuideSlug[] = [
  "twitch-vod-to-shorts",
  "podcast-to-shorts",
  "find-best-moments-in-video",
  "word-by-word-video-captions",
];

describe("content locale resolution", () => {
  it("resolves supported locales and falls back to English for de, zh, or unknown values", () => {
    for (const locale of GUIDE_LOCALES) {
      expect(resolveContentLocale(GUIDE_LOCALES, locale)).toBe(locale);
      expect(resolveGuideLocale(locale)).toBe(locale);
    }
    for (const locale of FALLBACK_LOCALES) {
      expect(resolveContentLocale(GUIDE_LOCALES, locale)).toBe("en");
      expect(resolveGuideLocale(locale)).toBe("en");
    }
    expect(resolveContentLocale(GUIDE_LOCALES, "fr")).toBe("en");
    expect(resolveContentLocale(GUIDE_LOCALES, undefined)).toBe("en");
    expect(resolveContentLocale(["en"], "pt")).toBe("en");
    expect(resolveContentLocale(undefined, "de")).toBe("de");
  });

  it("sets lang attribute only when target content falls back to another language", () => {
    for (const locale of GUIDE_LOCALES) {
      expect(contentLangAttr(GUIDE_LOCALES, locale)).toBeUndefined();
    }
    for (const locale of FALLBACK_LOCALES) {
      expect(contentLangAttr(GUIDE_LOCALES, locale)).toBe("en");
    }
    expect(contentLangAttr(["en"], "en")).toBeUndefined();
    expect(contentLangAttr(["en"], "pt")).toBe("en");
    expect(contentLangAttr(undefined, "pt")).toBeUndefined();
  });
});

describe("localized blog post metadata and links", () => {
  it("marks all five top guides with GUIDE_LOCALES in blogPosts and marketingLinks", () => {
    for (const slug of LOCALIZED_GUIDE_SLUGS) {
      expect(getBlogPost(slug)?.languages).toEqual(GUIDE_LOCALES);
      expect(getMarketingLink(`/blog/${slug}`)?.languages).toEqual(GUIDE_LOCALES);
    }
  });

  it("localizes blog post metadata for es, pt, and id and falls back to English for de and zh", () => {
    const unusedTranslate = () => {
      throw new Error("Should not call messages translator for guides with explicit languages");
    };

    for (const slug of LOCALIZED_GUIDE_SLUGS) {
      const english = getBlogPost(slug);
      expect(english, slug).toBeDefined();
      if (!english) continue;

      expect(applyBlogCopy(english, unusedTranslate, "en")).toEqual(english);
      for (const fallback of FALLBACK_LOCALES) {
        expect(applyBlogCopy(english, unusedTranslate, fallback)).toEqual(english);
      }

      for (const locale of TRANSLATED_LOCALES) {
        const localized = applyBlogCopy(english, unusedTranslate, locale);
        expect(localized.slug).toBe(english.slug);
        expect(localized.title, `${slug}:${locale}`).not.toBe(english.title);
        expect(localized.description, `${slug}:${locale}`).not.toBe(english.description);
        expect(localized.summary, `${slug}:${locale}`).not.toBe(english.summary);
        expect(localized.readingTime, `${slug}:${locale}`).not.toBe(english.readingTime);
        expect(localized.category, `${slug}:${locale}`).not.toBe(english.category);
        if (english.image) {
          expect(localized.image?.src).toBe(english.image.src);
          expect(localized.image?.alt, `${slug}:${locale}:alt`).not.toBe(english.image.alt);
        }
      }
    }
  });

  it("keeps English-only guides in English across every locale", () => {
    const englishOnly = getBlogPost("tiktok-publishing-is-live");
    expect(englishOnly?.languages).toEqual(["en"]);
    if (!englishOnly) return;
    for (const locale of ["en", "es", "pt", "id", "de", "zh"] as const) {
      expect(applyBlogCopy(englishOnly, () => "", locale)).toEqual(englishOnly);
    }
  });
});

describe("localized creator guides structural parity", () => {
  it("preserves section IDs, links, tables, examples, FAQs, and galleries across es, pt, and id", () => {
    for (const slug of CREATOR_GUIDE_SLUGS) {
      const english = getCreatorGuide(slug, "en");
      expect(english, slug).toBeDefined();
      if (!english) continue;

      for (const fallback of FALLBACK_LOCALES) {
        expect(getCreatorGuide(slug, fallback)).toEqual(english);
      }

      const englishGallery = getArticleClipGallery(slug, "en");
      expect(englishGallery, `${slug}:gallery`).toBeDefined();

      for (const locale of TRANSLATED_LOCALES) {
        const localized = getCreatorGuide(slug, locale);
        expect(localized, `${slug}:${locale}`).toBeDefined();
        if (!localized) continue;

        expect(localized.intro).not.toBe(english.intro);
        expect(localized.cta.title).not.toBe(english.cta.title);
        expect(localized.cta.body).not.toBe(english.cta.body);
        expect(localized.cta.label).not.toBe(english.cta.label);
        expect(localized.related).toEqual(english.related);

        expect(localized.sections.map((s) => s.id)).toEqual(english.sections.map((s) => s.id));
        localized.sections.forEach((section, index) => {
          const baseSection = english.sections[index];
          expect(section.heading, `${slug}:${locale}:${section.id}`).not.toBe(baseSection.heading);
          expect(section.paragraphs).toHaveLength(baseSection.paragraphs.length);
          expect(section.bullets?.length).toBe(baseSection.bullets?.length);
          expect(section.steps?.length).toBe(baseSection.steps?.length);
          expect(section.example).toBe(baseSection.example);
          expect(section.links?.map((l) => l.href)).toEqual(baseSection.links?.map((l) => l.href));
          if (baseSection.table) {
            expect(section.table?.headers).toHaveLength(baseSection.table.headers.length);
            expect(section.table?.rows).toHaveLength(baseSection.table.rows.length);
          }
        });

        expect(localized.faqs).toHaveLength(english.faqs.length);
        localized.faqs.forEach((faq, index) => {
          expect(faq.question).not.toBe(english.faqs[index].question);
          expect(faq.answer).not.toBe(english.faqs[index].answer);
        });

        const localizedGallery = getArticleClipGallery(slug, locale);
        expect(localizedGallery?.afterSection).toBe(englishGallery?.afterSection);
        expect(localizedGallery?.heading).not.toBe(englishGallery?.heading);
        expect(localizedGallery?.examples.map((e) => e.clipId)).toEqual(
          englishGallery?.examples.map((e) => e.clipId),
        );
      }
    }
  });

  it("localizes shell extras, clip example chrome, and editor figure copy", () => {
    const enShell = getArticleShellExtras("en");
    const enChrome = getClipExamplesChrome("en");
    const enFigure = getEditorFigureCopy("word-by-word-video-captions", "en");

    for (const fallback of FALLBACK_LOCALES) {
      expect(getArticleShellExtras(fallback)).toEqual(enShell);
      expect(getClipExamplesChrome(fallback)).toEqual(enChrome);
      expect(getEditorFigureCopy("word-by-word-video-captions", fallback)).toEqual(enFigure);
    }

    for (const locale of TRANSLATED_LOCALES) {
      expect(getArticleShellExtras(locale).seeHostedPlans).not.toBe(enShell.seeHostedPlans);
      expect(getClipExamplesChrome(locale).eyebrow).not.toBe(enChrome.eyebrow);
      expect(getClipExamplesChrome(locale).watchOriginal).not.toBe(enChrome.watchOriginal);
      expect(getEditorFigureCopy("word-by-word-video-captions", locale).caption).not.toBe(enFigure.caption);
    }
  });
});

describe("localized GTA 6 guide and interactive components", () => {
  it("provides localized copy for es, pt, and id and preserves structure and facts", () => {
    const english = getGta6Copy("en");

    for (const fallback of FALLBACK_LOCALES) {
      expect(getGta6Copy(fallback)).toEqual(english);
    }

    for (const locale of TRANSLATED_LOCALES) {
      const copy = getGta6Copy(locale);

      expect(copy.post.title).not.toBe(english.post.title);
      expect(copy.breadcrumb).not.toBe(english.breadcrumb);
      expect(copy.faqs).toHaveLength(english.faqs.length);
      expect(copy.mistakes.items).toHaveLength(english.mistakes.items.length);
      expect(copy.workflowDiagram.steps).toHaveLength(english.workflowDiagram.steps.length);
      expect(copy.clipAnatomy.parts.map(({ from, to }) => [from, to])).toEqual(
        english.clipAnatomy.parts.map(({ from, to }) => [from, to]),
      );
      expect(copy.rulesBoard.groups.map((g) => g.status)).toEqual(
        english.rulesBoard.groups.map((g) => g.status),
      );
      expect(copy.launchWeekTable.rows).toHaveLength(english.launchWeekTable.rows.length);
      expect(copy.momentMap.moments.map(({ minute, kind }) => ({ minute, kind }))).toEqual(
        english.momentMap.moments.map(({ minute, kind }) => ({ minute, kind })),
      );

      // Preserve canonical brand and game entity names.
      const serialized = JSON.stringify(copy);
      for (const entity of ["SupoClip", "Rockstar Games", "Whop Content Rewards", "Lucia", "Jason", "Leonida", "Vice City"]) {
        expect(serialized, `${locale}:${entity}`).toContain(entity);
      }
    }
  });
});
