import { Scissors } from "lucide-react";
import { ScoreRing } from "@/components/app/clip-cover";

/**
 * A looping story of what SupoClip does: a long video is scanned, its best
 * moments light up, scissors cut them out, and they drop into vertical clips.
 *
 * Every element shares one 8s timeline; keyframes are generated per element so
 * they stay in sync. Base styles are the finished state, which is what
 * reduced-motion users see.
 */
const DURATION = 8;
const FRAMES = 8;
const BARS = 72;

// Highlighted moments as [start, end] fractions of the timeline.
const MOMENTS = [
  { start: 0.08, end: 0.26, title: "The one habit that doubled my output", caption: "and that's why", score: 94, hue: 38, rotate: -6 },
  { start: 0.41, end: 0.59, title: "Nobody talks about this pricing mistake", caption: "it changed everything", score: 88, hue: 260, rotate: 0 },
  { start: 0.72, end: 0.9, title: "Why most startups die in year two", caption: "here's the thing", score: 81, hue: 160, rotate: 6 },
];

// Scissors sweep the strip between these points of the loop.
const CUT_FROM = 0.34;
const CUT_TO = 0.58;
const cutTimeAt = (x: number) => CUT_FROM + (CUT_TO - CUT_FROM) * x;
const pct = (fraction: number) => `${(fraction * 100).toFixed(2)}%`;

function waveHeight(i: number) {
  const value = Math.abs(Math.sin(i * 0.9) * 0.55 + Math.sin(i * 0.37 + 1) * 0.35 + Math.sin(i * 2.1) * 0.1);
  return 18 + Math.round(value * 82);
}

function keyframes() {
  const rules: string[] = [
    // Playhead scans the whole video first.
    `@keyframes cc-playhead { 0% { left: 0%; opacity: 0 } 3% { opacity: 1 } 26% { left: 100%; opacity: 1 } 29%, 100% { left: 100%; opacity: 0 } }`,
    `@keyframes cc-scissors { 0%, ${pct(CUT_FROM - 0.03)} { transform: translateX(-4%); opacity: 0 } ${pct(CUT_FROM)} { transform: translateX(0%); opacity: 1 } ${pct(CUT_TO)} { transform: translateX(100%); opacity: 1 } ${pct(CUT_TO + 0.03)}, 100% { transform: translateX(104%); opacity: 0 } }`,
    `@keyframes cc-source-label { 0%, 58% { opacity: 1 } 62%, 92% { opacity: 0 } 97%, 100% { opacity: 1 } }`,
    `@keyframes cc-result-label { 0%, 60% { opacity: 0 } 66%, 90% { opacity: 1 } 95%, 100% { opacity: 0 } }`,
    `@keyframes cc-strip { 0% { opacity: 0; transform: translateY(6px) } 4%, 90% { opacity: 1; transform: none } 97%, 100% { opacity: 0; transform: translateY(-4px) } }`,
  ];

  MOMENTS.forEach((moment, index) => {
    const lit = 0.12 + index * 0.05;
    const cutEnd = cutTimeAt(moment.end);
    rules.push(
      // The moment lights up, then lifts out of the strip once both edges are cut.
      `@keyframes cc-moment-${index} { 0%, ${pct(lit)} { opacity: 0; transform: none } ${pct(lit + 0.04)} { opacity: 1; transform: none } ${pct(cutEnd + 0.01)} { opacity: 1; transform: translateY(0) } ${pct(cutEnd + 0.06)} { opacity: 0; transform: translateY(26px) scale(0.9) } 100% { opacity: 0 } }`,
      `@keyframes cc-gap-${index} { 0%, ${pct(cutEnd + 0.02)} { opacity: 0 } ${pct(cutEnd + 0.05)}, 89% { opacity: 1 } 94%, 100% { opacity: 0 } }`,
      `@keyframes cc-clip-${index} { 0%, ${pct(cutEnd + 0.03)} { opacity: 0; transform: translateY(-130px) scale(0.35) rotate(0deg) } ${pct(cutEnd + 0.12)} { opacity: 1; transform: translateY(0) scale(1) rotate(${moment.rotate}deg) } 90% { opacity: 1; transform: translateY(0) scale(1) rotate(${moment.rotate}deg) } 96%, 100% { opacity: 0; transform: translateY(10px) scale(0.96) rotate(${moment.rotate}deg) } }`,
    );
    [moment.start, moment.end].forEach((edge, edgeIndex) => {
      const at = cutTimeAt(edge);
      rules.push(
        `@keyframes cc-cut-${index}-${edgeIndex} { 0%, ${pct(at - 0.005)} { opacity: 0; transform: scaleY(0) } ${pct(at + 0.01)} { opacity: 1; transform: scaleY(1) } 88% { opacity: 1; transform: scaleY(1) } 94%, 100% { opacity: 0; transform: scaleY(1) } }`,
      );
    });
  });

  return `${rules.join("\n")}
.cc-anim { animation-duration: ${DURATION}s; animation-iteration-count: infinite; animation-timing-function: cubic-bezier(.4,0,.2,1); animation-fill-mode: both; }
@media (prefers-reduced-motion: reduce) { .cc-anim { animation: none !important; } }`;
}

const css = keyframes();

