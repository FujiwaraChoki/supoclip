"use client";
import { useCallback, useEffect, useRef, useState } from "react";
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
  clamp,
  editDuration,
  frameRect,
  outputSize,
  outputTime,
  timecode,
  type EditDocument,
  type EditorState,
} from "@/lib/editor/document";
import { drawEditorFrame, loadEditorFont } from "@/lib/editor/render";
import type { InspectorTab } from "./inspector";
import { Panel, ToolButton } from "./studio-ui";

interface Props {
  doc: EditDocument;
  asset: EditorState;
  endpoint: string;
  segmentIndex: number;
  select: (index: number) => void;
  time: number;
  onTime: (time: number) => void;
  seekRequest: { time: number; nonce: number };
  tab: InspectorTab;
  update: (change: (doc: EditDocument) => EditDocument, group?: string) => void;
  videoRef: React.RefObject<HTMLVideoElement | null>;
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
  tab,
  update,
  videoRef,
}: Props) {
  const stageRef = useRef<HTMLDivElement>(null);
  const [previewWidth, setPreviewWidth] = useState(270);
  const canvasRef = useRef<HTMLCanvasElement>(null),
    audioRef = useRef<{ context: AudioContext; gain: GainNode } | null>(null);
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
    captionY: number;
  } | null>(null);
  const size = outputSize(doc, asset.width, asset.height);
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
          );
      }
      frame = requestAnimationFrame(draw);
    };
    frame = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(frame);
  }, [asset.height, asset.width, doc, loop, segmentIndex, select, videoRef]);
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
    ? "Caption font could not load. Check your connection before exporting."
    : tab === "framing"
      ? "Drag the video to reframe · captions stay in place"
      : tab === "captions"
        ? "Drag vertically to position captions"
        : null;
  return (
    <Panel aria-label="Video preview">
      <div className="flex items-center justify-between gap-3 border-b px-4 py-2">
        <div className="flex items-center gap-2 text-sm font-semibold">
          Preview
          <span className="text-xs font-normal text-muted-foreground tabular-nums">
            {size.width} × {size.height} · {Math.round(asset.fps)} fps
          </span>
        </div>
        <Tooltip>
          <TooltipTrigger asChild>
            <Toggle
              size="sm"
              pressed={guides}
              onPressedChange={setGuides}
              aria-label="Safe area"
            >
              <Scan />
              <span className="hidden sm:inline">Safe area</span>
            </Toggle>
          </TooltipTrigger>
          <TooltipContent>Show where platform UI covers the video</TooltipContent>
        </Tooltip>
      </div>
      <div
        ref={stageRef}
        className="relative flex h-[clamp(340px,calc(100dvh-640px),520px)] items-center justify-center bg-stone-950 bg-[radial-gradient(ellipse_at_50%_40%,var(--color-stone-800),transparent_70%)] px-6 py-5 max-md:h-[415px] max-md:px-4"
      >
        <div
          className="relative max-h-full max-w-full overflow-hidden rounded-md bg-black shadow-2xl ring-1 ring-white/10"
          style={{
            aspectRatio: `${size.width}/${size.height}`,
            width: `${previewWidth}px`,
          }}
        >
          <video
            ref={videoRef}
            src={`${endpoint}/media/clean.mp4`}
            preload="auto"
            playsInline
            className="hidden"
            onLoadedMetadata={() => {
              if (videoRef.current)
                videoRef.current.currentTime = seekRequest.nonce
                  ? seekRequest.time
                  : doc.segments[0].start;
            }}
            onTimeUpdate={(e) => onTime(e.currentTarget.currentTime)}
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
          <canvas
            ref={canvasRef}
            width={Math.round((size.width / size.height) * 640)}
            height={640}
            aria-label="Edited video preview"
            className={`block h-full w-full touch-none ${tab === "framing" || tab === "captions" ? "cursor-move" : "cursor-default"}`}
            onPointerDown={(e) => {
              if (tab !== "framing" && tab !== "captions") return;
              drag.current = {
                x: e.clientX,
                y: e.clientY,
                startX: doc.framing.x,
                startY: doc.framing.y,
                captionY: doc.captions.y,
              };
              e.currentTarget.setPointerCapture(e.pointerId);
            }}
            onPointerMove={(e) => {
              if (!drag.current) return;
              const box = e.currentTarget.getBoundingClientRect(),
                deltaX = e.clientX - drag.current.x,
                deltaY = e.clientY - drag.current.y;
              if (tab === "captions") {
                const y = clamp(
                  drag.current.captionY + deltaY / box.height,
                  0.1,
                  0.9,
                );
                update(
                  (d) => ({ ...d, captions: { ...d.captions, y } }),
                  "drag-caption",
                );
              } else {
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
              }
            }}
            onPointerUp={() => {
              drag.current = null;
            }}
            onPointerCancel={() => {
              drag.current = null;
            }}
          />
          {guides && (
            <div className="pointer-events-none absolute inset-x-[8%] top-[10%] bottom-[20%] rounded-lg border border-dashed border-white/60">
              <span className="absolute -top-5 text-[10px] text-white/70">
                Keep important content here
              </span>
            </div>
          )}
          {mediaError && (
            <div className="absolute inset-0 flex flex-col justify-center gap-3 bg-stone-950/90 p-5 text-center text-sm text-white">
              <p>Preview could not load.</p>
              <Button
                variant="secondary"
                size="sm"
                onClick={() => {
                  setMediaError(false);
                  videoRef.current?.load();
                }}
              >
                Retry preview
              </Button>
            </div>
          )}
        </div>
        {hint && (
          <p
            className={`absolute inset-x-0 bottom-1.5 px-4 text-center text-[11px] ${fontError ? "text-amber-300" : "text-stone-400"}`}
          >
            {hint}
          </p>
        )}
      </div>
      <div className="grid grid-cols-[1fr_auto_1fr] items-center gap-2 border-t px-3 py-2.5 sm:px-4">
        <div className="flex items-center gap-1">
          <Tooltip>
            <TooltipTrigger asChild>
              <Toggle
                size="sm"
                aria-label="Loop selected edit"
                pressed={loop}
                onPressedChange={setLoop}
              >
                <Repeat />
              </Toggle>
            </TooltipTrigger>
            <TooltipContent>Loop playback</TooltipContent>
          </Tooltip>
          <Select
            value={String(rate)}
            onValueChange={(value) => setRate(Number(value))}
          >
            <SelectTrigger
              size="sm"
              aria-label="Preview playback speed"
              className="h-8 w-[4.5rem] border-transparent shadow-none hover:bg-accent tabular-nums"
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
            label="Previous frame"
            shortcut="←"
            onClick={() => step(-1)}
          >
            <SkipBack />
          </ToolButton>
          <Button
            size="icon-lg"
            className="rounded-full"
            aria-label={playing ? "Pause" : "Play"}
            onClick={() => void play()}
          >
            {playing ? (
              <Pause className="fill-current" />
            ) : (
              <Play className="ml-0.5 fill-current" />
            )}
          </Button>
          <ToolButton label="Next frame" shortcut="→" onClick={() => step(1)}>
            <SkipForward />
          </ToolButton>
        </div>
        <div className="flex items-center justify-end gap-2 text-xs tabular-nums">
          {doc.muted && (
            <VolumeX
              aria-label="Muted"
              className="size-3.5 text-muted-foreground"
            />
          )}
          <span className="whitespace-nowrap font-medium">
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
    </Panel>
  );
}
