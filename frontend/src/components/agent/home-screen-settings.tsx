"use client";

import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { Brain, Check, Crown, LayoutPanelTop, Loader2, X } from "lucide-react";
import { toast } from "sonner";

import { AgentMark } from "@/components/agent/agent-mark";
import { useAgentStatus } from "@/hooks/use-agent-status";
import { TrialNote, useAgentUpgrade } from "@/components/agent/agent-upgrade";
import { Button } from "@/components/ui/button";
import { agentKeys, agentOffered, saveHomeMode, type AgentStatus, type HomeMode } from "@/lib/agent";
import { cn } from "@/lib/utils";

type Memory = { id: string; note: string };

async function fetchMemories(): Promise<Memory[]> {
  const response = await fetch("/api/agent/memories", { cache: "no-store" });
  if (!response.ok) throw new Error(String(response.status));
  return ((await response.json()) as { memories: Memory[] }).memories;
}

/** Settings → Home screen: agent or create form, and what the agent remembers. Saves on click. */
export function HomeScreenSettings({ Section }: {
  Section: (props: { id?: string; icon: React.ReactNode; title: string; description: string; children: React.ReactNode }) => React.ReactNode;
}) {
  const t = useTranslations("agent");
  const queryClient = useQueryClient();
  const status = useAgentStatus();
  const upgrade = useAgentUpgrade("settings_home_screen");
  const [saving, setSaving] = useState<HomeMode | null>(null);
  const { data: memories } = useQuery({ queryKey: ["agent", "memories"], queryFn: fetchMemories, enabled: status?.available === true });

  if (!status || !agentOffered(status)) return null;
  // Matches home: accounts never asked stay on the form until they pick the agent.
  const current: HomeMode = status.home_mode ?? (status.offer_choice ? "agent" : "manual");

  const choose = async (mode: HomeMode) => {
    if (mode === current && status.home_mode !== null) return;
    setSaving(mode);
    try {
      await saveHomeMode(mode);
      queryClient.setQueryData<AgentStatus>(agentKeys.status, { ...status, home_mode: mode });
      toast.success(t("settings.saved"));
    } catch {
      toast.error(t("choice.saveFailed"));
    } finally {
      setSaving(null);
    }
  };

  const forget = async (id: string) => {
    queryClient.setQueryData<Memory[]>(["agent", "memories"], (list) => list?.filter((m) => m.id !== id));
    const response = await fetch(`/api/agent/memories/${id}`, { method: "DELETE" });
    if (!response.ok) {
      toast.error(t("settings.forgetFailed"));
      void queryClient.invalidateQueries({ queryKey: ["agent", "memories"] });
    }
  };

  return (
    <Section id="home-screen" icon={<LayoutPanelTop className="size-4" />} title={t("settings.title")} description={t("settings.description")}>
      {!status.available ? (
        <div className="flex flex-col gap-4 rounded-xl border bg-muted/30 p-4 sm:flex-row sm:items-center">
          <AgentMark size="md" />
          <span className="min-w-0 flex-1">
            <span className="flex items-center gap-2 text-sm font-medium">
              {t("choice.agent.title")}
              <span className="inline-flex items-center gap-1 rounded-full border border-brand/25 bg-brand/5 px-1.5 py-px text-[10px] font-medium leading-4 text-brand">
                <Crown className="size-2.5" />{t("choice.creatorTag")}
              </span>
            </span>
            <span className="mt-0.5 block text-xs text-muted-foreground">
              {t(status.reason === "trial_used" ? "settings.trialUsed" : "settings.creatorOnly")}
            </span>
          </span>
          <Button type="button" size="sm" className="rounded-full" onClick={() => upgrade.show(status.reason === "trial_used")}>
            {t("trial.cta")}
          </Button>
        </div>
      ) : (
        <div className="space-y-5">
          <div role="radiogroup" aria-label={t("settings.title")} className="grid gap-2 sm:grid-cols-2">
            {(["agent", "manual"] as const).map((mode) => {
              const on = current === mode;
              return (
                <button
                  key={mode}
                  type="button"
                  role="radio"
                  aria-checked={on}
                  onClick={() => void choose(mode)}
                  disabled={saving !== null}
                  className={cn(
                    "relative flex items-start gap-3 rounded-xl border p-3 text-left transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                    on && "border-foreground ring-1 ring-foreground",
                  )}
                >
                  {mode === "agent" ? <AgentMark /> : (
                    <span className="flex size-8 shrink-0 items-center justify-center rounded-full bg-muted"><LayoutPanelTop className="size-4" /></span>
                  )}
                  <span className="min-w-0">
                    <span className="block text-sm font-medium">{t(`choice.${mode}.title`)}</span>
                    <span className="block text-xs text-muted-foreground">{t(`settings.${mode}Hint`)}</span>
                  </span>
                  <span className="ml-auto">
                    {saving === mode ? <Loader2 className="size-4 animate-spin" /> : on ? <Check className="size-4" /> : null}
                  </span>
                </button>
              );
            })}
          </div>
          <p className="text-xs text-muted-foreground">{t("settings.oneTime")}</p>
          {status.access === "trial" && <TrialNote status={status} onUpgrade={upgrade.show} className="justify-start text-left" />}

          <div className="border-t pt-4">
            <p className="flex items-center gap-2 text-sm font-medium"><Brain className="size-4 text-muted-foreground" />{t("settings.memoryTitle")}</p>
            <p className="mt-0.5 text-xs text-muted-foreground">{t("settings.memoryHint")}</p>
            {memories && memories.length > 0 ? (
              <ul className="mt-3 space-y-1.5">
                {memories.map((memory) => (
                  <li key={memory.id} className="group flex items-start gap-2 rounded-lg bg-muted/50 px-3 py-2 text-sm">
                    <span className="min-w-0 flex-1">{memory.note}</span>
                    <button type="button" onClick={() => void forget(memory.id)} aria-label={t("settings.forget")} className="text-muted-foreground opacity-60 transition-opacity hover:text-foreground group-hover:opacity-100">
                      <X className="size-3.5" />
                    </button>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="mt-3 text-xs italic text-muted-foreground">{t("settings.memoryEmpty")}</p>
            )}
          </div>
        </div>
      )}
      {upgrade.dialog}
    </Section>
  );
}