export function ClipCutAnimation() {
  return (
    <div className="relative mx-auto w-full max-w-[34rem] select-none" aria-hidden>
      <style>{css}</style>

      <div className="cc-anim relative" style={{ animationName: "cc-strip" }}>
        {/* Source video: a filmstrip of talking-head frames over a waveform. */}
        <div className="relative rounded-xl bg-white/5 p-1.5 ring-1 ring-white/10">
          <div className="relative flex h-16 gap-1 overflow-hidden rounded-lg">
            {Array.from({ length: FRAMES }, (_, i) => (
              <div
                key={i}
                className="relative flex-1 overflow-hidden rounded-[5px]"
                style={{ background: `linear-gradient(160deg, oklch(0.36 0.05 ${i % 2 ? 250 : 45}), oklch(0.2 0.02 ${i % 2 ? 250 : 45}))` }}
              >
                <span className="absolute bottom-0 left-1/2 h-5 w-9 -translate-x-1/2 rounded-t-full bg-white/15" />
                <span className="absolute bottom-[1.15rem] left-1/2 size-3.5 -translate-x-1/2 rounded-full bg-white/20" />
              </div>
            ))}

          </div>

          <div className="mt-1.5 flex h-7 items-center gap-[2px] px-0.5">
            {Array.from({ length: BARS }, (_, i) => {
              const at = i / BARS;
              const inMoment = MOMENTS.some((moment) => at >= moment.start && at < moment.end);
              return (
                <span
                  key={i}
                  className={inMoment ? "flex-1 rounded-full bg-brand/80" : "flex-1 rounded-full bg-white/25"}
                  style={{ height: `${waveHeight(i)}%` }}
                />
              );
            })}
          </div>

          {/* Each moment lights up, and once cut, lifts out and leaves a hole behind. */}
          {MOMENTS.map((moment, index) => {
            const box = { left: `calc(0.375rem + (100% - 0.75rem) * ${moment.start})`, width: `calc((100% - 0.75rem) * ${moment.end - moment.start})` };
            return (
              <div key={moment.title}>
                <div className="cc-anim absolute inset-y-1.5 rounded-md border border-dashed border-white/25 bg-stone-950" style={{ ...box, animationName: `cc-gap-${index}` }} />
                <div className="cc-anim absolute inset-y-1.5 rounded-md bg-brand/30 ring-2 ring-inset ring-brand" style={{ ...box, animationName: `cc-moment-${index}`, opacity: 0 }} />
              </div>
            );
          })}

          {/* Cut marks appear the moment the scissors pass each edge. */}
          {MOMENTS.flatMap((moment, index) =>
            [moment.start, moment.end].map((edge, edgeIndex) => (
              <span
                key={`${index}-${edgeIndex}`}
                className="cc-anim absolute -inset-y-2 w-0 origin-top border-l-2 border-dashed border-white"
                style={{ left: `calc(0.375rem + (100% - 0.75rem) * ${edge})`, animationName: `cc-cut-${index}-${edgeIndex}` }}
              />
            )),
          )}

          <span className="cc-anim absolute -inset-y-1 w-0.5 rounded-full bg-white shadow-[0_0_12px_white]" style={{ animationName: "cc-playhead", opacity: 0 }} />

          <div className="pointer-events-none absolute -top-5 left-1.5 right-1.5">
            <div className="cc-anim" style={{ animationName: "cc-scissors", opacity: 0 }}>
              <span className="-ml-[1.125rem] flex size-9 items-center justify-center rounded-full bg-white text-stone-950 shadow-lg shadow-black/40">
                <Scissors className="size-4 rotate-90" />
              </span>
            </div>
          </div>
        </div>

        <div className="relative mt-3 h-4 font-mono text-[11px] text-white/50">
          <span className="cc-anim absolute inset-0" style={{ animationName: "cc-source-label", opacity: 0 }}>podcast-episode-42.mp4 · 1:24:10</span>
          <span className="cc-anim absolute inset-0 text-white/80" style={{ animationName: "cc-result-label" }}>3 clips found · captioned · ready to post</span>
        </div>
      </div>

      <div className="mt-8 flex items-start justify-center gap-4">
        {MOMENTS.map((moment, index) => (
          <div
            key={moment.title}
            className="cc-anim relative aspect-[9/16] w-32 overflow-hidden rounded-2xl shadow-2xl ring-1 ring-white/10 xl:w-36"
            style={{
              animationName: `cc-clip-${index}`,
              transform: `rotate(${moment.rotate}deg)`,
              background: `linear-gradient(160deg, oklch(0.55 0.14 ${moment.hue}), oklch(0.2 0.04 ${moment.hue}))`,
              marginTop: moment.rotate === 0 ? 0 : "1.25rem",
            }}
          >
            <span className="absolute bottom-0 left-1/2 h-16 w-24 -translate-x-1/2 rounded-t-full bg-black/20" />
            <span className="absolute bottom-[3.6rem] left-1/2 size-11 -translate-x-1/2 rounded-full bg-black/20" />
            <div className="absolute inset-x-2.5 top-3 text-center text-[10px] font-bold leading-tight text-white">{moment.title}</div>
            <div className="absolute right-1.5 top-12 rounded-full bg-black/50 backdrop-blur"><ScoreRing score={moment.score} size={28} /></div>
            <div className="absolute inset-x-2 bottom-[24%] text-center text-[11px] font-extrabold uppercase leading-tight text-yellow-300 [text-shadow:0_2px_6px_rgb(0_0_0/0.7)]">{moment.caption}</div>
          </div>
        ))}
      </div>
    </div>
  );
}
