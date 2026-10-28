"use client";
import { useEffect, useRef, useState } from "react";
import { useTranslations } from "next-intl";
import { captionGroups, captionKey, hookRange, mappedWords, outputSize, outputTime, replaceCaptionGroup, type EditDocument, type EditorState, type TextLayer } from "@/lib/editor/document";
import { drawEditorTextLayer, formatHookEditorText, loadEditorFont, prepareTextDraft, textEditorLayout, textLayerBounds, type TextBounds, type TextDraft, type TextEditorLayout } from "@/lib/editor/render";
import { TransformableText } from "./transformable-text";

/** Titles and subtitles remain independent of the decoded picture in the editor. */
export function TextLayers({ doc, asset, time, index, videoRef, update, resolution, timelineDraft }: {
  doc: EditDocument;
  asset: EditorState;
  time: number;
  index: number;
  videoRef: React.RefObject<HTMLVideoElement | null>;
  update: (change: (doc: EditDocument) => EditDocument, group?: string) => void;
  resolution: { width: number; height: number };
  timelineDraft?: TextDraft;
}) {
  const t = useTranslations("editor");
  const hookCanvas = useRef<HTMLCanvasElement>(null);
  const captionCanvas = useRef<HTMLCanvasElement>(null);
  const frameRef = useRef<HTMLDivElement>(null);
  const [bounds, setBounds] = useState<Partial<Record<TextLayer, TextBounds>>>({});
  const [previewDraft, setPreviewDraft] = useState<TextDraft>();
  const draft = previewDraft ?? timelineDraft;
  const [layouts, setLayouts] = useState<Partial<Record<TextLayer, TextEditorLayout>>>({});
  const size = outputSize(doc, asset.width, asset.height);
  const at = outputTime(doc, Math.min(index, doc.segments.length - 1), time);
  const range = hookRange(doc);
  const group = captionGroups(mappedWords(doc), doc.captions.wordsPerLine)
    .find(words => at >= words[0].start && at < words[words.length - 1].end);
  const measure = (document: EditDocument, layer: TextLayer) => {
    const ctx = hookCanvas.current?.getContext("2d");
    return ctx ? textLayerBounds(ctx, document, size.width, size.height, at, layer, asset.captionLooks) : null;
  };
  const editDraft = (layer: TextLayer, text: string | null) => {
    const ctx = hookCanvas.current?.getContext("2d");
    setPreviewDraft(text === null || !ctx ? undefined : prepareTextDraft(doc, at, layer, text));
  };
  useEffect(() => {
    let cancelled = false;
    const refresh = () => {
      const ctx = hookCanvas.current?.getContext("2d");
      if (!ctx || cancelled) return;
      const hook = textLayerBounds(ctx, doc, size.width, size.height, at, "hook", asset.captionLooks, draft);
      const captions = textLayerBounds(ctx, doc, size.width, size.height, at, "captions", asset.captionLooks, draft)
        ?? textLayerBounds(ctx, doc, size.width, size.height, at, "captions", asset.captionLooks);
      setBounds({ ...(hook ? { hook } : {}), ...(captions ? { captions } : {}) });
      setLayouts({ hook: textEditorLayout(ctx, doc, size.width, size.height, at, "hook", draft), captions: textEditorLayout(ctx, doc, size.width, size.height, at, "captions", draft) });
    };
    refresh();
    void loadEditorFont(doc.captions.font).then(refresh).catch(() => {});
    return () => { cancelled = true; };
  }, [doc, at, size.width, size.height, asset.captionLooks, draft]);
  useEffect(() => {
    let frame: number;
    const draw = () => {
      const at = outputTime(doc, Math.min(index, doc.segments.length - 1), videoRef.current?.currentTime ?? time);
      for (const [canvas, layer] of [[hookCanvas.current, "hook"], [captionCanvas.current, "captions"]] as const) {
        const ctx = canvas?.getContext("2d");
        if (ctx) drawEditorTextLayer(ctx, doc, asset.width, asset.height, at, asset.captionLooks, layer, true, draft);
      }
      frame = requestAnimationFrame(draw);
    };
    frame = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(frame);
  }, [doc, asset, index, time, videoRef, draft]);
  const shared = { doc, asset, width: size.width, height: size.height, frame: frameRef, pause: () => videoRef.current?.pause(), update };
  return <div ref={frameRef} className="pointer-events-none absolute inset-0">
    <canvas ref={hookCanvas} aria-label={t("captions.hookTitle")} width={resolution.width} height={resolution.height} className="absolute inset-0 h-full w-full" />
    <canvas ref={captionCanvas} aria-label={t("timeline.tracks.captions")} width={resolution.width} height={resolution.height} className="absolute inset-0 h-full w-full" />
    {doc.hookTitle?.trim() && bounds.hook && (at >= range.start && at < range.end) && <TransformableText
      {...shared} layer="hook" label={t("captions.hookTitle")} value={doc.hookTitle} bounds={bounds.hook}
      buttonLabel={`${t("preview.region")}: ${t("captions.hookTitle")}: ${doc.hookTitle}`}
      measure={document => measure(document, "hook")}
      editorLayout={layouts.hook}
      formatValue={text => formatHookEditorText(doc, size.width, size.height, text)}
      onDraft={text => editDraft("hook", text)}
      onCommit={hookTitle => update(current => ({ ...current, hookTitle }))}
    />}
    {doc.captions.enabled && group && bounds.captions && <TransformableText
      {...shared} key={captionKey(doc, group)} captionId={captionKey(doc, group)} layer="captions" label={t("timeline.tracks.captions")}
      value={group.map(word => word.text).join(" ")} bounds={bounds.captions}
      buttonLabel={`${t("preview.region")}: ${group.map(word => word.text).join(" ")}`}
      measure={document => measure(document, "captions")}
      editorLayout={layouts.captions}
      onDraft={text => editDraft("captions", text)}
      onCommit={text => update(current => replaceCaptionGroup(current, group.map(word => word.id), text, asset.duration))}
    />}
  </div>;
}
