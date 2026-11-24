"use client";

import Link from "next/link";
import { useTranslations } from "next-intl";

import { PostingCalendar } from "@/components/calendar/posting-calendar";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useSocialAccess } from "@/hooks/use-social-access";

export default function CalendarPage() {
  const t = useTranslations("calendar");
  const access = useSocialAccess();
  if (access === null) {
    return (
      <main className="mx-auto w-full max-w-6xl px-4 pt-10 sm:px-6">
        <Skeleton className="h-9 w-48" />
        <Skeleton className="mt-6 h-[32rem] w-full rounded-2xl" />
      </main>
    );
  }
  if (!access) {
    return (
      <main className="mx-auto max-w-md px-4 py-24 text-center">
        <h1 className="font-display text-2xl font-bold tracking-tight">{t("title")}</h1>
        <p className="mt-2 text-sm text-muted-foreground">{t("unavailable")}</p>
        <Button asChild variant="outline" className="mt-5 rounded-full"><Link href="/">{t("goHome")}</Link></Button>
      </main>
    );
  }
  return <PostingCalendar />;
}
