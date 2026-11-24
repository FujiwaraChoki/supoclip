"use client";

import Link from "next/link";
import Image from "next/image";
import { usePathname, useRouter } from "next/navigation";
import { useLocale, useTranslations } from "next-intl";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Bot, CalendarDays, ChartColumn, CornerDownLeft, Film, ListChecks, ListOrdered, Play, Plus, Search, Settings, Share2, Shield, Sparkles, Target, Users,
} from "lucide-react";
import { useSession } from "@/lib/auth-client";
import { useBillingSummary } from "@/hooks/use-billing-summary";
import { useSocialAccess } from "@/hooks/use-social-access";
import { timeAgo, type GenerationSummary } from "@/lib/generations";
import { useGenerations } from "@/lib/queries/generations";
import { StatusBadge } from "@/components/app/status-badge";
import { Announcements } from "@/components/announcements";
import { ThemeToggle } from "@/components/theme-toggle";
import { useAppTheme } from "@/components/theme-provider";
import { availableWorkflows, useWorkflowsInfo } from "@/components/workflow-picker";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { Kbd } from "@/components/ui/kbd";
import { cn } from "@/lib/utils";
import { FeatureTourHost } from "@/components/feature-tour";
import { PebbleGuide } from "@/components/onboarding/pebble-guide";
import { usePebbleFlow } from "@/components/onboarding/pebble-flow";
import { pebbleDockActive } from "@/components/onboarding/pebble-dock";
import { useOnboardingStatus } from "@/hooks/use-onboarding-status";

// Agent's onboarding covers what the welcome tour shows.
const PEBBLE_SKIPS_TOURS = ["welcome"] as const;
import { WorkspaceSwitcher } from "@/components/workspace/workspace-switcher";
import { AccountMenu } from "@/components/app/account-menu";
import type { Workflow } from "@/lib/workflows";

// `mobile: false` keeps an item out of the five-slot mobile tab bar.
// `social: true` items only show when social publishing is enabled for the user.
type NavItem = { href: string; labelKey: "create" | "library" | "calendar" | "social" | "analytics" | "agents" | "settings"; icon: typeof Film; match: (path: string) => boolean; mobile?: boolean; social?: boolean };

const NAV: NavItem[] = [
  { href: "/", labelKey: "create", icon: Sparkles, match: (p) => p === "/" || p.startsWith("/agent") },
  { href: "/list", labelKey: "library", icon: Film, match: (p) => p.startsWith("/list") || p.startsWith("/tasks") },
  { href: "/calendar", labelKey: "calendar", icon: CalendarDays, match: (p) => p.startsWith("/calendar"), social: true },
  // Accounts are set up once; the calendar takes its slot in the five-slot mobile tab bar.
  { href: "/settings/social", labelKey: "social", icon: Share2, match: (p) => p.startsWith("/settings/social"), social: true, mobile: false },
  { href: "/analytics", labelKey: "analytics", icon: ChartColumn, match: (p) => p.startsWith("/analytics"), social: true },
  { href: "/settings/api-keys", labelKey: "agents", icon: Bot, match: (p) => p.startsWith("/settings/api-keys"), mobile: false },
  { href: "/settings", labelKey: "settings", icon: Settings, match: (p) => p === "/settings" },
];

/** What the create screen tells the tour host; other pages need nothing. */
export type ShellTour = {
  /** The user is doing something: don't start a tour, and leave one that's showing. */
  busy: boolean;
  onWorkflow?: (workflow: Workflow) => void;
  onEnd?: () => void;
  /** Open a section of the composer's settings menu for a step that points into it. */
  onSettingsSection?: (id: string) => void;
};

