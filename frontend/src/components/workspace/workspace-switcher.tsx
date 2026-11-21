"use client";

import { useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import {
  Check,
  ChevronsUpDown,
  Loader2,
  Plus,
  Settings2,
  User as UserIcon,
  Users,
} from "lucide-react";
import { toast } from "sonner";
import { useWorkspace } from "@/hooks/use-workspace";
import { getTeamBillingConfig } from "@/lib/billing-plans";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { cn } from "@/lib/utils";

export function WorkspaceSwitcher({
  compact = false,
  className,
}: {
  compact?: boolean;
  className?: string;
}) {
  const t = useTranslations("shell");
  const tTeam = useTranslations("team");
  const { activeTeam, activeTeamId, teams, switchWorkspace, createTeam } = useWorkspace(true);
  const [createOpen, setCreateOpen] = useState(false);
  const [teamName, setTeamName] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [switching, setSwitching] = useState(false);

  const teamConfig = getTeamBillingConfig();
  const label = activeTeam ? activeTeam.name : t("workspace.personal");

  // Keep solo users' navigation clean until they create or join a team.
  if (teams.length === 0) {
    return null;
  }

  const handleSelect = async (teamId: string | null) => {
    if (teamId === activeTeamId || switching) return;
    setSwitching(true);
    try {
      await switchWorkspace(teamId);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : tTeam("errors.switchFailed"));
      setSwitching(false);
    }
  };

  const handleCreate = async (event: React.FormEvent) => {
    event.preventDefault();
    const trimmed = teamName.trim();
    if (trimmed.length < 2) {
      toast.error(tTeam("errors.nameTooShort"));
      return;
    }
    setSubmitting(true);
    try {
      await createTeam(trimmed);
      setCreateOpen(false);
      setTeamName("");
      window.location.assign("/settings/team");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : tTeam("errors.createFailed"));
      setSubmitting(false);
    }
  };

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <button
            type="button"
            disabled={switching}
            aria-label={t("workspace.switchAria")}
            className={cn(
              "flex items-center gap-2 rounded-lg border border-sidebar-border bg-background text-left text-sm transition-colors hover:bg-sidebar-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-60",
              compact ? "size-9 justify-center px-0" : "h-10 w-full px-2.5",
              className,
            )}
          >
            <span
              className={cn(
                "flex size-6 shrink-0 items-center justify-center rounded-md text-xs font-semibold",
                activeTeam
                  ? "bg-brand/15 text-brand"
                  : "bg-muted text-muted-foreground",
              )}
            >
              {activeTeam ? <Users className="size-3.5" /> : <UserIcon className="size-3.5" />}
            </span>
            {!compact && (
              <>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-xs font-semibold leading-tight">{label}</span>
                  <span className="block truncate text-[11px] leading-tight text-muted-foreground">
                    {activeTeam
                      ? tTeam(`roles.${activeTeam.role}`)
                      : t("workspace.personalSubtitle")}
                  </span>
                </span>
                {switching ? (
                  <Loader2 className="size-3.5 shrink-0 animate-spin text-muted-foreground" />
                ) : (
                  <ChevronsUpDown className="size-3.5 shrink-0 text-muted-foreground" />
                )}
              </>
            )}
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start" className="w-60">
          <div className="px-2 py-1.5 text-xs font-medium text-muted-foreground">
            {t("workspace.label")}
          </div>
          <DropdownMenuItem onSelect={() => void handleSelect(null)} className="gap-2">
            <UserIcon className="size-4 text-muted-foreground" />
            <span className="min-w-0 flex-1 truncate">{t("workspace.personal")}</span>
            {!activeTeamId && <Check className="size-4 shrink-0 text-foreground" />}
          </DropdownMenuItem>
          {teams.length > 0 && <DropdownMenuSeparator />}
          {teams.map((team) => (
            <DropdownMenuItem
              key={team.id}
              onSelect={() => void handleSelect(team.id)}
              className="gap-2"
            >
              <Users className="size-4 shrink-0 text-muted-foreground" />
              <span className="min-w-0 flex-1">
                <span className="block truncate font-medium">{team.name}</span>
                <span className="block text-[11px] text-muted-foreground">
                  {tTeam("seats.shortCount", {
                    occupied: team.occupiedSeats,
                    limit: team.seatLimit,
                  })}
                </span>
              </span>
              {activeTeamId === team.id && <Check className="size-4 shrink-0 text-foreground" />}
            </DropdownMenuItem>
          ))}
          <DropdownMenuSeparator />
          {activeTeam && (
            <DropdownMenuItem asChild className="gap-2">
              <Link href="/settings/team">
                <Settings2 className="size-4 text-muted-foreground" />
                <span>{t("workspace.teamSettings")}</span>
              </Link>
            </DropdownMenuItem>
          )}
          <DropdownMenuItem onSelect={() => setCreateOpen(true)} className="gap-2">
            <Plus className="size-4 text-muted-foreground" />
            <span>{t("workspace.createTeam")}</span>
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>

      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>{tTeam("create.dialogTitle")}</DialogTitle>
            <DialogDescription>
              {tTeam("create.dialogDescription", {
                seats: teamConfig.includedSeats,
                hours: Math.round(teamConfig.baseMinuteLimit / 60),
                price: teamConfig.priceMonthly,
                extraPrice: teamConfig.seatPriceMonthly,
                extraHours: Math.round(teamConfig.extraSeatMinuteLimit / 60),
              })}
            </DialogDescription>
          </DialogHeader>
          <form onSubmit={handleCreate} className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="create-team-name">{tTeam("create.nameLabel")}</Label>
              <Input
                id="create-team-name"
                value={teamName}
                onChange={(event) => setTeamName(event.target.value)}
                placeholder={tTeam("create.namePlaceholder")}
                maxLength={80}
                required
                disabled={submitting}
                autoFocus
              />
            </div>
            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                onClick={() => setCreateOpen(false)}
                disabled={submitting}
              >
                {tTeam("actions.cancel")}
              </Button>
              <Button type="submit" disabled={submitting || teamName.trim().length < 2}>
                {submitting && <Loader2 className="size-4 animate-spin" />}
                {submitting ? tTeam("create.creating") : tTeam("create.submit")}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </>
  );
}
