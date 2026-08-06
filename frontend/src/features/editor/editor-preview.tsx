"use client";

import {
  type CSSProperties,
  type PointerEvent as ReactPointerEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import { cn } from "@/lib/utils";

import type { EditorAsset, EditorProject, TimelineItem } from "./types";

type MediaElement = HTMLVideoElement | HTMLAudioElement;

interface EditorPreviewProps {
  project: EditorProject;
  assets: EditorAsset[];
  currentTime: number;
  isPlaying: boolean;
  selectedItemId: string | null;
  cropMode: boolean;
  onSelectItem: (itemId: string | null) => void;
  onUpdateItem: (itemId: string, updates: Partial<TimelineItem>) => void;
  onBeginTransform: () => void;
  onEndTransform: () => void;
}

type ResizeCorner = "nw" | "ne" | "sw" | "se";
type CropEdge = "top" | "right" | "bottom" | "left";

function isItemActive(item: TimelineItem, time: number) {
  return !item.hidden && time >= item.start && time < item.start + item.duration;
}

function itemSourceTime(item: TimelineItem, time: number) {
  return Math.max(0, item.trimStart + (time - item.start) * item.speed);
}

function itemEnvelope(item: TimelineItem, time: number) {
  const elapsed = Math.max(0, time - item.start);
  const remaining = Math.max(0, item.start + item.duration - time);
  const fadeIn = item.fadeIn > 0 ? Math.min(1, elapsed / item.fadeIn) : 1;
  const fadeOut = item.fadeOut > 0 ? Math.min(1, remaining / item.fadeOut) : 1;
  return Math.min(fadeIn, fadeOut);
}

function cropStyle(item: TimelineItem): CSSProperties {
  const horizontal = Math.max(5, 100 - item.crop.left - item.crop.right);
  const vertical = Math.max(5, 100 - item.crop.top - item.crop.bottom);

  return {
    width: `${(100 / horizontal) * 100}%`,
    height: `${(100 / vertical) * 100}%`,
    left: `${(-item.crop.left / horizontal) * 100}%`,
    top: `${(-item.crop.top / vertical) * 100}%`,
  };
}

function mediaFilter(item: TimelineItem, previewScale: number) {
  return [
    `brightness(${item.effects.brightness}%)`,
    `contrast(${item.effects.contrast}%)`,
    `saturate(${item.effects.saturation}%)`,
    `blur(${item.effects.blur * previewScale}px)`,
    `hue-rotate(${item.effects.hue}deg)`,
  ].join(" ");
}

function trackPointerGesture(
  onMove: (event: PointerEvent) => void,
  onFinish: () => void,
) {
  let finished = false;
  const finish = () => {
    if (finished) return;
    finished = true;
    window.removeEventListener("pointermove", onMove);
    window.removeEventListener("pointerup", finish);
    window.removeEventListener("pointercancel", finish);
    window.removeEventListener("blur", finish);
    onFinish();
  };
  window.addEventListener("pointermove", onMove);
  window.addEventListener("pointerup", finish, { once: true });
  window.addEventListener("pointercancel", finish, { once: true });
  window.addEventListener("blur", finish, { once: true });
}

function captionTextAtTime(item: TimelineItem, time: number) {
  const words = item.text?.content.trim().split(/\s+/).filter(Boolean) ?? [];
  if (words.length <= 5) return words.join(" ");
  const progress = Math.min(0.9999, Math.max(0, (time - item.start) / Math.max(item.duration, 0.01)));
  const index = Math.floor(progress * words.length);
  const start = Math.max(0, Math.min(words.length - 5, index - 1));
  return words.slice(start, start + 5).join(" ");
}

export function EditorPreview({
  project,
  assets,
  currentTime,
  isPlaying,
  selectedItemId,
  cropMode,
  onSelectItem,
  onUpdateItem,
  onBeginTransform,
  onEndTransform,
}: EditorPreviewProps) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const mediaRefs = useRef(new Map<string, MediaElement>());
  const [hostSize, setHostSize] = useState({ width: 0, height: 0 });
  const assetMap = useMemo(() => new Map(assets.map((asset) => [asset.id, asset])), [assets]);

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;

    const measure = () => {
      const rect = host.getBoundingClientRect();
      setHostSize({ width: Math.max(0, rect.width - 40), height: Math.max(0, rect.height - 40) });
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(host);
    return () => observer.disconnect();
  }, []);

  const stageSize = useMemo(() => {
    if (!hostSize.width || !hostSize.height) return { width: 0, height: 0 };
    const aspect = project.canvas.width / project.canvas.height;
    const hostAspect = hostSize.width / hostSize.height;
    if (hostAspect > aspect) {
      const height = hostSize.height;
      return { width: height * aspect, height };
    }
    const width = hostSize.width;
    return { width, height: width / aspect };
  }, [hostSize, project.canvas.height, project.canvas.width]);

  const setMediaRef = useCallback((itemId: string, element: MediaElement | null) => {
    if (element) mediaRefs.current.set(itemId, element);
    else mediaRefs.current.delete(itemId);
  }, []);

  useEffect(() => {
    for (const item of project.items) {
      const element = mediaRefs.current.get(item.id);
      if (!element) continue;
      const active = isItemActive(item, currentTime);
      if (!active) {
        element.pause();
        continue;
      }

      const sourceTime = itemSourceTime(item, currentTime);
      if (Number.isFinite(element.duration)) {
        const boundedTime = Math.min(Math.max(0, sourceTime), Math.max(0, element.duration - 0.01));
        if (Math.abs(element.currentTime - boundedTime) > (isPlaying ? 0.3 : 0.03)) {
          element.currentTime = boundedTime;
        }
      }
      element.playbackRate = Math.min(4, Math.max(0.25, item.speed));
      element.muted = item.muted;
      element.volume = Math.min(1, Math.max(0, (item.volume / 100) * itemEnvelope(item, currentTime)));

      if (isPlaying) {
        void element.play().catch(() => undefined);
      } else {
        element.pause();
      }
    }
  }, [currentTime, isPlaying, project.items]);

  const beginDrag = useCallback(
    (event: ReactPointerEvent, item: TimelineItem) => {
      if (event.button !== 0) return;
      event.stopPropagation();
      onSelectItem(item.id);
      if (item.locked) return;
      event.currentTarget.setPointerCapture(event.pointerId);
      onBeginTransform();

      const startX = event.clientX;
      const startY = event.clientY;
      const initialX = item.transform.x;
      const initialY = item.transform.y;

      const onMove = (moveEvent: PointerEvent) => {
        if (!stageSize.width || !stageSize.height) return;
        const x = Math.min(150, Math.max(-50, initialX + ((moveEvent.clientX - startX) / stageSize.width) * 100));
        const y = Math.min(150, Math.max(-50, initialY + ((moveEvent.clientY - startY) / stageSize.height) * 100));
        onUpdateItem(item.id, { transform: { ...item.transform, x, y } });
      };

      trackPointerGesture(onMove, onEndTransform);
    },
    [onBeginTransform, onEndTransform, onSelectItem, onUpdateItem, stageSize.height, stageSize.width],
  );

  const beginResize = useCallback(
    (event: ReactPointerEvent, item: TimelineItem, corner: ResizeCorner) => {
      if (item.locked || event.button !== 0) return;
      event.preventDefault();
      event.stopPropagation();
      onBeginTransform();

      const startX = event.clientX;
      const startY = event.clientY;
      const initial = item.transform;
      const horizontalSign = corner.endsWith("e") ? 1 : -1;
      const verticalSign = corner.startsWith("s") ? 1 : -1;

      const onMove = (moveEvent: PointerEvent) => {
        if (!stageSize.width || !stageSize.height) return;
        const dx = ((moveEvent.clientX - startX) / stageSize.width) * 100;
        const dy = ((moveEvent.clientY - startY) / stageSize.height) * 100;
        const width = Math.max(4, initial.width + dx * horizontalSign);
        const height = Math.max(4, initial.height + dy * verticalSign);
        onUpdateItem(item.id, {
          transform: {
            ...initial,
            width,
            height,
            x: initial.x + (dx / 2),
            y: initial.y + (dy / 2),
          },
        });
      };

      trackPointerGesture(onMove, onEndTransform);
    },
    [onBeginTransform, onEndTransform, onUpdateItem, stageSize.height, stageSize.width],
  );

  const beginCrop = useCallback(
    (event: ReactPointerEvent, item: TimelineItem, edge: CropEdge) => {
      if (item.locked || event.button !== 0) return;
      event.preventDefault();
      event.stopPropagation();
      onBeginTransform();
      const initialClientX = event.clientX;
      const initialClientY = event.clientY;
      const initial = item.crop;
      const frameWidth = Math.max(1, stageSize.width * item.transform.width / 100);
      const frameHeight = Math.max(1, stageSize.height * item.transform.height / 100);

      const onMove = (moveEvent: PointerEvent) => {
        const dx = ((moveEvent.clientX - initialClientX) / frameWidth) * 100;
        const dy = ((moveEvent.clientY - initialClientY) / frameHeight) * 100;
        const crop = { ...initial };
        if (edge === "left") crop.left = Math.min(94 - crop.right, Math.max(0, initial.left + dx));
        if (edge === "right") crop.right = Math.min(94 - crop.left, Math.max(0, initial.right - dx));
        if (edge === "top") crop.top = Math.min(94 - crop.bottom, Math.max(0, initial.top + dy));
        if (edge === "bottom") crop.bottom = Math.min(94 - crop.top, Math.max(0, initial.bottom - dy));
        onUpdateItem(item.id, { crop });
      };

      trackPointerGesture(onMove, onEndTransform);
    },
    [onBeginTransform, onEndTransform, onUpdateItem, stageSize.height, stageSize.width],
  );

  const visualItems = project.items.filter(
    (item) => item.type !== "audio" && isItemActive(item, currentTime),
  );
  const audioItems = project.items.filter(
    (item) => item.type === "audio" && isItemActive(item, currentTime),
  );

  return (
    <div
      ref={hostRef}
      className="relative flex h-full min-h-0 w-full items-center justify-center overflow-hidden bg-[#0a0a0a] p-5"
      onPointerDown={() => onSelectItem(null)}
    >
      <div
        className="relative shrink-0 overflow-hidden shadow-[0_20px_60px_rgba(0,0,0,0.5)] ring-1 ring-white/10"
        style={{
          width: stageSize.width,
          height: stageSize.height,
          background: project.canvas.background,
        }}
        onPointerDown={(event) => event.stopPropagation()}
      >
        <div className="pointer-events-none absolute inset-x-0 top-1/2 z-[80] h-px bg-cyan-300/0" />
        <div className="pointer-events-none absolute inset-y-0 left-1/2 z-[80] w-px bg-cyan-300/0" />

        {visualItems.map((item, index) => {
          const asset = item.assetId ? assetMap.get(item.assetId) : undefined;
          const selected = item.id === selectedItemId;
          const frameStyle: CSSProperties = {
            left: `${item.transform.x}%`,
            top: `${item.transform.y}%`,
            width: `${item.transform.width}%`,
            height: `${item.transform.height}%`,
            opacity: (item.opacity / 100) * itemEnvelope(item, currentTime),
            transform: `translate(-50%, -50%) rotate(${item.transform.rotation}deg)`,
            zIndex: selected ? 90 : index + 1,
            mixBlendMode: item.blendMode as CSSProperties["mixBlendMode"],
          };
          const textValue = item.type === "caption" ? captionTextAtTime(item, currentTime) : item.text?.content;

          return (
            <div
              key={item.id}
              className={cn(
                "absolute touch-none",
                selected && "z-[90]",
                item.locked ? "cursor-not-allowed" : "cursor-move",
              )}
              style={frameStyle}
              onPointerDown={(event) => beginDrag(event, item)}
              onDoubleClick={() => onSelectItem(item.id)}
            >
              <div
                className={cn(
                  "relative h-full w-full overflow-hidden",
                  item.shape?.kind === "circle" && "rounded-full",
                )}
              >
                {(item.type === "video" || item.type === "image") && asset ? (
                  item.type === "video" ? (
                    <video
                      ref={(element) => setMediaRef(item.id, element)}
                      src={asset.url}
                      playsInline
                      preload="auto"
                      onLoadedMetadata={(event) => {
                        const element = event.currentTarget;
                        const sourceTime = itemSourceTime(item, currentTime);
                        element.currentTime = Math.min(Math.max(0, sourceTime), Math.max(0, element.duration - 0.01));
                      }}
                      className="pointer-events-none absolute max-w-none object-fill"
                      style={{ ...cropStyle(item), filter: mediaFilter(item, stageSize.width / project.canvas.width) }}
                    />
                  ) : (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img
                      src={asset.url}
                      alt=""
                      draggable={false}
                      className="pointer-events-none absolute max-w-none object-fill"
                      style={{ ...cropStyle(item), filter: mediaFilter(item, stageSize.width / project.canvas.width) }}
                    />
                  )
                ) : null}

                {(item.type === "text" || item.type === "caption") && item.text ? (
                  <div
                    className="flex h-full w-full whitespace-pre-wrap px-[3%] py-[2%]"
                    style={{
                      alignItems: "center",
                      justifyContent:
                        item.text.align === "left"
                          ? "flex-start"
                          : item.text.align === "right"
                            ? "flex-end"
                            : "center",
                      color: item.text.color,
                      backgroundColor: item.text.backgroundColor,
                      fontFamily: `${item.text.fontFamily}, Arial, ui-sans-serif, sans-serif`,
                      fontSize: `${item.text.fontSize * (stageSize.width / project.canvas.width)}px`,
                      fontWeight: item.text.fontWeight,
                      letterSpacing: `${item.text.letterSpacing * (stageSize.width / project.canvas.width)}px`,
                      lineHeight: item.text.lineHeight,
                      textAlign: item.text.align,
                      WebkitTextStroke: item.text.strokeWidth
                        ? `${item.text.strokeWidth * (stageSize.width / project.canvas.width)}px ${item.text.strokeColor}`
                        : undefined,
                      textShadow: `0 ${2 * (stageSize.width / project.canvas.width)}px ${12 * (stageSize.width / project.canvas.width)}px rgba(0,0,0,.32)`,
                    }}
                  >
                    {textValue}
                  </div>
                ) : null}

                {item.type === "shape" && item.shape ? (
                  <div
                    className="h-full w-full"
                    style={{
                      background: item.shape.fill,
                      borderRadius: item.shape.kind === "circle"
                        ? "9999px"
                        : `${item.shape.borderRadius * (stageSize.width / project.canvas.width)}px`,
                    }}
                  />
                ) : null}
              </div>

              {selected ? (
                <div className={cn(
                  "pointer-events-none absolute -inset-1 border shadow-[0_0_0_1px_rgba(0,0,0,.5)]",
                  cropMode && (item.type === "video" || item.type === "image") ? "border-dashed border-white" : "border-white",
                )}>
                  {cropMode && (item.type === "video" || item.type === "image") ? (
                    <>
                      <div className="absolute inset-x-1/3 inset-y-0 border-x border-white/35" />
                      <div className="absolute inset-x-0 inset-y-1/3 border-y border-white/35" />
                      {(["top", "right", "bottom", "left"] as CropEdge[]).map((edge) => (
                        <button
                          key={edge}
                          type="button"
                          aria-label={`Crop ${edge}`}
                          className={cn(
                            "pointer-events-auto absolute rounded-sm bg-white shadow",
                            (edge === "top" || edge === "bottom") && "left-1/2 h-2 w-10 -translate-x-1/2 cursor-ns-resize",
                            edge === "top" && "-top-[5px]",
                            edge === "bottom" && "-bottom-[5px]",
                            (edge === "left" || edge === "right") && "top-1/2 h-10 w-2 -translate-y-1/2 cursor-ew-resize",
                            edge === "left" && "-left-[5px]",
                            edge === "right" && "-right-[5px]",
                          )}
                          onPointerDown={(event) => beginCrop(event, item, edge)}
                        />
                      ))}
                    </>
                  ) : (["nw", "ne", "sw", "se"] as ResizeCorner[]).map((corner) => (
                    <button
                      key={corner}
                      type="button"
                      aria-label={`Resize ${corner}`}
                      className={cn(
                        "pointer-events-auto absolute size-3 rounded-[3px] border border-black/40 bg-white shadow",
                        corner.includes("n") ? "-top-2" : "-bottom-2",
                        corner.includes("w") ? "-left-2" : "-right-2",
                        corner === "nw" || corner === "se" ? "cursor-nwse-resize" : "cursor-nesw-resize",
                      )}
                      onPointerDown={(event) => beginResize(event, item, corner)}
                    />
                  ))}
                </div>
              ) : null}
            </div>
          );
        })}

        <div className="absolute size-0 overflow-hidden" aria-hidden="true">
          {audioItems.map((item) => {
            const asset = item.assetId ? assetMap.get(item.assetId) : undefined;
            return asset ? (
              <audio
                key={item.id}
                ref={(element) => setMediaRef(item.id, element)}
                src={asset.url}
                preload="auto"
                onLoadedMetadata={(event) => {
                  const element = event.currentTarget;
                  const sourceTime = itemSourceTime(item, currentTime);
                  element.currentTime = Math.min(Math.max(0, sourceTime), Math.max(0, element.duration - 0.01));
                }}
              />
            ) : null;
          })}
        </div>
      </div>
    </div>
  );
}
