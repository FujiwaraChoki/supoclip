"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ArrowRight, Megaphone, X } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";

import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { useAnnouncements } from "@/hooks/use-announcements";
import { intlLocale } from "@/i18n/config";
import { cn } from "@/lib/utils";

const READ_EVENT = "supoclip:announcements-read";
const memoryRead = new Map<string, string[]>();

function readIds(key: string): string[] {
  if (memoryRead.has(key)) return memoryRead.get(key)!;
  try {
    const value: unknown = JSON.parse(window.localStorage.getItem(key) ?? "[]");
    return Array.isArray(value) ? value.filter((id): id is string => typeof id === "string") : [];
  } catch {
    return memoryRead.get(key) ?? [];
  }
}

/** One announcement list, with read state scoped to the visitor or signed-in account. */
export function Announcements({ userId, compact = false, landing = false }: {
  userId?: string;
  compact?: boolean;
  landing?: boolean;
}) {
  const t = useTranslations("shell");
  const locale = useLocale();
  const key = `supoclip:announcements-read:${userId ?? "visitor"}`;
  const [read, setRead] = useState<{ key: string; ids: string[] } | null>(null);
  const [open, setOpen] = useState(false);
  const announcements = useAnnouncements();

  useEffect(() => {
    const sync = () => setRead({ key, ids: readIds(key) });
    const syncStorage = (event: StorageEvent) => {
      if (event.key !== null && event.key !== key) return;
      memoryRead.delete(key);
      sync();
    };
    sync();
    window.addEventListener("storage", syncStorage);
    window.addEventListener(READ_EVENT, sync);
    return () => {
      window.removeEventListener("storage", syncStorage);
      window.removeEventListener(READ_EVENT, sync);
    };
  }, [key]);

  if (announcements.length === 0) return null;

  const unread = read?.key === key ? announcements.filter((item) => !read.ids.includes(item.id)).length : 0;
  const label = landing ? announcements[0].title : t("announcements.whatsNew");
  const accessibleLabel = unread
    ? t("announcements.whatsNewUnread", { count: unread })
    : t("announcements.whatsNew");

  const changeOpen = (next: boolean) => {
    setOpen(next);
    if (!next) return;
    const ids = announcements.map((item) => item.id);
    setRead({ key, ids });
    try {
      window.localStorage.setItem(key, JSON.stringify(ids));
      memoryRead.delete(key);
    } catch {
      // The badge still clears for this visit when browser storage is unavailable.
      memoryRead.set(key, ids);
    }
    window.dispatchEvent(new Event(READ_EVENT));
  };

  return (
    <Popover open={open} onOpenChange={changeOpen}>
      <PopoverTrigger asChild>
        <button
          type="button"
          aria-label={accessibleLabel}
          title={compact ? t("announcements.whatsNew") : undefined}
          className={cn(
            "relative flex items-center gap-2 rounded-lg px-2.5 py-2 text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
            landing ? "rounded-full border border-brand/25 bg-brand-soft text-brand hover:border-brand/50" : "text-muted-foreground hover:bg-accent hover:text-foreground",
            compact && "justify-center px-2",
          )}
        >
          <Megaphone className="size-4 shrink-0" aria-hidden />
          {!compact && <span className="min-w-0 flex-1 text-left">{label}</span>}
          {unread > 0 && (compact
            ? <span aria-hidden className="absolute right-1 top-1 size-2 rounded-full bg-brand" />
            : <span aria-hidden className="rounded-full bg-brand px-1.5 py-0.5 text-[10px] font-semibold leading-none text-white">{landing ? t("announcements.new") : unread}</span>
          )}
        </button>
      </PopoverTrigger>
      <PopoverContent align={landing ? "start" : "end"} sideOffset={8} collisionPadding={16} className="w-[min(22rem,calc(100vw-2rem))] rounded-2xl p-0" aria-label={t("announcements.popover")}>
        <div className="flex items-center justify-between border-b px-4 py-3">
          <h2 className="font-display text-base font-semibold">{t("announcements.title")}</h2>
          <button type="button" onClick={() => setOpen(false)} aria-label={t("announcements.close")} className="rounded-md p-1 text-muted-foreground hover:bg-accent hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
            <X className="size-4" aria-hidden />
          </button>
        </div>
        <div className="max-h-[min(28rem,60dvh)] divide-y overflow-y-auto">
          {announcements.map((item) => (
            <article key={item.id} className="space-y-2 p-4">
              <time dateTime={item.publishedAt} className="text-xs text-muted-foreground">
                {new Date(item.publishedAt).toLocaleDateString(intlLocale(locale), { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" })}
              </time>
              <h3 className="font-semibold leading-snug">{item.title}</h3>
              <p className="text-sm leading-relaxed text-muted-foreground">{item.description}</p>
              <Link href={item.href} onClick={() => setOpen(false)} className="inline-flex items-center gap-1.5 text-sm font-medium text-brand underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
                {item.action}<ArrowRight className="size-3.5" aria-hidden />
              </Link>
            </article>
          ))}
        </div>
      </PopoverContent>
    </Popover>
  );
}
