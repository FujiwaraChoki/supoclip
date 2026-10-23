import type { Metadata } from "next";
import Image from "next/image";
import Link from "next/link";
import { ArrowLeft } from "lucide-react";
import { createTranslator } from "next-intl";
import { getLocale, getTranslations } from "next-intl/server";
import { intlLocale } from "@/i18n/config";
import { getPrismaClient } from "@/server/prisma";

export const dynamic = "force-dynamic";

const presentations: Record<string, { badge: string; border: string }> = {
  operational: { badge: "bg-emerald-100 text-emerald-900", border: "border-emerald-200" },
  degraded: { badge: "bg-amber-100 text-amber-900", border: "border-amber-200" },
  outage: { badge: "bg-red-100 text-red-900", border: "border-red-200" },
  maintenance: { badge: "bg-blue-100 text-blue-900", border: "border-blue-200" },
};

const STATUSES = ["operational", "degraded", "outage", "maintenance"] as const;

type StatusTranslator = (key: string, values?: Record<string, string | number>) => string;

function isClientStub(error: unknown) {
  return error instanceof Error && error.message.includes("not supported in Client Components");
}

async function statusTranslator(): Promise<StatusTranslator> {
  try {
    const translate = await getTranslations("status");
    return (key, values) => translate(key as never, values as never);
  } catch (error) {
    if (!isClientStub(error)) throw error;
    const messages = (await import("../../../messages/en/status.json")).default;
    const translate = createTranslator({ locale: "en", messages });
    return (key, values) => translate(key as never, values as never);
  }
}

async function requestLocale() {
  try {
    return await getLocale();
  } catch (error) {
    if (!isClientStub(error)) throw error;
    return "en";
  }
}

export async function generateMetadata(): Promise<Metadata> {
  const t = await statusTranslator();
  return {
    title: t("metadata.title"),
    description: t("metadata.description"),
    robots: { index: false, follow: true },
  };
}

export default async function StatusPage() {
  const t = await statusTranslator();
  const locale = await requestLocale();
  const statuses = await getPrismaClient().serviceStatus.findMany({ orderBy: { name: "asc" } }).catch((error: unknown) => {
    console.error("Failed to load service status:", error);
    return null;
  });
  return (
    <main className="min-h-screen bg-canvas text-foreground">
      <header className="border-b bg-background">
        <div className="mx-auto flex h-16 max-w-3xl items-center justify-between px-6">
          <Link href="/" className="flex items-center gap-2.5">
            <Image src="/logo.png" alt="" width={24} height={24} className="rounded-lg" />
            <span className="font-display text-lg font-bold">SupoClip</span>
          </Link>
          <span className="text-sm text-muted-foreground">{t("title")}</span>
        </div>
      </header>

      <div className="mx-auto max-w-3xl px-6 py-12 sm:py-20">
        <h1 className="font-display text-3xl font-bold tracking-tight sm:text-4xl">
          {t("title")}
        </h1>
        <p className="mt-3 text-muted-foreground">{t("intro")}</p>

        {!statuses?.length && (
          <p role="status" className="mt-8 rounded-2xl border bg-background p-6 text-muted-foreground">
            {t("unavailable")}
          </p>
        )}
        {statuses?.map((component) => {
          const presentation = presentations[component.status] ?? { badge: "bg-muted text-foreground", border: "border-border" };
          const state = (STATUSES as readonly string[]).includes(component.status) ? component.status : "unknown";
          return (
            <section key={component.id} aria-labelledby={`status-${component.id}`} className={`mt-8 rounded-2xl border ${presentation.border} bg-background p-6 sm:p-8`}>
              <div className="flex flex-wrap items-center justify-between gap-4">
                <h2 id={`status-${component.id}`} className="text-lg font-semibold">
                  {component.name}
                </h2>
                <span className={`rounded-full px-3 py-1 text-sm font-semibold ${presentation.badge}`}>
                  {t(`states.${state}`)}
                </span>
              </div>
              <div className="mt-6 border-t pt-6">
                <p className="text-sm font-medium">{component.headline}</p>
                <p className="mt-3 whitespace-pre-line leading-7 text-muted-foreground">
                  {component.description}
                </p>
                <p className="mt-6 text-xs text-muted-foreground">
                  {t("lastUpdated")}{" "}
                  <time dateTime={component.updatedAt.toISOString()}>{component.updatedAt.toLocaleString(intlLocale(locale), { timeZone: "Europe/Zurich", dateStyle: "long", timeStyle: "short" })}</time>{" "}
                  {t("timezone")}
                </p>
              </div>
            </section>
          );
        })}

        <Link href="/" className="mt-8 inline-flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground">
          <ArrowLeft aria-hidden="true" className="size-4" />
          {t("back")}
        </Link>
      </div>
    </main>
  );
}
