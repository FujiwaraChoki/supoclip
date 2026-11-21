// Render the dynamic-edit emoji atlas from the system Noto Color Emoji font (FINAL.md §12).
//
//   cd backend && .venv/bin/python scripts/fx/emoji_list.py | deno run --allow-read --allow-write --allow-env \
//     --allow-ffi --allow-sys=homedir --allow-net=registry.npmjs.org scripts/fx/build_emoji_atlas.ts \
//     --list - --out src/fx_assets/emoji          (`make fx-assets` runs this; `--list` also takes a file path)
//
// Writes `atlas.png` (16 columns of 160 px cells, transparent background, every glyph centred on the same
// baseline grid) and `atlas.json` ({"cell", "cols", "glyphs": {"🔥": {"x", "y"}}}, x/y = the cell's top-left px),
// plus `font.txt` naming the font version (write_manifest.py copies it into manifest.json).
// Fails when a glyph is missing from the font or a ZWJ sequence does not shape into one glyph, so the atlas
// never ships tofu. Requires the OFL-1.1 Noto Color Emoji font (Debian: fonts-noto-color-emoji).
// Symbols the emoji font does not have (₿ from EMOJI_KEYWORD_MAP) are drawn as a coin badge with the repo's
// OFL Inter font (backend/fonts/Inter.ttf), so every key still gets a colour sprite.

import { createCanvas, GlobalFonts } from "npm:@napi-rs/canvas@0.1.80";

const CELL = 160;
const COLS = 16;
const PAD = 6; // px kept clear on every side so neighbouring cells never bleed under bilinear sampling
const FAMILY = "SupoclipAtlasEmoji";
const SYMBOL_FAMILY = "SupoclipAtlasSymbol";
const SYMBOL_FONT = new URL("../../fonts/Inter.ttf", import.meta.url).pathname;
// Not in Noto Color Emoji: drawn as a coin (fill colour, glyph tilt in degrees).
const COIN_SYMBOLS: Record<string, { fill: string; rim: string; tilt: number }> = {
  "₿": { fill: "#F7931A", rim: "#C4700B", tilt: 14 },
};
const FONT_CANDIDATES = [
  "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf", // Debian / Ubuntu (the worker image)
  "/usr/share/fonts/noto/NotoColorEmoji.ttf", // Arch
  "/usr/share/fonts/google-noto-emoji/NotoColorEmoji.ttf", // Fedora
  "/System/Library/Fonts/NotoColorEmoji.ttf",
];

function arg(name: string): string | undefined {
  const i = Deno.args.indexOf(name);
  return i >= 0 ? Deno.args[i + 1] : undefined;
}

function exists(path: string): boolean {
  try {
    return Deno.statSync(path).isFile;
  } catch {
    return false;
  }
}

/** "Noto Color Emoji 2.051" from the font's `head.fontRevision` (16.16 fixed). */
function fontLabel(bytes: Uint8Array): string {
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const numTables = view.getUint16(4);
  for (let i = 0; i < numTables; i++) {
    const rec = 12 + i * 16;
    const tag = String.fromCharCode(...bytes.slice(rec, rec + 4));
    if (tag === "head") {
      const offset = view.getUint32(rec + 8);
      const revision = view.getInt32(offset + 4) / 65536;
      return `Noto Color Emoji ${revision.toFixed(3)}`;
    }
  }
  return "Noto Color Emoji";
}

type Ctx = ReturnType<ReturnType<typeof createCanvas>["getContext"]>;

/** A flat-shaded coin badge carrying a currency symbol (e.g. ₿), sized like the emoji around it. */
function drawCoin(ctx: Ctx, symbol: string, x: number, y: number, c: { fill: string; rim: string; tilt: number }) {
  const cx = x + CELL / 2;
  const cy = y + CELL / 2;
  const r = CELL / 2 - PAD - 4;
  ctx.save();
  ctx.beginPath();
  ctx.arc(cx, cy, r, 0, Math.PI * 2);
  ctx.fillStyle = c.rim;
  ctx.fill();
  const face = ctx.createRadialGradient(cx - r * 0.35, cy - r * 0.4, r * 0.1, cx, cy, r);
  face.addColorStop(0, "#FFC15E");
  face.addColorStop(1, c.fill);
  ctx.beginPath();
  ctx.arc(cx, cy, r * 0.9, 0, Math.PI * 2);
  ctx.fillStyle = face;
  ctx.fill();
  ctx.translate(cx, cy);
  ctx.rotate((c.tilt * Math.PI) / 180);
  ctx.font = `800 ${Math.round(r * 1.25)}px ${SYMBOL_FAMILY}`;
  const m = ctx.measureText(symbol);
  const inkW = m.actualBoundingBoxLeft + m.actualBoundingBoxRight;
  const inkH = m.actualBoundingBoxAscent + m.actualBoundingBoxDescent;
  ctx.fillStyle = "#FFFFFF";
  ctx.fillText(symbol, -inkW / 2 + m.actualBoundingBoxLeft, -inkH / 2 + m.actualBoundingBoxAscent);
  ctx.restore();
}

