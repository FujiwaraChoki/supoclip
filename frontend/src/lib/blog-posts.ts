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
    updatedAt: "2026-09-21",
    readingTime: "5 min read",
    author: "SupoClip",
    keywords: ["SupoClip vs supo.live", "supo.live alternative", "open-source video clipper", "self-hosted AI video clipping", "AI clip maker"],
    summary:
      "For creators and teams who want control over their clipping workflow, SupoClip is the better open-source alternative to supo.live.",
  },
  {
    slug: "best-free-opusclip-alternative",
    title: "Best, Free OpusClip Alternative",
    description:
      "Looking for a free OpusClip alternative? SupoClip is an open-source AI clip maker that turns long videos into captioned, vertical shorts you can self-host.",
    eyebrow: "OpusClip Alternative",
    category: "Comparison",
    publishedAt: "2026-05-07",
    updatedAt: "2026-05-07",
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
      "SupoClip is built for creators who want OpusClip-style AI clipping without committing to another credit-based subscription.",
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
