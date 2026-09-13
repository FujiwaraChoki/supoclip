import Link from "next/link";
import { Film } from "lucide-react";

import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState } from "@/components/empty-state";
import { TaskStatusDot } from "@/components/task-status-dot";
import { youTubeThumbnailUrl } from "@/lib/youtube-thumbnail";

export interface TaskSummary {
  id: string;
  source_title: string;
  source_type: string;
  source_url?: string | null;
  status: string;
  clips_count: number;
  created_at: string;
  updated_at: string;
}

const MAX_RECENT = 6;

export function RecentProjects({
  tasks,
  isLoading,
}: {
  tasks: TaskSummary[];
  isLoading: boolean;
}) {
  const recent = tasks.slice(0, MAX_RECENT);

  return (
    <section>
      <div className="flex items-center justify-between mb-2">
        <h2 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
          Recent Projects
        </h2>
        <Link href="/list" className="text-xs font-medium text-primary hover:underline">
          View all
        </Link>
      </div>

      {isLoading && (
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-32 border border-border" />
          ))}
        </div>
      )}

      {!isLoading && recent.length === 0 && (
        <EmptyState
          icon={Film}
          title="No projects yet"
          description="Paste a YouTube link or upload a video to get started."
          action={{ label: "Create your first clip", href: "/create" }}
        />
      )}

      {!isLoading && recent.length > 0 && (
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
          {recent.map((task) => {
            const thumbnail = youTubeThumbnailUrl(task.source_url);
            return (
              <Link
                key={task.id}
                href={`/tasks/${task.id}`}
                className="border border-border bg-background hover:border-primary transition-colors flex flex-col"
              >
                <div className="w-full h-16 border-b border-border flex items-center justify-center overflow-hidden">
                  {thumbnail ? (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img src={thumbnail} alt="" className="w-full h-full object-cover" />
                  ) : (
                    <Film className="w-5 h-5 text-muted-foreground" />
                  )}
                </div>
                <div className="p-2 flex flex-col gap-1 flex-1">
                  <p className="text-xs font-medium text-foreground truncate">{task.source_title}</p>
                  <p className="text-[11px] text-muted-foreground">
                    {task.clips_count} {task.clips_count === 1 ? "clip" : "clips"} ·{" "}
                    {new Date(task.updated_at).toLocaleDateString(undefined, {
                      month: "short",
                      day: "numeric",
                    })}
                  </p>
                  <TaskStatusDot status={task.status} className="mt-auto" />
                </div>
              </Link>
            );
          })}
        </div>
      )}
    </section>
  );
}
