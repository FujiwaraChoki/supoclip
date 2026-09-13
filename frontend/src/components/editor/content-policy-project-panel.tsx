"use client";

import { useCallback, useEffect, useState } from "react";
import { Switch } from "@/components/ui/switch";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { toast } from "@/lib/toast";
import { formatSupportMessage, parseApiError } from "@/lib/api-error";

type Sensitivity = "off" | "low" | "medium" | "high";

interface ContentPolicyProjectPanelProps {
  taskId: string;
}

/** Self-contained per-project content-policy toggle + sensitivity control.
 * Deliberately independent of the parent page's debounced-autosave/snapshot
 * machinery (font/caption/cleanup settings) — this saves immediately on
 * change via its own endpoint, same "small independent panel" shape as the
 * template-apply section above it. */
export function ContentPolicyProjectPanel({ taskId }: ContentPolicyProjectPanelProps) {
  const [enabled, setEnabled] = useState(true);
  const [sensitivity, setSensitivity] = useState<Sensitivity>("medium");
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);

  const buildSupportError = useCallback(async (response: Response, fallbackMessage: string) => {
    const parsed = await parseApiError(response, fallbackMessage);
    return formatSupportMessage(parsed);
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const response = await fetch(`/api/tasks/${taskId}/content-policy-settings`, {
          cache: "no-store",
        });
        if (!response.ok) return;
        const data = await response.json();
        if (cancelled) return;
        setEnabled(Boolean(data.enabled));
        setSensitivity((data.sensitivity as Sensitivity) ?? "medium");
      } finally {
        if (!cancelled) setIsLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [taskId]);

  const save = async (next: { enabled: boolean; sensitivity: Sensitivity }) => {
    setIsSaving(true);
    try {
      const response = await fetch(`/api/tasks/${taskId}/content-policy-settings`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(next),
      });
      if (!response.ok) throw new Error(await buildSupportError(response, "Failed to save content policy settings"));
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to save content policy settings");
    } finally {
      setIsSaving(false);
    }
  };

  if (isLoading) return null;

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <label className="text-xs font-medium text-muted-foreground">Content Policy Detection</label>
        <Switch
          checked={enabled}
          disabled={isSaving}
          onCheckedChange={(checked) => {
            setEnabled(checked);
            void save({ enabled: checked, sensitivity });
          }}
        />
      </div>
      {enabled && (
        <Select
          value={sensitivity}
          onValueChange={(value) => {
            const next = value as Sensitivity;
            setSensitivity(next);
            void save({ enabled, sensitivity: next });
          }}
        >
          <SelectTrigger>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="off">Off</SelectItem>
            <SelectItem value="low">Low — severe terms only</SelectItem>
            <SelectItem value="medium">Medium (default)</SelectItem>
            <SelectItem value="high">High — includes borderline terms</SelectItem>
          </SelectContent>
        </Select>
      )}
    </div>
  );
}
