"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslations } from "next-intl";
import { AudioLines, Captions, Film, Play, ScanSearch, SlidersHorizontal, Volume2, VolumeX } from "lucide-react";

import { usePrefersReducedMotion } from "@/hooks/use-prefers-reduced-motion";
import {
  DYNAMIC_EDITS_BROLL,
  DYNAMIC_EDITS_DEFAULT_INTENSITY,
  DYNAMIC_EDITS_INTENSITIES,
  DYNAMIC_EDITS_INTERVIEW,
  type DynamicEditsIntensity,
  type DynamicEditsVideo,
} from "@/lib/dynamic-edits-demo";
import { DEMO_SOURCE } from "@/lib/demo";
import { cn } from "@/lib/utils";
import { Reveal, SectionHeading } from "./reveal";

const POINTS = [
  { key: "captions", icon: Captions },
  { key: "motion", icon: ScanSearch },
  { key: "sound", icon: AudioLines },
  { key: "studio", icon: SlidersHorizontal },
] as const;

type PlayerId = "interview" | "broll";

/** HTMLMediaElement.HAVE_METADATA, spelled out so tests and SSR don't need the global. */
const HAVE_METADATA = 1;

/** Whether the section is close to the viewport (to start loading) and on screen (to keep playing). */
function useSectionVisibility() {
  const ref = useRef<HTMLElement>(null);
  const [near, setNear] = useState(false);
  const [onScreen, setOnScreen] = useState(false);

  useEffect(() => {
    const el = ref.current;
    if (!el || typeof IntersectionObserver === "undefined") return;
    const nearObserver = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          setNear(true);
          nearObserver.disconnect();
        }
      },
      { rootMargin: "400px 0px" },
    );
    const screenObserver = new IntersectionObserver(([entry]) => setOnScreen(entry.isIntersecting), { threshold: 0.2 });
    nearObserver.observe(el);
    screenObserver.observe(el);
    return () => {
      nearObserver.disconnect();
      screenObserver.disconnect();
    };
  }, []);

  return { ref, near, onScreen };
}

/**
 * A 9:16 phone player for one or more renders of the same clip. Changing
 * `active` starts the new render at the current position and shows it once it
 * plays, so the swap is seamless. Nothing loads until `near` is true.
 */
