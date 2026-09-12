"use client";

import type { HookStyle } from "@/lib/hook-style";

type TemplateSummary = {
  id: string;
  name: string;
  font_family?: string;
  font_size?: number;
  font_color?: string;
};

/**
 * CSS approximation of the burned-in hook title (not a pixel-accurate render
 * of the ASS/ffmpeg output — real rendering depends on ffmpeg at generation
 * time). Gives an at-a-glance sense of font/color/position/animation choices.
 */
export function HookTitlePreview({
  style,
  captionTemplate,
  availableTemplates,
}: {
  style: HookStyle;
  captionTemplate: string;
  availableTemplates: TemplateSummary[];
}) {
  const template = availableTemplates.find((t) => t.id === captionTemplate);
  const fontFamily = style.hook_font_family ?? template?.font_family ?? "inherit";
  const fontColor = style.hook_font_color ?? template?.font_color ?? "#FFFFFF";
  const backgroundColor = style.hook_background_color ?? "transparent";
  const outlineColor = style.hook_stroke_color ?? "#000000";
  const scale = style.hook_font_size_scale ?? 0.82;
  const position = style.hook_position ?? "top";
  const animation = style.hook_animation ?? "fade_pop";
  const shadow = style.hook_shadow ?? true;

  const animationKey = `${animation}-${position}-${fontColor}-${backgroundColor}-${scale}`;
  const animationStyle: React.CSSProperties =
    animation === "none"
      ? {}
      : {
          animationName:
            animation === "slide_down" ? "hookSlideDown" : animation === "fade" ? "hookFade" : "hookFadePop",
          animationDuration: "0.4s",
          animationTimingFunction: "ease-out",
        };

  return (
    <div className="space-y-1.5">
      <label className="text-xs text-stone-500">Preview (approximate)</label>
      <div
        className="relative w-full aspect-[9/16] max-h-56 rounded-lg bg-stone-900 overflow-hidden flex"
        style={{
          alignItems: position === "top" ? "flex-start" : position === "bottom" ? "flex-end" : "center",
          justifyContent: "center",
          padding: "10% 6%",
        }}
      >
        <style>{`
          @keyframes hookFadePop { from { opacity: 0; transform: scale(0.9); } to { opacity: 1; transform: scale(1); } }
          @keyframes hookFade { from { opacity: 0; } to { opacity: 1; } }
          @keyframes hookSlideDown { from { opacity: 0; transform: scaleY(0.6); } to { opacity: 1; transform: scaleY(1); } }
        `}</style>
        <span
          key={animationKey}
          className="text-center font-bold leading-tight rounded px-2 py-1"
          style={{
            fontFamily,
            color: fontColor,
            backgroundColor,
            fontSize: `${Math.round(scale * 22)}px`,
            WebkitTextStroke: `1px ${outlineColor}`,
            textShadow: shadow ? "0 2px 4px rgba(0,0,0,0.6)" : "none",
            ...animationStyle,
          }}
        >
          This Changes Everything
        </span>
      </div>
    </div>
  );
}
