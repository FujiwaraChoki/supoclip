"use client";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
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
  documentNeedsFx,
  editDuration,
  finalDuration,
  footageKey,
  footageUrl,
  frameRect,
  fxWarp,
  locate,
  mappedFx,
  outputSize,
  outputTime,
  timecode,
  type BRollFootage,
  type EditDocument,
  type EditorState,
} from "@/lib/editor/document";
import {
  drawEditorFrame,
  loadEditorFont,
  loadEditorFxResources,
  type BRollLayer,
  type EditorFxFrame,
  type EditorFxSession,
  type EditorMediaFrame,
  type TextDraft,
} from "@/lib/editor/render";
import { playPreview } from "@/lib/editor/playback";
import { FRAME, type ReactionCue } from "@/lib/fx/core/types";
import { isNontrivial, pieceLength, toEdit, toOutput, type Warp } from "@/lib/fx/core/warp";
import { storedCueId } from "@/lib/fx/studio-scene";
import type { InspectorTab } from "./inspector";
import { cn } from "@/lib/utils";
import { Panel, ToolButton } from "./studio-ui";
import { TextLayers } from "./text-layers";
import { usePreviewResolution } from "./use-preview-resolution";

/**
 * Dynamic-edit sound for the preview (SFX, music bed, bleeps), driven by the preview's final (F) clock. The preview
 * owns the AudioContext and the voice GainNode (clip volume); the controller schedules its own nodes on them.
 */
export interface PreviewFxAudio {
  /** The draft changed (cues, music, volume, mute). */
  setDocument(doc: EditDocument): void;
  /** Playback started or jumped: (re)schedule from final time `o`, advancing `rate` F seconds per wall second. */
  start(o: number, rate: number): void;
  /** Playback paused: silence and cancel everything scheduled. */
  stop(): void;
  dispose(): void;
}
export type PreviewFxAudioFactory = (
  audio: { context: AudioContext; destination: AudioNode; voice: GainNode },
  doc: EditDocument,
) => PreviewFxAudio;

/** One tick of the warped preview clock (pure; see warpPlaybackStep). */
export interface WarpStep {
  /** Final (F) seconds after this tick. */
  o: number;
  /** Edit (E) seconds the picture shows. */
  e: number;
  /** Edit seconds per final second at `o`: 1 normal, < 1 slow motion, 0 inside a freeze (hold). */
  rate: number;
  /** Segment and source time the video must show. */
  index: number;
  source: number;
  /** The video is more than a frame away from where it should be (or in the wrong segment). */
  seek: boolean;
  /** The clock reached the end of the finished clip. */
  ended: boolean;
}

const MAX_TICK = 0.25;

function pieceAt(warp: Warp, o: number) {
  return warp.pieces.find((p) => p.o0 <= o && o < p.o0 + pieceLength(p)) ?? null;
}

/**
 * The warped preview's master clock is final time F. Each tick advances F by wall time × the preview speed. While the
 * video plays a normal or slowed piece and sits within a frame of the clock, F follows the video (no needless seeks);
 * it never follows the video across a freeze, so holds always last their full length. Inside a speed piece the video
 * plays at `rate` × preview speed; inside a hold it is paused on the frozen frame. Drift over a frame re-seeks.
 */
export function warpPlaybackStep(
  doc: EditDocument,
  warp: Warp,
  o: number,
  dt: number,
  speed: number,
  video: { index: number; source: number } | null,
): WarpStep {
  let next = o + clamp(dt, 0, MAX_TICK) * speed;
  if (video && doc.segments.length) {
    const piece = pieceAt(warp, next);
    if (piece && piece.rate > 0) {
      const index = Math.min(video.index, doc.segments.length - 1);
      const fromVideo = toOutput(warp, outputTime(doc, index, video.source));
      if (pieceAt(warp, fromVideo) === piece && Math.abs(fromVideo - next) <= FRAME) next = fromVideo;
    }
  }
  const ended = next >= warp.outDuration - 1e-9;
  if (ended) next = warp.outDuration;
  const at = Math.max(0, Math.min(next, warp.outDuration - 1e-6));
  const piece = pieceAt(warp, at);
  const rate = piece ? piece.rate : 1;
  const e = toEdit(warp, at);
  const target = doc.segments.length ? locate(doc, e) : { index: 0, source: e };
  const seek = !!video && (video.index !== target.index
    || Math.abs(video.source - target.source) > (rate === 0 ? FRAME / 2 : FRAME));
  return { o: next, e, rate, index: target.index, source: target.source, seek, ended };
}

