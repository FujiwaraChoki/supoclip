import { defaultLocale, isLocale, type Locale } from "@/i18n/config";

export const GUIDE_LOCALES = ["en", "es", "pt", "id"] as const;

export type GuideLocale = (typeof GUIDE_LOCALES)[number];
export type TranslatedGuideLocale = Exclude<GuideLocale, "en">;

export const LOCALIZED_GUIDE_SLUGS = [
  "how-to-clip-gta-6",
  "twitch-vod-to-shorts",
  "podcast-to-shorts",
  "find-best-moments-in-video",
  "word-by-word-video-captions",
] as const;

export type LocalizedGuideSlug = (typeof LOCALIZED_GUIDE_SLUGS)[number];
export type LocalizedCreatorGuideSlug = Exclude<LocalizedGuideSlug, "how-to-clip-gta-6">;

export interface GuideSection {
  id: string;
  heading: string;
  paragraphs: string[];
  steps?: string[];
  bullets?: string[];
  table?: { caption: string; headers: string[]; rows: string[][] };
  links?: Array<{ href: string; label: string }>;
  example?: "editor";
}

export interface LocalizedBlogPostCopy {
  title: string;
  description: string;
  eyebrow: string;
  category: string;
  readingTime: string;
  summary: string;
  keywords?: string[];
  imageAlt?: string;
}

export interface LocalizedCreatorGuideContent {
  post: LocalizedBlogPostCopy;
  intro: string;
  sections: GuideSection[];
  faqs: Array<{ question: string; answer: string }>;
  cta: { title: string; body: string; label: string };
  gallery?: {
    heading: string;
    intro: string;
    notes: Record<string, string>;
  };
  editorFigure?: {
    ariaLabel: string;
    alt: string;
    caption: string;
  };
}

export interface ArticleShellExtras {
  seeHostedPlans: string;
  faqTocLabel: string;
}

export interface ClipExamplesChrome {
  eyebrow: string;
  fullClipMeta: (durationSeconds: number, sourceRange: string) => string;
  videoAriaLabel: (clipTitle: string, sourceTitle: string) => string;
  sourcePrefix: string;
  watchOriginal: string;
  detailsSummary: string;
  viewsNote: (formattedViews: string, checkedAt: string) => string;
  licenseRecord: string;
  noEndorsement: string;
  downloadAttribution: string;
  footerNote: string;
}

export type Gta6RuleStatus = "ok" | "warn" | "no";
export type Gta6MomentKind = "spoken" | "silent" | "skip";

