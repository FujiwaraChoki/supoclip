"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

type Action = "approve" | "decline" | "revoke";

interface AffiliateReviewActionsProps {
  affiliateId: string;
  status: string;
}

export function AffiliateReviewActions({ affiliateId, status }: AffiliateReviewActionsProps) {
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
        throw new Error(data.error || "Failed to update application");
      }
      setConfirming(null);
      router.refresh();
    } catch (reviewError) {
      setError(reviewError instanceof Error ? reviewError.message : "Failed to update application");
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
          placeholder={confirming === "decline" ? "Reason, sent to the applicant (optional)" : "Internal note (optional)"}
          maxLength={1000}
          className="h-8 w-64 text-xs"
        />
        <div className="flex gap-2">
          <Button variant="ghost" size="sm" onClick={() => setConfirming(null)} disabled={pending !== null}>
            Cancel
          </Button>
          <Button variant="destructive" size="sm" onClick={() => review(confirming)} disabled={pending !== null}>
            {pending ? "Saving..." : confirming === "decline" ? "Decline" : "Revoke"}
          </Button>
        </div>
        {error && <span className="text-xs text-red-600">{error}</span>}
      </div>
    );
  }

  return (
    <div className="flex items-center justify-end gap-2">
      {status === "pending" && (
        <>
          <Button variant="outline" size="sm" onClick={() => setConfirming("decline")} disabled={pending !== null}>
            Decline
          </Button>
          <Button size="sm" onClick={() => review("approve")} disabled={pending !== null}>
            {pending === "approve" ? "Approving..." : "Approve"}
          </Button>
        </>
      )}
      {status === "approved" && (
        <Button variant="outline" size="sm" onClick={() => setConfirming("revoke")} disabled={pending !== null}>
          Revoke
        </Button>
      )}
      {error && <span className="text-xs text-red-600">{error}</span>}
    </div>
  );
}
