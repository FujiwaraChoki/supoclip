"use client";
import { useEffect, useState, type RefObject } from "react";

/** Rasterize at screen pixels, including Retina screens and browser zoom. */
export function usePreviewResolution(frame: RefObject<HTMLDivElement | null>, output: { width: number; height: number }) {
  const [resolution, setResolution] = useState(output);
  useEffect(() => {
    const element = frame.current;
    if (!element) return;
    const refresh = () => {
      const rect = element.getBoundingClientRect();
      if (!rect.width) return;
      const width = Math.max(1, Math.min(output.width * 2, Math.round(rect.width * window.devicePixelRatio)));
      const height = Math.max(1, Math.round(width * output.height / output.width));
      setResolution(current => current.width === width && current.height === height ? current : { width, height });
    };
    const observer = new ResizeObserver(refresh);
    observer.observe(element);
    window.addEventListener("resize", refresh);
    refresh();
    return () => { observer.disconnect(); window.removeEventListener("resize", refresh); };
  }, [frame, output.width, output.height]);
  return resolution;
}
