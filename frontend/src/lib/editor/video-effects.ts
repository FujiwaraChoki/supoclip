export type VideoFx = {
  brightness: number; contrast: number; saturation: number;
  blur: number; hue: number; zoom: number;
};

export const DEFAULT_VIDEO_FX: VideoFx = {
  brightness: 100, contrast: 100, saturation: 100, blur: 0, hue: 0, zoom: 1,
};

export function videoFilter(fx: VideoFx, scale = 1) {
  return `brightness(${fx.brightness}%) contrast(${fx.contrast}%) saturate(${fx.saturation}%) blur(${fx.blur * scale}px) hue-rotate(${fx.hue}deg)`;
}

export function containRect(sourceWidth: number, sourceHeight: number, width: number, height: number) {
  const scale = Math.min(width / sourceWidth, height / sourceHeight);
  const drawWidth = sourceWidth * scale;
  const drawHeight = sourceHeight * scale;
  return { x: (width - drawWidth) / 2, y: (height - drawHeight) / 2, width: drawWidth, height: drawHeight };
}
