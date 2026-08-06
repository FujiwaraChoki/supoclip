export const EDITOR_SCHEMA_VERSION = 1 as const;

export type AssetKind = "video" | "image" | "audio";
export type AssetSource = "generated" | "uploaded";

export interface Asset {
  id: string;
  name: string;
  kind: AssetKind;
  source: AssetSource;
  url: string;
  duration: number;
  width?: number;
  height?: number;
  sizeBytes?: number;
  mimeType?: string;
}

export type TimelineItemType =
  | "video"
  | "image"
  | "audio"
  | "text"
  | "caption"
  | "shape";

export type TimelineTrack = "main" | "overlay" | "text" | "audio";

export type BlendMode =
  | "normal"
  | "multiply"
  | "screen"
  | "overlay"
  | "darken"
  | "lighten";

export interface TimelineTransform {
  x: number;
  y: number;
  width: number;
  height: number;
  rotation: number;
}

export interface TimelineCrop {
  top: number;
  right: number;
  bottom: number;
  left: number;
}

export interface TimelineEffects {
  brightness: number;
  contrast: number;
  saturation: number;
  blur: number;
  hue: number;
}

export type TextAlign = "left" | "center" | "right";

export interface TimelineText {
  content: string;
  fontFamily: string;
  fontSize: number;
  fontWeight: number;
  color: string;
  backgroundColor: string;
  align: TextAlign;
  letterSpacing: number;
  lineHeight: number;
  strokeColor: string;
  strokeWidth: number;
}

export interface TimelineShape {
  kind: "rectangle" | "circle" | "line";
  fill: string;
  borderRadius: number;
}

export interface TimelineItem {
  id: string;
  assetId?: string;
  type: TimelineItemType;
  name: string;
  track: TimelineTrack;
  start: number;
  duration: number;
  trimStart: number;
  speed: number;
  volume: number;
  muted: boolean;
  hidden: boolean;
  locked: boolean;
  opacity: number;
  blendMode: BlendMode;
  transform: TimelineTransform;
  crop: TimelineCrop;
  effects: TimelineEffects;
  text?: TimelineText;
  shape?: TimelineShape;
  fadeIn: number;
  fadeOut: number;
}

export interface ProjectCanvas {
  width: number;
  height: number;
  background: string;
  fps: number;
}

export interface Project {
  schemaVersion: typeof EDITOR_SCHEMA_VERSION;
  id: string;
  name: string;
  taskId: string;
  version: number;
  canvas: ProjectCanvas;
  duration: number;
  items: TimelineItem[];
}

// Compatibility names used by the editor UI components. The shorter aliases
// remain the canonical persisted schema names.
export type EditorAsset = Asset;
export type EditorProject = Project;
