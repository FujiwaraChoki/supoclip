"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useFormatter, useTranslations } from "next-intl";
import { useTheme } from "next-themes";
import { ArrowRight, CreditCard, Gauge, LogOut, Moon, Plus, Settings2, Sun, User as UserIcon, Users } from "lucide-react";
import { toast } from "sonner";
import { signOut } from "@/lib/auth-client";
import { resetAnalytics } from "@/lib/posthog";
import { billingPlanMessageKey } from "@/lib/billing-plans";
import { workspaceAllowance, type WorkspaceUsage, type WorkspaceUsagePayload } from "@/lib/workspace-usage";
import type { BillingSummary } from "@/hooks/use-billing-summary";
import { getUpgradeState } from "@/components/billing/upgrade-prompt";
import { Announcements } from "@/components/announcements";
import { CreateTeamDialog } from "@/components/workspace/create-team-dialog";
import { useWorkspace } from "@/hooks/use-workspace";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { cn } from "@/lib/utils";

export function initials(name?: string | null, email?: string | null) {
  const source = name?.trim() || email || "?";
  return source.split(/[\s@.]+/).filter(Boolean).slice(0, 2).map((part) => part[0]?.toUpperCase()).join("");
}

// One borderless wash for the whole row and its buttons; fades in and out.
const HOVER = "transition-colors duration-200 ease-out hover:bg-foreground/[0.055]";

/**
 * The sidebar footer: who is signed in, how much of this month's allowance is
 * left (ring + subtitle), What's new, and a menu with usage per workspace,
 * team actions, theme and sign out.
 */
export function AccountMenu({ user, billing, compact }: {
  user: { id: string; name?: string | null; email?: string | null };
  billing: BillingSummary | null;
  compact: boolean;
}) {
  const t = useTranslations("shell");
  const tCommon = useTranslations("common");
  const format = useFormatter();
  const { resolvedTheme, setTheme } = useTheme();
  const [createOpen, setCreateOpen] = useState(false);
  const [signingOut, setSigningOut] = useState(false);
  const { teamsAvailable } = useWorkspace(true);

  const metered = Boolean(billing?.monetization_enabled);
  const allowance = billing && metered ? workspaceAllowance(billing) : null;
  const isTeam = billing?.workspace_type === "team";
  const planName = billing ? tCommon(`plans.${billingPlanMessageKey(billing.plan)}`) : "";
  const subtitle = !allowance
    ? user.email
    : allowance.paused
    ? t("account.seatPaused")
    : allowance.left === null
    ? isTeam ? billing?.team_name || planName : planName
    : isTeam
    ? t("account.minutesLeft", { count: format.number(allowance.left) })
    : t("account.planMinutesLeft", { plan: planName, count: format.number(allowance.left) });
  const ringed = allowance && (allowance.poolLimit !== null || allowance.memberLimit !== null);

  const handleSignOut = async () => {
    setSigningOut(true);
    try { await signOut(); resetAnalytics(); window.location.assign("/sign-in"); }
    catch { toast.error(t("user.signOutError")); setSigningOut(false); }
  };

  const avatar = (
    <span className={cn("flex shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-brand to-amber-400 font-semibold text-white", ringed ? "size-7 text-[11px]" : "size-8 text-xs")}>
      {initials(user.name, user.email)}
    </span>
  );

  return (
    <div className={cn("group/account flex items-center gap-0.5 rounded-lg has-[[data-state=open]]:bg-foreground/[0.055]", HOVER, compact && "flex-col")}>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <button
            type="button"
            data-tour={metered ? "usage" : undefined}
            aria-label={compact ? t("account.menu") : undefined}
            className={cn(
              "flex min-w-0 items-center gap-2.5 rounded-lg p-2 text-left outline-none focus-visible:ring-2 focus-visible:ring-ring",
              compact ? "justify-center" : "flex-1",
            )}
          >
            {ringed ? <UsageRing fraction={allowance.fraction}>{avatar}</UsageRing> : avatar}
            {!compact && (
              <span className="min-w-0 flex-1 leading-tight">
                <span className="block truncate text-sm font-medium">{user.name || t("user.creator")}</span>
                <span className={cn("block truncate text-xs", allowance?.low || allowance?.paused ? "font-medium text-brand" : "text-muted-foreground")}>{subtitle}</span>
              </span>
            )}
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent side={compact ? "right" : "top"} align="start" sideOffset={6} className="w-64 rounded-xl p-1">
          <div className="flex items-center gap-2.5 px-2 pb-2.5 pt-2">
            <span className="flex size-9 shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-brand to-amber-400 text-[13px] font-semibold text-white">
              {initials(user.name, user.email)}
            </span>
            <div className="min-w-0 leading-tight">
              <p className="text-sm font-semibold">{user.name || t("user.creator")}</p>
              {user.email && <p className="mt-0.5 break-all text-xs text-muted-foreground">{user.email}</p>}
            </div>
          </div>
          {billing && metered && allowance && (
            <>
              <DropdownMenuSeparator className="mt-0" />
              <DropdownMenuSub>
                <DropdownMenuSubTrigger className="gap-2">
                  <Gauge />
                  <span className="flex-1">{t("account.usage")}</span>
                  {allowance.left !== null && (
                    <span className={cn("text-xs tabular-nums", allowance.low ? "font-medium text-brand" : "text-muted-foreground")}>
                      {t("account.minutesLeft", { count: format.number(allowance.left) })}
                    </span>
                  )}
                </DropdownMenuSubTrigger>
                <DropdownMenuSubContent sideOffset={8} className="w-72 rounded-xl p-1">
                  <WorkspaceUsageList current={billing} />
                </DropdownMenuSubContent>
              </DropdownMenuSub>
            </>
          )}
          <DropdownMenuSeparator />
          {isTeam && (
            <DropdownMenuItem asChild>
              <Link href="/settings/team"><Settings2 />{t("workspace.teamSettings")}</Link>
            </DropdownMenuItem>
          )}
          {teamsAvailable && (
            <DropdownMenuItem onSelect={() => setCreateOpen(true)}>
              <Plus />{t("workspace.createTeam")}
            </DropdownMenuItem>
          )}
          <div className="flex items-center gap-2 px-2 py-1 text-sm">
            {resolvedTheme === "dark" ? <Moon className="size-4 text-muted-foreground" /> : <Sun className="size-4 text-muted-foreground" />}
            <span className="flex-1">{t("account.theme")}</span>
            <div role="group" aria-label={t("account.theme")} className="flex gap-px rounded-md bg-muted p-0.5">
              {(["light", "dark"] as const).map((option) => {
                const Icon = option === "light" ? Sun : Moon;
                const active = (resolvedTheme ?? "light") === option;
                return (
                  <DropdownMenuItem
                    key={option}
                    role="menuitemradio"
                    aria-checked={active}
                    aria-label={t(option === "light" ? "account.themeLight" : "account.themeDark")}
                    onSelect={(event) => { event.preventDefault(); setTheme(option); }}
                    className={cn(
                      "size-6 justify-center rounded-[6px] p-0 transition-colors duration-150",
                      active ? "bg-background text-foreground shadow-xs focus:bg-background [&_svg:not([class*='text-'])]:text-foreground" : "focus:bg-background/60",
                    )}
                  >
                    <Icon className="size-3.5" />
                  </DropdownMenuItem>
                );
              })}
            </div>
          </div>
          <DropdownMenuSeparator />
          <DropdownMenuItem disabled={signingOut} onSelect={() => void handleSignOut()}>
            <LogOut />{signingOut ? t("user.signingOut") : t("user.signOut")}
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>

      <Announcements
        key={user.id}
        userId={user.id}
        compact
        className={cn(
          "size-9 shrink-0 rounded-md p-0 data-[state=open]:bg-foreground/[0.055] data-[state=open]:text-foreground",
          HOVER,
          !compact && "mr-1",
        )}
      />

      <CreateTeamDialog open={createOpen} onOpenChange={setCreateOpen} />
    </div>
  );
}

