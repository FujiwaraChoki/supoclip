"use client";

import { useLocale, useTranslations } from "next-intl";
import { BarChart3, CalendarClock, Check, Clock, Sparkles } from "lucide-react";

import { SocialProviderIcon } from "@/components/social-provider-icon";
import { DEMO_CLIPS } from "@/lib/demo";
import type { SocialProviderId } from "@/lib/social";
import { cn } from "@/lib/utils";
import { Reveal } from "./reveal";

// An illustrative week starting on a Monday; Wednesday is "today".
const WEEK_START = new Date(2026, 9, 26);
const TODAY_INDEX = 2;

type Slot = { day: number; hour: number; minute: number; clip: number; provider: SocialProviderId };

const SLOTS: Slot[] = [
  { day: 0, hour: 9, minute: 0, clip: 0, provider: "youtube" },
  { day: 1, hour: 18, minute: 0, clip: 1, provider: "youtube" },
  { day: 2, hour: 12, minute: 30, clip: 0, provider: "youtube" },
  { day: 3, hour: 18, minute: 0, clip: 1, provider: "youtube" },
  { day: 3, hour: 20, minute: 0, clip: 1, provider: "tiktok" },
  { day: 4, hour: 9, minute: 0, clip: 0, provider: "youtube" },
  { day: 5, hour: 11, minute: 0, clip: 1, provider: "instagram" },
  { day: 6, hour: 17, minute: 30, clip: 0, provider: "youtube" },
];

