import Image from "next/image";
import Link from "next/link";
import { ScoreRing } from "@/components/app/clip-cover";

const SHOWCASE = [
  { title: "The one habit that doubled my output", score: 94, hue: 38, rotate: "-rotate-6", offset: "translate-y-6" },
  { title: "Nobody talks about this pricing mistake", score: 88, hue: 260, rotate: "rotate-0", offset: "-translate-y-4 z-10 scale-105" },
  { title: "Why most startups die in year two", score: 81, hue: 160, rotate: "rotate-6", offset: "translate-y-8" },
];

/** Split-screen frame for sign-in and sign-up: form on the left, product showcase on the right. */
export function AuthFrame({ children, footer }: { children: React.ReactNode; footer: React.ReactNode }) {
  return (
    <div className="grid min-h-dvh bg-background lg:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)]">
      <div className="flex flex-col px-6 py-8 sm:px-12">
        <Link href="/" className="flex items-center gap-2 font-display text-lg font-bold tracking-tight">
          <Image src="/logo.png" alt="" width={24} height={24} className="size-6" priority />SupoClip
        </Link>
        <div className="mx-auto flex w-full max-w-sm flex-1 flex-col justify-center py-12">
          {children}
          <p className="mt-8 text-sm text-muted-foreground">{footer}</p>
        </div>
      </div>
      <div className="relative hidden overflow-hidden bg-stone-950 lg:block">
        <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_30%_20%,oklch(0.45_0.12_40/0.55),transparent_60%),radial-gradient(ellipse_at_80%_90%,oklch(0.4_0.1_260/0.45),transparent_55%)]" />
        <div className="relative flex h-full flex-col justify-between p-12 text-white">
          <p className="max-w-md font-display text-3xl font-bold leading-tight tracking-tight">
            One long video in.<br /><span className="text-white/60">A week of clips out.</span>
          </p>
          <div className="flex items-center justify-center gap-4 py-10" aria-hidden>
            {SHOWCASE.map((clip) => (
              <div key={clip.title} className={`relative aspect-[9/16] w-40 overflow-hidden rounded-2xl shadow-2xl ring-1 ring-white/10 xl:w-48 ${clip.rotate} ${clip.offset}`}
                style={{ background: `linear-gradient(160deg, oklch(0.55 0.14 ${clip.hue}), oklch(0.2 0.04 ${clip.hue}))` }}>
                <div className="absolute inset-x-3 top-4 text-center text-[11px] font-bold leading-tight">{clip.title}</div>
                <div className="absolute right-2 top-14 rounded-full bg-black/50 backdrop-blur"><ScoreRing score={clip.score} size={34} /></div>
                <div className="absolute inset-x-3 bottom-[22%] text-center text-sm font-extrabold uppercase text-yellow-300 [text-shadow:0_2px_6px_rgb(0_0_0/0.7)]">and that&apos;s why</div>
              </div>
            ))}
          </div>
          <p className="text-sm text-white/60">AI picks the moments, frames the speaker, and writes the captions. You just post.</p>
        </div>
      </div>
    </div>
  );
}
