"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useLocale, useTranslations } from "next-intl";

import { SocialProviderIcon } from "@/components/social-provider-icon";
import { intlLocale } from "@/i18n/config";
import { PROVIDER_LABELS, formatMetric, type SocialProviderId } from "@/lib/social";

export interface DailyViews {
  date: string;
  views: number;
  by_provider: Partial<Record<SocialProviderId, number>>;
}

const HEIGHT = 200;
const PAD = { top: 12, right: 8, bottom: 24, left: 40 };

function parseDay(value: string) {
  const [year, month, day] = value.split("-").map(Number);
  return new Date(year, month - 1, day);
}

/** Rounds up to 1, 2 or 5 × 10^n so gridlines land on readable numbers. */
function niceCeil(value: number) {
  if (value <= 1) return 1;
  const magnitude = 10 ** Math.floor(Math.log10(value));
  const step = [1, 2, 5, 10].find((candidate) => candidate * magnitude >= value) ?? 10;
  return step * magnitude;
}

/** Daily new views as a single monochrome area; the platform split lives in the tooltip. */
export function ViewsChart({ days }: { days: DailyViews[] }) {
  const t = useTranslations("analytics");
  const locale = useLocale();
  const dayLabel = useMemo(
    () => new Intl.DateTimeFormat(intlLocale(locale), { month: "short", day: "numeric" }),
    [locale],
  );
  const containerRef = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);
  const [hovered, setHovered] = useState<number | null>(null);

  useEffect(() => {
    const element = containerRef.current;
    if (!element) return;
    const observer = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width));
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  const plotWidth = Math.max(0, width - PAD.left - PAD.right);
  const plotHeight = HEIGHT - PAD.top - PAD.bottom;
  const yMax = niceCeil(Math.max(0, ...days.map((day) => day.views)));

  const points = useMemo(
    () =>
      days.map((day, index) => ({
        x: PAD.left + (days.length > 1 ? (index / (days.length - 1)) * plotWidth : plotWidth / 2),
        y: PAD.top + plotHeight - (day.views / yMax) * plotHeight,
      })),
    [days, plotWidth, plotHeight, yMax],
  );

  const line = points.map((point, index) => `${index ? "L" : "M"}${point.x},${point.y}`).join(" ");
  const baseline = PAD.top + plotHeight;
  const area = points.length
    ? `${line} L${points[points.length - 1].x},${baseline} L${points[0].x},${baseline} Z`
    : "";
  const xTicks = days.length > 2 ? [0, Math.floor((days.length - 1) / 2), days.length - 1] : days.map((_, index) => index);

  const onPointerMove = (event: React.PointerEvent<SVGRectElement>) => {
    const bounds = event.currentTarget.getBoundingClientRect();
    const ratio = (event.clientX - bounds.left) / bounds.width;
    setHovered(Math.min(days.length - 1, Math.max(0, Math.round(ratio * (days.length - 1)))));
  };

  const active = hovered === null ? null : days[hovered];
  const activePoint = hovered === null ? null : points[hovered];
  const total = days.reduce((sum, day) => sum + day.views, 0);

  return (
    <div ref={containerRef} className="relative">
      {width > 0 && (
        <svg
          width={width}
          height={HEIGHT}
          role="img"
          aria-label={t("chart.summary", { days: days.length, total: formatMetric(total) })}
        >
          {[0, yMax / 2, yMax].map((tick) => {
            const y = PAD.top + plotHeight - (tick / yMax) * plotHeight;
            return (
              <g key={tick}>
                <line x1={PAD.left} x2={width - PAD.right} y1={y} y2={y} className="stroke-border" strokeWidth={1} />
                <text x={PAD.left - 8} y={y} dy="0.32em" textAnchor="end" className="fill-muted-foreground text-[10px] tabular-nums">
                  {formatMetric(tick)}
                </text>
              </g>
            );
          })}
          {xTicks.map((index) => (
            <text
              key={index}
              x={points[index]?.x}
              y={HEIGHT - 6}
              textAnchor={index === 0 ? "start" : index === days.length - 1 ? "end" : "middle"}
              className="fill-muted-foreground text-[10px]"
            >
              {dayLabel.format(parseDay(days[index].date))}
            </text>
          ))}
          <path d={area} className="fill-foreground/[0.06]" />
          <path d={line} fill="none" className="stroke-foreground" strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
          {activePoint && (
            <>
              <line x1={activePoint.x} x2={activePoint.x} y1={PAD.top} y2={baseline} className="stroke-muted-foreground/50" strokeWidth={1} />
              <circle cx={activePoint.x} cy={activePoint.y} r={4} className="fill-foreground stroke-background" strokeWidth={2} />
            </>
          )}
          <rect
            x={PAD.left}
            y={PAD.top}
            width={plotWidth}
            height={plotHeight}
            fill="transparent"
            onPointerMove={onPointerMove}
            onPointerLeave={() => setHovered(null)}
          />
        </svg>
      )}

      {active && activePoint && (
        <div
          className="pointer-events-none absolute top-0 z-10 min-w-36 rounded-lg border bg-popover px-3 py-2 text-xs shadow-sm"
          style={{
            left: Math.min(Math.max(activePoint.x - 72, 0), Math.max(0, width - 152)),
          }}
        >
          <p className="text-muted-foreground">{dayLabel.format(parseDay(active.date))}</p>
          <p className="text-sm font-semibold tabular-nums">
            {t("chart.viewCount", { count: active.views, formatted: formatMetric(active.views) })}
          </p>
          {Object.entries(active.by_provider).length > 1 &&
            Object.entries(active.by_provider).map(([provider, views]) => (
              <p key={provider} className="mt-0.5 flex items-center gap-1.5 text-muted-foreground">
                <SocialProviderIcon provider={provider as SocialProviderId} className="size-3" />
                {PROVIDER_LABELS[provider as SocialProviderId]}
                <span className="ml-auto tabular-nums text-foreground">{formatMetric(views)}</span>
              </p>
            ))}
        </div>
      )}

      <table className="sr-only">
        <caption>{t("chart.caption")}</caption>
        <thead>
          <tr>
            <th>{t("chart.date")}</th>
            <th>{t("chart.views")}</th>
          </tr>
        </thead>
        <tbody>
          {days.map((day) => (
            <tr key={day.date}>
              <td>{day.date}</td>
              <td>{day.views}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
