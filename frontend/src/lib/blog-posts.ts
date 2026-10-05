import type { Metadata } from "next";
import { getSiteUrl } from "@/lib/site";

export { HOSTED_APP_URL, getSiteUrl } from "@/lib/site";

export interface BlogPost {
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
    title: "Best Free, Self-Hosted OpusClip Alternative",
    description:
      "Looking for a free OpusClip alternative? SupoClip is an open-source AI clip maker you can self-host for free. It turns long videos into captioned, vertical shorts.",
    eyebrow: "OpusClip Alternative",
    category: "Comparison",
    publishedAt: "2026-05-07",
    updatedAt: "2026-10-05",
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
    summary:
      "SupoClip is built for creators who want OpusClip-style AI clipping on their own infrastructure, without another credit-based subscription.",
  },
];

export function getBlogPost(slug: string) {
  return blogPosts.find((post) => post.slug === slug);
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
