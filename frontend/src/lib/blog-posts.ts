import type { Metadata } from "next";
import type { Locale } from "@/i18n/config";
import { getSiteUrl } from "@/lib/site";
import { creatorGuides } from "@/lib/creator-guides";
import {
  GUIDE_LOCALES,
  getLocalizedBlogPostCopy,
  resolveContentLocale,
} from "@/lib/localized-guides";

export { HOSTED_APP_URL, getSiteUrl } from "@/lib/site";

export interface BlogPost {
  /** Locales in which this editorial post is translated; omit when covered in all app locales via `messages/<locale>/blog.json`. */
  languages?: readonly Locale[];
  slug: string;
  image?: { src: string; alt: string; width: number; height: number };
  title: string;
  description: string;
  eyebrow: string;
  category: string;
  publishedAt: string;
  updatedAt: string;
  readingTime: string;
  author: string;
  keywords: string[];
  summary: string;
}

export const blogPosts: BlogPost[] = [
  {
    languages: ["en"],
    slug: "how-to-make-money-clipping",
    image: {
      src: "/blog/how-to-make-money-clipping.webp",
      alt: "Editorial illustration of a horizontal podcast recording with a waveform turning into three vertical captioned short clips with upward view badges and coins",
      width: 1672,
      height: 940,
    },
    title: "How to Make Money Clipping (Including Whop Content Rewards)",
    description:
      "A realistic guide to making money clipping podcasts and streams: how Whop Content Rewards CPM payouts and fees work, platform originality rules, an interactive income calculator, and how to land client retainers.",
    eyebrow: "Make money clipping",
    category: "Guide",
    publishedAt: "2026-11-25",
    updatedAt: "2026-11-25",
    readingTime: "12 min read",
    author: "SupoClip Team",
    keywords: [
      "how to make money clipping",
      "Whop clipping",
      "Whop Content Rewards",
      "Content Rewards clipping",
      "make money clipping podcasts",
      "clipping agency retainers",
      "short-form clipping workflow",
    ],
    summary:
      "How clippers actually get paid across Whop Content Rewards, client retainers, TikTok, and YouTube Shorts—with real rates, fee math, platform traps, and a repeatable editing workflow.",
  },
  {
    languages: GUIDE_LOCALES,
    slug: "how-to-clip-gta-6",
    image: { src: "/blog/how-to-clip-gta-6.webp", alt: "Illustration of a GTA-style sunset stream with a webcam inset turning into three vertical captioned clips", width: 1672, height: 941 },
    title: "How to Clip GTA 6 (and What It Really Pays)",
    description: "Clip GTA 6 streams into TikToks and Shorts: what clipping campaigns pay, Rockstar's rules, and a step-by-step workflow from VOD to captioned vertical clips.",
    eyebrow: "GTA 6 clipping",
    category: "Guide",
    publishedAt: "2026-11-17",
    updatedAt: "2026-11-17",
    readingTime: "10 min read",
    author: "SupoClip Team",
    keywords: ["how to clip GTA 6", "GTA 6 clipping", "make money clipping GTA 6", "GTA 6 clipping campaign", "GTA 6 clips", "clip GTA 6 streams", "GTA 6 TikTok clips"],
    summary: "Everyone is clipping GTA 6. Here is what it pays, which clips get taken down, and the workflow from a four-hour stream to vertical clips worth posting.",
  },
  {
    languages: ["en"],
    slug: "tiktok-publishing-is-live",
    title: "TikTok publishing is now live in SupoClip",
    description: "Publish clips straight to TikTok, select TikTok and YouTube together on the web, and schedule ahead on Creator. Here’s how to get started.",
    eyebrow: "Product update",
    category: "Product updates",
    publishedAt: "2026-10-30",
    updatedAt: "2026-10-30",
    readingTime: "3 min read",
    author: "SupoClip",
    keywords: ["SupoClip TikTok publishing", "publish clips to TikTok", "TikTok and YouTube publishing", "schedule TikTok posts"],
    summary: "Your finished clips can now go straight to TikTok. Connect your account, pick a clip, and publish from SupoClip—without downloading and uploading it again.",
  },
  {
    languages: ["en"],
    slug: "supoclip-vs-opusclip-klap-vizard",
    title: "SupoClip vs OpusClip vs Klap vs Vizard: Pricing and Features Compared",
    description: "SupoClip, OpusClip, Klap and Vizard compared on free plans, pricing per hour or clip, exports, imports, publishing and APIs, with a source for every claim.",
    eyebrow: "AI clipper comparison",
    category: "Comparison",
    publishedAt: "2026-11-24",
    updatedAt: "2026-11-24",
    readingTime: "9 min read",
    author: "SupoClip",
    keywords: ["SupoClip vs OpusClip", "OpusClip vs Klap", "OpusClip vs Vizard", "Klap vs Vizard", "OpusClip alternative", "Klap alternative", "Vizard alternative", "best AI clipping tool"],
    summary: "Four AI clippers, four ways to charge. Here is what each costs per hour of video or per clip, what the free plans really include, and which one fits how you work.",
  },
  {
    languages: ["en"],
    slug: "free-opusclip-alternatives",
    title: "Best Free OpusClip Alternatives in 2026 (Free Tiers Checked)",
    description: "Free OpusClip alternatives compared: the real free tiers of Reap, Quso, Vizard, CapCut, YouTube Studio and more, plus open-source clippers with no watermark or minute cap.",
    eyebrow: "OpusClip alternatives",
    category: "Comparison",
    publishedAt: "2026-11-24",
    updatedAt: "2026-11-24",
    readingTime: "11 min read",
    author: "SupoClip",
    keywords: ["free OpusClip alternatives", "best free OpusClip alternative", "OpusClip alternative free", "free AI clip maker", "free AI video clipper no watermark", "open source OpusClip alternative"],
    summary: "We read the pricing page of every hosted clipper with a free plan and the repositories of the open-source ones. Here is what free actually gets you, and where each one stops.",
  },
  ...creatorGuides.map((guide) => guide.post),
  {
    slug: "supoclip-vs-supo-live",
    image: { src: "/blog/supoclip-open-source-clipping.webp", alt: "Editorial illustration of a podcast recording becoming three vertical video clips", width: 1672, height: 941 },
    title: "SupoClip vs supo.live: Why Open Source Wins",
    description:
      "Compare SupoClip and supo.live for AI video clipping. See why SupoClip wins for open-source control, self-hosting, and customizable video workflows.",
    eyebrow: "Supo.live Alternative",
    category: "Comparison",
    publishedAt: "2026-09-21",
    updatedAt: "2026-10-05",
    readingTime: "5 min read",
    author: "SupoClip",
    keywords: ["SupoClip vs supo.live", "supo.live alternative", "open-source video clipper", "self-hosted AI video clipping", "AI clip maker"],
    summary:
      "For creators and teams who want control over their clipping workflow, SupoClip is the better open-source alternative to supo.live.",
  },
  {
    slug: "supoclip-vs-opusclip",
    title: "SupoClip vs OpusClip: A Sourced Comparison",
    description:
      "SupoClip vs OpusClip compared on deployment, pricing model, watermarks, model choice, and workflow, with a source linked for every claim.",
    eyebrow: "OpusClip Comparison",
    category: "Comparison",
    publishedAt: "2026-10-05",
    updatedAt: "2026-10-05",
    readingTime: "7 min read",
    author: "SupoClip",
    keywords: [
      "SupoClip vs OpusClip",
      "OpusClip alternative",
      "OpusClip vs open source",
      "self-hosted OpusClip alternative",
      "OpusClip watermark",
    ],
    summary:
      "A side-by-side of what each product documents, where the two differ, and which one fits your workflow.",
  },
  {
    slug: "self-host-supoclip-docker",
    title: "How to Self-Host SupoClip with Docker Compose",
    description:
      "Self-host SupoClip, the open-source AI video clipper: requirements, .env setup, a fully local Whisper + Ollama option, first-run checks, and a production checklist.",
    eyebrow: "Self-Hosting Guide",
    category: "Guide",
    publishedAt: "2026-10-05",
    updatedAt: "2026-10-05",
    readingTime: "9 min read",
    author: "SupoClip",
    keywords: [
      "self-host SupoClip",
      "self-hosted AI video clipper",
      "open-source video clipper Docker",
      "self-hosted OpusClip alternative",
      "Whisper Ollama video clipping",
    ],
    summary:
      "A practical walkthrough of the repository's Docker Compose stack: what each service does, which keys you need, how to run without cloud AI, and what to harden before production.",
  },
  {
    slug: "best-free-opusclip-alternative",
    title: "Free OpusClip Alternative: Self-Hosting Costs and Tradeoffs",
    description: "Looking for a free OpusClip alternative? Understand what self-hosting SupoClip costs, what you maintain, and when a paid hosted workflow is a better fit.",
    eyebrow: "OpusClip Alternative",
    category: "Comparison",
    publishedAt: "2026-05-07",
    updatedAt: "2026-10-26",
    readingTime: "6 min read",
    author: "SupoClip",
    keywords: [
      "free OpusClip alternative",
      "OpusClip alternative",
      "AI clip maker",
      "free AI video clipper",
      "open-source OpusClip alternative",
      "YouTube shorts clipper",
    ],
    summary: "The SupoClip source is free. This guide explains the hardware, provider costs, and maintenance behind a self-hosted clipping workflow so you can decide whether it fits.",
  },
];

