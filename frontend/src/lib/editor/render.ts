import {
  activeWordIndex,
  captionGroups,
  captionTransform,
  frameRect,
  hookRange,
  mappedWords,
  outputSize,
  textTransform,
  type TextLayer,
  type CaptionLook,
  type CaptionWord,
  type EditDocument,
} from "./document";

const groupCache = new WeakMap<
  EditDocument,
  ReturnType<typeof captionGroups>
>();
const lookCache = new WeakMap<
  EditDocument,
  { looks?: Record<string, CaptionLook>; look: ReturnType<typeof resolveLook> }
>();
const loaded = new Map<string, Promise<void>>();
export function loadEditorFont(name: string) {
  if (!loaded.has(name))
    loaded.set(
      name,
      (async () => {
        const font = new FontFace(
          `Editor-${name}`,
          `url(/api/fonts/${encodeURIComponent(name)})`,
        );
        await font.load();
        document.fonts.add(font);
      })().catch((error) => {
        loaded.delete(name);
        throw error;
      }),
    );
  return loaded.get(name)!;
}

// Used until the editor state delivers the templates (and for drafts without one).
const FALLBACK_LOOK: CaptionLook = {
  animation: "karaoke",
  wordPop: true,
  wordBox: false,
  wordBoxColor: null,
  highlightColor: null,
  emphasisColor: null,
  strokeColor: "#000000",
  strokeWidth: 3,
  shadow: true,
  glow: false,
  backgroundColor: null,
};
const HOOK_MARGIN = 0.07;

/** libass sizes fonts by ascender + descender, not the em box CSS uses. */
function setAssFont(ctx: CanvasRenderingContext2D, font: string, size: number) {
  ctx.font = `${size}px "Editor-${font}", sans-serif`;
  const metrics = ctx.measureText("A"),
    height = metrics.fontBoundingBoxAscent + metrics.fontBoundingBoxDescent;
  if (height > 0)
    ctx.font = `${(size * size) / height}px "Editor-${font}", sans-serif`;
}

/** Linear keyframes, as ASS `\t` interpolates them. */
function keyframes(t: number, points: [number, number][]) {
  if (t <= points[0][0]) return points[0][1];
  for (let i = 1; i < points.length; i++) {
    const [t1, v1] = points[i],
      [t0, v0] = points[i - 1];
    if (t <= t1) return v0 + ((v1 - v0) * (t - t0)) / (t1 - t0);
  }
  return points[points.length - 1][1];
}

/** Mirrors `editor_caption_template` and the generation renderer's pixel maths. */
function resolveLook(doc: EditDocument, looks?: Record<string, CaptionLook>) {
  const style = doc.captions,
    look = looks?.[style.template ?? "default"] ?? FALLBACK_LOOK;
  const animation = style.karaoke
    ? "karaoke"
    : look.animation === "karaoke"
      ? "none"
      : look.animation;
  const tiedEmphasis =
    (look.emphasisColor ?? look.highlightColor) === look.highlightColor;
  // The background toggle decides whether the template's box colour applies.
  const backgroundColor = style.background
    ? (look.backgroundColor ?? "#00000080")
    : null;
  return {
    ...look,
    animation,
    backgroundColor,
    emphasisColor: tiedEmphasis
      ? style.accent
      : (look.emphasisColor ?? style.accent),
    outlineColor: look.strokeColor ?? "#000000",
    backColor: backgroundColor ?? "#00000080",
  };
}
function cachedLook(doc: EditDocument, looks?: Record<string, CaptionLook>) {
  const cached = lookCache.get(doc);
  if (cached && cached.looks === looks) return cached.look;
  const look = resolveLook(doc, looks);
  lookCache.set(doc, { looks, look });
  return look;
}

/** Stock footage drawn over the whole frame, under the captions. */
export interface BRollLayer {
  width: number;
  height: number;
  alpha: number;
  draw: (x: number, y: number, width: number, height: number) => void;
}

