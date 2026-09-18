import { cn } from "@/lib/utils";

export const ACTIVE_TASK_STATUSES = ["queued", "processing"];
export const RESUMABLE_TASK_STATUSES = ["cancelled", "error"];
const styles: Record<string, [string, string]> = {
  completed: ["Completed", "bg-emerald-50 border-emerald-200 text-emerald-800"],
  processing: ["Processing", "bg-blue-50 border-blue-200 text-blue-800"],
  queued: ["Queued", "bg-amber-50 border-amber-200 text-amber-800"],
  error: ["Failed", "bg-red-50 border-red-200 text-red-800"],
  cancelled: ["Cancelled", "bg-stone-100 border-stone-200 text-stone-600"],
};

export function StatusBadge({ status }: { status: string }) {
  const [label, style] = styles[status] ?? [status, "bg-muted text-muted-foreground"];
  return <span className={cn("inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-medium capitalize", style)}>
    <span aria-hidden className={cn("h-1.5 w-1.5 rounded-full bg-current", status === "processing" && "motion-safe:animate-pulse")} />
    {label}
  </span>;
}
