// The create screen's caption-style tiles: one short FxScene per animated template, drawn live by fx-core over a
// procedural backdrop (a studio gradient and a speaker silhouette, no stock footage and no real person). Pure: the
// scene is plain data and the backdrop painters only use the FxContext2D subset, so the tiles, the tests and the
// napi goldens draw exactly the same picture.
import { fnv1a32, mulberry32 } from "./core/rng";
import type {
  AccentCue, AccentVariant, DoodleKind, FaceTrack, FxCaptionStyle, FxCaptionTemplate, FxCue, FxScene, FxWord,
} from "./core/types";
import type { FxContext2D } from "./core/host";
import { isListedFxTemplate } from "./core/types";

/** The demo renders in real output pixels so its layout is the clip's layout; tiles scale it down. */
export const DEMO_WIDTH = 1080;
export const DEMO_HEIGHT = 1920;
/** One loop of a tile. */
export const DEMO_DURATION = 4.0;
/** The frame shown with `prefers-reduced-motion` (§15): every style has its accent on screen here. */
export const DEMO_STATIC_T = 1.2;

/** Space-separated so every language splits the same way (the zh line puts spaces between its words too). */
export const DEMO_LINE = "Our revenue grew 300% in just one year";
export const DEMO_STICKER = "No way!";

const WORD_START = 0.1;
const WORD_STEP = 0.45;
const WORD_LENGTH = 0.4;

/** The silhouette's face in output px (1080×1920), where the face track puts it. */
export const DEMO_FACE = { x: 384, y: 560, w: 312, h: 392 } as const;

/**
 * Each animated template's caption look: a mirror of `FX_CAPTION_TEMPLATES_DICT` in `backend/src/caption_templates.py`
 * (font, base size, colours, stroke, box, position, words per line, case). A test pins it against the backend.
 */
export const DEMO_TEMPLATE_LOOKS: Readonly<Record<FxCaptionTemplate, {
  font: string; size: number; color: string; accent: string; stroke: string | null; strokeWidth: number;
  shadow: boolean; background: string | null; y: number; wordsPerLine: number; uppercase: boolean;
}>> = {
  marker_circle: { font: "TikTokSans-Bold", size: 30, color: "#FFFFFF", accent: "#FF3B30", stroke: "#000000", strokeWidth: 3, shadow: true, background: null, y: 0.78, wordsPerLine: 3, uppercase: false },
  emoji_drop: { font: "Poppins-ExtraBold", size: 30, color: "#FFFFFF", accent: "#FFD23F", stroke: "#000000", strokeWidth: 3, shadow: true, background: null, y: 0.55, wordsPerLine: 3, uppercase: false },
  slam: { font: "THEBOLDFONT", size: 34, color: "#FFFFFF", accent: "#FFE000", stroke: "#000000", strokeWidth: 4, shadow: true, background: null, y: 0.74, wordsPerLine: 3, uppercase: true },
  behind_head: { font: "TikTokSans-Bold", size: 28, color: "#FFFFFF", accent: "#FFD54A", stroke: null, strokeWidth: 0, shadow: true, background: null, y: 0.55, wordsPerLine: 4, uppercase: false },
  stat_counter: { font: "Poppins-ExtraBold", size: 30, color: "#FFFFFF", accent: "#00E676", stroke: null, strokeWidth: 0, shadow: true, background: null, y: 0.78, wordsPerLine: 4, uppercase: false },
  kinetic_stack: { font: "THEBOLDFONT", size: 32, color: "#FFFFFF", accent: "#FFE000", stroke: "#000000", strokeWidth: 4, shadow: true, background: null, y: 0.74, wordsPerLine: 3, uppercase: true },
  decode_glitch: { font: "TikTokSans-Bold", size: 28, color: "#FFFFFF", accent: "#00F0FF", stroke: "#000000", strokeWidth: 2, shadow: true, background: null, y: 0.78, wordsPerLine: 3, uppercase: true },
  highlighter_swipe: { font: "Poppins-ExtraBold", size: 30, color: "#FFFFFF", accent: "#34FD87", stroke: null, strokeWidth: 0, shadow: false, background: "#170904D9", y: 0.55, wordsPerLine: 3, uppercase: false },
  face_doodles: { font: "TikTokSans-Bold", size: 30, color: "#FFFFFF", accent: "#FFD23F", stroke: "#000000", strokeWidth: 3, shadow: true, background: null, y: 0.78, wordsPerLine: 3, uppercase: false },
};

