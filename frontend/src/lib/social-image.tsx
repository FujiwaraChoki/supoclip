import { readFile } from "node:fs/promises";
import path from "node:path";
import { ImageResponse } from "next/og";

// Bundled so the image never depends on a remote font URL (the Google Fonts
// URLs this used to fetch went 404 and every image fell back to the default
// font). Latin subsets from Fontsource, SIL OFL 1.1; licenses sit next to them.
export const SOCIAL_IMAGE_FONT_DIR = path.join("assets", "fonts");

const FONT_FILES = [
  { name: "Syne", file: "syne-latin-800-normal.woff", weight: 800 },
  { name: "Geist", file: "geist-latin-400-normal.woff", weight: 400 },
] as const;

type SocialImageFontWeight = (typeof FONT_FILES)[number]["weight"];

export type SocialImageFont = {
  name: string;
  data: Buffer;
  style: "normal";
  weight: SocialImageFontWeight;
};

export async function loadSocialImageFonts(cwd = process.cwd()): Promise<SocialImageFont[]> {
  return Promise.all(
    FONT_FILES.map(async ({ name, file, weight }) => ({
      name,
      data: await readFile(path.join(cwd, SOCIAL_IMAGE_FONT_DIR, file)),
      style: "normal" as const,
      weight,
    }))
  );
}

export async function createSocialImageResponse() {
  const fonts = await loadSocialImageFonts();

  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          flexDirection: "column",
          justifyContent: "center",
          alignItems: "center",
          background: "linear-gradient(145deg, #f5f5f0 0%, #e8e8e3 50%, #ddddd8 100%)",
          position: "relative",
          overflow: "hidden",
        }}
      >
        <div
          style={{
            position: "absolute",
            inset: 0,
            display: "flex",
            backgroundImage:
              "linear-gradient(rgba(0,0,0,0.03) 1px, transparent 1px), linear-gradient(90deg, rgba(0,0,0,0.03) 1px, transparent 1px)",
            backgroundSize: "40px 40px",
          }}
        />

        <div
          style={{
            position: "absolute",
            top: -80,
            right: -80,
            width: 320,
            height: 320,
            borderRadius: "50%",
            border: "1px solid rgba(0,0,0,0.06)",
            display: "flex",
          }}
        />
        <div
          style={{
            position: "absolute",
            bottom: -120,
            left: -60,
            width: 400,
            height: 400,
            borderRadius: "50%",
            border: "1px solid rgba(0,0,0,0.04)",
            display: "flex",
          }}
        />

        <div
          style={{
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            gap: 28,
            padding: "48px 64px",
            position: "relative",
          }}
        >
          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              width: 88,
              height: 88,
              borderRadius: 20,
              background: "linear-gradient(135deg, #3a3a38 0%, #2a2a28 100%)",
              boxShadow: "0 8px 32px rgba(0,0,0,0.15), 0 2px 8px rgba(0,0,0,0.1)",
            }}
          >
            <svg
              width="48"
              height="48"
              viewBox="0 0 24 24"
              fill="none"
              stroke="#f5f5f0"
              strokeWidth="1.8"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <circle cx="6" cy="6" r="3" />
              <circle cx="6" cy="18" r="3" />
              <line x1="20" y1="4" x2="8.12" y2="15.88" />
              <line x1="14.47" y1="14.48" x2="20" y2="20" />
              <line x1="8.12" y1="8.12" x2="12" y2="12" />
            </svg>
          </div>

          <div
            style={{
              display: "flex",
              alignItems: "baseline",
              fontFamily: "Syne",
              fontSize: 72,
              fontWeight: 800,
              color: "#1c1c1a",
              letterSpacing: "-2px",
              lineHeight: 1,
            }}
          >
            Supo
            <span
              style={{
                color: "#6b6b67",
              }}
            >
              Clip
            </span>
          </div>

          <div
            style={{
              display: "flex",
              fontFamily: "Geist",
              fontSize: 26,
              color: "#78786f",
              letterSpacing: "-0.3px",
              lineHeight: 1,
            }}
          >
            Turn long videos into viral-ready shorts
          </div>

          <div
            style={{
              display: "flex",
              gap: 12,
              marginTop: 8,
            }}
          >
            {["AI-Powered", "Auto Subtitles", "9:16 Vertical", "Open Source"].map(
              (label) => (
                <div
                  key={label}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    padding: "8px 18px",
                    borderRadius: 100,
                    background: "rgba(0,0,0,0.05)",
                    border: "1px solid rgba(0,0,0,0.08)",
                    fontFamily: "Geist",
                    fontSize: 15,
                    color: "#4a4a46",
                    letterSpacing: "-0.2px",
                  }}
                >
                  {label}
                </div>
              )
            )}
          </div>
        </div>

        <div
          style={{
            position: "absolute",
            bottom: 0,
            left: 0,
            right: 0,
            height: 4,
            background: "linear-gradient(90deg, #3a3a38 0%, #6b6b67 50%, #3a3a38 100%)",
            display: "flex",
          }}
        />
      </div>
    ),
    {
      width: 1200,
      height: 630,
      fonts,
    }
  );
}
