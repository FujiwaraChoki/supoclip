import type { Metadata } from "next";
import { NextIntlClientProvider } from "next-intl";
import { getLocale, getMessages, getTranslations } from "next-intl/server";
import { Geist_Mono, Montserrat } from "next/font/google";
import localFont from "next/font/local";
import Script from "next/script";
import "./globals.css";
import { AttributionTracker } from "@/components/attribution-tracker";
import { DataFastIdentity } from "@/components/datafast-identity";
import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { FeedbackButton } from "@/components/feedback-button";
import { PostHogIdentity } from "@/components/posthog-identity";
import { ThemeProvider } from "@/components/theme-provider";
import { intlLocale } from "@/i18n/config";
import { APP_STORE_ID, COMPANY_NAME, getSiteUrl } from "@/lib/site";

const montserrat = Montserrat({
  variable: "--font-montserrat",
  subsets: ["latin"],
  weight: ["400", "500", "600", "700", "800"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

const paperlogy = localFont({
  variable: "--font-paperlogy",
  display: "swap",
  src: [
    { path: "../../public/fonts/Paperlogy-400.woff2", weight: "400", style: "normal" },
    { path: "../../public/fonts/Paperlogy-500.woff2", weight: "500", style: "normal" },
    { path: "../../public/fonts/Paperlogy-600.woff2", weight: "600", style: "normal" },
    { path: "../../public/fonts/Paperlogy-700.woff2", weight: "700", style: "normal" },
    { path: "../../public/fonts/Paperlogy-800.woff2", weight: "800", style: "normal" },
  ],
});

const dataFastWebsiteId = process.env.NEXT_PUBLIC_DATAFAST_WEBSITE_ID;
const dataFastDomain = process.env.NEXT_PUBLIC_DATAFAST_DOMAIN;
const shouldTrackLocalhost = process.env.NEXT_PUBLIC_DATAFAST_ALLOW_LOCALHOST === "true";
const isDataFastEnabled = Boolean(dataFastWebsiteId && dataFastDomain);
// Tilly analytics runs next to DataFast; it only uses its own cookies and window.tilly.
const tillyWebsiteId = process.env.NEXT_PUBLIC_TILLY_WEBSITE_ID;

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("metadata");

  return {
  title: {
    default: t("title"),
    template: t("titleTemplate"),
  },
  description: t("description"),
  metadataBase: new URL(getSiteUrl()),
  applicationName: "TokoClippers",
  authors: [{ name: "TokoClippers Team", url: getSiteUrl() }],
  creator: "TokoClippers Team",
  publisher: COMPANY_NAME,
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
    siteName: "TokoClippers",
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
    <html lang={intlLocale(locale)} suppressHydrationWarning>
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
        {tillyWebsiteId ? (
          <>
            <Script id="tilly-queue" strategy="beforeInteractive">
              {`window.tilly = window.tilly || function() {
  window.tilly.q = window.tilly.q || [];
  window.tilly.q.push(arguments);
};`}
            </Script>
            <Script
              id="tilly-script"
              strategy="afterInteractive"
              src="https://tilly.samihindi.com/js/script.js"
              data-website-id={tillyWebsiteId}
              data-allow-localhost={shouldTrackLocalhost ? "true" : undefined}
            />
          </>
        ) : null}
      </head>
      <body className={`${montserrat.variable} ${geistMono.variable} ${paperlogy.variable} antialiased`}>
        <ThemeProvider>
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
        </ThemeProvider>
      </body>
    </html>
  );
}
