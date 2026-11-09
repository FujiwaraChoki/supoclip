// @vitest-environment node
import { ImageResponse } from "next/og";
import { afterEach, describe, expect, it, vi } from "vitest";
import { createSocialImageResponse, loadSocialImageFonts } from "./social-image";

const WOFF_SIGNATURE = "wOFF";

describe("social image fonts", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("loads the bundled Syne and Geist fonts without the network", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");

    const fonts = await loadSocialImageFonts();

    expect(fetchSpy).not.toHaveBeenCalled();
    expect(fonts.map(({ name, weight }) => ({ name, weight }))).toEqual([
      { name: "Syne", weight: 800 },
      { name: "Geist", weight: 400 },
    ]);
    for (const font of fonts) {
      expect(font.data.byteLength).toBeGreaterThan(1000);
      expect(font.data.subarray(0, 4).toString("latin1")).toBe(WOFF_SIGNATURE);
    }
  });

  it("fails loudly when the font files are missing", async () => {
    await expect(loadSocialImageFonts("/nonexistent")).rejects.toThrow(/ENOENT/);
  });

  it("renders a 1200x630 PNG with the bundled fonts", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");

    const response = await createSocialImageResponse();
    const png = Buffer.from(await response.arrayBuffer());

    expect(response.status).toBe(200);
    expect(response.headers.get("content-type")).toBe("image/png");
    expect(png.subarray(1, 4).toString("latin1")).toBe("PNG");
    expect(png.readUInt32BE(16)).toBe(1200);
    expect(png.readUInt32BE(20)).toBe(630);
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("draws text in Syne rather than the default font", async () => {
    const [syne] = await loadSocialImageFonts();
    const render = async (fonts?: ConstructorParameters<typeof ImageResponse>[1]) =>
      Buffer.from(
        await new ImageResponse(
          <div style={{ display: "flex", fontFamily: "Syne", fontSize: 48, fontWeight: 800 }}>SupoClip</div>,
          { width: 400, height: 100, ...fonts }
        ).arrayBuffer()
      );

    const withSyne = await render({ fonts: [syne] });
    const withDefault = await render();

    expect(withSyne.equals(withDefault)).toBe(false);
  });
});
