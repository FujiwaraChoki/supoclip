import type { Metadata } from "next";
import { Geist, Geist_Mono, Syne } from "next/font/google";
import "./globals.css";
import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { FeedbackButton } from "@/components/feedback-button";
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

export const metadata: Metadata = {
  title: {
    default: "SupoClip – Open-Source AI Video Clipper",
    template: "%s | SupoClip",
  },
  description:
    "Turn long videos into captioned short-form clips with open-source AI clipping, virality scoring, and face-aware vertical crops.",
  metadataBase: new URL(getSiteUrl()),
  applicationName: "SupoClip",
  authors: [{ name: "SupoClip Team", url: getSiteUrl() }],
  creator: "SupoClip Team",
  publisher: "SupoClip",
  category: "video software",
  icons: {
    icon: "/icon.png",
  },
  itunes: {
    appId: APP_STORE_ID,
  },
  openGraph: {
    title: "SupoClip – Open-Source AI Video Clipper",
    description:
      "Turn long videos into captioned short-form clips with open-source AI clipping.",
    siteName: "SupoClip",
    type: "website",
  },
  twitter: {
    card: "summary_large_image",
    title: "SupoClip – Open-Source AI Video Clipper",
    description:
      "Open-source AI clipping, virality scoring, captions, and face-aware vertical crops.",
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

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body className={`${geistSans.variable} ${geistMono.variable} ${syne.variable} antialiased`}>
        <TooltipProvider>
          {children}
          <FeedbackButton />
          <Toaster />
        </TooltipProvider>
      </body>
    </html>
  );
}
