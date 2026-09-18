import { downloadBlob } from "@/lib/clip-actions";
import {
  editDuration,
  outputSize,
  type EditDocument,
  type EditorState,
} from "./document";
import { drawEditorFrame, loadEditorFont } from "./render";

export async function exportDraft(
  url: string,
  doc: EditDocument,
  asset: EditorState,
  preset: string,
  signal: AbortSignal,
  progress: (value: number) => void,
) {
  const m = await import("mediabunny");
  const size = outputSize(doc, asset.width, asset.height);
  const bitrate = preset === "reels" ? 12_000_000 : 10_000_000;
  const audioCodec = (await m.canEncodeAudio("aac"))
    ? "aac"
    : (await m.canEncodeAudio("opus"))
      ? "opus"
      : null;
  if (
    !(await m.canEncodeVideo("avc", { ...size, bitrate })) ||
    (asset.hasAudio && !audioCodec)
  )
    throw new Error(
      "This browser cannot encode this video. Choose background export to render it on the server.",
    );
  await loadEditorFont(doc.captions.font);
  const response = await fetch(url, { signal });
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
  try {
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
    if (audio && !audioCodec)
      throw new Error("Audio encoding is unavailable. Use background export.");
    const audioSource =
      audio && audioCodec
        ? new m.AudioSampleSource({ codec: audioCodec, bitrate: 192_000 })
        : null;
    if (audioSource) output.addAudioTrack(audioSource);
    await output.start();
    const duration = editDuration(doc);
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
            drawEditorFrame(
              ctx,
              doc,
              sample.displayWidth,
              sample.displayHeight,
              at,
              (rect) =>
                sample.draw(ctx, rect.x, rect.y, rect.width, rect.height),
            );
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
    // Await both producers before releasing input/output, including on failure.
    const outcomes = await Promise.allSettled([renderVideo(), renderAudio()]);
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
  }
}
