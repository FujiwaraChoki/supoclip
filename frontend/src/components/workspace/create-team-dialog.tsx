"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";
import { Loader2 } from "lucide-react";
import { toast } from "sonner";
import { useWorkspace, WorkspaceRequestError } from "@/hooks/use-workspace";
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
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

/** Names a new team, creates it and opens its settings. Used by the workspace switcher and the account menu. */
export function CreateTeamDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const tTeam = useTranslations("team");
  const { createTeam } = useWorkspace(false);
  const [teamName, setTeamName] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const teamConfig = getTeamBillingConfig();

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
      onOpenChange(false);
      setTeamName("");
      window.location.assign("/settings/team");
    } catch (error) {
      toast.error(
        error instanceof WorkspaceRequestError && error.code === "teams_unavailable"
          ? tTeam("errors.teamsUnavailable")
          : (error instanceof Error && error.message) || tTeam("errors.createFailed"),
      );
      setSubmitting(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
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
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)} disabled={submitting}>
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
  );
}
