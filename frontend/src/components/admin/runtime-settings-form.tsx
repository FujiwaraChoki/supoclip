"use client";

import { useMemo, useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { Trash2 } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { useDebouncedEffect } from "@/lib/use-debounced-effect";

export type RuntimeSetting = {
  key: string;
  label: string;
  description: string;
  input_type: "password" | "text" | "select";
  options?: string[] | null;
  source: "environment" | "admin" | "unset";
  configured: boolean;
  has_admin_value: boolean;
  has_env_value: boolean;
  prefer_admin_value: boolean;
  overridden_by_env: boolean;
  updated_at?: string | null;
  /** Live effective value. Always null for password-type settings. */
  current_value?: string | null;
  /** When set, this setting can't actually take effect right now (e.g. no
   * GPU detected) — the field is shown disabled with this explanation
   * rather than silently accepting a value that won't do anything. */
  disabled_reason?: string | null;
};

type RuntimeSettingsFormProps = {
  settings: RuntimeSetting[];
  onSaved?: () => void;
};

function sourceBadge(setting: RuntimeSetting) {
  if (setting.source === "environment") {
    return <Badge className="bg-foreground text-background">Environment</Badge>;
  }
  if (setting.source === "admin") {
    return <Badge className="bg-blue-100 text-blue-800">Admin setting</Badge>;
  }
  return <Badge variant="outline">Unset</Badge>;
}

export function RuntimeSettingsForm({ settings, onSaved }: RuntimeSettingsFormProps) {
  const router = useRouter();
  const [values, setValues] = useState<Record<string, string>>({});
  const [deleteKeys, setDeleteKeys] = useState<Record<string, boolean>>({});
  const [priorityOverrides, setPriorityOverrides] = useState<Record<string, boolean>>(
    {},
  );
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isSaving, setIsSaving] = useState(false);
  const [isPending, startTransition] = useTransition();

  const hasChanges = useMemo(
    () =>
      Object.values(values).some((value) => value.trim()) ||
      Object.values(deleteKeys).some(Boolean) ||
      settings.some(
        (setting) =>
          priorityOverrides[setting.key] !== undefined &&
          priorityOverrides[setting.key] !== setting.prefer_admin_value,
      ),
    [deleteKeys, priorityOverrides, settings, values],
  );

  async function performSave() {
    setError(null);
    setMessage(null);
    setIsSaving(true);

    try {
      const updates = Object.fromEntries(
        Object.entries(values)
          .map(([key, value]) => [key, value.trim()] as const)
          .filter(([, value]) => Boolean(value)),
      );
      const keysToDelete = Object.entries(deleteKeys)
        .filter(([, shouldDelete]) => shouldDelete)
        .map(([key]) => key);
      const preferAdminValues = Object.fromEntries(
        settings
          .filter(
            (setting) =>
              priorityOverrides[setting.key] !== undefined &&
              priorityOverrides[setting.key] !== setting.prefer_admin_value,
          )
          .map((setting) => [setting.key, priorityOverrides[setting.key]]),
      );

      const response = await fetch("/api/admin/runtime-settings", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          updates,
          delete_keys: keysToDelete,
          prefer_admin_values: preferAdminValues,
        }),
      });

      if (!response.ok) {
        const payload = (await response.json().catch(() => null)) as {
          detail?: string;
          error?: string;
        } | null;
        setError(payload?.detail || payload?.error || "Failed to save settings");
        return;
      }

      setValues({});
      setDeleteKeys({});
      setPriorityOverrides({});
      setMessage("Saved.");
      onSaved?.();
      startTransition(() => router.refresh());
    } finally {
      setIsSaving(false);
    }
  }

  function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void performSave();
  }

  // Auto-save: persist pending edits shortly after the user stops typing, so
  // nothing requires a manual "Save" click. A trailing manual submit (Enter
  // key) is still handled by handleSubmit for immediate feedback.
  useDebouncedEffect(
    () => {
      if (hasChanges) void performSave();
    },
    [values, deleteKeys, priorityOverrides],
    900,
  );

  return (
    <form onSubmit={handleSubmit}>
      <div className="divide-y divide-border">
        {settings.map((setting) => (
          <div
            key={setting.key}
            className="grid gap-3 px-4 py-4 lg:grid-cols-[220px_1fr_160px_140px] lg:items-start"
          >
            <div>
              <div className="flex items-center gap-2">
                <p className="text-sm font-medium text-foreground">{setting.label}</p>
                {sourceBadge(setting)}
              </div>
              <p className="mt-1 text-xs font-mono text-muted-foreground">{setting.key}</p>
              {setting.input_type === "password" ? (
                setting.configured && (
                  <p className="mt-1 text-xs text-muted-foreground">Currently set (value hidden)</p>
                )
              ) : (
                <p className="mt-1 text-xs text-muted-foreground">
                  Current value:{" "}
                  <span className="font-mono font-medium text-foreground">
                    {setting.current_value && setting.current_value.length > 0
                      ? setting.current_value
                      : "(not set)"}
                  </span>
                </p>
              )}
            </div>

            <div>
              {setting.input_type === "select" ? (
                <>
                  <select
                    value={values[setting.key] ?? ""}
                    disabled={!!setting.disabled_reason}
                    onChange={(event) =>
                      setValues((current) => ({
                        ...current,
                        [setting.key]: event.target.value,
                      }))
                    }
                    className="w-full rounded-md border border-border px-3 py-2 text-sm text-foreground outline-none focus:border-ring disabled:cursor-not-allowed disabled:bg-background disabled:text-muted-foreground"
                  >
                    <option value="">
                      {setting.configured
                        ? `Keep current (${setting.current_value ?? "configured"})`
                        : "Select a value"}
                    </option>
                    {(setting.options ?? []).map((option) => (
                      <option key={option} value={option}>
                        {option}
                      </option>
                    ))}
                  </select>
                  {setting.disabled_reason && (
                    <p className="mt-1 text-xs text-foreground font-bold">{setting.disabled_reason}</p>
                  )}
                </>
              ) : (
                <input
                  type={setting.input_type}
                  value={values[setting.key] ?? ""}
                  onChange={(event) =>
                    setValues((current) => ({
                      ...current,
                      [setting.key]: event.target.value,
                    }))
                  }
                  placeholder={
                    setting.input_type === "password"
                      ? setting.configured
                        ? "Configured value is hidden"
                        : "Add value"
                      : setting.configured
                        ? `Keep current (${setting.current_value ?? "configured"})`
                        : "Add value"
                  }
                  className="w-full rounded-md border border-border px-3 py-2 text-sm text-foreground outline-none focus:border-ring"
                  autoComplete="off"
                />
              )}
              <p className="mt-1 text-xs text-muted-foreground">{setting.description}</p>
              {setting.overridden_by_env && (
                <p className="mt-1 text-xs text-foreground font-bold">
                  The saved admin value is present but ignored while the env var is set.
                </p>
              )}
            </div>

            <label className="flex items-center gap-2 text-sm text-foreground lg:justify-end">
              <input
                type="checkbox"
                checked={
                  priorityOverrides[setting.key] ?? setting.prefer_admin_value
                }
                disabled={!setting.has_admin_value && !values[setting.key]?.trim()}
                onChange={(event) =>
                  setPriorityOverrides((current) => ({
                    ...current,
                    [setting.key]: event.target.checked,
                  }))
                }
                className="h-4 w-4 rounded border-border"
              />
              Prefer saved
            </label>

            <label className="flex items-center gap-2 text-sm text-foreground lg:justify-end">
              <input
                type="checkbox"
                checked={Boolean(deleteKeys[setting.key])}
                disabled={!setting.has_admin_value}
                onChange={(event) =>
                  setDeleteKeys((current) => ({
                    ...current,
                    [setting.key]: event.target.checked,
                  }))
                }
                className="h-4 w-4 rounded border-border"
              />
              <Trash2 className="h-4 w-4" aria-hidden="true" />
              Clear saved
            </label>
          </div>
        ))}
      </div>

      <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border px-4 py-4">
        <div className="text-sm">
          {error && <p className="text-red-700">{error}</p>}
        </div>
        <p className="text-sm text-muted-foreground" aria-live="polite">
          {isPending || isSaving
            ? "Saving…"
            : hasChanges
              ? "Unsaved changes — saving shortly…"
              : message
                ? "Saved"
                : ""}
        </p>
      </div>
    </form>
  );
}
