"use client";

import { useState } from "react";
import { MessageSquare, Loader2 } from "lucide-react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";
import { useSession } from "@/lib/auth-client";
import { track } from "@/lib/datafast";

import { Button } from "@/components/ui/button";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";

export function FeedbackButton() {
  const t = useTranslations("shell");
  const { data: session } = useSession();
  const categories = [
    { value: "bug", label: t("feedback.bug") },
    { value: "feature", label: t("feedback.feature") },
    { value: "general", label: t("feedback.general") },
    { value: "sales", label: t("feedback.sales") },
  ];
  const [open, setOpen] = useState(false);
  const [category, setCategory] = useState("");
  const [message, setMessage] = useState("");
  const [submitting, setSubmitting] = useState(false);

  if (!session?.user) return null;

  const handleSubmit = async () => {
    if (!category || !message.trim()) return;

    setSubmitting(true);
    try {
      const res = await fetch("/api/feedback", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ category, message: message.trim() }),
      });

      if (!res.ok) {
        const data = await res.json().catch(() => null);
        throw new Error(data?.detail || t("feedback.failed"));
      }

      toast.success(t("feedback.success"));
      track("feedback_submitted", {
        category,
      });
      setCategory("");
      setMessage("");
      setOpen(false);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("feedback.error"));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          size="icon"
          aria-label={t("feedback.title")}
          className="fixed bottom-5 right-5 z-50 h-11 w-11 rounded-full shadow-lg max-md:[body:has([data-app-shell])_&]:bottom-[calc(5rem+env(safe-area-inset-bottom))]"
        >
          <MessageSquare className="h-5 w-5" />
        </Button>
      </PopoverTrigger>
      <PopoverContent
        side="top"
        align="end"
        className="w-80"
        sideOffset={12}
      >
        <div className="space-y-3">
          <h3 className="font-semibold text-sm">{t("feedback.title")}</h3>

          <Select value={category} onValueChange={setCategory}>
            <SelectTrigger className="w-full">
              <SelectValue placeholder={t("feedback.category")} />
            </SelectTrigger>
            <SelectContent>
              {categories.map((c) => (
                <SelectItem key={c.value} value={c.value}>
                  {c.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>

          <Textarea
            placeholder={t("feedback.placeholder")}
            value={message}
            onChange={(e) => setMessage(e.target.value)}
            rows={4}
            maxLength={2000}
          />

          <Button
            className="w-full"
            disabled={!category || !message.trim() || submitting}
            onClick={handleSubmit}
          >
            {submitting ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              t("feedback.submit")
            )}
          </Button>
        </div>
      </PopoverContent>
    </Popover>
  );
}