async function main(): Promise<void> {
  const listPath = arg("--list");
  const outDir = arg("--out");
  if (!listPath || !outDir) {
    console.error("usage: build_emoji_atlas.ts --list emoji.json --out DIR [--font PATH]");
    Deno.exit(2);
  }
  const fontPath = arg("--font") ?? FONT_CANDIDATES.find(exists);
  if (!fontPath || !exists(fontPath)) {
    console.error("Noto Color Emoji not found; pass --font /path/to/NotoColorEmoji.ttf");
    Deno.exit(2);
  }
  const listText = listPath === "-" ? await new Response(Deno.stdin.readable).text() : await Deno.readTextFile(listPath);
  const emoji: string[] = JSON.parse(listText);
  if (!Array.isArray(emoji) || emoji.length === 0 || new Set(emoji).size !== emoji.length) {
    console.error("emoji list must be a non-empty JSON array of unique strings");
    Deno.exit(2);
  }
  GlobalFonts.registerFromPath(fontPath, FAMILY);
  if (Object.keys(COIN_SYMBOLS).some((s) => emoji.includes(s))) {
    if (!exists(SYMBOL_FONT)) {
      console.error(`symbol font missing: ${SYMBOL_FONT}`);
      Deno.exit(2);
    }
    GlobalFonts.registerFromPath(SYMBOL_FONT, SYMBOL_FAMILY);
  }

  // One font size for every glyph (consistent visual weight), chosen so the widest glyph fits the padded cell.
  const probe = createCanvas(CELL, CELL).getContext("2d");
  let size = 128;
  for (;;) {
    probe.font = `${size}px ${FAMILY}`;
    const worst = Math.max(
      ...emoji.filter((e) => !(e in COIN_SYMBOLS)).map((e) => {
        const m = probe.measureText(e);
        return Math.max(
          m.actualBoundingBoxLeft + m.actualBoundingBoxRight,
          m.actualBoundingBoxAscent + m.actualBoundingBoxDescent,
        );
      }),
    );
    if (worst <= CELL - 2 * PAD || size <= 64) break;
    size -= 2;
  }

  const rows = Math.ceil(emoji.length / COLS);
  const atlas = createCanvas(COLS * CELL, rows * CELL);
  const ctx = atlas.getContext("2d");
  ctx.clearRect(0, 0, atlas.width, atlas.height);
  ctx.font = `${size}px ${FAMILY}`;
  ctx.textAlign = "left";
  ctx.textBaseline = "alphabetic";

  const glyphs: Record<string, { x: number; y: number }> = {};
  const problems: string[] = [];
  emoji.forEach((e, i) => {
    const x = (i % COLS) * CELL;
    const y = Math.floor(i / COLS) * CELL;
    const coin = COIN_SYMBOLS[e];
    if (coin) {
      drawCoin(ctx, e, x, y, coin);
    } else {
      ctx.font = `${size}px ${FAMILY}`;
      const m = ctx.measureText(e);
      if (m.width > size * 1.45) problems.push(`${e}: shaped wider than one glyph (${m.width.toFixed(0)}px)`);
      // centre the ink box in the cell
      const inkW = m.actualBoundingBoxLeft + m.actualBoundingBoxRight;
      const inkH = m.actualBoundingBoxAscent + m.actualBoundingBoxDescent;
      const dx = x + (CELL - inkW) / 2 + m.actualBoundingBoxLeft;
      const dy = y + (CELL - inkH) / 2 + m.actualBoundingBoxAscent;
      ctx.fillText(e, Math.round(dx), Math.round(dy));
    }
    const px = ctx.getImageData(x, y, CELL, CELL).data;
    let ink = 0;
    for (let p = 3; p < px.length; p += 4) if (px[p] > 32) ink++;
    // the emoji font has no fallback: a missing glyph draws nothing at all
    if (ink < CELL * CELL * 0.03) problems.push(`${e}: no glyph in ${coin ? "the symbol font" : "the emoji font"}`);
    glyphs[e] = { x, y };
  });
  if (problems.length) {
    console.error(problems.join("\n"));
    Deno.exit(1);
  }

  await Deno.mkdir(outDir, { recursive: true });
  await Deno.writeFile(`${outDir}/atlas.png`, atlas.toBuffer("image/png"));
  await Deno.writeTextFile(`${outDir}/atlas.json`, JSON.stringify({ cell: CELL, cols: COLS, glyphs }) + "\n");
  await Deno.writeTextFile(`${outDir}/font.txt`, fontLabel(await Deno.readFile(fontPath)) + "\n");
  console.log(`${emoji.length} glyphs at ${size}px → ${outDir}/atlas.png (${atlas.width}x${atlas.height})`);
}

await main();
