"use client";
import { useEffect, useRef, useState } from "react";

// make_assets pads each of its twelve thumbnails to 160 × 90 before tiling.
const FRAME_COUNT = 12;
const FRAME_ASPECT = 160 / 90;

/** Repeat correctly proportioned frames, sampling the segment's source range. */
export function Filmstrip({ url, start, end, duration }: {
  url: string;
  start: number;
  end: number;
  duration: number;
}) {
  const container = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ width: 0, height: 0 });
  useEffect(() => {
    if (!container.current) return;
    const observer = new ResizeObserver(([entry]) => {
      const { width, height } = entry.contentRect;
      setSize(current => current.width === width && current.height === height ? current : { width, height });
    });
    observer.observe(container.current);
    return () => observer.disconnect();
  }, []);
  const width = size.height * FRAME_ASPECT;
  const count = width > 0 ? Math.ceil(size.width / width) : 0;
  return (
    <div ref={container} aria-hidden className="pointer-events-none absolute inset-0 flex overflow-hidden">
      {Array.from({ length: count }, (_, index) => {
        const time = start + (index * width / Math.max(1, size.width)) * (end - start);
        const frame = Math.max(0, Math.min(FRAME_COUNT - 1, Math.floor(time / Math.max(0.01, duration) * FRAME_COUNT)));
        return <span key={index} className="h-full shrink-0 bg-no-repeat" style={{
          width,
          backgroundImage: `url(${url})`,
          backgroundSize: `${FRAME_COUNT * 100}% 100%`,
          backgroundPosition: `${frame / (FRAME_COUNT - 1) * 100}% 0`,
        }} />;
      })}
    </div>
  );
}