export function getBlogPost(slug: string) {
  return blogPosts.find((post) => post.slug === slug);
}

/** Overlay translated title, description, and listing copy for supported locales. */
export function applyBlogCopy(post: BlogPost, translate: (key: string) => string, locale?: string): BlogPost {
  if (post.languages) {
    const resolved = resolveContentLocale(post.languages, locale);
    if (resolved === "en") return post;
    const copy = getLocalizedBlogPostCopy(post.slug, resolved);
    if (!copy) return post;
    return {
      ...post,
      title: copy.title,
      description: copy.description,
      eyebrow: copy.eyebrow,
      category: copy.category,
      readingTime: copy.readingTime,
      summary: copy.summary,
      keywords: copy.keywords ?? post.keywords,
      image: post.image ? { ...post.image, alt: copy.imageAlt ?? post.image.alt } : undefined,
    };
  }
  const slug = post.slug;
  return {
    ...post,
    title: translate(`${slug}.title`),
    description: translate(`${slug}.description`),
    eyebrow: translate(`${slug}.eyebrow`),
    category: translate(`${slug}.category`),
    readingTime: translate(`${slug}.readingTime`),
    summary: translate(`${slug}.summary`),
    image: post.image ? { ...post.image, alt: translate(`${slug}.imageAlt`) } : undefined,
  };
}

export function getBlogPostMetadata(post: BlogPost): Metadata {
  const siteUrl = getSiteUrl();
  const url = `${siteUrl}/blog/${post.slug}`;

  return {
    title: { absolute: `${post.title} | SupoClip` },
    description: post.description,
    keywords: post.keywords,
    alternates: {
      canonical: url,
    },
    openGraph: {
      title: post.title,
      description: post.description,
      type: "article",
      url,
      siteName: "SupoClip",
      publishedTime: post.publishedAt,
      modifiedTime: post.updatedAt,
      authors: [post.author],
      tags: post.keywords,
      images: post.image ? [{ url: post.image.src, width: post.image.width, height: post.image.height, alt: post.image.alt }] : undefined,
    },
    twitter: {
      card: "summary_large_image",
      images: post.image ? [{ url: post.image.src, alt: post.image.alt }] : undefined,
      title: post.title,
      description: post.description,
    },
  };
}
