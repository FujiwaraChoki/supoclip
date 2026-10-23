import type { Metadata } from "next";
import { NextIntlClientProvider } from "next-intl";
import { getLocale, getMessages, getTranslations } from "next-intl/server";
import { Geist, Geist_Mono, Syne } from "next/font/google";
import Script from "next/script";
import "./globals.css";
import { AttributionTracker } from "@/components/attribution-tracker";
import { DataFastIdentity } from "@/components/datafast-identity";
import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { FeedbackButton } from "@/components/feedback-button";
import { PostHogIdentity } from "@/components/posthog-identity";
import { intlLocale } from "@/i18n/config";
import { APP_STORE_ID, getSiteUrl } from "@/lib/site";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

const syne = Syne({
  variable: "--font-syne",
  subsets: ["latin"],
  weight: ["400", "500", "600", "700", "800"],
});

const dataFastWebsiteId = process.env.NEXT_PUBLIC_DATAFAST_WEBSITE_ID;
const dataFastDomain = process.env.NEXT_PUBLIC_DATAFAST_DOMAIN;
const shouldTrackLocalhost = process.env.NEXT_PUBLIC_DATAFAST_ALLOW_LOCALHOST === "true";
const isDataFastEnabled = Boolean(dataFastWebsiteId && dataFastDomain);

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("metadata");

  return {
  title: {
    default: t("title"),
    template: t("titleTemplate"),
  },
  description: t("description"),
  metadataBase: new URL(getSiteUrl()),
  applicationName: "SupoClip",
  authors: [{ name: "SupoClip Team", url: getSiteUrl() }],
  creator: "SupoClip Team",
  publisher: "SupoClip",
  category: t("category"),
  icons: {
    icon: "/icon.png",
  },
  itunes: {
    appId: APP_STORE_ID,
  },
  openGraph: {
    title: t("title"),
    description: t("openGraphDescription"),
    siteName: "SupoClip",
    type: "website",
  },
  twitter: {
    card: "summary_large_image",
    title: t("title"),
    description: t("twitterDescription"),
  },
  robots: {
    index: true,
    follow: true,
    googleBot: {
      index: true,
      follow: true,
      "max-image-preview": "large",
      "max-snippet": -1,
      "max-video-preview": -1,
    },
  },
  };
}

export default async function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  const locale = await getLocale();
  const messages = await getMessages();

  return (
    <html lang={intlLocale(locale)}>
      <head>
        {isDataFastEnabled ? (
          <>
            <Script id="datafast-queue" strategy="beforeInteractive">
              {`window.datafast = window.datafast || function() {
  window.datafast.q = window.datafast.q || [];
  window.datafast.q.push(arguments);
};`}
            </Script>
            <Script
              id="datafast-script"
              strategy="afterInteractive"
              src="/js/script.js"
              data-website-id={dataFastWebsiteId}
              data-domain={dataFastDomain}
              data-allow-localhost={shouldTrackLocalhost ? "true" : undefined}
              data-disable-console="true"
            />
          </>
        ) : null}
      </head>
      <body className={`${geistSans.variable} ${geistMono.variable} ${syne.variable} antialiased`}>
        <NextIntlClientProvider locale={locale} messages={messages}>
          <TooltipProvider>
            {children}
            <DataFastIdentity />
            <PostHogIdentity />
            <AttributionTracker />
            <FeedbackButton />
            <Toaster />
          </TooltipProvider>
        </NextIntlClientProvider>
      </body>
    </html>
  );
}