/** Edit time of a source time, preferring the given segment when it plays that footage. */
export function editTimeAt(doc: EditDocument, preferred: number, source: number): number {
  const fits = (i: number) => doc.segments[i] && source >= doc.segments[i].start - 1e-6 && source <= doc.segments[i].end + 1e-6;
  const index = fits(preferred) ? preferred : doc.segments.findIndex((_, i) => fits(i));
  return clamp(outputTime(doc, index < 0 ? Math.min(preferred, doc.segments.length - 1) : index, source), 0, editDuration(doc));
}

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
  /** Dynamic-edit sound for the preview (lib/fx/audio-preview). */
  fxAudio?: PreviewFxAudioFactory;
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
  fxAudio,
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
  // Dynamic edits: the person matte and reaction GIFs are hidden players synced like B-roll.
  const needsFx = documentNeedsFx(doc);
  const base: "layout" | "clean" = asset.hasLayout && doc.framing.fit === "auto" && doc.framing.aspect === "vertical" ? "layout" : "clean";
  const mapped = useMemo(() => (needsFx ? mappedFx(doc) : []), [doc, needsFx]);
  const matteUrl = asset.matteVersion && (!asset.faceTrack || asset.faceTrack.base === base)
    && mapped.some((cue) => cue.kind === "accent" && cue.variant === "behind")
    ? `${endpoint}/media/matte.mp4?v=${encodeURIComponent(asset.matteVersion)}` : null;
  const reactionIds = useMemo(() => [...new Set(mapped
    .filter((cue): cue is ReactionCue => cue.kind === "reaction" && !!cue.url && !!cue.asset_id)
    .map((cue) => storedCueId(cue.id)))].sort(), [mapped]);
  const reactionKey = reactionIds.join("\u0000");
  const matteRef = useRef<HTMLVideoElement>(null);
  const reactionPlayers = useRef(new Map<string, HTMLVideoElement>());
  const [matteReady, setMatteReady] = useState(false);
  const [readyReactions, setReadyReactions] = useState<string[]>([]);
  const readyKey = readyReactions.join("\u0000");
  const session = useMemo<EditorFxSession>(() => ({
    base,
    faceTrack: asset.faceTrack ?? null,
    matte: !!matteUrl && matteReady,
    reactions: readyKey ? readyKey.split("\u0000") : [],
  }), [base, asset.faceTrack, matteUrl, matteReady, readyKey]);
  useEffect(() => setMatteReady(false), [matteUrl]);
  useEffect(() => setReadyReactions((ready) => ready.filter((id) => reactionKey.split("\u0000").includes(id))), [reactionKey]);
  // The warped clock (final time F) and the dynamic-edit audio controller.
  const warpState = useRef({ playing: false, o: 0, last: 0, internalPause: false });
  const fxAudioRef = useRef<PreviewFxAudio | null>(null);
  const audioClock = useRef<{ o: number; at: number; rate: number } | null>(null);
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
    const warp = fxWarp(doc);
    const warped = isNontrivial(warp);
    if (warped ? warpState.current.playing : !video.paused) {
      warpState.current.playing = false;
      video.pause();
      setPlaying(false);
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
    if (fxAudio && !fxAudioRef.current && documentNeedsFx(doc)) {
      if (!audioRef.current) {
        // A silent clip still gets its SFX and music.
        const context = new AudioContext();
        const gain = context.createGain();
        gain.connect(context.destination);
        audioRef.current = { context, gain };
        await context.resume();
      }
      fxAudioRef.current = fxAudio(
        { context: audioRef.current.context, destination: audioRef.current.context.destination, voice: audioRef.current.gain },
        doc,
      );
    }
    const s = doc.segments[segmentIndex];
    if (warped) {
      const at = editTimeAt(doc, segmentIndex, video.currentTime);
      let o = toOutput(warp, at);
      if (o >= warp.outDuration - FRAME) {
        o = 0;
        video.currentTime = doc.segments[0].start;
        select(0);
      }
      warpState.current = { playing: true, o, last: performance.now(), internalPause: false };
      setPlaying(true);
      if (pieceAt(warp, o)?.rate !== 0) await video.play().catch(() => setMediaError(true));
      return;
    }
    if (video.currentTime < s.start || video.currentTime >= s.end - 0.01)
      video.currentTime = s.start;
    await playPreview(video, () => setMediaError(true));
  }, [asset.hasAudio, doc, fxAudio, segmentIndex, select, videoRef]);
  useEffect(() => {
    if (!needsFx) return;
    void loadEditorFxResources(doc).catch(() => {});
    // Fonts and the atlas load once per page; the caption font is the only per-draft part.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [needsFx, doc.captions.font]);
  useEffect(() => {
    fxAudioRef.current?.setDocument(doc);
    audioClock.current = null;
  }, [doc]);
  useEffect(() => () => {
    fxAudioRef.current?.dispose();
    fxAudioRef.current = null;
  }, []);
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
      // A jump while the warped clock runs restarts it from the new place.
      if (warpState.current.playing) {
        warpState.current.o = toOutput(fxWarp(doc), editTimeAt(doc, segmentIndex, seekRequest.time));
        warpState.current.last = performance.now();
      }
      audioClock.current = null;
    }
    // Only an explicit seek request moves the playhead.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [seekRequest, videoRef, onTime]);
  useEffect(
    () => () => {
      void audioRef.current?.context.close();
    },
    [],
  );
  useEffect(() => {
    let frame: number;
    const warp = fxWarp(doc);
    const warped = isNontrivial(warp);
    if (!warped && warpState.current.playing) warpState.current.playing = false;
    const draw = () => {
      const canvas = canvasRef.current,
        video = videoRef.current;
      if (canvas && video && video.readyState >= 2) {
        const segment = doc.segments[segmentIndex] ?? doc.segments[0];
        let index = Math.min(segmentIndex, doc.segments.length - 1);
        let o: number | undefined;
        const state = warpState.current;
        if (warped && state.playing) {
          const now = performance.now();
          const tick = warpPlaybackStep(doc, warp, state.o, (now - state.last) / 1000, rate,
            video.seeking ? null : { index, source: video.currentTime });
          state.last = now;
          if (tick.ended) {
            if (loop) {
              state.o = 0;
              video.currentTime = doc.segments[0].start;
              index = 0;
              if (segmentIndex !== 0) select(0);
              audioClock.current = null;
            } else {
              state.playing = false;
              state.internalPause = !video.paused;
              video.pause();
              setPlaying(false);
              state.o = warp.outDuration;
            }
          } else {
            state.o = tick.o;
            if (tick.index !== index) {
              index = tick.index;
              select(tick.index);
            }
            if (tick.seek) video.currentTime = tick.source;
            if (tick.rate === 0) {
              if (!video.paused) {
                state.internalPause = true;
                video.pause();
              }
            } else {
              video.playbackRate = clamp(tick.rate * rate, 0.0625, 16);
              video.preservesPitch = true;
              // An ended video would restart from 0 on play(): let the clock run out the last frames instead.
              if (video.paused && !video.ended) void video.play().catch(() => {});
            }
          }
          o = state.o;
        } else if (!video.paused && video.currentTime >= segment.end - 0.012) {
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
              draw: (x, y, w, h, target) => (target ?? context)?.drawImage(player, x, y, w, h),
            };
        }
        const time = outputTime(doc, index, video.currentTime);
        const fxFrame: EditorFxFrame | undefined = needsFx ? {
          o: warped ? o ?? toOutput(warp, time) : undefined,
          session,
          matte: syncMatte(matteRef.current, video),
          reaction: (cue) => syncReaction(reactionPlayers.current.get(storedCueId(cue.id)), cue, time, video),
        } : undefined;
        if (needsFx) {
          for (const [id, player] of reactionPlayers.current)
            if (!mapped.some((cue) => cue.kind === "reaction" && storedCueId(cue.id) === id && time >= cue.start && time <= cue.end) && !player.paused)
              player.pause();
          syncFxAudio(warped && state.playing ? state.o : toOutput(warp, time), warped ? state.playing : !video.paused);
        }
        if (context)
          drawEditorFrame(
            context,
            doc,
            video.videoWidth || asset.width,
            video.videoHeight || asset.height,
            warped && o !== undefined ? toEdit(warp, Math.min(o, warp.outDuration - 1e-6)) : time,
            (rect, target) =>
              target.drawImage(video, rect.x, rect.y, rect.width, rect.height),
            asset.captionLooks,
            layer,
            "none",
            fxFrame,
          );
      }
      frame = requestAnimationFrame(draw);
    };
    /** Keep the dynamic-edit audio scheduled from the F clock; reschedule on play, jumps and drift. */
    const syncFxAudio = (o: number, isPlaying: boolean) => {
      const audio = fxAudioRef.current;
      if (!audio) return;
      const now = performance.now();
      const anchor = audioClock.current;
      if (!isPlaying) {
        if (anchor) audio.stop();
        audioClock.current = null;
        return;
      }
      const expected = anchor ? anchor.o + ((now - anchor.at) / 1000) * anchor.rate : Number.NaN;
      if (!anchor || anchor.rate !== rate || !(Math.abs(expected - o) <= 0.12)) {
        audio.start(o, rate);
        audioClock.current = { o, at: now, rate };
      }
    };
    frame = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(frame);
  }, [
    asset.captionLooks,
    asset.height,
    asset.width,
    doc,
    loop,
    mapped,
    needsFx,
    rate,
    segmentIndex,
    select,
    session,
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
            onPlay={() => {
              if (!warpState.current.playing) setPlaying(true);
            }}
            onPause={(e) => {
              const state = warpState.current;
              // The warped clock pauses the video for freezes; any other pause stops playback.
              if (state.internalPause) {
                state.internalPause = false;
                return;
              }
              // The source ran out under a running warped clock (a slow-motion tail, a hold at the very end): the
              // clock finishes the clip and loops or stops itself.
              if (state.playing && e.currentTarget.ended) return;
              state.playing = false;
              setPlaying(false);
            }}
            onEnded={() => {
              if (warpState.current.playing) return;
              if (loop && videoRef.current) {
                select(0);
                videoRef.current.currentTime = doc.segments[0].start;
                void playPreview(videoRef.current, () => setMediaError(true));
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
          {matteUrl && (
            <video
              ref={matteRef}
              crossOrigin="anonymous"
              src={matteUrl}
              preload="auto"
              muted
              playsInline
              className="hidden"
              onLoadedData={() => setMatteReady(true)}
              onError={() => setMatteReady(false)}
            />
          )}
          {reactionIds.map((id) => (
            <video
              key={id}
              ref={(player) => {
                if (player) reactionPlayers.current.set(id, player);
                else reactionPlayers.current.delete(id);
              }}
              crossOrigin="anonymous"
              src={`${endpoint}/fx/reaction/${encodeURIComponent(id)}`}
              preload="auto"
              muted
              loop
              playsInline
              className="hidden"
              onLoadedData={() => setReadyReactions((ready) => (ready.includes(id) ? ready : [...ready, id].sort()))}
              onError={() => setReadyReactions((ready) => ready.filter((other) => other !== id))}
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
          <TextLayers doc={doc} asset={asset} time={time} index={segmentIndex} videoRef={videoRef} update={update} resolution={resolution} timelineDraft={textDraft} fxSession={session} />
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
                toOutput(fxWarp(doc), outputTime(
                  doc,
                  Math.min(segmentIndex, doc.segments.length - 1),
                  time,
                )),
                0,
                finalDuration(doc),
              ),
            )}
            <span className="font-normal text-muted-foreground max-sm:hidden">
              {" "}
              / {timecode(finalDuration(doc))}
            </span>
          </span>
        </div>
      </div>
      {footer}
    </Panel>
  );
}

/** The matte frame for the picture's source time, or null while it is out of step (the text then stays in front). */
function syncMatte(matte: HTMLVideoElement | null, video: HTMLVideoElement): EditorMediaFrame | null {
  if (!matte) return null;
  const wanted = video.currentTime;
  if (Math.abs(matte.currentTime - wanted) > (video.paused ? 0.02 : 0.08) && !matte.seeking) matte.currentTime = wanted;
  matte.playbackRate = video.playbackRate;
  if (!video.paused && matte.paused) void matte.play().catch(() => {});
  if (video.paused && !matte.paused) matte.pause();
  if (matte.readyState < 2 || matte.seeking || Math.abs(matte.currentTime - wanted) > 0.1) return null;
  return { source: matte, width: matte.videoWidth, height: matte.videoHeight };
}

/** A reaction GIF loops from the start of its cue, like the server's PiP. */
function syncReaction(player: HTMLVideoElement | undefined, cue: ReactionCue, time: number, video: HTMLVideoElement): EditorMediaFrame | null {
  if (!player) return null;
  const length = Number.isFinite(player.duration) && player.duration > 0 ? player.duration : Infinity;
  const wanted = (time - cue.start) % length;
  if (Math.abs(player.currentTime - wanted) > (video.paused ? 0.04 : 0.3) && !player.seeking) player.currentTime = wanted;
  if (!video.paused && player.paused) void player.play().catch(() => {});
  if (video.paused && !player.paused) player.pause();
  return player.readyState >= 2 ? { source: player, width: player.videoWidth, height: player.videoHeight } : null;
}
