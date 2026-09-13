import Link from "next/link";

import type { TaskSummary } from "./recent-projects";

const STATUS_VERB: Record<string, string> = {
  completed: "finished",
  processing: "started processing",
  queued: "queued",
  error: "failed",
  cancelled: "cancelled",
};

function relativeTime(iso: string): string {
  const diffMs = Date.now() - new Date(iso).getTime();
  const minutes = Math.round(diffMs / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

/** Compact last-5-actions feed — the "quick stats" section, kept as an
 * activity list rather than aggregate counters since clip/hour totals
 * aren't tracked anywhere yet (would need new backend aggregation). */
export function ActivityFeed({ tasks }: { tasks: TaskSummary[] }) {
  const recent = tasks.slice(0, 5);
  if (recent.length === 0) return null;

  return (
    <section>
      <h2 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-2">Activity</h2>
      <ul className="border border-border divide-y divide-border">
        {recent.map((task) => (
          <li key={task.id}>
            <Link
              href={`/tasks/${task.id}`}
              className="flex items-center justify-between gap-3 px-3 py-1.5 text-xs border-l-2 border-transparent hover:border-foreground transition-colors"
            >
              <span className="truncate text-foreground">
                <span className="text-muted-foreground">{STATUS_VERB[task.status] ?? task.status}</span>{" "}
                {task.source_title}
              </span>
              <span className="text-muted-foreground font-mono shrink-0">{relativeTime(task.updated_at)}</span>
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}
