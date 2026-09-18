import { EXPORT_PRESETS, downloadBlob, getClipUrl } from "@/lib/clip-actions";
import { formatSupportMessage, parseApiError } from "@/lib/api-error";
import { containRect, videoFilter, type VideoFx } from "./video-effects";

/** Captions are already rendered by the backend. Never overlay a second copy. */
export async function exportVideo({ clip, presetId, trimRange, videoFx, volume, muted, onProgress }: {
  clip: { video_url: string; filename: string };
  presetId: string;
  trimRange: [number, number];
  videoFx: VideoFx;
  volume: number;
  muted: boolean;
  onProgress: (progress: number) => void;
}) {
  const response = await fetch(getClipUrl(clip.video_url, clip.filename));
  if (!response.ok) throw new Error(formatSupportMessage(await parseApiError(response, "Could not load the clip for export.")));
  const { Input, Output, Conversion, ALL_FORMATS, BlobSource, BufferTarget, Mp4OutputFormat, AudioSample } = await import("mediabunny");
  const preset = EXPORT_PRESETS.find((item) => item.id === presetId) ?? EXPORT_PRESETS[0];
  const input = new Input({ source: new BlobSource(await response.blob()), formats: ALL_FORMATS });
  const output = new Output({ format: new Mp4OutputFormat(), target: new BufferTarget() });
  const canvas = document.createElement("canvas");
  canvas.width = preset.width;
  canvas.height = preset.height;
  const context = canvas.getContext("2d");
  if (!context) { input.dispose(); throw new Error("Your browser could not create the export canvas."); }
  try {
    const conversion = await Conversion.init({
      input, output, trim: { start: trimRange[0], end: trimRange[1] },
      video: {
        forceTranscode: true, bitrate: preset.bitrate,
        process: (sample) => {
          const rect = containRect(sample.displayWidth, sample.displayHeight, preset.width, preset.height);
          context.fillStyle = "black";
          context.fillRect(0, 0, preset.width, preset.height);
          context.save();
          context.filter = videoFilter(videoFx);
          context.translate(preset.width / 2, preset.height / 2);
          context.scale(videoFx.zoom, videoFx.zoom);
          context.translate(-preset.width / 2, -preset.height / 2);
          sample.draw(context, rect.x, rect.y, rect.width, rect.height);
          context.restore();
          return canvas;
        },
      },
      audio: {
        forceTranscode: true, bitrate: 192_000,
        process: (sample) => {
          if (!muted && volume === 100) return sample;
          const gain = muted ? 0 : volume / 100;
          const data = new Float32Array(sample.allocationSize({ planeIndex: 0, format: "f32" }) / 4);
          sample.copyTo(data, { planeIndex: 0, format: "f32" });
          for (let i = 0; i < data.length; i++) data[i] *= gain;
          return new AudioSample({ data, format: "f32", numberOfChannels: sample.numberOfChannels,
            sampleRate: sample.sampleRate, timestamp: sample.timestamp });
        },
      },
    });
    if (!conversion.isValid || conversion.discardedTracks.length > 0) {
      throw new Error("This browser cannot export all of this clip's tracks. Download the original from the generation page or use a browser with video encoding support.");
    }
    conversion.onProgress = (value) => onProgress(Math.round(value * 100));
    await conversion.execute();
    if (!output.target.buffer) throw new Error("Export finished without a video. Please try again.");
    downloadBlob(new Blob([output.target.buffer], { type: "video/mp4" }), `${clip.filename.replace(/\.mp4$/i, "")}_${preset.id}.mp4`);
  } finally { input.dispose(); }
}