function UsageRing({ fraction, children }: { fraction: number; children: React.ReactNode }) {
  const radius = 16.5;
  const circumference = 2 * Math.PI * radius;
  return (
    <span className="relative flex size-9 shrink-0 items-center justify-center">
      <svg viewBox="0 0 36 36" aria-hidden className="absolute inset-0 size-9 -rotate-90">
        <circle cx="18" cy="18" r={radius} fill="none" strokeWidth="2" className="stroke-sidebar-border" />
        {fraction > 0 && (
          <circle
            cx="18" cy="18" r={radius} fill="none" strokeWidth="2" strokeLinecap="round"
            strokeDasharray={circumference} strokeDashoffset={circumference * (1 - fraction)}
            className="stroke-brand transition-[stroke-dashoffset] duration-500 ease-out"
          />
        )}
      </svg>
      {children}
    </span>
  );
}

/** Every workspace's allowance, the current one first. Shows the current one alone until the rest load. */
function WorkspaceUsageList({ current }: { current: BillingSummary }) {
  const t = useTranslations("shell");
  const { data: payload, isError: failed } = useQuery({
    queryKey: ["billing-usage"],
    queryFn: async () => {
      const response = await fetch("/api/billing/usage", { cache: "no-store" });
      if (!response.ok) throw new Error(`Failed to fetch usage: ${response.status}`);
      return (await response.json()) as WorkspaceUsagePayload;
    },
    retry: false,
  });

  const currentTeamId = current.workspace_type === "team" ? current.team_id ?? null : null;
  const fallback: WorkspaceUsage = { teamId: currentTeamId, name: current.team_name ?? null, role: null, summary: current };
  const loaded = payload?.workspaces.length ? payload.workspaces : [fallback];
  const items = [
    ...loaded.filter((item) => item.teamId === currentTeamId),
    ...loaded.filter((item) => item.teamId !== currentTeamId),
  ];

  return (
    <>
      <div className="px-2 py-1.5 text-xs font-medium text-muted-foreground">{t("account.usageThisMonth")}</div>
      <div className="divide-y">
        {items.map((item) => (
          <WorkspaceUsageBlock key={item.teamId ?? "personal"} item={item} current={item.teamId === currentTeamId} />
        ))}
      </div>
      {failed && <p className="px-2 pb-1.5 text-xs text-muted-foreground">{t("account.usageError")}</p>}
      <DropdownMenuSeparator />
      <DropdownMenuItem asChild>
        <Link href={currentTeamId ? "/settings/team" : "/settings#plan"}>
          <CreditCard /><span className="flex-1">{t("account.billing")}</span><ArrowRight className="size-3.5" />
        </Link>
      </DropdownMenuItem>
    </>
  );
}

