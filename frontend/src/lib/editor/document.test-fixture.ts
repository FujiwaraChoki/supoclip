import type { EditDocument } from "./document";
export const draft: EditDocument = {
  version: 1,
  segments: [{ id: "s", start: 0, end: 3 }],
  words: [
    { id: "a", start: 0, end: 0.5, text: "Hello", highlight: false },
    { id: "b", start: 1, end: 1.5, text: "world", highlight: true },
    { id: "c", start: 2, end: 2.5, text: "today", highlight: false },
  ],
  captions: {
    enabled: true,
    font: "TikTokSans-Regular",
    size: 54,
    color: "#FFFFFF",
    accent: "#FACC15",
    y: 0.78,
    background: true,
    wordsPerLine: 4,
  },
  framing: { aspect: "vertical", fit: "cover", zoom: 1, x: 0.5, y: 0.5 },
  effects: { brightness: 100, contrast: 100, saturation: 100, blur: 0, hue: 0 },
  volume: 1,
  muted: false,
};