export function drawEditorFrame(
  ctx: CanvasRenderingContext2D,
  doc: EditDocument,
  sourceWidth: number,
  sourceHeight: number,
  time: number,
  draw: (rect: ReturnType<typeof frameRect>) => void,
  looks?: Record<string, CaptionLook>,
  broll?: BRollLayer | null,
  textLayers: "all" | "none" = "all",
) {
  const { width, height } = ctx.canvas,
    fx = doc.effects;
  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = "black";
  ctx.fillRect(0, 0, width, height);
  ctx.save();
  ctx.filter = `brightness(${fx.brightness}%) contrast(${fx.contrast}%) saturate(${fx.saturation}%) blur(${(fx.blur * width) / 1080}px) hue-rotate(${fx.hue}deg)`;
  draw(frameRect(doc.framing, sourceWidth, sourceHeight, width, height));
  ctx.restore();
  if (broll && broll.alpha > 0 && broll.width > 0 && broll.height > 0) {
    // Fill the frame (cover), centred, as the export's scale+crop does.
    const scale = Math.max(width / broll.width, height / broll.height),
      w = broll.width * scale,
      h = broll.height * scale;
    ctx.save();
    ctx.globalAlpha = broll.alpha;
    broll.draw((width - w) / 2, (height - h) / 2, w, h);
    ctx.restore();
  }
  if (textLayers === "all") drawEditorTextLayer(ctx, doc, sourceWidth, sourceHeight, time, looks, "all", false);
}

/** Transparent editable layers in the viewer; composited only when exporting. */
export function drawEditorTextLayer(
  ctx: CanvasRenderingContext2D, doc: EditDocument, sourceWidth: number, sourceHeight: number,
  time: number, looks: Record<string, CaptionLook> | undefined, layer: "hook" | "captions" | "all", clear = true,
) {
  const { width, height } = ctx.canvas;
  if (clear) ctx.clearRect(0, 0, width, height);
  const out = outputSize(doc, sourceWidth, sourceHeight), look = cachedLook(doc, looks);
  ctx.save();
  ctx.scale(width / out.width, height / out.height);
  if (layer !== "hook" && doc.captions.enabled) drawCaptions(ctx, doc, out, time, look);
  if (layer !== "captions" && doc.hookTitle?.trim()) drawHookTitle(ctx, doc, out, time, look);
  ctx.restore();
}

function drawCaptions(
  ctx: CanvasRenderingContext2D,
  doc: EditDocument,
  { width, height }: { width: number; height: number },
  time: number,
  look: ReturnType<typeof resolveLook>,
) {
  let groups = groupCache.get(doc);
  if (!groups) {
    groups = captionGroups(mappedWords(doc), doc.captions.wordsPerLine);
    groupCache.set(doc, groups);
  }
  const group = groups.find(
    (group) => time >= group[0].start && time < group[group.length - 1].end,
  );
  if (!group) return;
  const transform = captionTransform(doc, group);
  const style = doc.captions,
    size = Math.max(1, Math.round(((transform.size ?? style.size) * width * transform.scale) / 1080)),
    karaoke = look.animation === "karaoke",
    active = karaoke ? group[activeWordIndex(group, time)] : undefined,
    label = (text: string) => (style.uppercase ? text.toUpperCase() : text);
  const outline =
    look.strokeColor && look.strokeWidth
      ? Math.max(
          look.strokeWidth,
          Math.round((size * look.strokeWidth) / 26),
        )
      : 0,
    shadow = look.shadow ? Math.max(2, Math.floor(size / 20)) : 0,
    boxPadding = Math.max(outline + 2, Math.floor(size / 5)),
    linePadding = Math.max(outline, Math.floor(size / 6)),
    centre = Math.round(height * (style.y + transform.y));
  const t = time - group[0].start,
    remaining = group[group.length - 1].end - time;
  let scale = 1,
    alpha = 1;
  if (karaoke && look.wordPop) scale = keyframes(t, [[0, 0.92], [0.14, 1]]);
  else if (look.animation === "pop")
    scale = keyframes(t, [[0, 0.88], [0.13, 1.06], [0.25, 1]]);
  else if (look.animation === "bounce")
    scale = keyframes(t, [[0, 0.7], [0.12, 1.12], [0.24, 1]]);
  else if (look.animation === "fade")
    alpha = Math.max(0, Math.min(1, t / 0.12, remaining / 0.12));

  ctx.save();
  ctx.globalAlpha = alpha;
  ctx.translate(width * (0.5 + transform.x), centre);
  ctx.scale(scale, scale);
  setAssFont(ctx, style.font, size);
  ctx.textBaseline = "middle";
  ctx.lineJoin = "round";
  // Fit each phrase in the safe width, using the same font for preview and browser export.
  const space = ctx.measureText(" ").width;
  const lines: CaptionWord[][] = [];
  let line: CaptionWord[] = [],
    lineWidth = 0;
  for (const word of group) {
    const wordWidth = ctx.measureText(label(word.text)).width;
    // libass wraps inside the style's 60px side margins.
    if (line.length && lineWidth + space + wordWidth > width - 120) {
      lines.push(line);
      line = [];
      lineWidth = 0;
    }
    line.push(word);
    lineWidth += wordWidth + (line.length > 1 ? space : 0);
  }
  if (line.length) lines.push(line);
  lines.forEach((words, index) => {
    const total =
      words.reduce((sum, w) => sum + ctx.measureText(label(w.text)).width, 0) +
      space * (words.length - 1);
    const y = (index - (lines.length - 1) / 2) * size * 1.2;
    if (look.backgroundColor) {
      ctx.fillStyle = look.backgroundColor;
      ctx.fillRect(
        -total / 2 - linePadding,
        y - size / 2 - linePadding,
        total + linePadding * 2,
        size + linePadding * 2,
      );
    }
    let x = -total / 2;
    for (const word of words) {
      const text = label(word.text),
        textWidth = ctx.measureText(text).width,
        isActive = word === active;
      if (isActive && look.wordBox) {
        ctx.fillStyle = look.wordBoxColor ?? style.accent;
        ctx.beginPath();
        ctx.roundRect(
          x - boxPadding,
          y - size * 0.45 - boxPadding,
          textWidth + boxPadding * 2,
          size * 0.9 + boxPadding * 2,
          boxPadding,
        );
        ctx.fill();
      } else if (!look.backgroundColor && (outline || shadow)) {
        ctx.save();
        if (shadow) {
          ctx.shadowColor = look.backColor;
          ctx.shadowOffsetX = ctx.shadowOffsetY = shadow;
        }
        if (look.glow) ctx.filter = "blur(4px)";
        ctx.strokeStyle = look.outlineColor;
        ctx.lineWidth = outline * 2;
        if (outline) ctx.strokeText(text, x, y);
        else {
          ctx.fillStyle = look.backColor;
          ctx.fillText(text, x, y);
        }
        ctx.restore();
      }
      ctx.fillStyle = isActive
        ? style.accent
        : word.highlight
          ? look.emphasisColor
          : style.color;
      ctx.fillText(text, x, y);
      x += textWidth + space;
    }
  });
  ctx.restore();
}

