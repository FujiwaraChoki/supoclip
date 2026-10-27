"use client";
import { useEffect, useRef, useState } from "react";
import { useTranslations } from "next-intl";
import { captionGroups, clamp, hookRange, mappedWords, outputSize, outputTime, replaceCaptionGroup, type EditDocument, type EditorState } from "@/lib/editor/document";
import { drawEditorTextLayer, hookTitleLayout } from "@/lib/editor/render";
import { InlineText } from "./inline-text";

/** Titles and subtitles remain independent of the decoded picture in the editor. */
export function TextLayers({ doc, asset, time, index, videoRef, update, width }: {
  doc: EditDocument;
  asset: EditorState;
  time: number;
  index: number;
  videoRef: React.RefObject<HTMLVideoElement | null>;
  update: (change: (doc: EditDocument) => EditDocument, group?: string) => void;
  width: number;
}) {
  const t = useTranslations("editor");
  const hookCanvas = useRef<HTMLCanvasElement>(null);
  const captionCanvas = useRef<HTMLCanvasElement>(null);
  const captionY = useRef(doc.captions.y);
  const [editingHook, setEditingHook] = useState(false);
  const [editingCaption, setEditingCaption] = useState(false);
  const size = outputSize(doc, asset.width, asset.height);
  const at = outputTime(doc, Math.min(index, doc.segments.length - 1), time);
  const range = hookRange(doc);
  const hook = hookTitleLayout(doc, size.width, size.height);
  const group = captionGroups(mappedWords(doc), doc.captions.wordsPerLine)
    .find(words => at >= words[0].start && at < words[words.length - 1].end);
  const scale = width / size.width;
  useEffect(() => {
    let frame: number;
    const draw = () => {
      const at = outputTime(doc, Math.min(index, doc.segments.length - 1), videoRef.current?.currentTime ?? time);
      for (const [canvas, layer] of [[hookCanvas.current, "hook"], [captionCanvas.current, "captions"]] as const) {
        const ctx = canvas?.getContext("2d");
        if (ctx) drawEditorTextLayer(ctx, doc, asset.width, asset.height, at, asset.captionLooks, layer);
      }
      frame = requestAnimationFrame(draw);
    };
    frame = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(frame);
  }, [doc, asset, index, time, videoRef]);
  const textStyle = {
    color: doc.captions.color,
    fontFamily: `"Editor-${doc.captions.font}", sans-serif`,
    textTransform: doc.captions.uppercase ? "uppercase" as const : "none" as const,
    textShadow: "0 1px 2px #000, 1px 0 2px #000, -1px 0 2px #000",
  };
  const className = "pointer-events-auto absolute left-[6%] w-[88%] resize-none cursor-text rounded-sm border border-transparent bg-transparent p-0 text-center outline-none hover:border-white/70 focus:border-sky-400 focus:bg-black/60 focus:ring-1 focus:ring-sky-400";
  return <div className="pointer-events-none absolute inset-0">
    <canvas ref={hookCanvas} aria-label={t("captions.hookTitle")} width={Math.round(size.width / size.height * 640)} height={640} className={`absolute inset-0 h-full w-full ${editingHook ? "invisible" : ""}`} />
    <canvas ref={captionCanvas} aria-label={t("timeline.tracks.captions")} width={Math.round(size.width / size.height * 640)} height={640} className={`absolute inset-0 h-full w-full ${editingCaption ? "invisible" : ""}`} />
    {doc.hookTitle?.trim() && (editingHook || (at >= range.start && at < range.end)) && <InlineText
      value={doc.hookTitle}
      label={t("captions.hookTitle")}
      buttonLabel={`${t("preview.region")}: ${t("captions.hookTitle")}: ${doc.hookTitle}`}
      maxLength={200}
      multiline
      className={className}
      style={{ ...textStyle, top: `${hook.top / size.height * 100}%`, height: Math.max(28, hook.lines.length * hook.px * 1.2 * scale), fontSize: Math.max(12, hook.px * scale), lineHeight: "1.2" }}
      onBegin={() => videoRef.current?.pause()}
      onEditing={setEditingHook}
      onCommit={hookTitle => update(current => ({ ...current, hookTitle }))}
    />}
    {doc.captions.enabled && group && <InlineText
      key={group.map(word => word.id).join(":")}
      value={group.map(word => word.text).join(" ")}
      label={t("timeline.tracks.captions")}
      maxLength={960}
      buttonLabel={`${t("preview.region")}: ${group.map(word => word.text).join(" ")}`}
      multiline
      className={className}
      style={{ ...textStyle, top: `${doc.captions.y * 100}%`, transform: "translateY(-50%)", minHeight: 28, height: Math.max(28, doc.captions.size * 2.4 * scale), fontSize: Math.max(12, doc.captions.size * scale), lineHeight: "1.2" }}
      onBegin={() => videoRef.current?.pause()}
      onEditing={setEditingCaption}
      onMoveStart={() => { captionY.current = doc.captions.y; }}
      onMove={(_dx, dy) => update(current => ({ ...current, captions: { ...current.captions, y: clamp(captionY.current + dy / (width / size.width * size.height), 0.1, 0.9) } }), "drag-caption")}
      onCommit={text => update(current => replaceCaptionGroup(current, group.map(word => word.id), text, asset.duration))}
    />}
  </div>;
}
