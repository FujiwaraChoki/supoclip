"use client";

import { useEffect, useState, type ReactNode } from "react";
import Link from "next/link";
import Image from "next/image";
import { usePathname, useRouter } from "next/navigation";
import { ArrowLeft, ChevronRight, List, LogOut, Menu, Settings, Shield, Sparkles, X } from "lucide-react";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { signOut, useSession } from "@/lib/auth-client";
import { formatBillingPlanName, isPaidBillingPlan } from "@/lib/billing-plans";
import { cn } from "@/lib/utils";

/** Only the fields the header renders — pages may hold a richer summary. */
export interface ShellBillingSummary {
  monetization_enabled: boolean;
  plan: string;
  usage_count: number;
  usage_limit: number | null;
  upgrade_required: boolean;
}

export interface Crumb {
  /** Omit on the final crumb — it renders as the current page. */
  href?: string;
  label: string;
}

interface AppShellProps {
  /** Simple "up one level" link, rendered in the sub-bar. */
  back?: { href: string; label: string };
  /** Trail for nested pages (Settings → API Keys). Shares the sub-bar with `back`. */
  breadcrumbs?: Crumb[];
  /**
   * Pages that already load the billing summary pass it in; everyone else lets
   * the shell fetch its own so the usage meter is consistent across the app.
   */
  billingSummary?: ShellBillingSummary | null;
  /** Background for the page wrapper — pages own their palette. */
  className?: string;
  children: ReactNode;
}

interface NavItem {
  href: string;
  label: string;
  icon: typeof List;
  isActive: (pathname: string) => boolean;
}

const NAV_ITEMS: NavItem[] = [
  { href: "/", label: "Create", icon: Sparkles, isActive: (pathname) => pathname === "/" },
  {
    href: "/list",
    label: "My Clips",
    icon: List,
    // A task detail page belongs to the clips section.
    isActive: (pathname) => pathname === "/list" || pathname.startsWith("/tasks"),
  },
  {
    href: "/settings",
    label: "Settings",
    icon: Settings,
    isActive: (pathname) => pathname.startsWith("/settings"),
  },
];

const ADMIN_ITEM: NavItem = {
  href: "/admin",
  label: "Admin",
  icon: Shield,
  isActive: (pathname) => pathname.startsWith("/admin"),
};

function UsageMeter({ summary, className }: { summary: ShellBillingSummary; className?: string }) {
  const ratio = summary.usage_limit ? summary.usage_count / summary.usage_limit : 0;

  return (
    <div className={cn("flex items-center gap-1.5", className)}>
      <div className="h-1.5 flex-1 min-w-16 bg-stone-200 rounded-full overflow-hidden">
        <div
          className={cn(
            "h-full rounded-full transition-all duration-500",
            ratio > 0.8 ? "bg-red-500" : "bg-stone-900",
          )}
          style={{ width: summary.usage_limit ? `${Math.min(ratio * 100, 100)}%` : "0%" }}
        />
      </div>
      <span className="text-[11px] text-muted-foreground tabular-nums whitespace-nowrap">
        {summary.usage_limit
          ? `${summary.usage_count}/${summary.usage_limit}`
          : `${summary.usage_count}`}
      </span>
    </div>
  );
}