/** Every font a demo scene draws with, for the host to load before the first real frame. */
export const DEMO_FONTS: readonly string[] = [...new Set([...Object.values(DEMO_TEMPLATE_LOOKS).map((look) => look.font), "ArchivoBlack-Regular"])];

/**
 * `edit_fx.caption_style.fx_caption_style` at the demo size: the size scales like `get_scaled_font_size`. `scale`
 * enlarges the captions for thumbnails (a 100 px tile can't show real-size captions legibly); 1 is the real look.
 */
export function demoCaptionStyle(template: FxCaptionTemplate, width = DEMO_WIDTH, scale = 1): FxCaptionStyle {
  const look = DEMO_TEMPLATE_LOOKS[template];
  if (isListedFxTemplate(template)) {
    // == edit_fx.caption_style.look_caption_style(template, "bold"): the listed styles demo their Bold look.
    return {
      template, font: "Poppins-ExtraBold", size_px: Math.round(Math.round((72 * width) / 1080) * scale), color: "#FFFFFF",
      accent: template === "highlighter_swipe" ? "#34FD87" : "#FFE00B", stroke_color: null, stroke_px: 0, shadow: true,
      background: template === "highlighter_swipe" ? "#170904D9" : null, y: look.y, words_per_line: 3, uppercase: false,
      enabled: true, placements: [], look: "bold",
    };
  }
  const sizePx = Math.round(Math.max(26, Math.min(132, Math.round(look.size * (width / 560)))) * scale);
  const strokePx = look.stroke && look.strokeWidth > 0
    ? Math.min(40, Math.max(look.strokeWidth, Math.round((sizePx * look.strokeWidth) / 26)))
    : 0;
  return {
    template, font: look.font, size_px: sizePx, color: look.color, accent: look.accent,
    stroke_color: look.stroke, stroke_px: strokePx, shadow: look.shadow, background: look.background, y: look.y,
    words_per_line: look.wordsPerLine, uppercase: look.uppercase, enabled: true, placements: [],
  };
}

/** The line as caption words, one every 0.45 s (closer together for long lines, so every word lands inside the loop);
 *  numbers are emphasis words like the resolver marks them. */
export function demoWords(line = DEMO_LINE): FxWord[] {
  let tokens = line.trim().split(/\s+/).filter(Boolean);
  // A translation that lost its number or its shape falls back to the English line, which every style is tuned for.
  if (tokens.length < 6 || tokens.length > 12 || !tokens.some((token) => /\d/.test(token))) tokens = DEMO_LINE.split(" ");
  const step = Math.min(WORD_STEP, (DEMO_DURATION - 0.5 - WORD_START) / tokens.length);
  return tokens.map((text, i) => {
    const start = round3(WORD_START + i * step);
    return { id: `w${i}`, start, end: round3(start + step * (WORD_LENGTH / WORD_STEP)), text, highlight: /\d/.test(text), run: "demo" };
  });
}

/** "$2.5M" → prefix "$", value 2.5, suffix "M"; "300%" → 300 and "%". */
export function parseSpokenNumber(text: string): { value: number; prefix: string; suffix: string } | null {
  const match = /^(\D*?)(\d[\d,.]*\d|\d)(\D*)$/.exec(text.trim());
  if (!match) return null;
  const value = Number(match[2].replace(/,/g, ""));
  return Number.isFinite(value) ? { value, prefix: match[1], suffix: match[3] } : null;
}

function cue(template: FxCaptionTemplate, n: number, variant: AccentVariant, start: number, end: number, extra: Partial<AccentCue> = {}): AccentCue {
  const id = `demo${n}.accent`;
  return {
    id, kind: "accent", variant, start: round3(start), end: round3(Math.min(end, DEMO_DURATION)),
    seed: fnv1a32(`demo:${template}:${id}`), beat: `demo${n}`, origin: "auto",
    text: "", emoji: "", doodle: "sparkles", value: null, prefix: "", suffix: "", ...extra,
  };
}

function doodle(template: FxCaptionTemplate, n: number, kind: DoodleKind, start: number, end: number, text: string): AccentCue {
  return cue(template, n, "doodle", start, end, { doodle: kind, text });
}

