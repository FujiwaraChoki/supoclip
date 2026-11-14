import { downloadBlob } from "@/lib/clip-actions";
import { loadFxManifest, type FxAssetManifest } from "@/lib/fx/assets-client";
import {
  BROWSER_EXPORT_REFUSAL_MESSAGES,
  browserExportRefusal,
  documentAudioInput,
  fxAudioProgram,
  hasFxAudio,
  renderOfflineMix,
  type FxAudioProgram,
  type OfflineMixOptions,
} from "@/lib/fx/offline-mix";
import {
  clamp,
  documentNeedsFx,
  editDuration,
  footageKey,
  footageUrl,
  mappedBroll,
  outputSize,
  renderSegments,
  sequence,
  type EditDocument,
  type EditorState,
} from "./document";
import { drawEditorFrame, loadEditorFont, loadEditorFxResources, type BRollLayer, type EditorFxSession } from "./render";

/** Dynamic-edit documents export at a fixed 30 fps on the F clock, like the server's P2 (non-warped: E == F). */
export const FX_EXPORT_FPS = 30;
type VoiceBuffer = { channels: Float32Array[]; sampleRate: number };
/** Seconds of mixed audio per encoded AudioSample. */
const AUDIO_CHUNK_SECONDS = 1;


/**
 * Throws the "Use background export." message for documents the browser cannot export (§10, §14.2): speed ramps or
 * freezes (warp), reaction clips, or text behind the speaker. The export panel shows the same reason up front.
 */
export function assertBrowserExportable(doc: EditDocument) {
  const refusal = browserExportRefusal(doc);
  if (refusal) throw new Error(BROWSER_EXPORT_REFUSAL_MESSAGES[refusal]);
}

/**
 * The document's dynamic-edit audio program for the export, or null when it changes nothing audible. A manifest that
 * cannot be loaded drops the SFX and music (with a warning) but keeps bleeps, as the server skips a missing asset.
 */
export async function exportAudioProgram(
  doc: EditDocument,
  manifest: () => Promise<FxAssetManifest> = () => loadFxManifest(),
): Promise<FxAudioProgram | null> {
  const input = documentAudioInput(doc);
  if (!input) return null;
  let loaded: FxAssetManifest | null = null;
  const needsAssets = input.music !== null || input.cues.some((c) => c.kind === "sfx");
  if (needsAssets) {
    try {
      loaded = await manifest();
    } catch (error) {
      console.warn("fx_export_manifest_unavailable", error);
    }
  }
  const program = fxAudioProgram(input, loaded);
  return hasFxAudio(program) ? program : null;
}

/** The edit-time frame times (F == E) of a 30 fps export, grouped by the segment that plays them. */
export function fxFrameTimes(doc: EditDocument, fps = FX_EXPORT_FPS) {
  const total = Math.round(editDuration(doc) * fps);
  const items = sequence(doc);
  return items.map((item) => {
    const frames: { index: number; at: number; source: number }[] = [];
    const first = Math.max(0, Math.ceil(item.start * fps - 1e-6));
    const last = Math.min(total, Math.ceil(item.end * fps - 1e-6));
    for (let n = first; n < last; n++) {
      const at = n / fps;
      // Nudge inside the frame so float error never returns the previous decoded frame; stay inside the segment.
      const source = Math.min(item.segment.end - 1e-4, item.segment.start + (at - item.start) + 1e-4);
      frames.push({ index: n, at, source });
    }
    return { segment: item.segment, frames };
  });
}

