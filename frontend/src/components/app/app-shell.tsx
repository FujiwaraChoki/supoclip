"use client";

import Link from "next/link";
import Image from "next/image";
import { usePathname, useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Bot, CornerDownLeft, Film, LogOut, Plus, Search, Settings, Shield, Sparkles,
} from "lucide-react";
import { toast } from "sonner";
import { signOut, useSession } from "@/lib/auth-client";
import { useBillingSummary } from "@/hooks/use-billing-summary";
import { formatBillingPlanName } from "@/lib/billing-plans";
import { fetchGenerations, timeAgo, type GenerationSummary } from "@/lib/generations";
import { StatusBadge } from "@/components/app/status-badge";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { Kbd } from "@/components/ui/kbd";
import { cn } from "@/lib/utils";

type NavItem = { href: string; label: string; icon: typeof Film; match: (path: string) => boolean };

const NAV: NavItem[] = [
  { href: "/", label: "Create", icon: Sparkles, match: (p) => p === "/" },
  { href: "/list", label: "Library", icon: Film, match: (p) => p.startsWith("/list") || p.startsWith("/tasks") },
  { href: "/settings/api-keys", label: "Agents & API", icon: Bot, match: (p) => p.startsWith("/settings/api-keys") },
  { href: "/settings", label: "Settings", icon: Settings, match: (p) => p === "/settings" },
];

function initials(name?: string | null, email?: string | null) {
  const source = name?.trim() || email || "?";
  return source.split(/[\s@.]+/).filter(Boolean).slice(0, 2).map((part) => part[0]?.toUpperCase()).join("");
}