export function AppShell({ back, breadcrumbs, billingSummary, className, children }: AppShellProps) {
  const { data: session, isPending } = useSession();
  const router = useRouter();
  const pathname = usePathname() ?? "";
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const [fetchedBilling, setFetchedBilling] = useState<ShellBillingSummary | null>(null);

  const user = session?.user;
  const isAdmin = Boolean((user as { is_admin?: boolean } | undefined)?.is_admin);
  const navItems = isAdmin ? [...NAV_ITEMS, ADMIN_ITEM] : NAV_ITEMS;
  const billing = billingSummary ?? fetchedBilling;
  const ownsBillingFetch = billingSummary === undefined;

  useEffect(() => {
    if (!ownsBillingFetch || !session?.user?.id) return;

    let cancelled = false;
    fetch("/api/tasks/billing-summary", { cache: "no-store" })
      .then((response) => (response.ok ? response.json() : null))
      .then((data: ShellBillingSummary | null) => {
        if (!cancelled && data) setFetchedBilling(data);
      })
      .catch(() => undefined);

    return () => {
      cancelled = true;
    };
  }, [ownsBillingFetch, session?.user?.id]);

  // Single source of truth for sign-out across every page that uses the shell.
  const handleSignOut = async () => {
    await signOut();
    router.push("/sign-in");
    router.refresh();
  };

  const planBadge = billing?.monetization_enabled ? (
    <Badge
      className={cn(
        "text-[10px] px-1.5 py-0 h-5",
        isPaidBillingPlan(billing.plan) && !billing.upgrade_required
          ? "bg-stone-900 text-white"
          : "bg-amber-100 text-amber-800 border border-amber-200",
      )}
    >
      {isPaidBillingPlan(billing.plan) && !billing.upgrade_required
        ? formatBillingPlanName(billing.plan)
        : "Upgrade required"}
    </Badge>
  ) : null;

  return (
    <div className={cn("min-h-screen bg-background", className)}>
      <header className="border-b bg-background relative">
        <div className="max-w-7xl mx-auto px-4 py-4">
          <div className="flex justify-between items-center gap-4">
            <div className="flex items-center gap-6 min-w-0">
              <Link href="/" className="flex items-center gap-3 min-w-0">
                <Image src="/logo.png" alt="" width={24} height={24} className="rounded-lg" />
                <span className="text-xl font-bold text-foreground">SupoClip</span>
              </Link>

              {user && (
                <nav aria-label="Primary" className="hidden md:flex items-center gap-1">
                  {navItems.map((item) => {
                    const active = item.isActive(pathname);
                    return (
                      <Link
                        key={item.href}
                        href={item.href}
                        aria-current={active ? "page" : undefined}
                        className={cn(
                          "rounded-lg px-3 py-2 text-sm font-medium transition-colors",
                          active
                            ? "bg-muted text-foreground"
                            : "text-muted-foreground hover:bg-muted hover:text-foreground",
                        )}
                      >
                        {item.label}
                      </Link>
                    );
                  })}
                </nav>
              )}
            </div>

            {user ? (
              <>
                {/* Desktop account area */}
                <div className="hidden md:flex items-center gap-2">
                  {billing?.monetization_enabled && (
                    <div className="flex items-center gap-2 mr-1">
                      {planBadge}
                      {!billing.upgrade_required && <UsageMeter summary={billing} className="w-24" />}
                    </div>
                  )}
                  <Button variant="outline" size="sm" onClick={handleSignOut}>
                    Sign Out
                  </Button>
                  <Link
                    href="/settings"
                    aria-label="Account settings"
                    className="flex items-center gap-3 hover:bg-muted rounded-lg px-3 py-2 transition-colors"
                  >
                    <Avatar className="w-8 h-8">
                      <AvatarImage src={user.image || ""} />
                      <AvatarFallback className="bg-muted text-foreground text-sm">
                        {user.name?.charAt(0) || user.email?.charAt(0) || "U"}
                      </AvatarFallback>
                    </Avatar>
                    <div className="hidden lg:block">
                      <p className="text-sm font-medium text-foreground">{user.name}</p>
                      <p className="text-xs text-muted-foreground">{user.email}</p>
                    </div>
                  </Link>
                </div>

                {/* Mobile hamburger */}
                <div className="flex items-center gap-2 md:hidden">
                  {planBadge}
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => setMobileMenuOpen(!mobileMenuOpen)}
                    className="p-2"
                    aria-label="Toggle menu"
                    aria-expanded={mobileMenuOpen}
                  >
                    {mobileMenuOpen ? <X className="w-5 h-5" /> : <Menu className="w-5 h-5" />}
                  </Button>
                </div>
              </>
            ) : (
              // Signed out: no user menu, just a way back in. Nothing while the
              // session is still resolving, so the button doesn't flash.
              !isPending && (
                <Link href="/sign-in">
                  <Button size="sm">Sign In</Button>
                </Link>
              )
            )}
          </div>
        </div>

        {/* Mobile menu dropdown */}
        {user && mobileMenuOpen && (
          <div className="md:hidden border-t bg-background absolute left-0 right-0 z-50 shadow-lg">
            <div className="px-4 py-3 space-y-1">
              <Link
                href="/settings"
                onClick={() => setMobileMenuOpen(false)}
                className="flex items-center gap-3 rounded-lg px-3 py-2.5 hover:bg-muted transition-colors"
              >
                <Avatar className="w-8 h-8">
                  <AvatarImage src={user.image || ""} />
                  <AvatarFallback className="bg-muted text-foreground text-sm">
                    {user.name?.charAt(0) || user.email?.charAt(0) || "U"}
                  </AvatarFallback>
                </Avatar>
                <div className="min-w-0">
                  <p className="text-sm font-medium text-foreground truncate">{user.name}</p>
                  <p className="text-xs text-muted-foreground truncate">{user.email}</p>
                </div>
              </Link>

              <Separator />

              {billing?.monetization_enabled && (
                billing.upgrade_required ? (
                  <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2">
                    <p className="text-xs font-medium text-amber-900">Choose a paid plan to process videos.</p>
                  </div>
                ) : (
                  <UsageMeter summary={billing} className="px-3 py-2" />
                )
              )}

              {navItems.map((item) => {
                const NavIcon = item.icon;
                const active = item.isActive(pathname);
                return (
                  <Link
                    key={item.href}
                    href={item.href}
                    onClick={() => setMobileMenuOpen(false)}
                    aria-current={active ? "page" : undefined}
                    className={cn(
                      "flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm transition-colors",
                      active
                        ? "bg-muted font-medium text-foreground"
                        : "text-muted-foreground hover:bg-muted hover:text-foreground",
                    )}
                  >
                    <NavIcon className="w-4 h-4" />
                    {item.label}
                  </Link>
                );
              })}

              <Separator />

              <button
                onClick={() => {
                  setMobileMenuOpen(false);
                  handleSignOut();
                }}
                className="flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm text-red-600 hover:bg-red-50 transition-colors w-full text-left"
              >
                <LogOut className="w-4 h-4" />
                Sign Out
              </button>
            </div>
          </div>
        )}
      </header>

      {(back || breadcrumbs?.length) && (
        <div className="border-b bg-background">
          <div className="max-w-7xl mx-auto px-4 py-2">
            {back && (
              <Link href={back.href}>
                <Button variant="ghost" size="sm" className="text-muted-foreground hover:text-foreground">
                  <ArrowLeft className="w-4 h-4" />
                  {back.label}
                </Button>
              </Link>
            )}

            {breadcrumbs?.length ? (
              <nav aria-label="Breadcrumb">
                <ol className="flex flex-wrap items-center gap-1 text-sm">
                  {breadcrumbs.map((crumb, index) => (
                    <li key={`${crumb.label}-${index}`} className="flex items-center gap-1">
                      {index > 0 && (
                        <ChevronRight className="w-3.5 h-3.5 text-muted-foreground" aria-hidden="true" />
                      )}
                      {crumb.href ? (
                        <Link
                          href={crumb.href}
                          className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-muted-foreground hover:bg-muted hover:text-foreground transition-colors"
                        >
                          {index === 0 && <ArrowLeft className="w-3.5 h-3.5" aria-hidden="true" />}
                          {crumb.label}
                        </Link>
                      ) : (
                        <span aria-current="page" className="px-2 py-1 font-medium text-foreground">
                          {crumb.label}
                        </span>
                      )}
                    </li>
                  ))}
                </ol>
              </nav>
            ) : null}
          </div>
        </div>
      )}

      {children}
    </div>
  );
}
