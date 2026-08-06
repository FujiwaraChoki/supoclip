"use client";

import { Monitor } from "lucide-react";
import { Separator } from "@/components/ui/separator";

export interface FontOption {
  name: string;
  display_name: string;
  format?: string;
}

interface ClipPhonePreviewProps {
  /** Collapses the preview away (used while the upload dropzone is showing). */
  collapsed: boolean;
  thumbnailUrl: string | null;
  /** Explicit user choices — null means "inherit the caption template's value". */
  fontFamily: string | null;
  fontSize: number | null;
  fontColor: string | null;
  /** Resolved values actually rendered in the mock. */
  previewFontFamily: string;
  previewFontSize: number;
  previewFontColor: string;
  availableFonts: FontOption[];
  templateName: string;
}

export function ClipPhonePreview({
  collapsed,
  thumbnailUrl,
  fontFamily,
  fontSize,
  fontColor,
  previewFontFamily,
  previewFontSize,
  previewFontColor,
  availableFonts,
  templateName,
}: ClipPhonePreviewProps) {
  return (
    <div
      className={`hidden lg:block flex-shrink-0 overflow-hidden transition-all duration-500 ease-in-out ${
        collapsed ? "w-0 opacity-0" : "w-[340px] opacity-100"
      }`}
      aria-hidden={collapsed}
    >
      <div
        className={`w-[340px] transition-all duration-500 ease-in-out ${
          collapsed
            ? "translate-x-6 scale-[0.97] opacity-0"
            : "translate-x-0 scale-100 opacity-100"
        }`}
      >
        <div className="lg:sticky lg:top-8">
          <div className="flex items-center justify-center gap-2 mb-5 text-sm text-stone-400">
            <Monitor className="w-4 h-4" />
            <span>Live Preview</span>
          </div>

          {/* Phone Frame — realistic iPhone style */}
          <div className="mx-auto" style={{ maxWidth: "300px" }}>
            <div
              className="relative bg-stone-950"
              style={{ borderRadius: "3rem", padding: "12px" }}
            >
              {/* Screen with inner radius */}
              <div
                className="relative overflow-hidden bg-black"
                style={{ borderRadius: "2.25rem", height: "580px" }}
              >
                {/* Status bar */}
                <div className="absolute top-0 left-0 right-0 z-20 px-6 pt-3 flex justify-between items-center">
                  <span className="text-white text-xs font-semibold">9:41</span>
                  {/* Dynamic Island */}
                  <div className="absolute top-2.5 left-1/2 -translate-x-1/2 w-24 h-7 bg-black rounded-full" />
                  <div className="flex items-center gap-1">
                    {/* Signal */}
                    <svg width="16" height="12" viewBox="0 0 16 12" className="text-white">
                      <rect x="0" y="8" width="3" height="4" rx="0.5" fill="currentColor" />
                      <rect x="4.5" y="5" width="3" height="7" rx="0.5" fill="currentColor" />
                      <rect x="9" y="2" width="3" height="10" rx="0.5" fill="currentColor" />
                      <rect x="13.5" y="0" width="3" height="12" rx="0.5" fill="currentColor" opacity="0.3" />
                    </svg>
                    {/* WiFi */}
                    <svg width="14" height="12" viewBox="0 0 14 12" className="text-white ml-0.5">
                      <path d="M7 10.5a1.5 1.5 0 100 3 1.5 1.5 0 000-3z" fill="currentColor" />
                      <path d="M3.5 8.5a5 5 0 017 0" stroke="currentColor" strokeWidth="1.5" fill="none" strokeLinecap="round" />
                      <path d="M1 5.5a8.5 8.5 0 0112 0" stroke="currentColor" strokeWidth="1.5" fill="none" strokeLinecap="round" />
                    </svg>
                    {/* Battery */}
                    <svg width="26" height="12" viewBox="0 0 26 12" className="text-white ml-0.5">
                      <rect x="0" y="1" width="22" height="10" rx="2" stroke="currentColor" strokeWidth="1" fill="none" />
                      <rect x="2" y="3" width="16" height="6" rx="1" fill="currentColor" />
                      <rect x="23" y="4" width="2" height="4" rx="0.5" fill="currentColor" opacity="0.4" />
                    </svg>
                  </div>
                </div>

                {/* Video background */}
                {thumbnailUrl ? (
                  <div
                    className="absolute inset-0 bg-cover bg-center scale-105 blur-sm"
                    style={{ backgroundImage: `url(${thumbnailUrl})` }}
                  />
                ) : (
                  <div className="absolute inset-0 bg-gradient-to-b from-stone-600 via-stone-500 to-stone-700" />
                )}
                <div className="absolute inset-0 bg-black/20" />
                {/* Bottom gradient for readability over lower UI */}
                <div className="absolute inset-x-0 bottom-0 h-60 bg-gradient-to-t from-black/70 via-black/30 to-transparent z-[1]" />

                {/* TikTok-style top navigation */}
                <div className="absolute top-12 left-0 right-0 z-10 flex justify-center items-center gap-5">
                  <span className="text-white/50 text-xs font-medium">Following</span>
                  <span className="text-white text-xs font-semibold relative">
                    For You
                    <div className="absolute -bottom-1.5 left-1/2 -translate-x-1/2 w-6 h-0.5 bg-white rounded-full" />
                  </span>
                </div>

                {/* Right side action buttons — TikTok style */}
                <div className="absolute right-3 space-y-5 z-10" style={{ bottom: "260px" }}>
                  {/* Profile */}
                  <div className="flex flex-col items-center gap-1">
                    <div className="w-9 h-9 rounded-full bg-white/20 border-2 border-white/40" />
                    <div className="w-4 h-4 rounded-full bg-red-500 -mt-3 border border-black flex items-center justify-center">
                      <span className="text-white text-[7px] font-bold">+</span>
                    </div>
                  </div>
                  {/* Heart */}
                  <div className="flex flex-col items-center gap-0.5">
                    <svg width="26" height="26" viewBox="0 0 24 24" fill="white" className="opacity-90">
                      <path d="M12 21.35l-1.45-1.32C5.4 15.36 2 12.28 2 8.5 2 5.42 4.42 3 7.5 3c1.74 0 3.41.81 4.5 2.09C13.09 3.81 14.76 3 16.5 3 19.58 3 22 5.42 22 8.5c0 3.78-3.4 6.86-8.55 11.54L12 21.35z"/>
                    </svg>
                    <span className="text-white text-[10px] font-semibold">24.5K</span>
                  </div>
                  {/* Comment */}
                  <div className="flex flex-col items-center gap-0.5">
                    <svg width="24" height="24" viewBox="0 0 24 24" fill="white" className="opacity-90">
                      <path d="M20 2H4c-1.1 0-2 .9-2 2v18l4-4h14c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2z"/>
                    </svg>
                    <span className="text-white text-[10px] font-semibold">482</span>
                  </div>
                  {/* Share */}
                  <div className="flex flex-col items-center gap-0.5">
                    <svg width="24" height="24" viewBox="0 0 24 24" fill="white" className="opacity-90">
                      <path d="M18 16.08c-.76 0-1.44.3-1.96.77L8.91 12.7c.05-.23.09-.46.09-.7s-.04-.47-.09-.7l7.05-4.11c.54.5 1.25.81 2.04.81 1.66 0 3-1.34 3-3s-1.34-3-3-3-3 1.34-3 3c0 .24.04.47.09.7L8.04 9.81C7.5 9.31 6.79 9 6 9c-1.66 0-3 1.34-3 3s1.34 3 3 3c.79 0 1.5-.31 2.04-.81l7.12 4.16c-.05.21-.08.43-.08.65 0 1.61 1.31 2.92 2.92 2.92s2.92-1.31 2.92-2.92-1.31-2.92-2.92-2.92z"/>
                    </svg>
                    <span className="text-white text-[10px] font-semibold">Share</span>
                  </div>
                </div>

                {/* Subtitle area — positioned above creator info */}
                <div className="absolute left-0 right-0 z-10" style={{ bottom: "195px" }}>
                  <div className="mx-4">
                    <p
                      style={{
                        color: previewFontColor,
                        fontSize: `${Math.max(Math.min(previewFontSize * 0.6, 22), 11)}px`,
                        fontFamily: `'${previewFontFamily}', system-ui, -apple-system, sans-serif`,
                        textAlign: 'center',
                        lineHeight: '1.5',
                        textShadow: '0 2px 8px rgba(0,0,0,0.8), 0 0px 2px rgba(0,0,0,0.9)',
                      }}
                      className="font-bold"
                    >
                      Your subtitle will look like this
                    </p>
                  </div>
                </div>

                {/* Bottom left — creator info */}
                <div className="absolute left-3 z-10 max-w-[60%]" style={{ bottom: "110px" }}>
                  <p className="text-white text-xs font-bold mb-1">@creator_name</p>
                  <p className="text-white/80 text-[10px] leading-snug">
                    Check out this amazing clip generated by AI
                  </p>
                  <div className="flex items-center gap-1.5 mt-2">
                    <svg width="10" height="10" viewBox="0 0 24 24" fill="white" className="opacity-70">
                      <path d="M12 3v10.55c-.59-.34-1.27-.55-2-.55-2.21 0-4 1.79-4 4s1.79 4 4 4 4-1.79 4-4V7h4V3h-6z"/>
                    </svg>
                    <span className="text-white/70 text-[9px]">Original Sound - creator_name</span>
                  </div>
                </div>

                {/* Bottom nav bar */}
                <div className="absolute bottom-0 left-0 right-0 z-20 bg-black px-2 pt-2 pb-5">
                  <div className="flex items-center justify-around">
                    <div className="flex flex-col items-center gap-0.5">
                      <svg width="18" height="18" viewBox="0 0 24 24" fill="white">
                        <path d="M10 20v-6h4v6h5v-8h3L12 3 2 12h3v8z"/>
                      </svg>
                      <span className="text-white text-[8px]">Home</span>
                    </div>
                    <div className="flex flex-col items-center gap-0.5">
                      <svg width="18" height="18" viewBox="0 0 24 24" fill="white" opacity="0.5">
                        <path d="M15.5 14h-.79l-.28-.27C15.41 12.59 16 11.11 16 9.5 16 5.91 13.09 3 9.5 3S3 5.91 3 9.5 5.91 16 9.5 16c1.61 0 3.09-.59 4.23-1.57l.27.28v.79l5 4.99L20.49 19l-4.99-5z"/>
                      </svg>
                      <span className="text-white/50 text-[8px]">Discover</span>
                    </div>
                    <div className="relative -mt-3">
                      <div className="w-10 h-7 rounded-lg bg-white flex items-center justify-center">
                        <span className="text-black text-lg font-bold leading-none">+</span>
                      </div>
                    </div>
                    <div className="flex flex-col items-center gap-0.5">
                      <svg width="18" height="18" viewBox="0 0 24 24" fill="white" opacity="0.5">
                        <path d="M20 2H4c-1.1 0-2 .9-2 2v18l4-4h14c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2z"/>
                      </svg>
                      <span className="text-white/50 text-[8px]">Inbox</span>
                    </div>
                    <div className="flex flex-col items-center gap-0.5">
                      <div className="w-5 h-5 rounded-full bg-white/30" />
                      <span className="text-white/50 text-[8px]">Me</span>
                    </div>
                  </div>
                  {/* Home indicator */}
                  <div className="w-28 h-1 bg-white/40 rounded-full mx-auto mt-2" />
                </div>
              </div>
            </div>

            {/* Caption info below phone */}
            <div className="mt-6 space-y-3 px-2">
              <div className="flex items-center justify-between text-xs text-stone-500">
                <span>Font</span>
                <span className="text-stone-700 font-medium">
                  {fontFamily
                    ? availableFonts.find((font) => font.name === fontFamily)?.display_name || fontFamily
                    : `${previewFontFamily} (template default)`}
                </span>
              </div>
              <Separator />
              <div className="flex items-center justify-between text-xs text-stone-500">
                <span>Size</span>
                <span className="text-stone-700 font-medium">
                  {fontSize ? `${fontSize}px` : `${previewFontSize}px (template default)`}
                </span>
              </div>
              <Separator />
              <div className="flex items-center justify-between text-xs text-stone-500">
                <span>Color</span>
                <div className="flex items-center gap-2">
                  <div className="w-3 h-3 rounded-full border border-stone-300" style={{ backgroundColor: previewFontColor }} />
                  <span className="text-stone-700 font-medium">
                    {fontColor ? fontColor : `${previewFontColor} (template default)`}
                  </span>
                </div>
              </div>
              <Separator />
              <div className="flex items-center justify-between text-xs text-stone-500">
                <span>Template</span>
                <span className="text-stone-700 font-medium">{templateName}</span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