export interface Gta6ArticleCopy {
  post: LocalizedBlogPostCopy;
  breadcrumb: string;
  toc: {
    whyNow: string;
    whatItPays: string;
    rules: string;
    workflow: string;
    launchWeek: string;
    mistakes: string;
    faq: string;
  };
  faqs: Array<{ question: string; answer: string }>;
  cta: { title: string; body: string; label: string };
  heroCaption: string;
  intro: [string, string];
  whyNow: {
    heading: string;
    p1LinkText: string;
    p1AfterLinkBeforeDate: string;
    p1Date: string;
    p1AfterDate: string;
    p2BeforeStrong: string;
    p2Strong: string;
    p2AfterStrong: string;
  };
  whatItPays: {
    heading: string;
    lead: string;
    li1Label: string;
    li1BeforeLink: string;
    li1LinkText: string;
    li1AfterLink: string;
    li2Label: string;
    li2BeforeLink: string;
    li2LinkText: string;
    li2AfterLink: string;
    mathIntro: string;
  };
  rules: {
    heading: string;
    p1BeforeLink: string;
    p1LinkText: string;
    p1BetweenLinkAndEm: string;
    p1Em: string;
    p1AfterEm: string;
    p2: string;
  };
  workflow: {
    heading: string;
    lead: string;
    s1Heading: string;
    s1Body: string;
    s2Heading: string;
    s2Body: string;
    s3Heading: string;
    s3BodyBefore: string;
    s3BodyAfter: string;
    s4Heading: string;
    s4Body: string;
    s5Heading: string;
    s5Body: string;
    editorAlt: string;
    editorCaption: string;
    s6Heading: string;
    s6Body: string;
    scheduleLinkLabel: string;
  };
  launchWeek: {
    heading: string;
    lead: string;
  };
  mistakes: {
    heading: string;
    items: Array<{ label: string; body: string }>;
    moreBeforeFirst: string;
    moreFirstLink: string;
    moreBetween: string;
    moreSecondLink: string;
    moreAfter: string;
  };
  sources: {
    heading: string;
    gtaSix: string;
    rockstarPolicy: string;
    whopContentRewards: string;
    youtubeMonetization: string;
    checkedNote: string;
  };
  calculator: {
    eyebrow: string;
    title: string;
    description: string;
    clipsPerWeekLabel: string;
    clipsPerWeekHint: string;
    viewsPerClipLabel: string;
    viewsPerClipHint: string;
    rateLabel: string;
    rateHint: string;
    capLabel: string;
    capNone: string;
    capHint: string;
    vodHoursLabel: string;
    vodHoursUnit: string;
    vodHoursHint: string;
    payoutLabel: string;
    payoutDetail: (clipsPerMonth: number, formattedViews: string) => string;
    perClipLabel: string;
    cappedDetail: string;
    uncappedDetail: string;
    afterPlanLabel: string;
    planFitsDetail: (minutes: number, planName: string, formattedPrice: string, planMinutes: number) => string;
    planOverDetail: (minutes: number) => string;
    averageLead: string;
    averageBody: string;
    disclaimer: string;
  };
  workflowDiagram: {
    caption: string;
    youLabel: string;
    appLabel: string;
    steps: Array<{ title: string; you: string; app: string }>;
  };
  reactionLayout: {
    svgTitle: string;
    svgDesc: string;
    sourceTitle: string;
    sourceSubtitle: string;
    captionsHere: string;
    outputTitle: string;
    figcaption: string;
  };
  clipAnatomy: {
    eyebrow: string;
    title: string;
    hookBarNote: string;
    secondsUnit: string;
    parts: Array<{ label: string; from: number; to: number; note: string }>;
  };
  rulesBoard: {
    caption: string;
    groups: Array<{ status: Gta6RuleStatus; label: string; items: string[] }>;
  };
  launchWeekTable: {
    caption: string;
    headers: [string, string, string];
    rows: Array<[string, string, string]>;
  };
  momentMap: {
    eyebrow: string;
    title: string;
    description: string;
    legendAria: string;
    momentsAria: string;
    kinds: Record<Gta6MomentKind, { label: string; short: string }>;
    moments: Array<{ minute: number; kind: Gta6MomentKind; title: string; note: string }>;
    showTableSummary: string;
    tableHeaders: [string, string, string];
  };
}

export interface TranslatedGuideBundle {
  locale: TranslatedGuideLocale;
  shell: ArticleShellExtras;
  clipExamplesChrome: ClipExamplesChrome;
  gta6: Gta6ArticleCopy;
  creatorGuides: Record<LocalizedCreatorGuideSlug, LocalizedCreatorGuideContent>;
}

/**
 * Resolve the locale to render for an editorial page or link.
 * When `supported` is omitted, the content is available in every app locale.
 * Otherwise, returns `activeLocale` when supported, falling back to `supported[0]` (usually `"en"`).
 */
export function resolveContentLocale(
  supported: readonly Locale[] | undefined,
  activeLocale: string | null | undefined,
): Locale {
  const normalized: Locale = isLocale(activeLocale) ? activeLocale : defaultLocale;
  if (!supported || supported.length === 0) return normalized;
  return supported.includes(normalized) ? normalized : (supported[0] ?? defaultLocale);
}

/** Resolve one of the 4 localized guide locales (`en`, `es`, `pt`, `id`), falling back to `en`. */
export function resolveGuideLocale(activeLocale: string | null | undefined): GuideLocale {
  return resolveContentLocale(GUIDE_LOCALES, activeLocale) as GuideLocale;
}

/**
 * Returns an HTML `lang` attribute only when the target content falls back to a different
 * language than the active UI locale (for example, `"en"` when browsing in `"de"` or `"zh"`).
 */
export function contentLangAttr(
  supported: readonly Locale[] | undefined,
  activeLocale: string | null | undefined,
): Locale | undefined {
  if (!supported || supported.length === 0) return undefined;
  const normalized: Locale = isLocale(activeLocale) ? activeLocale : defaultLocale;
  const resolved = resolveContentLocale(supported, normalized);
  return resolved === normalized ? undefined : resolved;
}
