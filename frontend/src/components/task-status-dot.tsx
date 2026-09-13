const STATUS_CONFIG: Record<string, { label: string; dotClass: string; textClass: string }> = {
  completed: { label: "Completed", dotClass: "rounded-full bg-primary", textClass: "text-primary font-semibold" },
  processing: { label: "Processing", dotClass: "rounded-full bg-secondary animate-pulse", textClass: "text-secondary font-semibold" },
  queued: { label: "Queued", dotClass: "rounded-full bg-foreground", textClass: "text-foreground" },
  error: { label: "Error", dotClass: "bg-foreground", textClass: "text-foreground font-bold" },
  cancelled: { label: "Cancelled", dotClass: "rounded-full border border-foreground", textClass: "text-muted-foreground" },
};

/** Compact dot + label status indicator — the operator-panel counterpart to
 * the fuller pill badge on the /list page. Shape (filled circle / outlined
 * circle / filled square) carries the state distinction alongside color, per
 * DESIGN.md's "no red/yellow" functional-color rule. */
export function TaskStatusDot({ status, className }: { status: string; className?: string }) {
  const config = STATUS_CONFIG[status] ?? { label: status, dotClass: "rounded-full border border-foreground", textClass: "text-muted-foreground" };
  return (
    <span className={`inline-flex items-center gap-1.5 text-xs ${config.textClass} ${className ?? ""}`}>
      <span className={`w-1.5 h-1.5 ${config.dotClass}`} />
      {config.label}
    </span>
  );
}
