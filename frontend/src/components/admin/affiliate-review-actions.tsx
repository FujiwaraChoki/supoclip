"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

type Action = "approve" | "decline" | "revoke" | "app_store_code_added";

interface AffiliateReviewActionsProps {
  affiliateId: string;
  status: string;
  appStoreCodeAdded: boolean;
}

export function AffiliateReviewActions({ affiliateId, status, appStoreCodeAdded }: AffiliateReviewActionsProps) {
  const t = useTranslations("admin");
  const [pending, setPending] = useState<Action | null>(null);
  const [confirming, setConfirming] = useState<"decline" | "revoke" | null>(null);
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const router = useRouter();

  const review = async (action: Action) => {
    setError(null);
    setPending(action);
    try {
      const response = await fetch(`/api/admin/affiliates/${affiliateId}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action, reason: reason.trim() || undefined }),
      });
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(data.error || t("creators.updateFailed"));
      }
      setConfirming(null);
      router.refresh();
    } catch (reviewError) {
      setError(reviewError instanceof Error ? reviewError.message : t("creators.updateFailed"));
    } finally {
      setPending(null);
    }
  };

  if (confirming) {
    return (
      <div className="flex flex-col items-end gap-2">
        <Input
          value={reason}
          onChange={(event) => setReason(event.target.value)}
          placeholder={confirming === "decline" ? t("creators.declineReason") : t("creators.revokeNote")}
          maxLength={1000}
          className="h-8 w-64 text-xs"
        />
        <div className="flex gap-2">
          <Button variant="ghost" size="sm" onClick={() => setConfirming(null)} disabled={pending !== null}>
            {t("cancel")}
          </Button>
          <Button variant="destructive" size="sm" onClick={() => review(confirming)} disabled={pending !== null}>
            {pending ? t("savingDots") : confirming === "decline" ? t("creators.decline") : t("creators.revoke")}
          </Button>
        </div>
        {confirming === "revoke" && appStoreCodeAdded && (
          <span className="text-xs text-muted-foreground">{t("creators.deactivateStore")}</span>
        )}
        {error && <span className="text-xs text-red-600">{error}</span>}
      </div>
    );
  }

  return (
    <div className="flex items-center justify-end gap-2">
      {status === "pending" && (
        <>
          <Button variant="outline" size="sm" onClick={() => setConfirming("decline")} disabled={pending !== null}>
            {t("creators.decline")}
          </Button>
          <Button size="sm" onClick={() => review("approve")} disabled={pending !== null}>
            {pending === "approve" ? t("creators.approving") : t("creators.approve")}
          </Button>
        </>
      )}
      {status === "approved" &&
        (appStoreCodeAdded ? (
          <span className="text-xs text-muted-foreground">{t("creators.storeCodeAdded")}</span>
        ) : (
          <Button variant="outline" size="sm" onClick={() => review("app_store_code_added")} disabled={pending !== null}>
            {pending === "app_store_code_added" ? t("savingDots") : t("creators.markStoreCode")}
          </Button>
        ))}
      {status === "approved" && (
        <Button variant="outline" size="sm" onClick={() => setConfirming("revoke")} disabled={pending !== null}>
          {t("creators.revoke")}
        </Button>
      )}
      {error && <span className="text-xs text-red-600">{error}</span>}
    </div>
  );
}
