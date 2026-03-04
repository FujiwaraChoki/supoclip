import React, { useRef } from "react";

interface DynamicVideoPlayerProps {
  src: string;
  poster?: string;
  autoPlay?: boolean;
  muted?: boolean;
  loop?: boolean;
  className?: string;
}

const DynamicVideoPlayer: React.FC<DynamicVideoPlayerProps> = ({
  src,
  poster = "/placeholder-video.jpg",
  autoPlay = false,
  muted = false,
  loop = false,
  className = "",
}) => {
  const videoRef = useRef<HTMLVideoElement | null>(null);

  return (
    <div
      className={`relative rounded-lg overflow-hidden ${className}`}
      style={{ height: "min(70vh, 600px)", aspectRatio: "9 / 16" }}
    >
      <video
        ref={videoRef}
        controls
        autoPlay={autoPlay}
        muted={muted}
        loop={loop}
        poster={poster}
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
