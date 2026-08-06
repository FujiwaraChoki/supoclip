import React, { useEffect, useRef, useState } from "react";

const VERTICAL_OUTPUT_FORMATS = new Set(["vertical", "vertical_pan", "vertical_split"]);

/**
 * Maps a task's `output_format` to the CSS aspect-ratio the clip was rendered at.
 * Returns null for "original" and any unrecognized format, since those keep the
 * source dimensions and can be portrait, square, or an odd landscape — the
 * player measures those from the video itself instead.
 */
export function aspectRatioForOutputFormat(outputFormat?: string | null): string | null {
  if (!outputFormat) return "9 / 16";
  return VERTICAL_OUTPUT_FORMATS.has(outputFormat) ? "9 / 16" : null;
}

interface DynamicVideoPlayerProps {
  src: string;
  poster?: string;
  autoPlay?: boolean;
  muted?: boolean;
  loop?: boolean;
  className?: string;
  /** Task output format — resolved to an aspect ratio when `aspectRatio` is not given. */
  outputFormat?: string | null;
  /** Explicit CSS aspect-ratio, wins over `outputFormat`. */
  aspectRatio?: string;
  /** "fixed" keeps the tall standalone player, "fill" sizes to the container width. */
  sizing?: "fixed" | "fill";
}

const DynamicVideoPlayer: React.FC<DynamicVideoPlayerProps> = ({
  src,
  poster = "/placeholder-video.jpg",
  autoPlay = false,
  muted = false,
  loop = false,
  className = "",
  outputFormat,
  aspectRatio,
  sizing = "fixed",
}) => {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const [measuredRatio, setMeasuredRatio] = useState<string | null>(null);
  const declaredRatio = aspectRatio ?? aspectRatioForOutputFormat(outputFormat);
  const ratio = declaredRatio ?? measuredRatio ?? "16 / 9";

  useEffect(() => {
    setMeasuredRatio(null);
  }, [src]);

  const handleLoadedMetadata = () => {
    const video = videoRef.current;
    if (!video?.videoWidth || !video.videoHeight) return;
    setMeasuredRatio(`${video.videoWidth} / ${video.videoHeight}`);
  };

  return (
    <div
      className={`relative rounded-lg overflow-hidden ${className}`}
      style={{
        aspectRatio: ratio,
        ...(sizing === "fill" ? { width: "100%" } : { height: "min(70vh, 600px)" }),
      }}
    >
      <video
        ref={videoRef}
        controls
        preload="metadata"
        autoPlay={autoPlay}
        muted={muted}
        loop={loop}
        poster={poster}
        onLoadedMetadata={handleLoadedMetadata}
        className="absolute inset-0 w-full h-full object-contain"
        tabIndex={0}
        aria-label="Video player"
      >
        <source src={src} type="video/mp4" />
        Your browser does not support the video tag.
      </video>
    </div>
  );
};

export default DynamicVideoPlayer;
