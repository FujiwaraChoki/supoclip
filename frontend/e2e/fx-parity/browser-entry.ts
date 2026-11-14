// Browser half of e2e/fx-parity.spec.ts: bundled by esbuild at test time and loaded into a blank Chromium page.
// It draws one frame of an FxScene through fx-core and the browser FxHost the way the studio preview does
// (lib/editor/render.ts drawBase: black, then the framed picture under the op's transform and filter).
import { prepareScene, renderFrame, type EmojiAtlas, type FxContext2D, type FxScene } from "../../src/lib/fx/core";
import { clockAt } from "../../src/lib/fx/core/frame";
import { createBrowserHost, loadEmojiAtlas, loadFxFont } from "../../src/lib/fx/browser-host";

let atlas: EmojiAtlas | null = null;

async function setup(stems: string[]): Promise<void> {
  await Promise.all(stems.map((stem) => loadFxFont(stem, `/fonts/${encodeURIComponent(stem)}`)));
  atlas = await loadEmojiAtlas("/atlas.png", "/atlas.json");
}

function fromBase64(b64: string): Uint8ClampedArray<ArrayBuffer> {
  const raw = atob(b64);
  const out = new Uint8ClampedArray(new ArrayBuffer(raw.length));
  for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
  return out;
}

async function toBase64(blob: Blob): Promise<string> {
  const bytes = new Uint8Array(await blob.arrayBuffer());
  let raw = "";
  for (let i = 0; i < bytes.length; i += 0x8000) raw += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(raw);
}

/** Renders `scene` at output time `o` over the RGBA picture; returns the frame as base64 PNG. */
async function render(scene: FxScene, o: number, pictureB64: string): Promise<string> {
  const { width: w, height: h } = scene;
  const picture = new OffscreenCanvas(w, h);
  picture.getContext("2d")!.putImageData(new ImageData(fromBase64(pictureB64), w, h), 0, 0);
  const host = createBrowserHost({
    drawBase(ctx) {
      const c = ctx as unknown as OffscreenCanvasRenderingContext2D;
      c.fillStyle = "black";
      c.fillRect(0, 0, w, h);
      c.drawImage(picture, 0, 0);
    },
    atlas: () => atlas,
  });
  const prepared = prepareScene(scene, host);
  const out = new OffscreenCanvas(w, h);
  const ctx = out.getContext("2d")!;
  ctx.fillStyle = "#000";
  ctx.fillRect(0, 0, w, h);
  renderFrame(ctx as unknown as FxContext2D, prepared, clockAt(prepared, o), host);
  return toBase64(await out.convertToBlob({ type: "image/png" }));
}

(globalThis as unknown as { fxParity: unknown }).fxParity = { setup, render };