function WorkspaceUsageBlock({ item, current }: { item: WorkspaceUsage; current: boolean }) {
  const t = useTranslations("shell");
  const tCommon = useTranslations("common");
  const format = useFormatter();
  const allowance = workspaceAllowance(item.summary);
  const upgrade = getUpgradeState(item.summary);
  const planName = tCommon(`plans.${billingPlanMessageKey(item.summary.plan)}`);
  const canManage = item.role === "owner" || item.role === "admin";

  let nudge: { label: string; href?: string } | null = null;
  if (!item.teamId) {
    if (!upgrade?.isPaid) nudge = { label: t("usage.pickPlan"), href: "/settings#plan" };
    else if (upgrade.nextPlan && (upgrade.atLimit || upgrade.nearLimit)) {
      nudge = { label: t("usage.moreWith", { name: tCommon(`plans.${billingPlanMessageKey(upgrade.nextPlan.id)}`) }), href: "/settings#plan" };
    }
  } else if (allowance.binding === "member" && allowance.low && !allowance.paused) {
    nudge = { label: t("account.askAdmin") };
  } else if (canManage && (!upgrade?.isPaid || allowance.low)) {
    nudge = { label: upgrade?.isPaid ? t("usage.addTeamSeats") : t("usage.activateTeam"), href: "/settings/team" };
  }

  return (
    <div className="grid gap-1.5 px-2 py-2 text-xs">
      <div className="flex items-center gap-2">
        {item.teamId ? <Users className="size-3.5 shrink-0 text-muted-foreground" /> : <UserIcon className="size-3.5 shrink-0 text-muted-foreground" />}
        <span className="min-w-0 flex-1 truncate text-[13px] font-medium">{item.teamId ? item.name || planName : t("workspace.personal")}</span>
        {current && <span className="text-[11px] text-muted-foreground">{t("account.current")}</span>}
      </div>
      {item.teamId ? (
        <>
          {allowance.paused ? (
            <p className="text-muted-foreground">{t("account.seatPausedLong")}</p>
          ) : allowance.memberLimit !== null ? (
            <Meter label={t("account.yourLimit")} used={allowance.memberUsed} limit={allowance.memberLimit} />
          ) : null}
          {allowance.memberLimit !== null ? (
            <p className="tabular-nums text-muted-foreground">
              {allowance.poolLimit !== null
                ? t("account.teamPoolOf", { used: format.number(allowance.poolUsed), limit: format.number(allowance.poolLimit) })
                : t("account.teamPoolUsed", { used: format.number(allowance.poolUsed) })}
            </p>
          ) : (
            <Meter label={t("account.teamPool")} used={allowance.poolUsed} limit={allowance.poolLimit} />
          )}
        </>
      ) : (
        <Meter label={t("account.planLabel", { plan: planName })} used={allowance.poolUsed} limit={allowance.poolLimit} />
      )}
      {nudge && (nudge.href ? (
        <DropdownMenuItem asChild className="-mx-1 px-1 py-1 text-xs font-medium">
          <Link href={nudge.href}>{nudge.label}<ArrowRight className="ml-auto size-3 text-foreground" /></Link>
        </DropdownMenuItem>
      ) : (
        <p className="font-medium text-brand">{nudge.label}</p>
      ))}
    </div>
  );
}

function Meter({ label, used, limit }: { label: string; used: number; limit: number | null }) {
  const t = useTranslations("shell");
  const format = useFormatter();
  return (
    <div className="grid gap-1.5">
      <div className="flex items-center justify-between gap-2">
        <span className="text-muted-foreground">{label}</span>
        <span className="tabular-nums">{limit !== null
          ? t("usage.minutesOf", { used: format.number(used), limit: format.number(limit) })
          : t("usage.minutes", { used: format.number(used) })}</span>
      </div>
      {limit ? (
        <div className="h-1 overflow-hidden rounded-full bg-muted">
          <div className="h-full rounded-full bg-brand" style={{ width: `${Math.min(100, (used / limit) * 100)}%` }} />
        </div>
      ) : null}
    </div>
  );
}
