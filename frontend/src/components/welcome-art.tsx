/**
 * The welcome tour's opening picture, drawn in code in the workflow glyphs' style: a long
 * video is scanned, its best moments light up, and each becomes a vertical clip with
 * captions and a score. One 7 s loop; with reduced motion it rests on the finished frame.
 */

const LOOP_S = 7;
const FRAME = { x: 20, y: 38, w: 170, h: 96 };
const TRACK = { x: 20, y: 150, w: 170 };
/** Highlight ranges on the timeline, as fractions, and the clip each one becomes. */
const MOMENTS = [
  { from: 0.1, to: 0.24, card: 214, score: 87 },
  { from: 0.44, to: 0.6, card: 272, score: 94 },
  { from: 0.76, to: 0.9, card: 330, score: 81 },
] as const;
const CARD = { y: 43, w: 48, h: 86 };
/** The playhead crosses the track in the first part of the loop, then everything holds and fades back. */
const SWEEP = 0.62;
const HOLD_END = 0.9;

const pct = (value: number) => `${(value * 100).toFixed(1)}%`;

/** Keyframes that switch something on at `at` (fraction of the loop) and off again before the loop restarts. */
function appear(name: string, at: number, from = "opacity:0", to = "opacity:1") {
  const on = Math.min(at + 0.05, HOLD_END - 0.01);
  return `@keyframes ${name}{0%,${pct(at)}{${from}}${pct(on)},${pct(HOLD_END)}{${to}}97%,100%{${from}}}`;
}

const css = [
  `.wa *{animation-duration:${LOOP_S}s;animation-iteration-count:infinite;animation-timing-function:ease-out;transform-box:fill-box;transform-origin:center}`,
  `@keyframes wa-head{0%{transform:translateX(0);opacity:1}${pct(SWEEP)}{transform:translateX(${TRACK.w}px);opacity:1}${pct(SWEEP + 0.04)},100%{transform:translateX(${TRACK.w}px);opacity:0}}`,
  `.wa-head{animation-name:wa-head;animation-timing-function:linear}`,
  ...MOMENTS.flatMap((moment, index) => {
    const lit = moment.from * SWEEP;
    return [
      appear(`wa-seg${index}`, lit),
      // Hidden too while undrawn: a round cap on a zero-length dash still shows as a dot.
      appear(`wa-link${index}`, lit + 0.03, "stroke-dashoffset:1;opacity:0", "stroke-dashoffset:0;opacity:1"),
      appear(`wa-card${index}`, lit + 0.08, "opacity:0;transform:translateY(6px) scale(.92)", "opacity:1;transform:none"),
      appear(`wa-cap${index}`, lit + 0.14),
      `.wa-seg${index}{animation-name:wa-seg${index}}`,
      `.wa-link${index}{animation-name:wa-link${index}}`,
      `.wa-card${index}{animation-name:wa-card${index}}`,
      `.wa-cap${index}{animation-name:wa-cap${index}}`,
    ];
  }),
  // The spoken word moves along the caption, like the burned-in captions do.
  `@keyframes wa-word{0%,30%{transform:translateX(0)}36%,63%{transform:translateX(13px)}69%,100%{transform:translateX(26px)}}`,
  `.wa-word{animation-name:wa-word;animation-duration:1.8s;animation-timing-function:steps(1,end)}`,
  `@media (prefers-reduced-motion:reduce){.wa *{animation:none!important}}`,
].join("");

/** Someone talking, in outline: head and shoulders, cropped by whatever frame holds them. */
function Speaker({ cx, top, scale = 1 }: { cx: number; top: number; scale?: number }) {
  const r = 11 * scale;
  return (
    <g fill="currentColor" fillOpacity="0.14">
      <circle cx={cx} cy={top + r} r={r} />
      <path d={`M${cx - 26 * scale} ${top + 62 * scale}c0-17 ${12 * scale}-27 ${26 * scale}-27s${26 * scale} 10 ${26 * scale} 27z`} />
    </g>
  );
}

