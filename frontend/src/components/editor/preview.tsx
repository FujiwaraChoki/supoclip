"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslations } from "next-intl";
import {
  Pause,
  Play,
  SkipBack,
  SkipForward,
  Repeat,
  Scan,
  VolumeX,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Toggle } from "@/components/ui/toggle";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import {
  brollAlpha,
  brollAt,
  brollOf,
  clamp,
  editDuration,
  footageKey,
  footageUrl,
  frameRect,
  outputSize,
  outputTime,
  timecode,
  type BRollFootage,
  type EditDocument,
  type EditorState,
} from "@/lib/editor/document";
import { drawEditorFrame, loadEditorFont, type TextDraft, type BRollLayer } from "@/lib/editor/render";
import type { InspectorTab } from "./inspector";
import { cn } from "@/lib/utils";
import { Panel, ToolButton } from "./studio-ui";
import { TextLayers } from "./text-layers";
import { usePreviewResolution } from "./use-preview-resolution";

interface Props {
  doc: EditDocument;
  asset: EditorState;
  endpoint: string;
  segmentIndex: number;
  select: (index: number) => void;
  time: number;
  onTime: (time: number) => void;
  seekRequest: { time: number; nonce: number };
  textDraft?: TextDraft;
  tab: InspectorTab;
  update: (change: (doc: EditDocument) => EditDocument, group?: string) => void;
  videoRef: React.RefObject<HTMLVideoElement | null>;
  /** Shown under the transport, inside the viewer card (the AI edit field). */
  footer?: React.ReactNode;
}
export function Preview({
  doc,
  asset,
  endpoint,
  segmentIndex,
  select,
  time,
  onTime,
  seekRequest,
  textDraft,
  tab,
  update,
  videoRef,
  footer,
}: Props) {
  const t = useTranslations("editor");
  const stageRef = useRef<HTMLDivElement>(null);
  const pictureRef = useRef<HTMLDivElement>(null);
  const [previewWidth, setPreviewWidth] = useState(270);
  const canvasRef = useRef<HTMLCanvasElement>(null),
    audioRef = useRef<{ context: AudioContext; gain: GainNode } | null>(null);
  // One hidden, muted player per stock video, kept in step with the clip.
  const brollPlayers = useRef(new Map<string, HTMLVideoElement>());
  const footage = [
    ...new Map(brollOf(doc).map((b) => [footageKey(b.footage), b.footage])).entries(),
  ] as [string, BRollFootage][];
  const [playing, setPlaying] = useState(false),
    [loop, setLoop] = useState(true),
    [guides, setGuides] = useState(false),
    [rate, setRate] = useState(1),
    [mediaError, setMediaError] = useState(false),
    [fontError, setFontError] = useState(false);
  const drag = useRef<{
    x: number;
    y: number;
    startX: number;
    startY: number;
  } | null>(null);
  const size = outputSize(doc, asset.width, asset.height);
  const resolution = usePreviewResolution(pictureRef, size);
  useEffect(() => {
    const stage = stageRef.current;
    if (!stage) return;
    const observer = new ResizeObserver(([entry]) =>
      setPreviewWidth(
        Math.min(
          entry.contentRect.width,
          (entry.contentRect.height * size.width) / size.height,
        ),
      ),
    );
    observer.observe(stage);
    return () => observer.disconnect();
  }, [size.width, size.height]);
  const play = useCallback(async () => {
    const video = videoRef.current;
    if (!video) return;
    if (!video.paused) {
      video.pause();
      return;
    }
    if (!audioRef.current && asset.hasAudio) {
      const context = new AudioContext();
      const gain = context.createGain();
      context.createMediaElementSource(video).connect(gain);
      gain.connect(context.destination);
      audioRef.current = { context, gain };
    }
    if (audioRef.current) {
      audioRef.current.gain.gain.value = doc.muted ? 0 : doc.volume;
      await audioRef.current.context.resume();
    }
    const s = doc.segments[segmentIndex];
    if (video.currentTime < s.start || video.currentTime >= s.end - 0.01)
      video.currentTime = s.start;
    await video.play().catch(() => setMediaError(true));
  }, [asset.hasAudio, doc, segmentIndex, videoRef]);
  useEffect(() => {
    void loadEditorFont(doc.captions.font)
      .then(() => setFontError(false))
      .catch(() => setFontError(true));
  }, [doc.captions.font]);
  useEffect(() => {
    const video = videoRef.current;
    if (video) {
      video.muted = doc.muted;
      video.playbackRate = rate;
    }
    if (audioRef.current)
      audioRef.current.gain.gain.value = doc.muted ? 0 : doc.volume;
  }, [doc.volume, doc.muted, rate, videoRef]);
  useEffect(() => {
    const video = videoRef.current;
    if (video) {
      video.currentTime = seekRequest.time;
      onTime(seekRequest.time);
    }
  }, [seekRequest, videoRef, onTime]);
  useEffect(
    () => () => {
      void audioRef.current?.context.close();
    },
    [],
  );
  useEffect(() => {
    let frame: number;
    const draw = () => {
      const canvas = canvasRef.current,
        video = videoRef.current;
      if (canvas && video && video.readyState >= 2) {
        const segment = doc.segments[segmentIndex] ?? doc.segments[0];
        if (!video.paused && video.currentTime >= segment.end - 0.012) {
          const next = segmentIndex + 1;
          if (next < doc.segments.length) {
            video.currentTime = doc.segments[next].start;
            select(next);
          } else if (loop) {
            video.currentTime = doc.segments[0].start;
            select(0);
          } else {
            video.pause();
            video.currentTime = segment.end;
          }
        }
        const context = canvas.getContext("2d");
        const sourceTime = video.currentTime,
          active = brollAt(doc, sourceTime),
          activeKey = active ? footageKey(active.footage) : null;
        let layer: BRollLayer | null = null;
        for (const [key, player] of brollPlayers.current) {
          if (key !== activeKey || !active) {
            if (!player.paused) player.pause();
            continue;
          }
          const wanted = active.offset + sourceTime - active.start;
          // Paused scrubbing seeks precisely; playback only corrects real drift.
          if (Math.abs(player.currentTime - wanted) > (video.paused ? 0.04 : 0.3))
            player.currentTime = wanted;
          player.playbackRate = video.playbackRate;
          if (!video.paused && player.paused) void player.play().catch(() => {});
          if (video.paused && !player.paused) player.pause();
          if (player.readyState >= 2)
            layer = {
              width: player.videoWidth,
              height: player.videoHeight,
              alpha: brollAlpha(active, sourceTime),
              draw: (x, y, w, h) => context?.drawImage(player, x, y, w, h),
            };
        }
        if (context)
          drawEditorFrame(
            context,
            doc,
            video.videoWidth || asset.width,
            video.videoHeight || asset.height,
            outputTime(
              doc,
              Math.min(segmentIndex, doc.segments.length - 1),
              video.currentTime,
            ),
            (rect) =>
              context.drawImage(video, rect.x, rect.y, rect.width, rect.height),
            asset.captionLooks,
            layer,
            "none",
          );
      }
      frame = requestAnimationFrame(draw);
    };
    frame = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(frame);
  }, [
    asset.captionLooks,
    asset.height,
    asset.width,
    doc,
    loop,
    segmentIndex,
    select,
    videoRef,
  ]);
  const step = (direction: number) => {
    const video = videoRef.current;
    if (!video) return;
    video.pause();
    video.currentTime = clamp(
      video.currentTime + direction / asset.fps,
      0,
      asset.duration,
    );
    onTime(video.currentTime);
  };
  const hint = fontError
    ? t("preview.fontError")
    : tab === "framing"
      ? t("preview.framingHint")
      : tab === "captions"
        ? t("preview.captionHint")
        : null;
  return (
    <Panel
      aria-label={t("preview.region")}
      className="flex flex-col lg:min-h-0 lg:flex-1"
    >
      <div
        ref={stageRef}
        className="relative flex h-[clamp(360px,calc(100dvh-560px),560px)] items-center justify-center px-6 pt-10 pb-3 max-md:h-[440px] lg:h-auto lg:min-h-0 lg:flex-1"
      >
        <div className="absolute inset-x-3 top-2.5 flex items-center justify-between gap-3">
          <span className="pl-1 text-xs text-muted-foreground tabular-nums">
            {t("preview.size", {
              width: size.width,
              height: size.height,
              fps: Math.round(asset.fps),
            })}
          </span>
          <Tooltip>
            <TooltipTrigger asChild>
              <Toggle
                size="sm"
                pressed={guides}
                onPressedChange={setGuides}
                aria-label={t("preview.safeArea")}
                className="h-7 rounded-full px-2.5 text-xs text-muted-foreground data-[state=on]:text-foreground"
              >
                <Scan />
                <span className="hidden sm:inline">{t("preview.safeArea")}</span>
              </Toggle>
            </TooltipTrigger>
            <TooltipContent>{t("preview.safeAreaTip")}</TooltipContent>
          </Tooltip>
        </div>
        <div
          ref={pictureRef}
          className="relative max-h-full max-w-full overflow-hidden rounded-xl bg-black shadow-[0_8px_30px_rgb(0_0_0/0.12)] ring-1 ring-black/5"
          style={{
            aspectRatio: `${size.width}/${size.height}`,
            width: `${previewWidth}px`,
          }}
        >
          <video
            ref={videoRef}
            crossOrigin="anonymous"
            src={`${endpoint}/media/${asset.hasLayout && doc.framing.fit === "auto" && doc.framing.aspect === "vertical" ? "layout" : "clean"}.mp4`}
            preload="auto"
            playsInline
            className="hidden"
            onLoadedMetadata={() => {
              if (videoRef.current)
                videoRef.current.currentTime = Math.min(videoRef.current.duration, Math.max(0, time));
            }}
            onTimeUpdate={(e) => {
              // Changing between the generated composition and raw landscape
              // resets the element to 0 before metadata loads. Keep the playhead
              // until that new source is ready to seek.
              if (e.currentTarget.readyState >= 2) onTime(e.currentTarget.currentTime);
            }}
            onPlay={() => setPlaying(true)}
            onPause={() => setPlaying(false)}
            onEnded={() => {
              if (loop && videoRef.current) {
                select(0);
                videoRef.current.currentTime = doc.segments[0].start;
                void videoRef.current.play();
              } else setPlaying(false);
            }}
            onError={() => setMediaError(true)}
          />
          {footage.map(([key, item]) => (
            <video
              key={key}
              ref={(player) => {
                if (player) brollPlayers.current.set(key, player);
                else brollPlayers.current.delete(key);
              }}
              crossOrigin="anonymous"
              src={footageUrl(endpoint, item)}
              preload="auto"
              muted
              playsInline
              className="hidden"
            />
          ))}
          <canvas
            ref={canvasRef}
            width={resolution.width}
            height={resolution.height}
            aria-label={t("preview.canvas")}
            className={`block h-full w-full touch-none ${tab === "framing" ? "cursor-move" : "cursor-default"}`}
            onPointerDown={(e) => {
              if (tab !== "framing") return;
              drag.current = {
                x: e.clientX,
                y: e.clientY,
                startX: doc.framing.x,
                startY: doc.framing.y,
              };
              e.currentTarget.setPointerCapture(e.pointerId);
            }}
            onPointerMove={(e) => {
              if (!drag.current) return;
              const box = e.currentTarget.getBoundingClientRect(),
                deltaX = e.clientX - drag.current.x,
                deltaY = e.clientY - drag.current.y;
                const rect = frameRect(
                  doc.framing,
                  asset.width,
                  asset.height,
                  box.width,
                  box.height,
                );
                const x = clamp(
                    drag.current.startX +
                      (Math.abs(box.width - rect.width) > 1
                        ? deltaX / (box.width - rect.width)
                        : 0),
                    0,
                    1,
                  ),
                  y = clamp(
                    drag.current.startY +
                      (Math.abs(box.height - rect.height) > 1
                        ? deltaY / (box.height - rect.height)
                        : 0),
                    0,
                    1,
                  );
                update(
                  (d) => ({ ...d, framing: { ...d.framing, x, y } }),
                  "drag-frame",
                );
            }}
            onPointerUp={() => {
              drag.current = null;
            }}
            onPointerCancel={() => {
              drag.current = null;
            }}
          />
          <TextLayers doc={doc} asset={asset} time={time} index={segmentIndex} videoRef={videoRef} update={update} resolution={resolution} timelineDraft={textDraft} />
          {guides && (
            <div className="pointer-events-none absolute inset-x-[8%] top-[10%] bottom-[20%] rounded-lg border border-dashed border-white/60">
              <span className="absolute -top-5 text-[10px] text-white/70">
                {t("preview.safeContent")}
              </span>
            </div>
          )}
          {mediaError && (
            <div className="absolute inset-0 flex flex-col justify-center gap-3 bg-stone-950/90 p-5 text-center text-sm text-white">
              <p>{t("preview.loadFailed")}</p>
              <Button
                variant="secondary"
                size="sm"
                onClick={() => {
                  setMediaError(false);
                  videoRef.current?.load();
                }}
              >
                {t("preview.retry")}
              </Button>
            </div>
          )}
        </div>
        {hint && (
          <p
            className={cn(
              "pointer-events-none absolute inset-x-0 top-3.5 px-40 text-center text-[11px] max-md:hidden",
              fontError ? "text-amber-600" : "text-muted-foreground",
            )}
          >
            {hint}
          </p>
        )}
      </div>
      <div className="grid shrink-0 grid-cols-[1fr_auto_1fr] items-center gap-2 px-3 pb-2 sm:px-4">
        <div className="flex items-center gap-1">
          <Tooltip>
            <TooltipTrigger asChild>
              <Toggle
                size="sm"
                aria-label={t("preview.loop")}
                pressed={loop}
                onPressedChange={setLoop}
              >
                <Repeat />
              </Toggle>
            </TooltipTrigger>
            <TooltipContent>{t("preview.loopTip")}</TooltipContent>
          </Tooltip>
          <Select
            value={String(rate)}
            onValueChange={(value) => setRate(Number(value))}
          >
            <SelectTrigger
              size="sm"
              aria-label={t("preview.speed")}
              className="h-8 w-[4.5rem] rounded-full border-transparent shadow-none hover:bg-accent tabular-nums"
            >
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {[0.5, 0.75, 1, 1.25, 1.5, 2].map((n) => (
                <SelectItem key={n} value={String(n)}>
                  {n}×
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="flex items-center gap-1">
          <ToolButton
            label={t("preview.previousFrame")}
            shortcut="←"
            onClick={() => step(-1)}
          >
            <SkipBack />
          </ToolButton>
          <Button
            size="icon-lg"
            className="size-11 rounded-full shadow-sm"
            aria-label={playing ? t("preview.pause") : t("preview.play")}
            onClick={() => void play()}
          >
            {playing ? (
              <Pause className="fill-current" />
            ) : (
              <Play className="ml-0.5 fill-current" />
            )}
          </Button>
          <ToolButton label={t("preview.nextFrame")} shortcut="→" onClick={() => step(1)}>
            <SkipForward />
          </ToolButton>
        </div>
        <div className="flex items-center justify-end gap-2 text-xs tabular-nums">
          {doc.muted && (
            <VolumeX
              aria-label={t("preview.muted")}
              className="size-3.5 text-muted-foreground"
            />
          )}
          <span className="whitespace-nowrap font-mono text-[13px] font-medium">
            {timecode(
              clamp(
                outputTime(
                  doc,
                  Math.min(segmentIndex, doc.segments.length - 1),
                  time,
                ),
                0,
                editDuration(doc),
              ),
            )}
            <span className="font-normal text-muted-foreground max-sm:hidden">
              {" "}
              / {timecode(editDuration(doc))}
            </span>
          </span>
        </div>
      </div>
      {footer}
    </Panel>
  );
}
