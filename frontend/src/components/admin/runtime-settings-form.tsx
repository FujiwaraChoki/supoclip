"use client";

import { useMemo, useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { Save, Trash2 } from "lucide-react";

import { Badge } from "@/components/ui/badge";

export type RuntimeSetting = {
  key: string;
  label: string;
  description: string;
  input_type: "password" | "text";
  source: "environment" | "admin" | "unset";
  configured: boolean;
  has_admin_value: boolean;
  has_env_value: boolean;
  prefer_admin_value: boolean;
  overridden_by_env: boolean;
  updated_at?: string | null;
};

type RuntimeSettingsFormProps = {
  settings: RuntimeSetting[];
};

function sourceBadge(setting: RuntimeSetting) {
  if (setting.source === "environment") {
    return <Badge className="bg-black text-white">Environment</Badge>;
  }
  if (setting.source === "admin") {
    return <Badge className="bg-blue-100 text-blue-800">Admin setting</Badge>;
  }
  return <Badge variant="outline">Unset</Badge>;
}

export function RuntimeSettingsForm({ settings }: RuntimeSettingsFormProps) {
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

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
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
      setMessage("Settings saved.");
      startTransition(() => router.refresh());
    } finally {
      setIsSaving(false);
    }
  }

  return (
    <form onSubmit={handleSubmit}>
      <div className="divide-y divide-gray-200">
        {settings.map((setting) => (
          <div
            key={setting.key}
            className="grid gap-3 px-4 py-4 lg:grid-cols-[220px_1fr_160px_140px] lg:items-start"
          >
            <div>
              <div className="flex items-center gap-2">
                <p className="text-sm font-medium text-black">{setting.label}</p>
                {sourceBadge(setting)}
              </div>
              <p className="mt-1 text-xs font-mono text-gray-500">{setting.key}</p>
            </div>

            <div>
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
                  setting.configured ? "Configured value is hidden" : "Add value"
                }
                className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm text-black outline-none focus:border-black"
                autoComplete="off"
              />
              <p className="mt-1 text-xs text-gray-600">{setting.description}</p>
              {setting.overridden_by_env && (
                <p className="mt-1 text-xs text-amber-700">
                  The saved admin value is present but ignored while the env var is set.
                </p>
              )}
            </div>

            <label className="flex items-center gap-2 text-sm text-gray-700 lg:justify-end">
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
                className="h-4 w-4 rounded border-gray-300"
              />
              Prefer saved
            </label>

            <label className="flex items-center gap-2 text-sm text-gray-700 lg:justify-end">
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
                className="h-4 w-4 rounded border-gray-300"
              />
              <Trash2 className="h-4 w-4" aria-hidden="true" />
              Clear saved
            </label>
          </div>
        ))}
      </div>

      <div className="flex flex-wrap items-center justify-between gap-3 border-t border-gray-200 px-4 py-4">
        <div className="text-sm">
          {error && <p className="text-red-700">{error}</p>}
          {message && <p className="text-green-700">{message}</p>}
        </div>
        <button
          type="submit"
          disabled={!hasChanges || isPending || isSaving}
          className="inline-flex items-center gap-2 rounded-md bg-black px-4 py-2 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-50"
        >
          <Save className="h-4 w-4" aria-hidden="true" />
          {isPending || isSaving ? "Saving" : "Save settings"}
        </button>
      </div>
    </form>
  );
}