export function WelcomeArt() {
  const trackX = (fraction: number) => TRACK.x + fraction * TRACK.w;
  return (
    <div className="relative aspect-[2/1] overflow-hidden bg-muted text-foreground" aria-hidden>
      <svg viewBox="0 0 400 200" className="wa size-full">
        <style>{css}</style>

        {/* The long video, with the moments picked on its timeline. */}
        <rect x={FRAME.x} y={FRAME.y} width={FRAME.w} height={FRAME.h} rx="8" fill="var(--background)" stroke="currentColor" strokeOpacity="0.2" strokeWidth="1.25" />
        <svg x={FRAME.x} y={FRAME.y} width={FRAME.w} height={FRAME.h} overflow="hidden">
          <Speaker cx={FRAME.w * 0.36} top={22} scale={1.25} />
          <Speaker cx={FRAME.w * 0.72} top={30} scale={1.05} />
        </svg>
        <rect x={TRACK.x} y={TRACK.y - 3} width={TRACK.w} height="6" rx="3" fill="currentColor" fillOpacity="0.12" />
        {MOMENTS.map((moment, index) => (
          <rect key={index} className={`wa-seg${index} text-brand`} x={trackX(moment.from)} y={TRACK.y - 3} width={(moment.to - moment.from) * TRACK.w} height="6" rx="3" fill="currentColor" />
        ))}
        <g className="wa-head">
          <rect x={TRACK.x - 1} y={TRACK.y - 9} width="2" height="18" rx="1" fill="currentColor" fillOpacity="0.75" />
        </g>

        {/* Each moment becomes a vertical clip. */}
        {MOMENTS.map((moment, index) => {
          const fromX = trackX((moment.from + moment.to) / 2);
          const toX = moment.card + CARD.w / 2;
          const bottom = CARD.y + CARD.h;
          return (
            <g key={index}>
              <path
                className={`wa-link${index} text-brand`}
                d={`M${fromX} ${TRACK.y + 4}C${fromX} ${TRACK.y + 34} ${toX} ${TRACK.y + 30} ${toX} ${bottom + 4}`}
                pathLength={1}
                strokeDasharray="1"
                fill="none"
                stroke="currentColor"
                strokeOpacity="0.55"
                strokeWidth="1.25"
                strokeLinecap="round"
              />
              <g className={`wa-card${index}`}>
                <rect x={moment.card} y={CARD.y} width={CARD.w} height={CARD.h} rx="6" fill="var(--background)" stroke="currentColor" strokeOpacity={index === 1 ? 0.55 : 0.25} strokeWidth="1.25" />
                <svg x={moment.card} y={CARD.y} width={CARD.w} height={CARD.h} overflow="hidden">
                  <Speaker cx={CARD.w / 2} top={18} scale={0.95} />
                </svg>
                <rect x={moment.card + 5} y={CARD.y + 5} width="15" height="9" rx="4.5" className="text-brand" fill="currentColor" />
                <text x={moment.card + 12.5} y={CARD.y + 11.6} textAnchor="middle" fontSize="6.5" fontWeight="700" fill="var(--background)">{moment.score}</text>
                <g className={`wa-cap${index}`}>
                  <rect x={moment.card + 7} y={CARD.y + CARD.h * 0.72} width="34" height="5" rx="2.5" fill="currentColor" fillOpacity="0.4" />
                  <rect className="wa-word text-brand" x={moment.card + 7} y={CARD.y + CARD.h * 0.72} width="9" height="5" rx="2.5" fill="currentColor" />
                  <rect x={moment.card + 12} y={CARD.y + CARD.h * 0.72 + 8} width="24" height="5" rx="2.5" fill="currentColor" fillOpacity="0.25" />
                </g>
              </g>
            </g>
          );
        })}
      </svg>
    </div>
  );
}