/** Split the title into one line, or two lines balanced around the middle. */
function balanceTitle(words: string[], maxChars: number) {
  const text = words.join(" ");
  if (text.length <= maxChars || words.length < 2) return [text];
  let best = [text],
    longest = text.length;
  for (let i = 1; i < words.length; i++) {
    const lines = [words.slice(0, i).join(" "), words.slice(i).join(" ")];
    const candidate = Math.max(lines[0].length, lines[1].length);
    if (candidate < longest) {
      longest = candidate;
      best = lines;
    }
  }
  return best;
}

export function hookTitleLayout(doc: EditDocument, width: number, height: number) {
  const title = doc.hookTitle?.trim() ?? "";
  const text = doc.captions.uppercase ? title.toUpperCase() : title;
  const captionPx = Math.max(1, Math.round((doc.captions.size * width) / 1080));
  const basePx = Math.max(34, Math.min(66, Math.floor(captionPx * 0.82)));
  const usable = width - 2 * Math.max(48, Math.floor(width * HOOK_MARGIN));
  const maxChars = Math.max(10, Math.floor(usable / (basePx * 0.52)));
  const lines = balanceTitle(text.split(/\s+/), maxChars);
  const longest = Math.max(...lines.map(line => line.length));
  const originalPx = longest > maxChars ? Math.max(30, Math.min(basePx, Math.floor(usable / (longest * 0.52)))) : basePx;
  const transform = textTransform(doc, "hook");
  const px = originalPx * transform.scale;
  const top = Math.max(48, Math.floor(height * HOOK_MARGIN)) + transform.y * height + (originalPx - px) * lines.length * 0.6;
  return { lines, px, usable, top, x: width * (0.5 + transform.x) };
}

