"use client";

import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState } from "@/components/empty-state";
import { useDelayedFlag } from "@/hooks/use-delayed-flag";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { formatSupportMessage, parseApiError } from "@/lib/api-error";
import { ArrowLeft, Trash2, RotateCcw, Loader2 } from "lucide-react";
import Link from "next/link";
import { toast } from "@/lib/toast";

interface Task {
  id: string;
  source_title: string;
  source_type: string;
  status: string;
  clips_count: number;
  created_at: string;
  updated_at: string;
}

async function fetchTrashList() {
  const response = await fetch("/api/tasks/trash?limit=500", { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`Failed to fetch trash: ${response.status}`);
  }
  const data = await response.json();
  return (data.tasks || []) as Task[];
}

export default function TrashPage() {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const showLoading = useDelayedFlag(isLoading);
  const [pendingId, setPendingId] = useState<string | null>(null);
  const [purgeTarget, setPurgeTarget] = useState<Task | null>(null);

  const load = async () => {
    try {
      setIsLoading(true);
      const nextTasks = await fetchTrashList();
      setTasks(nextTasks);
    } catch (err) {
      console.error("Error fetching trash:", err);
      toast.error(err instanceof Error ? err.message : "Failed to load trash");
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const handleRestore = async (task: Task) => {
    setPendingId(task.id);
    try {
      const response = await fetch(`/api/tasks/${task.id}/restore`, { method: "POST" });
      if (!response.ok) {
        const parsed = await parseApiError(response, "Failed to restore generation");
        throw new Error(formatSupportMessage(parsed));
      }
      toast.success(`"${task.source_title}" restored.`);
      setTasks((current) => current.filter((t) => t.id !== task.id));
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to restore generation");
    } finally {
      setPendingId(null);
    }
  };

  const handlePurge = async () => {
    if (!purgeTarget) return;
    const task = purgeTarget;
    setPendingId(task.id);
    try {
      const response = await fetch(`/api/tasks/${task.id}/purge`, { method: "DELETE" });
      if (!response.ok) {
        const parsed = await parseApiError(response, "Failed to permanently delete generation");
        throw new Error(formatSupportMessage(parsed));
      }
      toast.success(`"${task.source_title}" permanently deleted.`);
      setTasks((current) => current.filter((t) => t.id !== task.id));
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to permanently delete generation");
    } finally {
      setPendingId(null);
      setPurgeTarget(null);
    }
  };

  const formatDate = (dateString: string) =>
    new Intl.DateTimeFormat("en-US", {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    }).format(new Date(dateString));

  return (
    <div className="min-h-screen bg-background">
      <div className="border-b border-border bg-background">
        <div className="max-w-5xl mx-auto px-4 sm:px-6 py-5">
          <div className="flex items-center gap-3 mb-4">
            <Link href="/">
              <Button variant="ghost" size="sm" className="text-muted-foreground hover:text-foreground">
                <ArrowLeft className="w-4 h-4" />
                Back
              </Button>
            </Link>
          </div>
          <h1 className="text-2xl font-bold tracking-tight text-foreground">
            Trash
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            {tasks.length} item{tasks.length === 1 ? "" : "s"} &middot; restore or permanently delete
          </p>
        </div>
      </div>

      <div className="max-w-5xl mx-auto px-4 sm:px-6 py-6">
        {showLoading ? (
          <div className="space-y-3">
            {[1, 2, 3].map((i) => (
              <div
                key={i}
                className="flex items-center gap-4 border border-border bg-background p-4"
              >
                <div className="flex-1 space-y-2">
                  <Skeleton className="h-4 w-64" />
                  <Skeleton className="h-3 w-40" />
                </div>
                <Skeleton className="h-8 w-24" />
              </div>
            ))}
          </div>
        ) : tasks.length === 0 ? (
          <EmptyState
            icon={Trash2}
            title="Trash is empty"
            description="Deleted generations show up here and can be restored within reach."
            action={{ label: "Back to generations", href: "/list" }}
          />
        ) : (
          <div className="space-y-2">
            {tasks.map((task) => (
              <div
                key={task.id}
                className="flex items-center justify-between gap-4 border border-border bg-background p-4"
              >
                <div className="min-w-0">
                  <p className="text-sm font-medium text-foreground truncate">
                    {task.source_title}
                  </p>
                  <div className="flex items-center gap-2 text-xs text-muted-foreground mt-0.5">
                    <span className="capitalize">{task.source_type}</span>
                    <span>&middot;</span>
                    <span>{formatDate(task.updated_at)}</span>
                    <span>&middot;</span>
                    <span>
                      {task.clips_count} {task.clips_count === 1 ? "clip" : "clips"}
                    </span>
                  </div>
                </div>
                <div className="flex items-center gap-2 flex-shrink-0">
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => void handleRestore(task)}
                    disabled={pendingId === task.id}
                  >
                    {pendingId === task.id ? (
                      <Loader2 className="w-4 h-4 animate-spin" />
                    ) : (
                      <RotateCcw className="w-4 h-4" />
                    )}
                    Restore
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => setPurgeTarget(task)}
                    disabled={pendingId === task.id}
                    className="text-foreground font-bold hover:bg-foreground hover:text-background"
                  >
                    <Trash2 className="w-4 h-4" />
                    Delete forever
                  </Button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      <AlertDialog open={purgeTarget !== null} onOpenChange={(open) => !open && setPurgeTarget(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Permanently delete this generation?</AlertDialogTitle>
            <AlertDialogDescription>
              {purgeTarget && `"${purgeTarget.source_title}" `}
              and all of its clips will be permanently deleted. This action cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={pendingId !== null}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => void handlePurge()}
              disabled={pendingId !== null}
              className="bg-foreground text-background hover:bg-foreground/90"
            >
              {pendingId !== null ? (
                <>
                  <Loader2 className="w-4 h-4 animate-spin" />
                  Deleting...
                </>
              ) : (
                "Delete forever"
              )}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
