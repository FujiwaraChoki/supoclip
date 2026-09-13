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
  overrideText,
  compact,
}: {
  style: HookStyle;
  captionTemplate: string;
  availableTemplates: TemplateSummary[];
  /** Show this hook text instead of the sample copy (used by the hook comparison UI). */
  overrideText?: string;
  /** Smaller preview box, for use inside per-variant comparison cards. */
  compact?: boolean;
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

  const highlightColor = style.hook_highlight_color ?? "#FFE000";

  const animationKey = `${animation}-${position}-${fontColor}-${backgroundColor}-${scale}`;
  const animationStyle: React.CSSProperties =
    animation === "none"
      ? {}
      : {
          animationName:
            animation === "slide_down"
              ? "hookSlideDown"
              : animation === "fade"
              ? "hookFade"
              : animation === "zoom_punch"
              ? "hookZoomPunch"
              : animation === "bounce"
              ? "hookBounce"
              : animation === "pulse"
              ? "hookPulse"
              : "hookFadePop",
          animationDuration: animation === "pulse" ? "1s" : "0.4s",
          animationTimingFunction: "ease-out",
          animationIterationCount: animation === "pulse" ? "infinite" : 1,
        };

  return (
    <div className="space-y-1.5">
      {!compact && <label className="text-xs text-stone-500">Preview (approximate)</label>}
      <div
        className={`relative w-full aspect-[9/16] rounded-lg bg-stone-900 overflow-hidden flex ${
          compact ? "max-h-40" : "max-h-56"
        }`}
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
          @keyframes hookZoomPunch { from { transform: scale(1); } to { transform: scale(1.1); } }
          @keyframes hookBounce {
            0% { opacity: 0; transform: scale(0.6); }
            60% { opacity: 1; transform: scale(1.12); }
            80% { transform: scale(0.94); }
            100% { transform: scale(1); }
          }
          @keyframes hookPulse {
            0%, 100% { opacity: 1; transform: scale(1); }
            50% { transform: scale(1.06); }
          }
        `}</style>
        <span
          key={animationKey}
          className="text-center font-bold leading-tight rounded-[3px]"
          style={{
            fontFamily,
            color: fontColor,
            backgroundColor,
            fontSize: `${Math.round(scale * 22)}px`,
            padding: backgroundColor === "transparent" ? 0 : "0.3em 0.3em",
            WebkitTextStroke: `1px ${outlineColor}`,
            textShadow: shadow ? "0 2px 4px rgba(0,0,0,0.6)" : "none",
            ...animationStyle,
          }}
        >
          {overrideText ? (
            overrideText
          ) : (
            <>
              This <span style={{ color: highlightColor }}>Changes</span> Everything
            </>
          )}
        </span>
      </div>
    </div>
  );
}