/** The generated clip's headline: top safe area, fading in and out over ~4s. */
function drawHookTitle(
  ctx: CanvasRenderingContext2D,
  doc: EditDocument,
  { width, height }: { width: number; height: number },
  time: number,
  look: ReturnType<typeof resolveLook>,
) {
  const { start, end } = hookRange(doc);
  if (time < start || time >= end) return;
  const style = doc.captions;
  const { lines, px, top, x: centreX } = hookTitleLayout(doc, width, height);
  const hasOutline = !!look.strokeColor && look.strokeWidth > 0,
    // Mirrors build_hook_title_ass: an opaque box only without an outline.
    boxed = !hasOutline && !!look.backgroundColor,
    outline = boxed
      ? Math.max(4, Math.floor(px / 6))
      : hasOutline
        ? Math.max(look.strokeWidth, Math.round((px * look.strokeWidth) / 26))
        : Math.max(2, Math.floor(px / 16)),
    shadow = look.shadow ? Math.max(2, Math.floor(px / 20)) : 0;
  const t = time - start;
  ctx.save();
  ctx.globalAlpha = Math.max(0, Math.min(1, t / 0.16, (end - time) / 0.24));
  const lineHeight = px * 1.2,
    scale = look.wordPop ? keyframes(t, [[0, 0.9], [0.16, 1]]) : 1;
  ctx.translate(centreX, top + (lineHeight * lines.length) / 2);
  ctx.scale(scale, scale);
  setAssFont(ctx, style.font, px);
  ctx.textBaseline = "middle";
  ctx.lineJoin = "round";
  const space = ctx.measureText(" ").width;
  lines.forEach((line, index) => {
    const y = (index - (lines.length - 1) / 2) * lineHeight,
      words = line.split(" "),
      total =
        words.reduce((sum, w) => sum + ctx.measureText(w).width, 0) +
        space * (words.length - 1);
    if (boxed) {
      ctx.fillStyle = look.outlineColor;
      ctx.fillRect(
        -total / 2 - outline,
        y - lineHeight / 2 - outline / 2,
        total + outline * 2,
        lineHeight + outline,
      );
    }
    let x = -total / 2;
    for (const word of words) {
      if (!boxed) {
        ctx.save();
        if (shadow) {
          ctx.shadowColor = look.backColor;
          ctx.shadowOffsetX = ctx.shadowOffsetY = shadow;
        }
        ctx.strokeStyle = look.outlineColor;
        ctx.lineWidth = outline * 2;
        ctx.strokeText(word, x, y);
        ctx.restore();
      }
      // Numbers take the accent; the export also accents power words.
      ctx.fillStyle = /\d/.test(word) ? look.emphasisColor : style.color;
      ctx.fillText(word, x, y);
      x += ctx.measureText(word).width + space;
    }
  });
  ctx.restore();
}

export interface TextBounds { x: number; y: number; width: number; height: number }

/** Selection geometry in output pixels, measured with the renderer's font. */
export function textLayerBounds(ctx: CanvasRenderingContext2D, doc: EditDocument, width: number, height: number, time: number, layer: TextLayer, looks?: Record<string, CaptionLook>): TextBounds | null {
  const look = cachedLook(doc, looks);
  if (layer === "hook") {
    if (!doc.hookTitle?.trim()) return null;
    const { lines, px, top, x } = hookTitleLayout(doc, width, height);
    setAssFont(ctx, doc.captions.font, px);
    const pad = Math.max(5, Math.round(px * (look.strokeWidth || 2) / 26)) + 3;
    const w = Math.max(...lines.map(line => ctx.measureText(line).width)) + pad * 2;
    return { x: x - w / 2, y: top - pad, width: w, height: lines.length * px * 1.2 + pad * 2 };
  }
  const group = captionGroups(mappedWords(doc), doc.captions.wordsPerLine)
    .find(words => time >= words[0].start && time < words[words.length - 1].end);
  if (!group || !doc.captions.enabled) return null;
  const transform = captionTransform(doc, group);
  const px = Math.max(1, Math.round((transform.size ?? doc.captions.size) * width * transform.scale / 1080));
  setAssFont(ctx, doc.captions.font, px);
  const space = ctx.measureText(" ").width;
  let lineWidth = 0, longest = 0, lines = 1;
  for (const word of group) {
    const w = ctx.measureText(doc.captions.uppercase ? word.text.toUpperCase() : word.text).width;
    if (lineWidth && lineWidth + space + w > width - 120) { longest = Math.max(longest, lineWidth); lineWidth = 0; lines++; }
    lineWidth += (lineWidth ? space : 0) + w;
  }
  const pad = Math.max(px / 5, look.strokeWidth * px / 26, 5) + 3;
  const w = Math.max(longest, lineWidth) + pad * 2, h = lines * px * 1.2 + pad * 2;
  return { x: width * (0.5 + transform.x) - w / 2, y: height * (doc.captions.y + transform.y) - h / 2, width: w, height: h };
}
