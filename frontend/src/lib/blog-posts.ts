import type { Metadata } from "next";

export const HOSTED_APP_URL = "https://supoclip.com";

export interface BlogPost {
  slug: string;
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

export function getSiteUrl() {
  const fallbackUrl = "http://localhost:3000";

  try {
    return new URL(process.env.NEXT_PUBLIC_APP_URL || fallbackUrl).origin;
  } catch {
    return fallbackUrl;
  }
}

export function getBlogPostMetadata(post: BlogPost): Metadata {
  const siteUrl = getSiteUrl();
  const url = `${siteUrl}/blog/${post.slug}`;

  return {
    title: `${post.title} | SupoClip Blog`,
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
    },
    twitter: {
      card: "summary_large_image",
      title: post.title,
      description: post.description,
    },
  };
}
