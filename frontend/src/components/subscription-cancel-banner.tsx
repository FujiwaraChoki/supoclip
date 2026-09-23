"use client";

import { useState } from "react";
import { AlertCircle, Loader2 } from "lucide-react";
import { toast } from "sonner";
import { track } from "@/lib/datafast";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Textarea } from "@/components/ui/textarea";

interface SubscriptionCancelBannerProps {
  cancelAt: string;
  onRestarted?: () => void;
  className?: string;
}

export function SubscriptionCancelBanner({
  cancelAt,
  onRestarted,
  className,
}: SubscriptionCancelBannerProps) {
  const [feedbackOpen, setFeedbackOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isRestarting, setIsRestarting] = useState(false);

  const cancelDate = new Date(cancelAt).toLocaleDateString(undefined, {
    month: "long",
    day: "numeric",
    year: "numeric",
  });

  const handleRestart = async () => {
    setIsRestarting(true);
    try {
      const res = await fetch("/api/billing/resume", { method: "POST" });
      if (!res.ok) {
        const data = await res.json().catch(() => null);
        throw new Error(data?.error || "Unable to restart subscription");
      }
      toast.success("Your subscription will renew as normal.");
      track("subscription_restarted", {});
      onRestarted?.();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Something went wrong");
    } finally {
      setIsRestarting(false);
    }
  };

  const handleSubmitReason = async () => {
    if (!reason.trim()) return;
    setIsSubmitting(true);
    try {
      const res = await fetch("/api/feedback", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ category: "cancellation", message: reason.trim() }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => null);
        throw new Error(data?.detail || "Failed to send feedback");
      }
      toast.success("Thanks for letting us know.");
      track("feedback_submitted", { category: "cancellation" });
      setReason("");
      setFeedbackOpen(false);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Something went wrong");
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <>
      <Alert className={`border-amber-200 bg-amber-50 ${className ?? ""}`}>
        <AlertCircle className="h-4 w-4 text-amber-600" />
        <AlertDescription className="text-sm text-amber-900">
          <span>
            Your subscription is scheduled to cancel on{" "}
            <span className="font-medium">{cancelDate}</span>.
          </span>
          <span className="mt-1 flex flex-wrap gap-x-4 gap-y-1">
            <button
              type="button"
              onClick={() => setFeedbackOpen(true)}
              className="font-semibold underline underline-offset-2"
            >
              Let us know why
            </button>
            <button
              type="button"
              onClick={handleRestart}
              disabled={isRestarting}
              className="inline-flex items-center gap-1 font-semibold underline underline-offset-2 disabled:opacity-60"
            >
              {isRestarting && <Loader2 className="h-3 w-3 animate-spin" />}
              Restart subscription
            </button>
          </span>
        </AlertDescription>
      </Alert>

      <Dialog open={feedbackOpen} onOpenChange={setFeedbackOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Why are you cancelling?</DialogTitle>
            <DialogDescription>
              Your answer goes straight to the team and helps us fix what isn&apos;t working.
            </DialogDescription>
          </DialogHeader>
          <Textarea
            placeholder="What made you decide to cancel?"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            rows={4}
            maxLength={2000}
          />
          <DialogFooter>
            <Button
              onClick={handleSubmitReason}
              disabled={!reason.trim() || isSubmitting}
            >
              {isSubmitting ? <Loader2 className="h-4 w-4 animate-spin" /> : "Send"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
