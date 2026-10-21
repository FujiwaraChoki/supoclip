"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { Button } from "@/components/ui/button";

interface LegacyHdToggleProps {
  userId: string;
  legacyHd: boolean;
}

/** Grants or removes grandfathered 1080p for a Pro subscriber. */
export function LegacyHdToggle({ userId, legacyHd }: LegacyHdToggleProps) {
  const t = useTranslations("admin");
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const router = useRouter();

  const handleToggle = async () => {
    setError(null);
    setIsLoading(true);
    try {
      const response = await fetch(`/api/admin/users/${userId}/legacy-hd`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ legacy_hd: !legacyHd }),
      });
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(data.error || t("users.hdUpdateFailed"));
      }
      router.refresh();
    } catch (toggleError) {
      setError(toggleError instanceof Error ? toggleError.message : t("users.hdUpdateFailed"));
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="flex items-center justify-end gap-2">
      <Button variant="outline" size="sm" onClick={handleToggle} disabled={isLoading}>
        {isLoading ? t("savingDots") : legacyHd ? t("users.removeHd") : t("users.keepHd")}
      </Button>
      {error && <span className="text-xs text-red-600">{error}</span>}
    </div>
  );
}
