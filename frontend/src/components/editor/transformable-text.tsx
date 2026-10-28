"use client";
import { useRef, useState } from "react";
import { useTranslations } from "next-intl";
import { clamp, textTransform, uid, type CaptionTransform, type EditDocument, type TextLayer } from "@/lib/editor/document";
import type { TextBounds, TextEditorLayout } from "@/lib/editor/render";
import { keepTextInFrame, resizeTextScale, TEXT_CORNERS, type TextCorner } from "@/lib/editor/text-transform";
import { InlineText } from "./inline-text";

export function TransformableText({ doc, layer, captionId, label, value, buttonLabel, bounds, width, height, frame, measure, pause, update, onDraft, editorLayout, formatValue, onCommit }: {
  doc: EditDocument;
  layer: TextLayer;
  captionId?: string;
  label: string;
  value: string;
  buttonLabel: string;
  bounds: TextBounds;
  width: number;
  height: number;
  frame: React.RefObject<HTMLDivElement | null>;
  measure: (doc: EditDocument) => TextBounds | null;
  pause: () => void;
  update: (change: (doc: EditDocument) => EditDocument, group?: string) => void;
  onDraft: (text: string | null) => void;
  editorLayout?: TextEditorLayout;
  formatValue?: (text: string) => string;
  onCommit: (text: string) => void;
}) {
  const t = useTranslations("editor");
  const [editing, setEditing] = useState(false);
  const drag = useRef<{ doc: EditDocument; bounds: TextBounds; group: string; sx: number; sy: number; corner?: TextCorner; x: number; y: number } | null>(null);
  const placement = (document: EditDocument): CaptionTransform => (captionId ? document.captionTransforms?.[captionId] : undefined) ?? textTransform(document, layer);
  const begin = (corner?: TextCorner, x = 0, y = 0) => {
    pause();
    const rect = frame.current?.getBoundingClientRect();
    if (!rect?.width || !rect.height) return;
    drag.current = { doc, bounds, group: `gesture:${uid()}`, sx: width / rect.width, sy: height / rect.height, corner, x, y };
  };
  const move = (dx: number, dy: number) => {
    const start = drag.current;
    if (!start) return;
    dx *= start.sx;
    dy *= start.sy;
    const original = placement(start.doc);
    let transform = { ...original, x: original.x + dx / width, y: original.y + dy / height };
    if (start.corner) transform = { ...original, scale: resizeTextScale(start.bounds, start.corner, dx, dy, original.scale) };
    const withTransform = (base: EditDocument): EditDocument => captionId
      ? { ...base, captionTransforms: { ...base.captionTransforms, [captionId]: { ...transform, size: transform.size ?? null } } }
      : { ...base, textTransforms: { ...base.textTransforms, [layer]: transform } };
    let nextBounds = measure(withTransform(start.doc));
    if (!nextBounds) return;
    if (start.corner) {
      for (let attempt = 0; attempt < 4 && (nextBounds.width > width - 16 || nextBounds.height > height - 16); attempt++) {
        transform.scale = clamp(transform.scale * Math.min((width - 16) / nextBounds.width, (height - 16) / nextBounds.height), 0.25, 4);
        nextBounds = measure(withTransform(start.doc))!;
      }
      // Keep the opposite corner fixed, including when larger captions wrap.
      const east = start.corner.endsWith("e"), south = start.corner.startsWith("s");
      const x = start.bounds.x + (east ? 0 : start.bounds.width - nextBounds.width);
      const y = start.bounds.y + (south ? 0 : start.bounds.height - nextBounds.height);
      transform.x += (x - nextBounds.x) / width;
      transform.y += (y - nextBounds.y) / height;
      nextBounds = { ...nextBounds, x, y };
    }
    transform = keepTextInFrame(transform, nextBounds, width, height);
    update(current => withTransform(current), start.group);
  };
  const scale = frame.current ? frame.current.getBoundingClientRect().width / width : 0.3;
  return <div role="group" aria-label={`${t("preview.region")}: ${label}`} className="group pointer-events-none absolute rounded-sm ring-1 ring-transparent hover:ring-sky-400 focus-within:ring-sky-400"
    style={{ left: `${bounds.x / width * 100}%`, top: `${bounds.y / height * 100}%`, width: `${bounds.width / width * 100}%`, height: `${bounds.height / height * 100}%` }}>
    <InlineText value={value} label={label} buttonLabel={buttonLabel} multiline maxLength={layer === "hook" ? 200 : 960}
      className="pointer-events-auto absolute inset-0 h-full w-full resize-none overflow-hidden cursor-text rounded-sm border border-transparent bg-transparent p-0 text-center outline-none hover:border-sky-400 focus:border-sky-400 focus:bg-transparent focus:ring-1 focus:ring-sky-400 selection:text-transparent selection:bg-sky-400/30"
      style={{ color: doc.captions.color, fontFamily: `"Editor-${doc.captions.font}", sans-serif`, fontSize: Math.max(12, Math.min(bounds.height * 0.6, (placement(doc).size ?? doc.captions.size) * placement(doc).scale) * scale), lineHeight: "1.2", textTransform: doc.captions.uppercase ? "uppercase" : "none", textShadow: "0 1px 2px black" }}
      onBegin={pause} onCommit={onCommit}
      onEditing={setEditing}
      onDraft={onDraft}
      formatValue={formatValue}
      editorStyle={editorLayout ? {
        color: "transparent", WebkitTextFillColor: "transparent", caretColor: doc.captions.color, textShadow: "none", borderColor: "transparent", boxShadow: "none",
        fontSize: editorLayout.fontSize * scale, lineHeight: `${editorLayout.lineHeight * scale}px`,
        fontWeight: 400, fontStyle: "normal", fontKerning: "normal", letterSpacing: "normal",
        width: editorLayout.wrapWidth * scale, left: "50%", transform: "translateX(-50%)",
        paddingTop: Math.max(0, (bounds.height - Math.max(1, editorLayout.lines.length) * editorLayout.lineHeight) / 2) * scale,
      } : undefined}
      onMoveStart={() => begin()} onMove={move}
      onNudge={(dx, dy) => { begin(); move(dx, dy); }}
    />
    {!editing && TEXT_CORNERS.map(corner => <button key={corner} type="button"
      aria-label={t("preview.resizeText", { layer: label, corner: t(`preview.corners.${corner}`) })}
      className={`pointer-events-auto absolute z-10 size-3 touch-none rounded-[2px] border border-white bg-sky-500 opacity-0 shadow-sm outline-none group-hover:opacity-100 group-focus-within:opacity-100 focus:opacity-100 ${corner === "nw" || corner === "se" ? "cursor-nwse-resize" : "cursor-nesw-resize"}`}
      style={{ left: corner.endsWith("e") ? "100%" : 0, top: corner.startsWith("s") ? "100%" : 0, transform: "translate(-50%,-50%)" }}
      onPointerDown={event => { event.preventDefault(); event.stopPropagation(); begin(corner, event.clientX, event.clientY); event.currentTarget.setPointerCapture(event.pointerId); }}
      onPointerMove={event => {
        const start = drag.current;
        if (start?.corner && event.buttons & 1) move(event.clientX - start.x, event.clientY - start.y);
      }}
      onPointerUp={() => { drag.current = null; }} onPointerCancel={() => { drag.current = null; }} onLostPointerCapture={() => { drag.current = null; }}
      onKeyDown={event => {
        if (!["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)) return;
        event.preventDefault(); event.stopPropagation();
        begin(corner);
        const grow = event.key === "ArrowRight" || event.key === "ArrowUp";
        move((corner.endsWith("e") ? 1 : -1) * (grow ? 8 : -8), (corner.startsWith("s") ? 1 : -1) * (grow ? 8 : -8));
        drag.current = null;
      }}
    />)}
  </div>;
}
