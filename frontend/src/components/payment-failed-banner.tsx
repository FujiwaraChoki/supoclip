"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";
import { AlertCircle, Loader2 } from "lucide-react";
import { toast } from "sonner";
import { track } from "@/lib/datafast";

import { Alert, AlertDescription } from "@/components/ui/alert";

interface PaymentFailedBannerProps {
  className?: string;
}

/** Shown instead of a new checkout while Stripe is still collecting a subscription. */
export function PaymentFailedBanner({ className }: PaymentFailedBannerProps) {
  const t = useTranslations("settings");
  const [isOpening, setIsOpening] = useState(false);

  const openPortal = async () => {
    setIsOpening(true);
    try {
      const response = await fetch("/api/billing/portal", { method: "POST" });
      const data: { url?: string; error?: string } = await response.json().catch(() => ({}));
      if (!response.ok || !data.url) {
        throw new Error(data.error || t("paymentFailed.failed"));
      }
      track("billing_portal_opened", { reason: "payment_failed" });
      window.location.href = data.url;
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t("paymentFailed.failed"));
      setIsOpening(false);
    }
  };

  return (
    <Alert className={`border-amber-200 bg-amber-50 ${className ?? ""}`}>
      <AlertCircle className="h-4 w-4 text-amber-600" />
      <AlertDescription className="text-sm text-amber-900">
        <span>{t("paymentFailed.message")}</span>
        <span className="mt-1 flex flex-wrap gap-x-4 gap-y-1">
          <button
            type="button"
            onClick={openPortal}
            disabled={isOpening}
            className="inline-flex items-center gap-1 font-semibold underline underline-offset-2 disabled:opacity-60"
          >
            {isOpening && <Loader2 className="h-3 w-3 animate-spin" />}
            {t("paymentFailed.action")}
          </button>
        </span>
      </AlertDescription>
    </Alert>
  );
}