/** The accents (and the odd picture cue) that show what each template does, timed to the demo words. */
export function demoCues(template: FxCaptionTemplate, words: readonly FxWord[], sticker = DEMO_STICKER): FxCue[] {
  const key = words[1];
  const numberIndex = Math.max(2, words.findIndex((word) => /\d/.test(word.text)));
  const number = words[numberIndex] ?? words[3];
  const verb = words[2];
  const last = words[words.length - 1];
  const spoken = parseSpokenNumber(number.text) ?? { value: 300, prefix: "", suffix: "%" };
  switch (template) {
    case "marker_circle": return [
      cue(template, 1, "circle", key.start, key.start + 1.44, { text: key.text }),
      cue(template, 2, "arrow", number.start, number.start + 1.4, { text: number.text }),
      cue(template, 3, "underline", last.start, last.start + 0.9, { text: last.text }),
    ];
    case "emoji_drop": return [
      cue(template, 1, "emoji", key.start - 2 / 30, key.start + 1.14, { text: key.text, emoji: "💰" }),
      cue(template, 2, "highlight", number.start, number.start + 0.9, { text: number.text }),
      cue(template, 3, "emoji", last.start - 2 / 30, last.start + 0.9, { text: last.text, emoji: "📈" }),
    ];
    case "slam": return [
      cue(template, 1, "slam", key.start, key.start + 0.8, { text: key.text }),
      { id: "demo1.shake", kind: "shake", start: key.start, end: round3(key.start + 0.25), seed: fnv1a32("demo:slam:shake1"), beat: "demo1", origin: "auto", amp: 12, freq: 18 },
      cue(template, 2, "slam", number.start, number.start + 0.9, { text: number.text }),
      { id: "demo2.shake", kind: "shake", start: number.start, end: round3(number.start + 0.25), seed: fnv1a32("demo:slam:shake2"), beat: "demo2", origin: "auto", amp: 16, freq: 18 },
    ];
    case "behind_head": return [
      cue(template, 1, "behind", key.start + 1 / 30, key.start + 1.5, { text: key.text }),
      cue(template, 2, "highlight", number.start, number.start + 0.9, { text: number.text }),
    ];
    case "stat_counter": return [cue(template, 1, "counter", 0.25, 2.7, { text: number.text, ...spoken })];
    case "kinetic_stack": return [cue(template, 1, "kinetic", words[0].start, last.end + 0.3, { text: words.map((word) => word.text).join(" ") })];
    case "decode_glitch": return [
      cue(template, 1, "decode", verb.start, verb.start + 0.8, { text: verb.text }),
      cue(template, 2, "decode", last.start, last.start + 0.7, { text: last.text }),
    ];
    case "highlighter_swipe": return [
      cue(template, 1, "highlight", key.start, key.start + 1.5, { text: key.text }),
      cue(template, 2, "highlight", number.start, number.start + 1.2, { text: number.text }),
    ];
    case "face_doodles": return [
      doodle(template, 1, "sparkles", 0.3, 2.0, sticker),
      doodle(template, 2, "arrows", 2.15, 3.5, sticker),
    ];
  }
}

/** The silhouette's face as a still 6 fps track (normalised rows with YuNet landmarks), so face drawers find it. */
export function demoFaceTrack(width = DEMO_WIDTH, height = DEMO_HEIGHT): FaceTrack {
  const { x, y, w, h } = DEMO_FACE;
  // Landmarks in output px (person's right eye first), normalised like the analysis output.
  const marks = [x + w * 0.31, y + h * 0.42, x + w * 0.69, y + h * 0.42, x + w * 0.5, y + h * 0.6, x + w * 0.35, y + h * 0.78, x + w * 0.65, y + h * 0.78];
  const row = [x / width, y / height, w / width, h / height, 0.98, ...marks.map((v, i) => (i % 2 === 0 ? v / width : v / height))];
  const samples: [number, number[][]][] = [];
  for (let i = 0; i <= Math.ceil(DEMO_DURATION * 6); i++) samples.push([round3(i / 6), [row.map(round4)]]);
  return { version: 1, space: "output", fps: 6, samples };
}

/** Caption enlargement the create-screen tiles use (see `demoCaptionStyle`). */
export const TILE_CAPTION_SCALE = 1.7;

