import React, { useRef, useState } from "react";
import { Button } from "@/components/ui/button";

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
  poster,
  autoPlay = false,
  muted = false,
  loop = false,
  className = "",
}) => {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const [failedSrc, setFailedSrc] = useState<string | null>(null);

  return (
    <div
      className={`relative rounded-lg overflow-hidden ${className}`}
      style={{ height: "min(70vh, 600px)", maxWidth: "calc(100vw - 4rem)", aspectRatio: "9 / 16" }}
    >
      <video
        key={src}
        ref={videoRef}
        src={src}
        onError={() => setFailedSrc(src)}
        onLoadedData={() => setFailedSrc(null)}
        controls
        autoPlay={autoPlay}
        muted={muted}
        loop={loop}
        poster={poster}
        className="absolute inset-0 w-full h-full object-contain"
        tabIndex={0}
        aria-label="Video player"
      >
        Your browser does not support the video tag.
      </video>
      {failedSrc === src && <div role="alert" className="absolute inset-0 flex flex-col items-center justify-center gap-3 bg-black/90 px-4 text-center text-sm text-white">
        <p>Couldn&apos;t load this clip.</p>
        <Button variant="secondary" size="sm" onClick={() => { setFailedSrc(null); videoRef.current?.load(); }}>Retry playback</Button>
      </div>}
    </div>
  );
};

export default DynamicVideoPlayer;
