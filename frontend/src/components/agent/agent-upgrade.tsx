"use client";

import { useCallback, useState } from "react";
import { useTranslations } from "next-intl";
import { useQueryClient } from "@tanstack/react-query";
import { Crown } from "lucide-react";

import { UpgradeDialog } from "@/components/billing/upgrade-prompt";
import { useAgentStatus } from "@/hooks/use-agent-status";
import { useBillingSummary } from "@/hooks/use-billing-summary";
import { agentKeys, trialLeft, type AgentStatus } from "@/lib/agent";
import { useSession } from "@/lib/auth-client";
import { cn } from "@/lib/utils";

/** The upgrade dialog about Agent (Creator highlighted, the agent film as its picture), opened from anywhere. */
export function useAgentUpgrade(source: string) {
  const { data: session } = useSession();
  const [billing] = useBillingSummary(Boolean(session?.user));
  const [open, setOpen] = useState(false);
  const [trialUsed, setTrialUsed] = useState(false);
  const show = useCallback((used = false) => {
    setTrialUsed(used);
    setOpen(true);
  }, []);
  const dialog = (
    <UpgradeDialog open={open} onOpenChange={setOpen} reason={{ kind: "agent", trialUsed }} billing={billing} source={source} />
  );
  return { show, dialog };
}

/** Whether an API error means "this needs Creator" (no plan for it, or the free messages are used). */
export function needsCreator(error: unknown): { trialUsed: boolean } | null {
  const code = (error as { code?: string } | null)?.code;
  if (code === "AGENT_PLAN") return { trialUsed: false };
  if (code === "AGENT_TRIAL_USED") return { trialUsed: true };
  return null;
}

/**
 * Everything a place that sends messages needs about the plan: the status, the upgrade dialog,
 * and `handleError`, which turns a "needs Creator" answer into that dialog instead of a toast.
 */
export function useAgentGate(source: string) {
  const status = useAgentStatus();
  const queryClient = useQueryClient();
  const { show, dialog } = useAgentUpgrade(source);
  const refresh = useCallback(() => queryClient.invalidateQueries({ queryKey: agentKeys.status }), [queryClient]);
  const handleError = useCallback(
    (error: unknown) => {
      const need = needsCreator(error);
      if (!need) return false;
      show(need.trialUsed);
      void refresh();
      return true;
    },
    [refresh, show],
  );
  const locked = Boolean(status && !status.available && (status.reason === "plan" || status.reason === "trial_used"));
  return { status, locked, handleError, refresh, show, dialog };
}

/** One quiet line by the composer: free messages left on Pro, or that Agent needs Creator now. */
export function TrialNote({ status, onUpgrade, className }: { status: AgentStatus | null | undefined; onUpgrade: (trialUsed: boolean) => void; className?: string }) {
  const t = useTranslations("agent.trial");
  if (!status) return null;
  const left = trialLeft(status);
  const trial = status.available && status.access === "trial" && left !== null;
  const locked = !status.available && (status.reason === "plan" || status.reason === "trial_used");
  if (!trial && !locked) return null;
  const trialUsed = status.reason === "trial_used";
  return (
    <p className={cn("flex flex-wrap items-center justify-center gap-x-1.5 gap-y-1 text-center text-xs text-muted-foreground", className)}>
      {trial ? (
        <span className="inline-flex items-center gap-1.5">
          <span className="flex gap-0.5" aria-hidden>
            {Array.from({ length: status.trial!.limit }, (_, i) => (
              <span key={i} className={cn("size-1.5 rounded-full", i < left ? "bg-brand" : "bg-muted-foreground/25")} />
            ))}
          </span>
          {t("left", { count: left })}
        </span>
      ) : (
        <span>{t(trialUsed ? "used" : "locked")}</span>
      )}
      <span aria-hidden>·</span>
      <button type="button" onClick={() => onUpgrade(trialUsed)} className="inline-flex items-center gap-1 font-medium text-foreground underline-offset-2 hover:underline">
        <Crown className="size-3 text-brand" />
        {t(trial ? "unlimited" : "cta")}
      </button>
    </p>
  );
}