export function PhonePlayer<K extends string>({
  sources,
  active,
  label,
  badge,
  near,
  onScreen,
  muted,
  onToggleMuted,
  className,
}: {
  sources: Record<K, DynamicEditsVideo>;
  active: K;
  label: string;
  badge?: string;
  near: boolean;
  onScreen: boolean;
  muted: boolean;
  onToggleMuted: () => void;
  className?: string;
}) {
  const t = useTranslations("marketing.dynamicEdits.player");
  const reducedMotion = usePrefersReducedMotion();
  const videos = useRef(new Map<K, HTMLVideoElement>());
  const [shown, setShown] = useState<K>(active);
  const shownRef = useRef<K>(active);
  const swapId = useRef(0);
  // null until the visitor presses play or pause: then autoplay follows reduced motion.
  const [intent, setIntent] = useState<boolean | null>(null);
  const [playing, setPlaying] = useState(false);
  const [progress, setProgress] = useState(0);

  const wantsPlay = near && onScreen && (intent ?? !reducedMotion);

  const reveal = useCallback((key: K) => {
    const previous = videos.current.get(shownRef.current);
    shownRef.current = key;
    setShown(key);
    if (previous && previous !== videos.current.get(key)) previous.pause();
  }, []);

  // Swap renders at the same playback position.
  useEffect(() => {
    if (active === shownRef.current) return;
    const from = videos.current.get(shownRef.current);
    const to = videos.current.get(active);
    if (!to) return;
    const id = ++swapId.current;
    const sync = () => {
      if (id !== swapId.current || !from) return;
      const time = from.currentTime;
      to.currentTime = Number.isFinite(to.duration) && to.duration > 0 ? time % to.duration : time;
    };
    if (to.readyState >= HAVE_METADATA) sync();
    else to.addEventListener("loadedmetadata", sync, { once: true });

    if (from && !from.paused) {
      to.muted = from.muted;
      const done = () => {
        if (id === swapId.current) reveal(active);
      };
      const started = to.play();
      if (started && typeof started.then === "function") started.then(done, done);
      else done();
    } else {
      reveal(active);
    }
    return () => to.removeEventListener("loadedmetadata", sync);
  }, [active, reveal]);

  useEffect(() => {
    for (const video of videos.current.values()) video.muted = muted;
  }, [muted]);

  useEffect(() => {
    const video = videos.current.get(shown);
    if (!video) return;
    if (!wantsPlay) {
      video.pause();
      return;
    }
    const started = video.play();
    if (started && typeof started.catch === "function") {
      // Autoplay refused (e.g. data saver): fall back to the poster and a play button.
      started.catch(() => setIntent(false));
    }
  }, [wantsPlay, shown]);

  const togglePlay = () => {
    const video = videos.current.get(shown);
    setIntent(!(video && !video.paused));
  };

  return (
    <div className={cn("relative rounded-[2.1rem] bg-foreground p-[7px] shadow-2xl dark:bg-muted dark:ring-1 dark:ring-border", className)}>
      <div className="relative overflow-hidden rounded-[1.65rem] bg-black" style={{ aspectRatio: "9/16" }}>
        {(Object.keys(sources) as K[]).map((key) => (
          <video
            key={key}
            ref={(el) => {
              if (el) videos.current.set(key, el);
              else videos.current.delete(key);
            }}
            data-clip={key}
            src={sources[key].src}
            poster={sources[key].poster}
            muted={muted}
            loop
            playsInline
            preload={near && key === shown && (intent ?? !reducedMotion) ? "auto" : "none"}
            aria-hidden={key !== shown}
            tabIndex={-1}
            className={cn(
              "absolute inset-0 size-full object-cover",
              key === shown ? "opacity-100" : "pointer-events-none opacity-0",
            )}
            onPlay={() => key === shownRef.current && setPlaying(true)}
            onPause={() => key === shownRef.current && setPlaying(false)}
            onPlaying={() => key === shownRef.current && setPlaying(true)}
            onTimeUpdate={(e) => {
              if (key !== shownRef.current) return;
              const el = e.currentTarget;
              if (el.duration) setProgress(el.currentTime / el.duration);
            }}
          />
        ))}

        <button
          type="button"
          onClick={togglePlay}
          aria-label={`${playing ? t("pause") : t("play")}: ${label}`}
          className="absolute inset-0 flex items-center justify-center focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-white/80"
        >
          <span
            className={cn(
              "flex size-12 items-center justify-center rounded-full bg-white/85 shadow-lg backdrop-blur-sm transition-opacity duration-200",
              playing ? "opacity-0" : "opacity-100",
            )}
          >
            <Play className="ml-0.5 size-5 text-black" />
          </span>
        </button>

        {badge && (
          <span className="pointer-events-none absolute left-2.5 top-2.5 rounded-full bg-black/55 px-2.5 py-1 text-[11px] font-medium text-white backdrop-blur-sm">
            {badge}
          </span>
        )}

        <button
          type="button"
          onClick={onToggleMuted}
          aria-label={muted ? t("unmute") : t("mute")}
          aria-pressed={!muted}
          className={cn(
            "absolute right-2.5 top-2.5 flex h-8 items-center gap-1.5 rounded-full bg-black/55 text-white backdrop-blur-sm transition-colors hover:bg-black/70",
            muted ? "px-3 text-[11px] font-medium" : "w-8 justify-center",
          )}
        >
          {muted ? (
            <>
              <VolumeX className="size-3.5" />
              {t("tapForSound")}
            </>
          ) : (
            <Volume2 className="size-3.5" />
          )}
        </button>

        <div className="pointer-events-none absolute inset-x-0 bottom-0 h-[3px] bg-white/20">
          <div className="h-full bg-white/85" style={{ width: `${Math.round(progress * 1000) / 10}%` }} />
        </div>
      </div>
    </div>
  );
}

function IntensityMeter({ level }: { level: number }) {
  return (
    <span className="flex h-3.5 shrink-0 items-end gap-[3px]" aria-hidden>
      {DYNAMIC_EDITS_INTENSITIES.map((_, i) => (
        <span
          key={i}
          className={cn("w-[3px] rounded-full transition-colors duration-300", i <= level ? "bg-brand" : "bg-foreground/15")}
          style={{ height: `${35 + i * 22}%` }}
        />
      ))}
    </span>
  );
}

export function IntensitySwitcher({
  value,
  onChange,
}: {
  value: DynamicEditsIntensity;
  onChange: (value: DynamicEditsIntensity) => void;
}) {
  const t = useTranslations("marketing.dynamicEdits");
  return (
    <div
      role="group"
      aria-label={t("switcherLabel")}
      className="flex h-[42px] w-full max-w-[340px] rounded-full border bg-background p-1 text-[13px] shadow-sm sm:text-sm"
    >
      {DYNAMIC_EDITS_INTENSITIES.map((intensity) => (
        <button
          key={intensity}
          type="button"
          aria-pressed={value === intensity}
          onClick={() => onChange(intensity)}
          className={cn(
            "flex-auto whitespace-nowrap rounded-full px-2.5 font-medium transition-colors",
            value === intensity
              ? "bg-foreground text-background shadow-sm"
              : "text-muted-foreground hover:text-foreground",
          )}
        >
          {t(`intensity.${intensity}.label`)}
        </button>
      ))}
    </div>
  );
}

