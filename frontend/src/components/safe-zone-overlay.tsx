"use client";

import {
  PLATFORM_SAFE_ZONES,
  SAFE_ZONE_PLATFORM_IDS,
  intersectionInsets,
  type SafeZoneSelection,
} from "@/lib/safe-zones";

interface SafeZoneOverlayProps {
  selection: SafeZoneSelection;
}

/**
 * Non-interactive guide showing where a platform's own UI (username, caption,
 * like/share rail, nav) will sit over the exported frame. Preview-only — this
 * never gets burned into the render.
 *
 * Follows DESIGN.md's locked 4-color palette: no fills or alpha, just ink
 * (warning/unsafe boundary) and teal (safe-area boundary) hairlines, per the
 * project's "warning is weight, not a hue" convention.
 */
export function SafeZoneOverlay({ selection }: SafeZoneOverlayProps) {
  const platforms =
    selection === "all"
      ? SAFE_ZONE_PLATFORM_IDS.map((id) => PLATFORM_SAFE_ZONES[id])
      : [PLATFORM_SAFE_ZONES[selection]];

  const commonInsets = selection === "all" ? intersectionInsets() : null;

  return (
    <svg
      className="absolute inset-0 w-full h-full pointer-events-none"
      viewBox="0 0 100 100"
      preserveAspectRatio="none"
      aria-hidden="true"
    >
      {platforms.map((platform, index) => {
        const { top, bottom, left, right } = platform.insets;
        const x = left;
        const y = top;
        const w = Math.max(0, 100 - left - right);
        const h = Math.max(0, 100 - top - bottom);
        return (
          <g key={platform.id}>
            <rect
              x={x}
              y={y}
              width={w}
              height={h}
              fill="none"
              stroke="var(--color-ink)"
              strokeWidth={0.3}
              strokeDasharray="1.5 1.2"
              vectorEffect="non-scaling-stroke"
            />
            {selection === "all" && (
              <text
                x={x + 1}
                y={y + 3 + index * 3.2}
                fontSize={2.6}
                fill="var(--color-ink)"
                fontFamily="var(--font-sans, sans-serif)"
              >
                {platform.label}
              </text>
            )}
            {selection !== "all" && (
              <text
                x={x + 1.5}
                y={y + 4}
                fontSize={3}
                fill="var(--color-ink)"
                fontFamily="var(--font-sans, sans-serif)"
              >
                {platform.label} safe zone
              </text>
            )}
          </g>
        );
      })}

      {commonInsets && (
        <rect
          x={commonInsets.left}
          y={commonInsets.top}
          width={Math.max(0, 100 - commonInsets.left - commonInsets.right)}
          height={Math.max(0, 100 - commonInsets.top - commonInsets.bottom)}
          fill="none"
          stroke="var(--color-teal)"
          strokeWidth={0.5}
          vectorEffect="non-scaling-stroke"
        />
      )}
    </svg>
  );
}