/** Signed-in application chrome: sidebar on desktop, tab bar on mobile, ⌘K palette and feature tours everywhere. */
export function AppShell({ children, tour }: { children: React.ReactNode; /** Set by the create screen, the only page a tour starts on. */ tour?: ShellTour }) {
  const t = useTranslations("shell");
  const { data: session } = useSession();
  const pathname = usePathname();
  const router = useRouter();
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [billing] = useBillingSummary(Boolean(session?.user));
  // The editor is a full-screen workspace with its own back button: no app
  // chrome there. Elements stay in place (hidden) so children keep their position.
  const immersive = /^\/tasks\/[^/]+\/edit/.test(pathname);
  const compact = immersive;
  const navigate = useCallback((path: string) => router.push(path), [router]);
  const onboarding = useOnboardingStatus(Boolean(session?.user));
  const [pebbleFlow] = usePebbleFlow(session?.user?.id);
  const pebbleGroup = onboarding?.variant === "pebble";
  // Agent's guide and a feature tour never talk over each other.
  const pebbleGuiding = pebbleGroup && !onboarding?.completed && pebbleDockActive(pebbleFlow.stage);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setPaletteOpen((open) => !open);
        return;
      }
      const target = event.target as HTMLElement | null;
      const typing = target?.closest("input, textarea, select, [contenteditable=true], [role=dialog]");
      if (!typing && !event.metaKey && !event.ctrlKey && !event.altKey && event.key.toLowerCase() === "n") {
        event.preventDefault();
        router.push("/");
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [router]);

  // Children must keep the same tree position whether or not the session has
  // loaded — otherwise the page remounts and re-runs its initial fetches.
  const user = session?.user;
  useAppTheme(Boolean(user));
  const admin = Boolean((user as { is_admin?: boolean } | undefined)?.is_admin);
  const socialAccess = useSocialAccess(Boolean(user));
  const labeled = NAV.map((item) => ({ ...item, label: t(`nav.${item.labelKey}`) }));
  const nav = labeled.filter((item) => !item.social || socialAccess === true);
  const mobileNav = nav.filter((item) => item.mobile !== false);
  const items = admin ? [...nav, { href: "/admin", label: t("nav.admin"), icon: Shield, match: (p: string) => p.startsWith("/admin") }] : nav;

  return (
    <div data-app-shell={user ? "" : undefined} className={cn(user && "flex min-h-dvh bg-canvas")}>
      {user ? <aside
        className={cn(
          "sticky top-0 hidden h-dvh shrink-0 flex-col border-r border-sidebar-border bg-sidebar",
          !immersive && "md:flex",
          compact ? "w-16 items-center px-2" : "w-60 px-3",
        )}
      >
        <Link href="/" className={cn("flex h-14 items-center gap-2 font-display text-lg font-bold tracking-tight", compact ? "justify-center" : "px-2")}>
          <Image src="/logo.png" alt="" width={24} height={24} className="size-6 dark:invert" priority />
          {!compact && "SupoClip"}
        </Link>

        <WorkspaceSwitcher compact={compact} className="mb-3" />

        <SidebarLink compact={compact} href="/" label={t("nav.newGeneration")} shortcut="N" className="mb-4 bg-foreground text-background hover:bg-foreground/90 hover:text-background">
          <Plus className="size-4" />
        </SidebarLink>

        <nav aria-label={t("search.mainNav")} className="flex w-full flex-col gap-0.5">
          {items.map((item) => {
            const active = item.match(pathname);
            return (
              <SidebarLink key={item.href} compact={compact} href={item.href} label={item.label} active={active} tour={"labelKey" in item ? `nav-${item.labelKey}` : undefined}>
                <item.icon className="size-4" />
              </SidebarLink>
            );
          })}
        </nav>

        <button
          type="button"
          onClick={() => setPaletteOpen(true)}
          className={cn(
            "mt-4 flex h-9 w-full items-center gap-2 rounded-lg border border-sidebar-border bg-background text-sm text-muted-foreground transition-colors hover:text-foreground",
            compact ? "justify-center px-0" : "px-2.5",
          )}
          aria-label={t("search.generations")}
        >
          <Search className="size-4 shrink-0" />
          {!compact && <><span className="flex-1 text-left">{t("search.label")}</span><Kbd>⌘K</Kbd></>}
        </button>

        <div className="mt-auto w-full pb-3">
          <AccountMenu user={user} billing={billing} compact={compact} />
        </div>
      </aside> : null}

      <div className={cn(user && "flex min-w-0 flex-1 flex-col", user && !immersive && "pb-[calc(4rem+env(safe-area-inset-bottom))] md:pb-0")}>
        {user && !immersive ? <header className="sticky top-0 z-30 flex h-14 items-center justify-between border-b bg-background/80 px-4 backdrop-blur md:hidden">
          <Link href="/" className="flex items-center gap-2 font-display text-lg font-bold tracking-tight">
            <Image src="/logo.png" alt="" width={22} height={22} className="size-[22px] dark:invert" />SupoClip
          </Link>
          <div className="flex items-center gap-1">
            <WorkspaceSwitcher compact className="size-9 border-0 bg-transparent" />
            <Announcements key={user.id} userId={user.id} compact />
            <ThemeToggle tooltipSide="bottom" className="rounded-lg p-2 hover:bg-accent [&_svg]:size-5" />
            <button type="button" onClick={() => setPaletteOpen(true)} aria-label={t("search.generations")} className="rounded-lg p-2 text-muted-foreground hover:bg-accent">
              <Search className="size-5" />
            </button>
          </div>
        </header> : null}
        {children}
      </div>

      {user && !immersive ? <nav aria-label={t("search.mobileNav")} style={{ gridTemplateColumns: `repeat(${mobileNav.length}, minmax(0, 1fr))` }} className="fixed inset-x-0 bottom-0 z-40 grid border-t bg-background/90 pb-[env(safe-area-inset-bottom)] backdrop-blur md:hidden">
        {mobileNav.map((item) => {
          const active = item.match(pathname);
          return (
            <Link key={item.href} href={item.href} data-tour={`nav-${item.labelKey}`} aria-current={active ? "page" : undefined}
              className={cn("flex h-16 flex-col items-center justify-center gap-1 text-[11px] font-medium", active ? "text-foreground" : "text-muted-foreground")}>
              <item.icon className={cn("size-5", active && "text-brand")} />{item.label}
            </Link>
          );
        })}
      </nav> : null}

      {user ? <CommandPalette open={paletteOpen} onOpenChange={setPaletteOpen} /> : null}
      <PebbleGuide pathname={pathname} navigate={navigate} immersive={immersive} />
      <FeatureTourHost
        enabled={Boolean(user) && !immersive && !pebbleGuiding}
        skip={pebbleGroup ? PEBBLE_SKIPS_TOURS : undefined}
        pathname={pathname}
        navigate={navigate}
        canStart={tour !== undefined && pathname === "/"}
        busy={tour?.busy}
        onWorkflow={tour?.onWorkflow}
        onEnd={tour?.onEnd}
        onSettingsSection={tour?.onSettingsSection}
      />
    </div>
  );
}

