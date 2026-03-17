import type { Metadata } from "next";
import { Geist, Geist_Mono, Syne } from "next/font/google";
import Script from "next/script";
import "./globals.css";
import { DataFastIdentity } from "@/components/datafast-identity";
import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { FeedbackButton } from "@/components/feedback-button";

const defaultMetadataBase = "http://localhost:3000";

function getMetadataBase() {
  try {
    return new URL(process.env.NEXT_PUBLIC_APP_URL || defaultMetadataBase);
  } catch {
    return new URL(defaultMetadataBase);
  }
}

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

export const metadata: Metadata = {
  title: "SupoClip",
  description: "Turn long videos into viral-ready shorts.",
  metadataBase: getMetadataBase(),
  icons: {
    icon: "/icon.png",
  },
  openGraph: {
    title: "SupoClip",
    description: "Turn long videos into viral-ready shorts.",
    siteName: "SupoClip",
    type: "website",
  },
  twitter: {
    card: "summary_large_image",
    title: "SupoClip",
    description: "Turn long videos into viral-ready shorts.",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
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
        <TooltipProvider>
          {children}
          <DataFastIdentity />
          <FeedbackButton />
          <Toaster />
        </TooltipProvider>
      </body>
    </html>
  );
}
