import type { Metadata } from "next";

import { HomeRouter } from "@/components/home-router";
import { getSiteUrl } from "@/lib/site";

const siteUrl = getSiteUrl();

export const metadata: Metadata = {
  title: {
    absolute: "SupoClip – Open-Source AI Video Clipper for Shorts",
  },
  description:
    "Turn long videos into captioned YouTube Shorts, TikToks, and Reels with open-source AI clipping, virality scoring, and face-aware 9:16 crops.",
  alternates: {
    canonical: siteUrl,
  },
  keywords: [
    "AI video clipper",
    "open-source video clipper",
    "YouTube Shorts clipper",
    "long video to shorts",
    "AI clip maker",
  ],
  openGraph: {
    title: "SupoClip – Open-Source AI Video Clipper for Shorts",
    description:
      "Turn long videos into captioned YouTube Shorts, TikToks, and Reels with open-source AI clipping.",
    url: siteUrl,
    siteName: "SupoClip",
    type: "website",
  },
  twitter: {
    card: "summary_large_image",
    title: "SupoClip – Open-Source AI Video Clipper for Shorts",
    description:
      "Open-source AI clipping, virality scoring, captions, and face-aware vertical crops.",
  },
};

const structuredData = [
  {
    "@context": "https://schema.org",
    "@type": "Organization",
    name: "SupoClip",
    url: siteUrl,
    logo: `${siteUrl}/logo.png`,
    sameAs: ["https://github.com/FujiwaraChoki/supoclip"],
  },
  {
    "@context": "https://schema.org",
    "@type": "SoftwareApplication",
    name: "SupoClip",
    applicationCategory: "MultimediaApplication",
    operatingSystem: "Web, Docker",
    url: siteUrl,
    description:
      "An open-source AI video clipper for finding highlights, creating vertical crops, adding word-synced captions, and exporting short-form videos.",
    featureList: [
      "AI-assisted clip discovery and virality scoring",
      "Face-aware 9:16 vertical cropping",
      "Word-synced animated captions",
      "YouTube Shorts, TikTok, and Instagram Reels exports",
      "Open-source self-hosting",
    ],
    offers: {
      "@type": "Offer",
      price: "0",
      priceCurrency: "USD",
      category: "Self-hosted open-source edition",
    },
  },
  {
    "@context": "https://schema.org",
    "@type": "WebSite",
    name: "SupoClip",
    url: siteUrl,
    description: "Open-source AI video clipping for short-form content.",
  },
];

export default function HomePage() {
  return (
    <>
      {structuredData.map((entry) => (
        <script
          key={entry["@type"]}
          type="application/ld+json"
          dangerouslySetInnerHTML={{ __html: JSON.stringify(entry) }}
        />
      ))}
      <HomeRouter />
    </>
  );
}