/** One template's demo scene (1080×1920, identity warp). `line` is the localised demo sentence. */
export function demoScene(template: FxCaptionTemplate, options: { line?: string; sticker?: string; captionScale?: number } = {}): FxScene {
  const words = demoWords(options.line);
  const cues = demoCues(template, words, options.sticker || DEMO_STICKER);
  const behind = cues.filter((c) => c.kind === "accent" && c.variant === "behind");
  return {
    version: 1, width: DEMO_WIDTH, height: DEMO_HEIGHT, edit_duration: DEMO_DURATION, out_duration: DEMO_DURATION, warp: [],
    template, cues, words, caption: demoCaptionStyle(template, DEMO_WIDTH, options.captionScale ?? 1), grade: "none", grain: 0, progress_bar: false,
    progress_color: "#FF7A1A", loop_ending: "none", hook_window: null, hook_title_suppressed: false,
    face_track: demoFaceTrack(), broll_windows: [],
    matte_windows: behind.map((c) => ({ start: c.start, end: c.end, person: true })),
    seed: fnv1a32(`demo:${template}`),
  };
}

// ----------------------------------------------------------------------------------------------- backdrop

/**
 * The studio behind the speaker, in a `width × height` canvas (authored at 1080×1920 and scaled to cover): a dusk
 * gradient, a warm key light behind the head, a window of soft bokeh and a floor fade. Deterministic.
 */
export function paintDemoBackdrop(ctx: FxContext2D, width: number, height: number): void {
  ctx.save();
  coverTransform(ctx, width, height);
  const sky = ctx.createLinearGradient(0, 0, 0, DEMO_HEIGHT);
  sky.addColorStop(0, "#1B1F3A");
  sky.addColorStop(0.45, "#3A2A4F");
  sky.addColorStop(0.78, "#5B3346");
  sky.addColorStop(1, "#1A1420");
  ctx.fillStyle = sky;
  ctx.fillRect(0, 0, DEMO_WIDTH, DEMO_HEIGHT);

  // Key light: a warm glow behind the head that gives the silhouette its rim.
  const glow = ctx.createRadialGradient(560, 760, 40, 560, 760, 760);
  glow.addColorStop(0, "rgba(255,170,110,0.85)");
  glow.addColorStop(0.35, "rgba(255,120,110,0.35)");
  glow.addColorStop(1, "rgba(255,120,110,0)");
  ctx.fillStyle = glow;
  ctx.fillRect(0, 0, DEMO_WIDTH, DEMO_HEIGHT);

  // A cool fill from the window side.
  const fill = ctx.createRadialGradient(80, 300, 20, 80, 300, 900);
  fill.addColorStop(0, "rgba(110,180,255,0.35)");
  fill.addColorStop(1, "rgba(110,180,255,0)");
  ctx.fillStyle = fill;
  ctx.fillRect(0, 0, DEMO_WIDTH, DEMO_HEIGHT);

  // Bokeh: out-of-focus lights on the back wall.
  const random = mulberry32(20261201);
  const palette = ["255,196,120", "255,140,160", "140,200,255", "255,230,170"];
  for (let i = 0; i < 26; i++) {
    const x = random() * DEMO_WIDTH, y = 120 + random() * 1080, r = 18 + random() * 54;
    const tone = palette[Math.floor(random() * palette.length)];
    const dot = ctx.createRadialGradient(x, y, 0, x, y, r);
    dot.addColorStop(0, `rgba(${tone},${(0.16 + random() * 0.22).toFixed(3)})`);
    dot.addColorStop(0.7, `rgba(${tone},0.06)`);
    dot.addColorStop(1, `rgba(${tone},0)`);
    ctx.fillStyle = dot;
    ctx.beginPath();
    ctx.arc(x, y, r, 0, Math.PI * 2);
    ctx.fill();
  }

  // Floor and vignette, so captions at the bottom always sit on something dark.
  const floor = ctx.createLinearGradient(0, 1240, 0, DEMO_HEIGHT);
  floor.addColorStop(0, "rgba(10,8,16,0)");
  floor.addColorStop(1, "rgba(10,8,16,0.75)");
  ctx.fillStyle = floor;
  ctx.fillRect(0, 1240, DEMO_WIDTH, DEMO_HEIGHT - 1240);
  const vignette = ctx.createRadialGradient(540, 900, 600, 540, 900, 1250);
  vignette.addColorStop(0, "rgba(0,0,0,0)");
  vignette.addColorStop(1, "rgba(0,0,0,0.45)");
  ctx.fillStyle = vignette;
  ctx.fillRect(0, 0, DEMO_WIDTH, DEMO_HEIGHT);
  ctx.restore();
}

/** The speaker: a head-and-shoulders silhouette with a warm rim light. Only the person, on transparency (the
 *  behind-head cutout draws this layer over the words). */
