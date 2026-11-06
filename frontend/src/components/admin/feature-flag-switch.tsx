"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { Switch } from "@/components/ui/switch";

export type FeatureFlagField = "web_enabled" | "ios_enabled" | "admins_bypass";

interface FeatureFlagSwitchProps {
  flagKey: string;
  field: FeatureFlagField;
  checked: boolean;
  label: string;
}

/** Switches one column of a feature flag (`/api/admin/feature-flags/[key]`). */
export function FeatureFlagSwitch({ flagKey, field, checked, label }: FeatureFlagSwitchProps) {
  const t = useTranslations("admin");
  const [value, setValue] = useState(checked);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const router = useRouter();

  const handleChange = async (next: boolean) => {
    setError(null);
    setIsLoading(true);
    setValue(next);
    try {
      const response = await fetch(`/api/admin/feature-flags/${encodeURIComponent(flagKey)}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ [field]: next }),
      });
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(data.error || t("flags.updateFailed"));
      }
      router.refresh();
    } catch (changeError) {
      setValue(!next);
      setError(changeError instanceof Error ? changeError.message : t("flags.updateFailed"));
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="flex flex-col items-start gap-1">
      <Switch checked={value} onCheckedChange={handleChange} disabled={isLoading} aria-label={label} />
      {error && <span className="text-xs text-red-600">{error}</span>}
    </div>
  );
}
