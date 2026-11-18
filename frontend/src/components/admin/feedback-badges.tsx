import { cn } from "@/lib/utils";
import type { DeliveryStatus, FeedbackCategory, FeedbackStatus } from "@/lib/feedback-inbox";

const pill = "inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[11px] font-medium whitespace-nowrap";

const categoryStyles: Record<FeedbackCategory, string> = {
  bug: "bg-red-500/10 text-red-700 dark:text-red-400",
  feature: "bg-blue-500/10 text-blue-700 dark:text-blue-400",
  general: "bg-purple-500/10 text-purple-700 dark:text-purple-400",
  sales: "bg-emerald-500/10 text-emerald-700 dark:text-emerald-400",
  cancellation: "bg-amber-500/10 text-amber-700 dark:text-amber-400",
};

const statusStyles: Record<FeedbackStatus | DeliveryStatus, string> = {
  open: "bg-sky-500/10 text-sky-700 dark:text-sky-400",
  in_progress: "bg-amber-500/10 text-amber-700 dark:text-amber-400",
  resolved: "bg-emerald-500/10 text-emerald-700 dark:text-emerald-400",
  pending: "bg-amber-500/10 text-amber-700 dark:text-amber-400",
  delivered: "bg-emerald-500/10 text-emerald-700 dark:text-emerald-400",
  failed: "bg-red-500/10 text-red-700 dark:text-red-400",
  cancelled: "bg-stone-500/10 text-stone-600 dark:text-stone-400",
};

/** Labels come from the caller, so these work in server and client components. */
export function FeedbackCategoryBadge({ category, label }: { category: FeedbackCategory; label: string }) {
  return <span className={cn(pill, categoryStyles[category] ?? "bg-muted text-muted-foreground")}>{label}</span>;
}

export function FeedbackStatusBadge({ status, label, className }: { status: FeedbackStatus | DeliveryStatus; label: string; className?: string }) {
  return (
    <span className={cn(pill, statusStyles[status] ?? "bg-muted text-muted-foreground", className)}>
      <span aria-hidden className="h-1.5 w-1.5 rounded-full bg-current" />
      {label}
    </span>
  );
}