function SidebarLink({ href, label, active, compact, shortcut, className, tour, children }: {
  href: string; label: string; active?: boolean; compact: boolean; shortcut?: string; className?: string; /** `data-tour` target for the welcome tour. */ tour?: string; children: React.ReactNode;
}) {
  const link = (
    <Link
      data-tour={tour}
      href={href}
      aria-current={active ? "page" : undefined}
      aria-label={compact ? label : undefined}
      className={cn(
        "flex h-9 w-full items-center gap-2.5 rounded-lg text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        compact ? "justify-center" : "px-2.5",
        active ? "bg-background text-foreground shadow-xs ring-1 ring-sidebar-border" : "text-muted-foreground hover:bg-sidebar-accent hover:text-foreground",
        className,
      )}
    >
      {children}
      {!compact && <span className="flex-1">{label}</span>}
      {!compact && shortcut && <span className="text-[11px] opacity-60">{shortcut}</span>}
    </Link>
  );
  if (!compact) return link;
  return <Tooltip><TooltipTrigger asChild>{link}</TooltipTrigger><TooltipContent side="right">{label}</TooltipContent></Tooltip>;
}

type PaletteEntry = { id: string; label: string; hint?: string; href: string; icon: typeof Film; generation?: GenerationSummary };

// "New series" and friends open the create screen with that workflow picked.
const WORKFLOW_SHORTCUTS = [
  { id: "series", icon: ListOrdered },
  { id: "topic", icon: Target },
  { id: "recap", icon: ListChecks },
  { id: "trailer", icon: Play },
] as const;

const NO_GENERATIONS: GenerationSummary[] = [];