export function paintDemoPerson(ctx: FxContext2D, width: number, height: number): void {
  ctx.save();
  coverTransform(ctx, width, height);
  const { x, y, w, h } = DEMO_FACE;
  const cx = x + w / 2;
  const top = y - h * 0.24;          // crown of the hair
  const chin = y + h * 1.02;
  const neckTop = chin - 40, shoulder = chin + 150;

  const body = () => {
    ctx.beginPath();
    ctx.moveTo(cx - 540, DEMO_HEIGHT + 40);
    // Left shoulder and arm, sloping into the neck.
    ctx.bezierCurveTo(cx - 540, shoulder + 260, cx - 470, shoulder + 40, cx - 300, shoulder + 6);
    ctx.bezierCurveTo(cx - 190, shoulder - 14, cx - 112, shoulder - 52, cx - 104, neckTop + 50);
    ctx.lineTo(cx - 100, neckTop);
    // Jaw, cheek and hair on the left.
    ctx.bezierCurveTo(cx - 150, chin - 70, cx - w * 0.56, y + h * 0.62, cx - w * 0.56, y + h * 0.36);
    ctx.bezierCurveTo(cx - w * 0.62, y + h * 0.02, cx - w * 0.42, top, cx - 10, top);
    // Hair on the right, a little fuller where the light catches it.
    ctx.bezierCurveTo(cx + w * 0.44, top - 6, cx + w * 0.64, y + h * 0.04, cx + w * 0.58, y + h * 0.38);
    ctx.bezierCurveTo(cx + w * 0.56, y + h * 0.64, cx + 150, chin - 70, cx + 100, neckTop);
    ctx.lineTo(cx + 104, neckTop + 50);
    ctx.bezierCurveTo(cx + 112, shoulder - 52, cx + 190, shoulder - 14, cx + 300, shoulder + 6);
    ctx.bezierCurveTo(cx + 470, shoulder + 40, cx + 540, shoulder + 260, cx + 540, DEMO_HEIGHT + 40);
    ctx.closePath();
  };

  const tone = ctx.createLinearGradient(cx - 300, top, cx + 300, DEMO_HEIGHT);
  tone.addColorStop(0, "#2E2536");
  tone.addColorStop(0.45, "#1A1422");
  tone.addColorStop(1, "#0B0910");
  ctx.fillStyle = tone;
  body();
  ctx.fill();

  ctx.save();
  body();
  ctx.clip();
  // Rim light: the warm key catching hair and shoulders, and a cooler edge from the window side.
  ctx.lineWidth = 22;
  ctx.shadowBlur = 28;
  ctx.strokeStyle = "rgba(255,172,120,0.6)";
  ctx.shadowColor = "rgba(255,150,110,0.95)";
  ctx.translate(-6, 0);
  body();
  ctx.stroke();
  ctx.translate(12, 0);
  ctx.strokeStyle = "rgba(150,190,255,0.22)";
  ctx.shadowColor = "rgba(120,170,255,0.6)";
  body();
  ctx.stroke();
  ctx.restore();

  // A collar line and soft face light, so it reads as a person (and nobody in particular).
  ctx.strokeStyle = "rgba(255,190,150,0.12)";
  ctx.lineWidth = 10;
  ctx.lineCap = "round";
  ctx.beginPath();
  ctx.moveTo(cx - 150, shoulder - 6);
  ctx.quadraticCurveTo(cx, shoulder + 110, cx + 150, shoulder - 6);
  ctx.stroke();
  const face = ctx.createRadialGradient(cx + 24, y + h * 0.48, 10, cx + 24, y + h * 0.48, w * 0.6);
  face.addColorStop(0, "rgba(255,190,150,0.2)");
  face.addColorStop(1, "rgba(255,190,150,0)");
  ctx.fillStyle = face;
  ctx.beginPath();
  ctx.ellipse(cx, y + h * 0.52, w * 0.5, h * 0.52, 0, 0, Math.PI * 2);
  ctx.fill();
  ctx.restore();
}

/** Maps the 1080×1920 authoring space onto the canvas, covering it (centre crop for other aspects). */
function coverTransform(ctx: FxContext2D, width: number, height: number): void {
  const scale = Math.max(width / DEMO_WIDTH, height / DEMO_HEIGHT);
  ctx.translate((width - DEMO_WIDTH * scale) / 2, (height - DEMO_HEIGHT * scale) / 2);
  ctx.scale(scale, scale);
}

function round3(value: number): number { return Math.round(value * 1000) / 1000; }
function round4(value: number): number { return Math.round(value * 10000) / 10000; }