function WeekCalendar() {
  const t = useTranslations("marketing.publish.calendar");
  const locale = useLocale();
  const weekday = new Intl.DateTimeFormat(locale, { weekday: "short" });
  const time = new Intl.DateTimeFormat(locale, { hour: "numeric", minute: "2-digit" });
  const days = Array.from({ length: 7 }, (_, i) => new Date(WEEK_START.getFullYear(), WEEK_START.getMonth(), WEEK_START.getDate() + i));

  return (
    <div className="overflow-hidden rounded-[26px] border bg-card shadow-2xl">
      <div className="flex items-center justify-between border-b px-5 py-3.5">
        <p className="text-sm font-medium">{t("week")}</p>
        <div className="flex items-center gap-3 text-[11px] text-muted-foreground">
          <span className="flex items-center gap-1.5">
            <span className="size-1.5 rounded-full bg-foreground" />
            {t("published")}
          </span>
          <span className="flex items-center gap-1.5">
            <span className="size-1.5 rounded-full bg-brand" />
            {t("scheduled")}
          </span>
        </div>
      </div>
      <div className="overflow-x-auto">
        <div className="grid min-w-[600px] grid-cols-7 divide-x lg:min-w-0">
          {days.map((day, dayIndex) => (
            <div key={dayIndex} className={cn("min-h-[240px] p-2", dayIndex === TODAY_INDEX && "bg-muted/40")}>
              {/* Weekdays only: dates on a static mock would go stale. */}
              <div className="mb-3 flex px-1 pt-1">
                <span
                  className={cn(
                    "rounded-full px-2 py-0.5 text-xs",
                    dayIndex === TODAY_INDEX ? "bg-foreground font-semibold text-background" : "text-muted-foreground",
                  )}
                >
                  {weekday.format(day)}
                </span>
              </div>
              <div className="space-y-2">
                {SLOTS.filter((slot) => slot.day === dayIndex).map((slot) => {
                  const clip = DEMO_CLIPS[slot.clip];
                  const soon = slot.provider !== "youtube";
                  const published = !soon && dayIndex < TODAY_INDEX;
                  const at = new Date(day.getFullYear(), day.getMonth(), day.getDate(), slot.hour, slot.minute);
                  return (
                    <div
                      key={`${slot.day}-${slot.provider}`}
                      className={cn(
                        "rounded-xl border p-1.5",
                        soon ? "border-dashed opacity-55" : "bg-background shadow-sm",
                      )}
                    >
                      <div className="relative overflow-hidden rounded-lg" style={{ aspectRatio: "4/5" }}>
                        {/* eslint-disable-next-line @next/next/no-img-element */}
                        <img src={clip.poster} alt="" className={cn("size-full object-cover", soon && "grayscale")} />
                        <span className="absolute left-1 top-1 flex size-5 items-center justify-center rounded-md bg-black/55 text-white backdrop-blur">
                          <SocialProviderIcon provider={slot.provider} className="size-3" />
                        </span>
                        {!soon && (
                          <span
                            className={cn(
                              "absolute right-1 top-1 flex size-5 items-center justify-center rounded-md backdrop-blur",
                              published ? "bg-white text-black" : "bg-black/55",
                            )}
                            aria-label={published ? t("published") : t("scheduled")}
                          >
                            {published ? <Check className="size-3" strokeWidth={3} /> : <span className="size-1.5 rounded-full bg-brand" />}
                          </span>
                        )}
                      </div>
                      <p className="mt-1.5 truncate px-0.5 font-mono text-[10px] tabular-nums text-muted-foreground">
                        {soon ? t("soon") : time.format(at)}
                      </p>
                    </div>
                  );
                })}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

export function PublishSection() {
  const t = useTranslations("marketing.publish");
  const platforms = [
    { provider: "youtube", live: true },
    { provider: "tiktok", live: false },
    { provider: "instagram", live: false },
  ] as const;
  const points = [
    { key: "schedule", icon: CalendarClock },
    { key: "drafts", icon: Sparkles },
    { key: "learn", icon: BarChart3 },
  ] as const;

  return (
    <section id="publish" className="dark scroll-mt-24 bg-background py-24 text-foreground md:py-32">
      <div className="mx-auto max-w-6xl px-6">
        <div className="grid gap-12 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.3fr)] lg:items-center lg:gap-16">
          <Reveal>
            <p className="mb-3 flex items-center gap-2 text-sm font-medium text-muted-foreground">
              <span className="size-1.5 rounded-full bg-brand" />
              {t("eyebrow")}
            </p>
            <h2 className="font-display text-4xl font-bold leading-[1.05] tracking-tight text-balance sm:text-5xl lg:text-[3.25rem]">
              {t("title")}
            </h2>
            <p className="mt-5 max-w-md text-base leading-relaxed text-muted-foreground text-pretty">{t("body")}</p>

            <ul className="mt-8 divide-y rounded-2xl border">
              {platforms.map(({ provider, live }) => (
                <li key={provider} className="flex items-center gap-3 px-4 py-3">
                  <SocialProviderIcon provider={provider} className={cn("size-[18px]", !live && "opacity-50")} />
                  <span className={cn("flex-1 text-sm font-medium", !live && "text-muted-foreground")}>
                    {t(`platforms.${provider}`)}
                  </span>
                  {live ? (
                    <span className="flex items-center gap-1.5 rounded-full bg-foreground px-2.5 py-0.5 text-xs font-medium text-background">
                      <span className="relative flex size-1.5">
                        <span className="absolute inline-flex size-full animate-ping rounded-full bg-emerald-500 opacity-60" />
                        <span className="relative inline-flex size-1.5 rounded-full bg-emerald-500" />
                      </span>
                      {t("platforms.live")}
                    </span>
                  ) : (
                    <span className="flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs text-muted-foreground">
                      <Clock className="size-3" />
                      {t("platforms.soon")}
                    </span>
                  )}
                </li>
              ))}
            </ul>
          </Reveal>

          <Reveal delay={0.1} className="min-w-0">
            <WeekCalendar />
          </Reveal>
        </div>

        <div className="mt-16 grid gap-8 border-t pt-12 md:grid-cols-3">
          {points.map(({ key, icon: Icon }, i) => (
            <Reveal key={key} delay={i * 0.08}>
              <Icon className="mb-4 size-5 text-muted-foreground" />
              <h3 className="font-semibold tracking-tight">{t(`points.${key}.title`)}</h3>
              <p className="mt-2 text-sm leading-relaxed text-muted-foreground">{t(`points.${key}.description`)}</p>
            </Reveal>
          ))}
        </div>

        <Reveal>
          <p className="mt-12 text-sm text-muted-foreground">{t("plan")}</p>
        </Reveal>
      </div>
    </section>
  );
}