function CommandPalette({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const t = useTranslations("shell");
  const tHome = useTranslations("home");
  const locale = useLocale();
  const router = useRouter();
  const [query, setQuery] = useState("");
  const { data: session } = useSession();
  // Disabled while closed; opening the palette refetches the list.
  const generations: GenerationSummary[] = useGenerations(open ? session?.user?.id : undefined).data ?? NO_GENERATIONS;
  const [active, setActive] = useState(0);
  const listRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    setQuery("");
    setActive(0);
  }, [open]);

  const socialAccess = useSocialAccess(open);
  const workflowsInfo = useWorkflowsInfo(open);
  const entries = useMemo<PaletteEntry[]>(() => {
    const q = query.trim().toLowerCase();
    const offered = availableWorkflows(workflowsInfo);
    const actions: PaletteEntry[] = [
      { id: "new", label: t("nav.newGeneration"), hint: t("palette.newHint"), href: "/", icon: Plus },
      ...WORKFLOW_SHORTCUTS.filter((shortcut) => offered.has(shortcut.id)).map((shortcut) => ({
        id: `new-${shortcut.id}`, label: tHome(`palette.${shortcut.id}`), href: `/?workflow=${shortcut.id}`, icon: shortcut.icon,
      })),
      { id: "library", label: t("palette.library"), href: "/list", icon: Film },
      ...(socialAccess === true ? [{ id: "calendar", label: t("nav.calendar"), hint: t("palette.calendarHint"), href: "/calendar", icon: CalendarDays },
      { id: "social", label: t("palette.social"), hint: t("palette.socialHint"), href: "/settings/social", icon: Share2 },
      { id: "analytics", label: t("nav.analytics"), hint: t("palette.analyticsHint"), href: "/analytics", icon: ChartColumn }] : []),
      { id: "keys", label: t("nav.agents"), hint: t("palette.agentsHint"), href: "/settings/api-keys", icon: Bot },
      { id: "team", label: t("palette.team"), hint: t("palette.teamHint"), href: "/settings/team", icon: Users },
      { id: "settings", label: t("nav.settings"), href: "/settings", icon: Settings },
    ].filter((entry) => !q || entry.label.toLowerCase().includes(q));
    const matches = generations
      .filter((generation) => !q || generation.source_title.toLowerCase().includes(q))
      .slice(0, 8)
      .map((generation) => ({
        id: generation.id, label: generation.source_title, hint: timeAgo(generation.created_at, locale),
        href: `/tasks/${generation.id}`, icon: Film, generation,
      }));
    return [...actions, ...matches];
  }, [generations, query, socialAccess, workflowsInfo, t, tHome, locale]);

  const go = useCallback((entry: PaletteEntry | undefined) => {
    if (!entry) return;
    onOpenChange(false);
    router.push(entry.href);
  }, [onOpenChange, router]);

  useEffect(() => {
    listRef.current?.querySelector(`[data-index="${active}"]`)?.scrollIntoView({ block: "nearest" });
  }, [active]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent showCloseButton={false} className="top-[18%] max-w-xl translate-y-0 gap-0 overflow-hidden p-0">
        <DialogTitle className="sr-only">{t("search.dialog")}</DialogTitle>
        <div className="flex items-center gap-3 border-b px-4">
          <Search className="size-4 text-muted-foreground" />
          <input
            autoFocus
            value={query}
            onChange={(event) => { setQuery(event.target.value); setActive(0); }}
            onKeyDown={(event) => {
              if (event.key === "ArrowDown") { event.preventDefault(); setActive((i) => Math.min(entries.length - 1, i + 1)); }
              if (event.key === "ArrowUp") { event.preventDefault(); setActive((i) => Math.max(0, i - 1)); }
              if (event.key === "Enter") { event.preventDefault(); go(entries[active]); }
            }}
            placeholder={t("search.placeholder")}
            aria-label={t("search.inputLabel")}
            className="h-12 flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground"
          />
          <Kbd>esc</Kbd>
        </div>
        <div ref={listRef} className="max-h-80 overflow-y-auto p-1.5" role="listbox">
          {entries.length === 0 && <p className="px-3 py-8 text-center text-sm text-muted-foreground">{t("search.empty", { query })}</p>}
          {entries.map((entry, index) => (
            <button
              key={entry.id}
              type="button"
              role="option"
              aria-selected={index === active}
              data-index={index}
              onMouseMove={() => setActive(index)}
              onClick={() => go(entry)}
              className={cn("flex w-full items-center gap-3 rounded-lg px-3 py-2 text-left text-sm", index === active && "bg-accent")}
            >
              <entry.icon className="size-4 shrink-0 text-muted-foreground" />
              <span className="min-w-0 flex-1 truncate">{entry.label}</span>
              {entry.generation ? <StatusBadge status={entry.generation.status} /> : null}
              {entry.hint && <span className="shrink-0 text-xs text-muted-foreground">{entry.hint}</span>}
              {index === active && <CornerDownLeft className="size-3.5 text-muted-foreground" />}
            </button>
          ))}
        </div>
      </DialogContent>
    </Dialog>
  );
}
