import Image from "next/image";
import Link from "next/link";
import { getTranslations } from "next-intl/server";
import { ClipCutAnimation } from "@/components/auth/clip-cut-animation";

/** Split-screen frame for sign-in and sign-up: form on the left, product showcase on the right. */
export async function AuthFrame({ children, footer }: { children: React.ReactNode; footer: React.ReactNode }) {
  const t = await getTranslations("auth");
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
        <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_30%_20%,rgb(255_255_255/0.07),transparent_60%)]" />
        <div className="relative flex h-full flex-col justify-between p-12 text-white">
          <p className="max-w-md font-display text-3xl font-bold leading-tight tracking-tight">
            {t("frame.headline")}<br /><span className="text-white/60">{t("frame.headlineMuted")}</span>
          </p>
          <div className="py-10">
            <ClipCutAnimation />
          </div>
          <p className="text-sm text-white/60">{t("frame.caption")}</p>
        </div>
      </div>
    </div>
  );
}
