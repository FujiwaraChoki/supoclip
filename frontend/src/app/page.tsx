import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";

import { HomeRouter } from "@/components/home-router";
import { APP_STORE_URL, getSiteUrl } from "@/lib/site";

const siteUrl = getSiteUrl();

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("marketing");
  const title = t("metadata.title");

  return {
    title: {
      absolute: title,
    },
    description: t("metadata.description"),
    alternates: {
      canonical: siteUrl,
    },
    keywords: [
      t("metadata.keywords.clipper"),
      t("metadata.keywords.openSource"),
      t("metadata.keywords.shorts"),
      t("metadata.keywords.longToShorts"),
      t("metadata.keywords.maker"),
    ],
    openGraph: {
      title,
      description: t("metadata.openGraphDescription"),
      url: siteUrl,
      siteName: "SupoClip",
      type: "website",
    },
    twitter: {
      card: "summary_large_image",
      title,
      description: t("metadata.twitterDescription"),
    },
  };
}

export default async function HomePage() {
  const t = await getTranslations("marketing");
  const structuredData = [
    {
      "@context": "https://schema.org",
      "@type": "Organization",
      name: "SupoClip",
      url: siteUrl,
      logo: `${siteUrl}/logo.png`,
      sameAs: ["https://github.com/FujiwaraChoki/supoclip", APP_STORE_URL],
    },
    {
      "@context": "https://schema.org",
      "@type": "SoftwareApplication",
      name: "SupoClip",
      applicationCategory: "MultimediaApplication",
      operatingSystem: "Web, Docker",
      url: siteUrl,
      description: t("metadata.structured.software"),
      featureList: [
        t("metadata.structured.featureDiscovery"),
        t("metadata.structured.featureCrop"),
        t("metadata.structured.featureCaptions"),
        t("metadata.structured.featureExports"),
        t("metadata.structured.featureSelfHost"),
      ],
      offers: {
        "@type": "Offer",
        price: "0",
        priceCurrency: "USD",
        category: t("metadata.structured.offerCategory"),
      },
    },
    {
      "@context": "https://schema.org",
      "@type": "MobileApplication",
      name: "SupoClip",
      applicationCategory: "MultimediaApplication",
      operatingSystem: "iOS",
      url: APP_STORE_URL,
      installUrl: APP_STORE_URL,
      description: t("metadata.structured.ios"),
      offers: {
        "@type": "Offer",
        price: "0",
        priceCurrency: "USD",
      },
    },
    {
      "@context": "https://schema.org",
      "@type": "WebSite",
      name: "SupoClip",
      url: siteUrl,
      description: t("metadata.structured.website"),
    },
  ];

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