export async function exportDraft(
  url: string,
  doc: EditDocument,
  asset: EditorState,
  preset: string,
  signal: AbortSignal,
  progress: (value: number) => void,
  mixOptions: OfflineMixOptions = {},
) {
  assertBrowserExportable(doc);
  // Preserve a continuous dissolve (and frame timestamps) through UI splits.
  doc = { ...doc, segments: renderSegments(doc.segments) };
  const m = await import("mediabunny");
  const size = outputSize(doc, asset.width, asset.height);
  const bitrate = preset === "reels" ? 12_000_000 : 10_000_000;
  const fxProgram = await exportAudioProgram(doc);
  const wantsAudio = asset.hasAudio || !!fxProgram;
  const audioCodec = (await m.canEncodeAudio("aac"))
    ? "aac"
    : (await m.canEncodeAudio("opus"))
      ? "opus"
      : null;
  if (
    !(await m.canEncodeVideo("avc", { ...size, bitrate })) ||
    (wantsAudio && !audioCodec)
  )
    throw new Error(
      "This browser cannot encode this video. Choose background export to render it on the server.",
    );
  await loadEditorFont(doc.captions.font);
  const fxFrames = documentNeedsFx(doc);
  if (fxFrames) {
    try {
      await loadEditorFxResources(doc);
    } catch {
      throw new Error("Could not load the edit fonts. Use background export.");
    }
  }
  const useLayout = !!asset.hasLayout && doc.framing.fit === "auto" && doc.framing.aspect === "vertical";
  const mediaUrl = useLayout ? url.replace(/clean\.mp4$/, "layout.mp4") : url;
  // Behind-the-speaker text and reactions are refused above, so no matte or GIF is needed here.
  const fxSession: EditorFxSession = { base: useLayout ? "layout" : "clean", faceTrack: asset.faceTrack ?? null, matte: false };
  const response = await fetch(mediaUrl, { signal });
  if (!response.ok) throw new Error("Could not load the source video.");
  const input = new m.Input({
    source: new m.BlobSource(await response.blob()),
    formats: m.ALL_FORMATS,
  });
  const output = new m.Output({
    format: new m.Mp4OutputFormat(),
    target: new m.BufferTarget(),
  });
  const canvas = document.createElement("canvas");
  canvas.width = size.width;
  canvas.height = size.height;
  const ctx = canvas.getContext("2d");
  if (!ctx) {
    input.dispose();
    throw new Error("Canvas is unavailable. Use background export.");
  }
  let finalized = false;
  // Stock footage is decoded alongside the clip and drawn under the captions.
  const pieces = mappedBroll(doc);
  const footage = new Map<string, { input: InstanceType<typeof m.Input>; sink: InstanceType<typeof m.VideoSampleSink> }>();
  const endpoint = url.replace(/\/media\/clean\.mp4$/, "");
  try {
    for (const piece of pieces) {
      const key = footageKey(piece.clip.footage);
      if (footage.has(key)) continue;
      const response = await fetch(footageUrl(endpoint, piece.clip.footage), { signal });
      if (!response.ok) throw new Error("Could not load B-roll footage. Use background export.");
      const footageInput = new m.Input({
        source: new m.BlobSource(await response.blob()),
        formats: m.ALL_FORMATS,
      });
      const track = await footageInput.getPrimaryVideoTrack();
      if (!track || !(await track.canDecode())) {
        footageInput.dispose();
        throw new Error("This browser cannot decode the B-roll. Use background export.");
      }
      footage.set(key, { input: footageInput, sink: new m.VideoSampleSink(track) });
    }
    const video = await input.getPrimaryVideoTrack(),
      audio = await input.getPrimaryAudioTrack();
    if (
      !video ||
      !(await video.canDecode()) ||
      (audio && !(await audio.canDecode()))
    )
      throw new Error(
        "This browser cannot decode the source. Use background export.",
      );
    const videoSource = new m.CanvasSource(canvas, { codec: "avc", bitrate });
    output.addVideoTrack(videoSource);
    if ((audio || fxProgram) && !audioCodec)
      throw new Error("Audio encoding is unavailable. Use background export.");
    const audioSource =
      (audio || fxProgram) && audioCodec
        ? new m.AudioSampleSource({ codec: audioCodec, bitrate: 192_000 })
        : null;
    if (audioSource) output.addAudioTrack(audioSource);
    await output.start();
    const duration = editDuration(doc);
    const brollLayer = async (at: number) => {
      const piece = pieces.find((p) => at >= p.start && at < p.end);
      const brollSample = piece
        ? await footage
            .get(footageKey(piece.clip.footage))
            ?.sink.getSample(piece.offset + at - piece.start)
        : null;
      let layer: BRollLayer | null = null;
      if (piece && brollSample) {
        const fade = piece.fadeDuration;
        const alpha = clamp(
          Math.min(
            piece.fadeIn && fade > 0 ? (at - piece.start) / fade : 1,
            piece.fadeOut && fade > 0 ? (piece.end - at) / fade : 1,
          ),
          0,
          1,
        );
        layer = {
          width: brollSample.displayWidth,
          height: brollSample.displayHeight,
          alpha,
          draw: (x, y, w, h, target) => brollSample.draw(target ?? ctx, x, y, w, h),
        };
      }
      return { layer, brollSample };
    };
    const renderVideo = async () => {
      let cursor = 0;
      for (const segment of doc.segments) {
        const sink = new m.VideoSampleSink(video);
        for await (const sample of sink.samples(segment.start, segment.end)) {
          try {
            signal.throwIfAborted();
            const start = Math.max(segment.start, sample.timestamp),
              end = Math.min(segment.end, sample.timestamp + sample.duration);
            if (end <= start) continue;
            const at = cursor + start - segment.start;
            const { layer, brollSample } = await brollLayer(at);
            try {
              drawEditorFrame(
                ctx,
                doc,
                sample.displayWidth,
                sample.displayHeight,
                at,
                (rect, target) =>
                  sample.draw(target, rect.x, rect.y, rect.width, rect.height),
                asset.captionLooks,
                layer,
              );
            } finally {
              brollSample?.close();
            }
            await videoSource.add(at, end - start);
            progress(
              Math.min(99, Math.round(((at + end - start) / duration) * 100)),
            );
          } finally {
            sample.close();
          }
        }
        cursor += segment.end - segment.start;
      }
      videoSource.close();
    };
    // fx-core animates per output frame (grain, shakes, counters): draw exactly one frame every 1/30 s of F.
    const renderFxVideo = async () => {
      const groups = fxFrameTimes(doc);
      const total = Math.max(1, groups.reduce((sum, g) => sum + g.frames.length, 0));
      let done = 0;
      for (const { frames } of groups) {
        if (!frames.length) continue;
        const sink = new m.VideoSampleSink(video);
        let k = 0;
        let last: InstanceType<typeof m.VideoSample> | null = null;
        try {
          for await (const sample of sink.samplesAtTimestamps(frames.map((f) => f.source))) {
            const frame = frames[k++];
            if (!frame) {
              sample?.close();
              continue;
            }
            signal.throwIfAborted();
            // A missing frame repeats the previous picture; before the first decodable one the picture is black, but
            // the frame (captions, effects) is still drawn so the export keeps exactly one frame per 1/30 s.
            if (sample && sample !== last) {
              last?.close();
              last = sample;
            }
            const picture = last;
            const { layer, brollSample } = await brollLayer(frame.at);
            try {
              drawEditorFrame(
                ctx,
                doc,
                picture?.displayWidth ?? asset.width,
                picture?.displayHeight ?? asset.height,
                frame.at,
                (rect, target) => picture?.draw(target, rect.x, rect.y, rect.width, rect.height),
                asset.captionLooks,
                layer,
                "all",
                { o: frame.at, session: fxSession },
              );
            } finally {
              brollSample?.close();
            }
            await videoSource.add(frame.at, 1 / FX_EXPORT_FPS);
            done += 1;
            progress(Math.min(99, Math.round((done / total) * 100)));
          }
        } finally {
          last?.close();
        }
      }
      videoSource.close();
    };
    const renderAudio = async () => {
      if (!audio || !audioSource) return;
      let cursor = 0;
      for (const segment of doc.segments) {
        const sink = new m.AudioSampleSink(audio);
        for await (const sample of sink.samples(segment.start, segment.end)) {
          try {
            signal.throwIfAborted();
            const startFrame = Math.max(
              0,
              Math.ceil((segment.start - sample.timestamp) * sample.sampleRate),
            );
            const endFrame = Math.min(
              sample.numberOfFrames,
              Math.floor((segment.end - sample.timestamp) * sample.sampleRate),
            );
            if (endFrame <= startFrame) continue;
            const full = new Float32Array(
              sample.allocationSize({ planeIndex: 0, format: "f32" }) / 4,
            );
            sample.copyTo(full, { planeIndex: 0, format: "f32" });
            const data = full.slice(
              startFrame * sample.numberOfChannels,
              endFrame * sample.numberOfChannels,
            );
            const gain = doc.muted ? 0 : doc.volume;
            for (let i = 0; i < data.length; i++) data[i] *= gain;
            const next = new m.AudioSample({
              data,
              format: "f32",
              numberOfChannels: sample.numberOfChannels,
              sampleRate: sample.sampleRate,
              timestamp:
                cursor +
                sample.timestamp +
                startFrame / sample.sampleRate -
                segment.start,
            });
            try {
              await audioSource.add(next);
            } finally {
              next.close();
            }
          } finally {
            sample.close();
          }
        }
        cursor += segment.end - segment.start;
      }
      audioSource.close();
    };
    // Dynamic-edit audio: the voice is gathered on the edit timeline, then mixed with SFX, bleeps and the ducked bed
    // in an OfflineAudioContext (§14.2). No loudness stage, like the server studio export.
    const renderFxAudio = async (program: FxAudioProgram) => {
      if (!audioSource) return;
      let voice: VoiceBuffer | null = null;
      if (audio) {
        let cursor = 0;
        for (const segment of doc.segments) {
          const sink = new m.AudioSampleSink(audio);
          for await (const sample of sink.samples(segment.start, segment.end)) {
            try {
              signal.throwIfAborted();
              const rate = sample.sampleRate;
              const startFrame = Math.max(0, Math.ceil((segment.start - sample.timestamp) * rate));
              const endFrame = Math.min(sample.numberOfFrames, Math.floor((segment.end - sample.timestamp) * rate));
              if (endFrame <= startFrame) continue;
              if (!voice) {
                const length = Math.ceil(duration * rate) + 1;
                voice = {
                  sampleRate: rate,
                  channels: Array.from({ length: sample.numberOfChannels }, () => new Float32Array(length)),
                };
              }
              const at = Math.round(
                (cursor + sample.timestamp + startFrame / rate - segment.start) * voice.sampleRate,
              );
              const count = Math.min(endFrame - startFrame, voice.channels[0].length - Math.max(0, at));
              if (count <= 0 || at < 0) continue;
              voice.channels.forEach((plane, c) => {
                const source = Math.min(c, sample.numberOfChannels - 1);
                sample.copyTo(plane.subarray(at, at + count), {
                  planeIndex: source,
                  format: "f32-planar",
                  frameOffset: startFrame,
                  frameCount: count,
                });
              });
            } finally {
              sample.close();
            }
          }
          cursor += segment.end - segment.start;
        }
      }
      const mixed = await renderOfflineMix(
        program,
        voice ? { ...(voice as VoiceBuffer), gain: doc.muted ? 0 : doc.volume } : null,
        { ...mixOptions, signal },
      );
      const [left, right] = mixed.channels;
      const chunk = Math.round(AUDIO_CHUNK_SECONDS * mixed.sampleRate);
      for (let from = 0; from < left.length; from += chunk) {
        signal.throwIfAborted();
        const n = Math.min(chunk, left.length - from);
        const data = new Float32Array(n * 2);
        data.set(left.subarray(from, from + n), 0);
        data.set(right.subarray(from, from + n), n);
        const next = new m.AudioSample({
          data,
          format: "f32-planar",
          numberOfChannels: 2,
          sampleRate: mixed.sampleRate,
          timestamp: from / mixed.sampleRate,
        });
        try {
          await audioSource.add(next);
        } finally {
          next.close();
        }
      }
      audioSource.close();
    };
    // Await both producers before releasing input/output, including on failure.
    const outcomes = await Promise.allSettled([
      fxFrames ? renderFxVideo() : renderVideo(),
      fxProgram ? renderFxAudio(fxProgram) : renderAudio(),
    ]);
    const failed = outcomes.find(
      (r): r is PromiseRejectedResult => r.status === "rejected",
    );
    if (failed) throw failed.reason;
    signal.throwIfAborted();
    await output.finalize();
    finalized = true;
    if (!output.target.buffer) throw new Error("Export produced no video.");
    signal.throwIfAborted();
    downloadBlob(
      new Blob([output.target.buffer], { type: "video/mp4" }),
      `supoclip-${preset}.mp4`,
    );
    progress(100);
  } finally {
    if (!finalized) await output.cancel().catch(() => {});
    input.dispose();
    for (const { input: footageInput } of footage.values()) footageInput.dispose();
  }
}