/** The intensity demo: one clip, four renders, a switcher and a line about the selected look. */
export function IntensityDemo({
  near,
  onScreen,
  muted,
  onToggleMuted,
}: {
  near: boolean;
  onScreen: boolean;
  muted: boolean;
  onToggleMuted: () => void;
}) {
  const t = useTranslations("marketing.dynamicEdits");
  const [intensity, setIntensity] = useState<DynamicEditsIntensity>(DYNAMIC_EDITS_DEFAULT_INTENSITY);
  const name = t(`intensity.${intensity}.label`);

  return (
    <div className="flex w-full flex-col items-center lg:w-[360px]">
      <IntensitySwitcher value={intensity} onChange={setIntensity} />
      <PhonePlayer
        sources={DYNAMIC_EDITS_INTERVIEW}
        active={intensity}
        label={t("player.label", { intensity: name })}
        badge={name}
        near={near}
        onScreen={onScreen}
        muted={muted}
        onToggleMuted={onToggleMuted}
        className="mt-6 w-[248px] sm:w-[268px]"
      />
      <p className="mt-5 flex min-h-[3.75rem] max-w-[340px] items-start gap-2.5 text-sm leading-relaxed text-muted-foreground" aria-live="polite">
        <span className="pt-[3px]">
          <IntensityMeter level={DYNAMIC_EDITS_INTENSITIES.indexOf(intensity)} />
        </span>
        <span>
          <span className="font-medium text-foreground">{name}.</span> {t(`intensity.${intensity}.description`)}
        </span>
      </p>
    </div>
  );
}

export function DynamicEditsSection() {
  const t = useTranslations("marketing.dynamicEdits");
  const { ref, near, onScreen } = useSectionVisibility();
  // One player has sound at a time, so the two clips never talk over each other.
  const [soundOn, setSoundOn] = useState<PlayerId | null>(null);
  const toggleSound = (id: PlayerId) => setSoundOn((current) => (current === id ? null : id));

  return (
    <section ref={ref} id="dynamic-edits" className="scroll-mt-24 py-24 md:py-32">
      <div className="mx-auto max-w-6xl px-6">
        <SectionHeading eyebrow={t("eyebrow")} title={t("title")} body={t("body")} />

        <Reveal delay={0.05} className="mt-14">
          <div className="relative overflow-hidden rounded-[28px] border bg-canvas px-5 py-10 sm:px-10 md:py-14">
            <div
              aria-hidden
              className="pointer-events-none absolute inset-0 opacity-[0.06] [background-image:radial-gradient(circle_at_1px_1px,currentColor_1px,transparent_0)] [background-size:22px_22px] [mask-image:radial-gradient(ellipse_at_center,black,transparent_75%)]"
            />
            <div className="relative flex flex-col items-center gap-14 lg:flex-row lg:items-start lg:justify-center lg:gap-20">
              <IntensityDemo
                near={near}
                onScreen={onScreen}
                muted={soundOn !== "interview"}
                onToggleMuted={() => toggleSound("interview")}
              />

              <div className="flex w-full flex-col items-center lg:w-[360px]">
                <h3 className="flex h-[42px] items-center gap-2 rounded-full border bg-background px-4 text-sm font-medium shadow-sm">
                  <Film className="size-4 text-muted-foreground" />
                  {t("broll.title")}
                </h3>
                <PhonePlayer
                  sources={{ max: DYNAMIC_EDITS_BROLL }}
                  active="max"
                  label={t("broll.title")}
                  near={near}
                  onScreen={onScreen}
                  muted={soundOn !== "broll"}
                  onToggleMuted={() => toggleSound("broll")}
                  className="mt-6 w-[248px] sm:w-[268px]"
                />
                <p className="mt-5 max-w-[340px] text-sm leading-relaxed text-muted-foreground text-pretty">
                  {t("broll.caption")}
                </p>
              </div>
            </div>
            <p className="relative mt-10 text-center text-xs text-muted-foreground">
              {t.rich("source", {
                title: DEMO_SOURCE.title,
                link: (chunks) => (
                  <a href={DEMO_SOURCE.url} target="_blank" rel="noopener noreferrer" className="underline underline-offset-2 hover:text-foreground">
                    {chunks}
                  </a>
                ),
              })}
            </p>
          </div>
        </Reveal>

        <div className="mt-16 grid gap-x-8 gap-y-10 sm:grid-cols-2 lg:grid-cols-4">
          {POINTS.map(({ key, icon: Icon }, i) => (
            <Reveal key={key} delay={i * 0.05}>
              <Icon className="mb-3 size-5 text-muted-foreground" />
              <h3 className="font-semibold tracking-tight">{t(`points.${key}.title`)}</h3>
              <p className="mt-1.5 text-sm leading-relaxed text-muted-foreground">{t(`points.${key}.description`)}</p>
            </Reveal>
          ))}
        </div>
      </div>
    </section>
  );
}
