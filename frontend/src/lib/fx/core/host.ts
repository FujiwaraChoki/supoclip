// fx-core host contract (§4.3, VERBATIM). The browser (lib/fx/browser-host.ts) and deno (fx_runtime/host.ts) implement
// FxHost; frameOps() never touches a host, rasterize() is the only code that draws.
export type Mat2D = [number, number, number, number, number, number];   // canvas setTransform(a,b,c,d,e,f)
export type Blend = "source-over" | "multiply" | "screen" | "lighter" | "overlay" | "soft-light"
  | "destination-in" | "difference" | "color";
export interface Shadow { color: string; blur: number; dx: number; dy: number }

/** Primitive draw op: the parity seam. frameOps() is pure and pixel-free; rasterize() is the only code that
 *  touches a canvas. Coordinates are output pixels; drawers author constants in u = min(W,H)/1080 units (§11). */
export type DrawOp =
  | { op: "picture"; transform: Mat2D; filter: string; alpha: number; blend: Blend;
      channel: "rgb" | "r" | "g" | "b"; dx: number; dy: number; cutout: boolean;
      clip: [number, number, number, number] | null }           // base picture (video + B-roll + PiP); cutout = alpha-masked person
  | { op: "text"; text: string; font: string; px: number; x: number; y: number; align: "left" | "center" | "right";
      fill: string | null; stroke: string | null; strokePx: number; join: "round" | "miter"; shadow: Shadow | null;
      letterSpacing: number; transform: Mat2D; alpha: number; blend: Blend;
      clip?: [number, number, number, number] | null }           // clip: output-px band (glitch slices), not transformed
  | { op: "path"; points: number[]; closed: boolean; smooth: boolean; stroke: string | null; strokePx: number;
      fill: string | null; cap: "round" | "butt"; dash: [number, number] | null; dashOffset: number;
      transform: Mat2D; alpha: number; blend: Blend;
      shadow?: Shadow | null }                                    // points = [x0,y0,x1,y1,...]; shadow: cast by the fill
  | { op: "rect"; x: number; y: number; w: number; h: number; radius: number; fill: string | null;
      stroke: string | null; strokePx: number; transform: Mat2D; alpha: number; blend: Blend;
      shadow?: Shadow | null }                                    // cast by the fill (the hook title's card)
  | { op: "sprite"; key: string; x: number; y: number; w: number; h: number; transform: Mat2D; alpha: number; blend: Blend }
  | { op: "gradient"; kind: "linear" | "radial"; x0: number; y0: number; r0: number; x1: number; y1: number;
      r1: number; stops: [number, string][]; rect: [number, number, number, number]; alpha: number; blend: Blend }
  | { op: "grain"; amount: number; seed: number; frame: number }
  | { op: "scanlines"; alpha: number; spacing: number; offset: number }
  | { op: "pixelate"; x: number; y: number; w: number; h: number; block: number }
  | { op: "frame0"; alpha: number };                              // cached first output frame (loop crossfade)

export interface FxImage { readonly width: number; readonly height: number }
export interface FxCanvas { width: number; height: number; getContext(kind: "2d"): FxContext2D }
/** The subset of CanvasRenderingContext2D fx-core may use (both Chrome and @napi-rs/canvas implement it). */
export type FxContext2D = Pick<CanvasRenderingContext2D,
  "save" | "restore" | "setTransform" | "transform" | "translate" | "rotate" | "scale" | "beginPath" | "closePath"
  | "moveTo" | "lineTo" | "quadraticCurveTo" | "bezierCurveTo" | "arc" | "ellipse" | "rect" | "roundRect" | "fill"
  | "stroke" | "clip" | "fillRect" | "clearRect" | "fillText" | "strokeText" | "measureText" | "drawImage"
  | "getImageData" | "putImageData" | "createLinearGradient" | "createRadialGradient" | "setLineDash">
  & { globalAlpha: number; globalCompositeOperation: string; filter: string; font: string; fillStyle: unknown;
      strokeStyle: unknown; lineWidth: number; lineJoin: CanvasLineJoin; lineCap: CanvasLineCap; lineDashOffset: number;
      shadowColor: string; shadowBlur: number; shadowOffsetX: number; shadowOffsetY: number; textAlign: CanvasTextAlign;
      textBaseline: CanvasTextBaseline; letterSpacing?: string; imageSmoothingEnabled: boolean;
      imageSmoothingQuality: ImageSmoothingQuality };

export interface EmojiAtlas { image: FxImage; cell: number; glyphs: Record<string, { x: number; y: number }> }

export interface FxHost {
  createCanvas(w: number, h: number): FxCanvas;
  atlas(): EmojiAtlas | null;                 // null until loaded (browser); drawers skip emoji then
  fontFamily(stem: string): string;           // CSS family for a bundled font stem; "FX-<stem>" on both hosts
  supportsFilter: boolean;                    // ctx.filter usable; false → grade via gradeMatrix ImageData path
  /** Draw the base picture (P1 frame on the server; video + B-roll + reaction PiP in the browser) into ctx with
   *  the op's transform/filter. cutout=true draws only the person (input alpha on server; matte video in browser). */
  drawPicture(ctx: FxContext2D, op: Extract<DrawOp, { op: "picture" }>): void;
  frame0(): FxImage | null;                   // first output frame, cached by the host (loop crossfade)
}