/** Signed-in application chrome: sidebar on desktop, tab bar on mobile, ⌘K palette everywhere. */
export function AppShell({ children }: { children: React.ReactNode }) {
  const { data: session } = useSession();
  const pathname = usePathname();
  const router = useRouter();
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [billing] = useBillingSummary(Boolean(session?.user));
  // The editor needs every pixel — collapse the sidebar to an icon rail there.
  const compact = /^\/tasks\/[^/]+\/edit/.test(pathname);

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
  const admin = Boolean((user as { is_admin?: boolean } | undefined)?.is_admin);
  const items = admin ? [...NAV, { href: "/admin", label: "Admin", icon: Shield, match: (p: string) => p.startsWith("/admin") }] : NAV;

  return (
    <div data-app-shell={user ? "" : undefined} className={cn(user && "flex min-h-dvh bg-canvas")}>
      {user ? <aside
        className={cn(
          "sticky top-0 hidden h-dvh shrink-0 flex-col border-r border-sidebar-border bg-sidebar md:flex",
          compact ? "w-16 items-center px-2" : "w-60 px-3",
        )}
      >
        <Link href="/" className={cn("flex h-14 items-center gap-2 font-display text-lg font-bold tracking-tight", compact ? "justify-center" : "px-2")}>
          <Image src="/logo.png" alt="" width={24} height={24} className="size-6" priority />
          {!compact && "SupoClip"}
        </Link>

        <SidebarLink compact={compact} href="/" label="New generation" shortcut="N" className="mb-4 mt-1 bg-foreground text-background hover:bg-foreground/90 hover:text-background">
          <Plus className="size-4" />
        </SidebarLink>

        <nav aria-label="Main navigation" className="flex w-full flex-col gap-0.5">
          {items.map((item) => {
            const active = item.match(pathname);
            return (
              <SidebarLink key={item.href} compact={compact} href={item.href} label={item.label} active={active}>
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
          aria-label="Search generations"
        >
          <Search className="size-4 shrink-0" />
          {!compact && <><span className="flex-1 text-left">Search</span><Kbd>⌘K</Kbd></>}
        </button>

        <div className="mt-auto w-full space-y-2 pb-3">
          {!compact && billing?.monetization_enabled && <UsageCard billing={billing} />}
          <UserRow compact={compact} name={user.name} email={user.email} />
        </div>
      </aside> : null}

      <div className={cn(user && "flex min-w-0 flex-1 flex-col pb-[calc(4rem+env(safe-area-inset-bottom))] md:pb-0")}>
        {user ? <header className="sticky top-0 z-30 flex h-14 items-center justify-between border-b bg-background/80 px-4 backdrop-blur md:hidden">
          <Link href="/" className="flex items-center gap-2 font-display text-lg font-bold tracking-tight">
            <Image src="/logo.png" alt="" width={22} height={22} className="size-[22px]" />SupoClip
          </Link>
          <button type="button" onClick={() => setPaletteOpen(true)} aria-label="Search generations" className="rounded-lg p-2 text-muted-foreground hover:bg-accent">
            <Search className="size-5" />
          </button>
        </header> : null}
        {children}
      </div>

      {user ? <nav aria-label="Mobile navigation" className="fixed inset-x-0 bottom-0 z-40 grid grid-cols-4 border-t bg-background/90 pb-[env(safe-area-inset-bottom)] backdrop-blur md:hidden">
        {NAV.map((item) => {
          const active = item.match(pathname);
          return (
            <Link key={item.href} href={item.href} aria-current={active ? "page" : undefined}
              className={cn("flex h-16 flex-col items-center justify-center gap-1 text-[11px] font-medium", active ? "text-foreground" : "text-muted-foreground")}>
              <item.icon className={cn("size-5", active && "text-brand")} />{item.label}
            </Link>
          );
        })}
      </nav> : null}

      {user ? <CommandPalette open={paletteOpen} onOpenChange={setPaletteOpen} /> : null}
    </div>
  );
}

function SidebarLink({ href, label, active, compact, shortcut, className, children }: {
  href: string; label: string; active?: boolean; compact: boolean; shortcut?: string; className?: string; children: React.ReactNode;
}) {
  const link = (
    <Link
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

function UsageCard({ billing }: { billing: NonNullable<ReturnType<typeof useBillingSummary>[0]> }) {
  const limit = billing.usage_limit;
  const pct = limit ? Math.min(100, (billing.usage_count / limit) * 100) : 0;
  return (
    <Link href="/settings" className="block rounded-xl border border-sidebar-border bg-background p-3 text-xs transition-colors hover:border-foreground/20">
      <div className="flex items-center justify-between">
        <span className="font-semibold">{formatBillingPlanName(billing.plan)}</span>
        <span className="text-muted-foreground tabular-nums">{billing.usage_count}{limit ? ` / ${limit}` : ""}</span>
      </div>
      {limit ? <div className="mt-2 h-1 overflow-hidden rounded-full bg-muted"><div className="h-full rounded-full bg-brand" style={{ width: `${pct}%` }} /></div> : null}
      <p className="mt-2 text-muted-foreground">{billing.upgrade_required ? "Choose a plan to keep clipping" : "generations used this period"}</p>
    </Link>
  );
}

function UserRow({ compact, name, email }: { compact: boolean; name?: string | null; email?: string | null }) {
  const [signingOut, setSigningOut] = useState(false);
  const handleSignOut = async () => {
    setSigningOut(true);
    try { await signOut(); window.location.assign("/sign-in"); }
    catch { toast.error("Could not sign out. Please try again."); setSigningOut(false); }
  };
  return (
    <div className={cn("flex items-center gap-2 rounded-lg", compact ? "flex-col" : "px-1.5 py-1")}>
      <span className="flex size-8 shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-brand to-amber-400 text-xs font-semibold text-white">
        {initials(name, email)}
      </span>
      {!compact && (
        <div className="min-w-0 flex-1 leading-tight">
          <p className="truncate text-sm font-medium">{name || "Creator"}</p>
          <p className="truncate text-xs text-muted-foreground">{email}</p>
        </div>
      )}
      <Tooltip>
        <TooltipTrigger asChild>
          <button type="button" onClick={() => void handleSignOut()} disabled={signingOut} aria-label="Sign out"
            className="rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-sidebar-accent hover:text-foreground disabled:opacity-50">
            <LogOut className="size-4" />
          </button>
        </TooltipTrigger>
        <TooltipContent side={compact ? "right" : "top"}>{signingOut ? "Signing out…" : "Sign out"}</TooltipContent>
      </Tooltip>
    </div>
  );
}

type PaletteEntry = { id: string; label: string; hint?: string; href: string; icon: typeof Film; generation?: GenerationSummary };

function CommandPalette({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const router = useRouter();
  const [query, setQuery] = useState("");
  const [generations, setGenerations] = useState<GenerationSummary[]>([]);
  const [active, setActive] = useState(0);
  const listRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    setQuery("");
    setActive(0);
    void fetchGenerations().then(setGenerations).catch(() => {});
  }, [open]);

  const entries = useMemo<PaletteEntry[]>(() => {
    const q = query.trim().toLowerCase();
    const actions: PaletteEntry[] = [
      { id: "new", label: "New generation", hint: "Paste a link or upload", href: "/", icon: Plus },
      { id: "library", label: "Open library", href: "/list", icon: Film },
      { id: "keys", label: "Agents & API", hint: "API keys & MCP", href: "/settings/api-keys", icon: Bot },
      { id: "settings", label: "Settings", href: "/settings", icon: Settings },
    ].filter((entry) => !q || entry.label.toLowerCase().includes(q));
    const matches = generations
      .filter((generation) => !q || generation.source_title.toLowerCase().includes(q))
      .slice(0, 8)
      .map((generation) => ({
        id: generation.id, label: generation.source_title, hint: timeAgo(generation.created_at),
        href: `/tasks/${generation.id}`, icon: Film, generation,
      }));
    return [...actions, ...matches];
  }, [generations, query]);

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
        <DialogTitle className="sr-only">Search</DialogTitle>
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
            placeholder="Search generations or jump to…"
            aria-label="Search generations or jump to a page"
            className="h-12 flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground"
          />
          <Kbd>esc</Kbd>
        </div>
        <div ref={listRef} className="max-h-80 overflow-y-auto p-1.5" role="listbox">
          {entries.length === 0 && <p className="px-3 py-8 text-center text-sm text-muted-foreground">Nothing matches “{query}”.</p>}
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
