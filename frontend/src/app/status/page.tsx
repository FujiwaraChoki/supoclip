import type { Metadata } from "next";
import Image from "next/image";
import Link from "next/link";
import { ArrowLeft } from "lucide-react";
import { getPrismaClient } from "@/server/prisma";

export const dynamic = "force-dynamic";

const presentations: Record<string, { label: string; badge: string; border: string }> = {
  operational: { label: "Operational", badge: "bg-emerald-100 text-emerald-900", border: "border-emerald-200" },
  degraded: { label: "Degraded", badge: "bg-amber-100 text-amber-900", border: "border-amber-200" },
  outage: { label: "Outage", badge: "bg-red-100 text-red-900", border: "border-red-200" },
  maintenance: { label: "Maintenance", badge: "bg-blue-100 text-blue-900", border: "border-blue-200" },
};

export const metadata: Metadata = {
  title: "Service status",
  description: "Current service status and incident updates for SupoClip.",
  robots: { index: false, follow: true },
};

export default async function StatusPage() {
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
          <span className="text-sm text-muted-foreground">Service status</span>
        </div>
      </header>

      <div className="mx-auto max-w-3xl px-6 py-12 sm:py-20">
        <h1 className="font-display text-3xl font-bold tracking-tight sm:text-4xl">
          Service status
        </h1>
        <p className="mt-3 text-muted-foreground">Updates on the SupoClip hosted service.</p>

        {!statuses?.length && (
          <p role="status" className="mt-8 rounded-2xl border bg-background p-6 text-muted-foreground">
            Service status is temporarily unavailable. Please check back later.
          </p>
        )}
        {statuses?.map((component) => {
          const presentation = presentations[component.status] ?? { label: "Unknown", badge: "bg-muted text-foreground", border: "border-border" };
          return (
            <section key={component.id} aria-labelledby={`status-${component.id}`} className={`mt-8 rounded-2xl border ${presentation.border} bg-background p-6 sm:p-8`}>
              <div className="flex flex-wrap items-center justify-between gap-4">
                <h2 id={`status-${component.id}`} className="text-lg font-semibold">
                  {component.name}
                </h2>
                <span className={`rounded-full px-3 py-1 text-sm font-semibold ${presentation.badge}`}>
                  {presentation.label}
                </span>
              </div>
              <div className="mt-6 border-t pt-6">
                <p className="text-sm font-medium">{component.headline}</p>
                <p className="mt-3 whitespace-pre-line leading-7 text-muted-foreground">
                  {component.description}
                </p>
                <p className="mt-6 text-xs text-muted-foreground">
                  Last updated: <time dateTime={component.updatedAt.toISOString()}>{component.updatedAt.toLocaleString("en-GB", { timeZone: "Europe/Zurich", dateStyle: "long", timeStyle: "short" })} (Zurich)</time>
                </p>
              </div>
            </section>
          );
        })}

        <Link href="/" className="mt-8 inline-flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground">
          <ArrowLeft aria-hidden="true" className="size-4" />
          Back to SupoClip
        </Link>
      </div>
    </main>
  );
}
