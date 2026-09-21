"use client";

import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Checkbox } from "@/components/ui/checkbox";
import { Separator } from "@/components/ui/separator";
import {
  Tooltip,
  TooltipTrigger,
  TooltipContent,
} from "@/components/ui/tooltip";
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
import { LOCAL_USER_ID } from "@/lib/local-user";
import { formatSupportMessage, parseApiError } from "@/lib/api-error";
import { cn } from "@/lib/utils";
import { EmptyState } from "@/components/empty-state";
import { useDelayedFlag } from "@/hooks/use-delayed-flag";
import { toast } from "@/lib/toast";
import {
  ArrowLeft,
  Clock,
  PlayCircle,
  AlertCircle,
  CheckCircle,
  Loader2,
  PauseCircle,
  RotateCcw,
  Trash2,
  X,
} from "lucide-react";
import Link from "next/link";

interface Task {
  id: string;
  user_id: string;
  source_id: string;
  source_title: string;
  source_type: string;
  source_url?: string | null;
  status: string;
  task_type?: string;
  progress?: number;
  progress_message?: string | null;
  clips_count: number;
  created_at: string;
  updated_at: string;
}

/** Ranking projects have no single `source` row, so `source_title` comes
 * back null from the API — fall back to a generic label rather than
 * rendering blank. */
function projectTitle(task: Task): string {
  if (task.source_title) return task.source_title;
  return task.task_type === "ranking" ? "Ranking compilation" : "Untitled";
}

/** Both project types share this one list (per CLAUDE.md's Ranking tool
 * section) but open in their own tool's route. */
function projectHref(task: Task): string {
  return task.task_type === "ranking" ? `/rank/tasks/${task.id}` : `/tasks/${task.id}`;
}

/** YouTube thumbnail URL derived client-side from the source URL — no backend work needed. */
function youTubeThumbnailUrl(sourceUrl: string | null | undefined): string | null {
  if (!sourceUrl) return null;
  const match = sourceUrl.match(
    /(?:youtube\.com\/(?:watch\?v=|shorts\/|embed\/)|youtu\.be\/)([a-zA-Z0-9_-]{11})/,
  );
  return match ? `https://img.youtube.com/vi/${match[1]}/hqdefault.jpg` : null;
}

type BatchAction = "cancel" | "resume" | "delete" | null;

const ACTIVE_TASK_STATUSES = ["queued", "processing"];
const RESUMABLE_TASK_STATUSES = ["cancelled", "error"];

async function fetchTasksList() {
  // The backend defaults to 50 tasks/request; "select all" on this page
  // needs to actually see every task, not just the first page, or it
  // silently only selects (and deletes) whatever happened to be loaded.
  const response = await fetch("/api/tasks/?limit=500", {
    cache: "no-store",
  });

  if (!response.ok) {
    throw new Error(`Failed to fetch tasks: ${response.status}`);
  }

  const data = await response.json();
  return (data.tasks || []) as Task[];
}

async function buildSupportError(response: Response, fallbackMessage: string) {
  const parsed = await parseApiError(response, fallbackMessage);
  return formatSupportMessage(parsed);
}

const STATUS_CONFIG: Record<
  string,
  { label: string; dotClass: string; bgClass: string; textClass: string }
> = {
  completed: {
    label: "Completed",
    dotClass: "bg-primary",
    bgClass: "border-primary",
    textClass: "text-primary",
  },
  processing: {
    label: "Processing",
    dotClass: "bg-secondary animate-pulse",
    bgClass: "border-secondary",
    textClass: "text-secondary",
  },
  queued: {
    label: "Queued",
    dotClass: "bg-foreground",
    bgClass: "border-border",
    textClass: "text-foreground",
  },
  error: {
    label: "Error",
    dotClass: "bg-background",
    bgClass: "bg-foreground border-foreground",
    textClass: "text-background font-bold",
  },
  cancelled: {
    label: "Cancelled",
    dotClass: "bg-muted-foreground",
    bgClass: "border-border",
    textClass: "text-muted-foreground",
  },
};

export default function ListPage() {
  // Local-first: no login, so there's no real session — kept as a constant so
  // the existing "session?.user?.id" checks keep working.
  const session = { user: { id: LOCAL_USER_ID } };
  const [tasks, setTasks] = useState<Task[]>([]);
  const [selectedTaskIds, setSelectedTaskIds] = useState<string[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [batchNotice, setBatchNotice] = useState<{
    tone: "success" | "error";
    message: string;
  } | null>(null);
  const [activeBatchAction, setActiveBatchAction] = useState<BatchAction>(null);
  const [showDeleteDialog, setShowDeleteDialog] = useState(false);
  const showLoading = useDelayedFlag(isLoading);

  useEffect(() => {
    const loadTasks = async () => {
      if (!session?.user?.id) {
        setTasks([]);
        setSelectedTaskIds([]);
        setIsLoading(false);
        return;
      }

      try {
        setIsLoading(true);
        setError(null);
        const nextTasks = await fetchTasksList();
        setTasks(nextTasks);
        setSelectedTaskIds((current) =>
          current.filter((taskId) => nextTasks.some((task) => task.id === taskId)),
        );
      } catch (err) {
        console.error("Error fetching tasks:", err);
        setError(err instanceof Error ? err.message : "Failed to load tasks");
      } finally {
        setIsLoading(false);
      }
    };

    void loadTasks();
  }, [session?.user?.id]);

  const refreshTasks = async () => {
    const nextTasks = await fetchTasksList();
    setTasks(nextTasks);
    setSelectedTaskIds((current) =>
      current.filter((taskId) => nextTasks.some((task) => task.id === taskId)),
    );
  };

  // Lightweight polling so queued/processing rows show live progress without
  // a full page reload — only runs while something is actually in flight.
  const hasActiveTasks = tasks.some((task) => ACTIVE_TASK_STATUSES.includes(task.status));
  useEffect(() => {
    if (!hasActiveTasks) return;
    const interval = setInterval(() => {
      void refreshTasks().catch((err) => console.error("Error polling tasks:", err));
    }, 5000);
    return () => clearInterval(interval);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hasActiveTasks]);

  const selectedTasks = tasks.filter((task) => selectedTaskIds.includes(task.id));
  const selectedCount = selectedTasks.length;
  const completedCount = tasks.filter((task) => task.status === "completed").length;
  const activeCount = tasks.filter((task) => ACTIVE_TASK_STATUSES.includes(task.status)).length;
  const attentionCount = tasks.filter((task) => RESUMABLE_TASK_STATUSES.includes(task.status)).length;
  const cancelableCount = selectedTasks.filter((task) =>
    ACTIVE_TASK_STATUSES.includes(task.status),
  ).length;
  const resumableCount = selectedTasks.filter((task) =>
    RESUMABLE_TASK_STATUSES.includes(task.status),
  ).length;
  const allVisibleSelected = tasks.length > 0 && tasks.every((task) => selectedTaskIds.includes(task.id));
  const someSelected = selectedCount > 0 && !allVisibleSelected;

  const formatDate = (dateString: string) => {
    const date = new Date(dateString);
    return new Intl.DateTimeFormat("en-US", {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    }).format(date);
  };

  const handleToggleTask = (taskId: string) => {
    setBatchNotice(null);
    setSelectedTaskIds((current) => {
      if (current.includes(taskId)) {
        return current.filter((id) => id !== taskId);
      }
      return [...current, taskId];
    });
  };

  const handleToggleAllVisible = () => {
    setBatchNotice(null);
    if (allVisibleSelected) {
      setSelectedTaskIds([]);
      return;
    }
    setSelectedTaskIds(tasks.map((task) => task.id));
  };

  const runBatchAction = async (
    action: Exclude<BatchAction, null>,
    targetTaskIds: string[],
    requestFactory: (taskId: string) => Promise<Response>,
    labels: {
      empty: string;
      fallback: string;
      success: (count: number) => string;
      partial: (successCount: number, failureCount: number, firstError: string) => string;
    },
  ) => {
    if (!session?.user?.id) return;

    if (targetTaskIds.length === 0) {
      setBatchNotice({ tone: "error", message: labels.empty });
      return;
    }

    setActiveBatchAction(action);
    setBatchNotice(null);

    const results = await Promise.allSettled(
      targetTaskIds.map(async (taskId) => {
        const response = await requestFactory(taskId);
        if (!response.ok) {
          throw new Error(await buildSupportError(response, labels.fallback));
        }
        return taskId;
      }),
    );

    const fulfilled = results.filter(
      (result): result is PromiseFulfilledResult<string> => result.status === "fulfilled",
    );
    const rejected = results.filter(
      (result): result is PromiseRejectedResult => result.status === "rejected",
    );

    try {
      if (fulfilled.length > 0) await refreshTasks();

      if (rejected.length === 0) {
        const message = labels.success(fulfilled.length);
        setBatchNotice({ tone: "success", message });
        toast.success(message);
      } else {
        const firstFailure = rejected[0]?.reason;
        const firstError =
          firstFailure instanceof Error
            ? firstFailure.message
            : typeof firstFailure === "string"
              ? firstFailure
              : labels.fallback;
        const message = labels.partial(fulfilled.length, rejected.length, firstError);
        setBatchNotice({
          tone: "error",
          message,
        });
        toast.error(message);
      }
    } catch (refreshError) {
      console.error("Error refreshing task list:", refreshError);
      const message =
        refreshError instanceof Error
          ? refreshError.message
          : "The batch action finished, but the list could not be refreshed.";
      setBatchNotice({
        tone: "error",
        message,
      });
      toast.error(message);
    } finally {
      setActiveBatchAction(null);
    }
  };

  const handleCancelSelected = async () => {
    const targetTaskIds = selectedTasks
      .filter((task) => ACTIVE_TASK_STATUSES.includes(task.status))
      .map((task) => task.id);

    await runBatchAction(
      "cancel",
      targetTaskIds,
      (taskId) => fetch(`/api/tasks/${taskId}/cancel`, { method: "POST" }),
      {
        empty: "No active generations in selection to cancel.",
        fallback: "Failed to cancel generation",
        success: (count) => `${count} generation${count === 1 ? "" : "s"} cancelled.`,
        partial: (s, f, err) => `${s} cancelled, ${f} failed. ${err}`,
      },
    );
  };

  const handleResumeSelected = async () => {
    const targetTaskIds = selectedTasks
      .filter((task) => RESUMABLE_TASK_STATUSES.includes(task.status))
      .map((task) => task.id);

    await runBatchAction(
      "resume",
      targetTaskIds,
      (taskId) => fetch(`/api/tasks/${taskId}/resume`, { method: "POST" }),
      {
        empty: "No failed or cancelled generations in selection to resume.",
        fallback: "Failed to resume generation",
        success: (count) => `${count} generation${count === 1 ? "" : "s"} resumed.`,
        partial: (s, f, err) => `${s} resumed, ${f} failed. ${err}`,
      },
    );
  };

  const handleDeleteSelected = async () => {
    const targetTaskIds = [...selectedTaskIds];

    await runBatchAction(
      "delete",
      targetTaskIds,
      (taskId) => fetch(`/api/tasks/${taskId}`, { method: "DELETE" }),
      {
        empty: "Select at least one generation to delete.",
        fallback: "Failed to move generation to Trash",
        success: (count) => `${count} generation${count === 1 ? "" : "s"} moved to Trash.`,
        partial: (s, f, err) => `${s} moved to Trash, ${f} failed. ${err}`,
      },
    );

    setShowDeleteDialog(false);
  };

  /* ── Status badge renderer ────────────────────────────────── */

  const getStatusBadge = (status: string) => {
    const config = STATUS_CONFIG[status];
    if (!config) {
      return (
        <Badge variant="outline" className="capitalize">
          {status}
        </Badge>
      );
    }
    return (
      <span
        className={cn(
          "inline-flex items-center gap-1.5 border px-2.5 py-0.5 text-xs font-medium",
          config.bgClass,
          config.textClass,
        )}
      >
        <span className={cn("h-1.5 w-1.5 rounded-full", config.dotClass)} />
        {config.label}
      </span>
    );
  };

  /* ── Main render ──────────────────────────────────────────── */

  return (
    <div className="min-h-screen bg-background">
      {/* ── Page header ──────────────────────────────────────── */}
      <div className="border-b border-border bg-background">
        <div className="max-w-5xl mx-auto px-4 sm:px-6 py-5">
          <div className="flex items-center gap-3 mb-4">
            <Link href="/">
              <Button variant="ghost" size="sm" className="text-muted-foreground hover:text-foreground">
                <ArrowLeft className="w-4 h-4" />
                Back
              </Button>
            </Link>
            <Link href="/trash">
              <Button variant="ghost" size="sm" className="text-muted-foreground hover:text-foreground">
                <Trash2 className="w-4 h-4" />
                Trash
              </Button>
            </Link>
          </div>

          <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
            <div>
              <h1 className="text-2xl font-bold tracking-tight text-foreground">
                Generations
              </h1>
              <p className="mt-1 text-sm text-muted-foreground">
                {tasks.length} total &middot; manage and review your clips
              </p>
            </div>

            {!isLoading && !error && tasks.length > 0 && (
              <div className="flex items-center gap-2">
                {completedCount > 0 && (
                  <span className="inline-flex items-center gap-1.5 border border-primary px-2.5 py-1 text-xs font-medium text-primary">
                    <span className="h-1.5 w-1.5 rounded-full bg-primary" />
                    {completedCount} done
                  </span>
                )}
                {activeCount > 0 && (
                  <span className="inline-flex items-center gap-1.5 border border-secondary px-2.5 py-1 text-xs font-medium text-secondary">
                    <span className="h-1.5 w-1.5 rounded-full bg-secondary animate-pulse" />
                    {activeCount} active
                  </span>
                )}
                {attentionCount > 0 && (
                  <span className="inline-flex items-center gap-1.5 border border-foreground px-2.5 py-1 text-xs font-bold text-foreground">
                    <span className="h-1.5 w-1.5 rounded-full bg-foreground" />
                    {attentionCount} need attention
                  </span>
                )}
              </div>
            )}
          </div>
        </div>
      </div>

      {/* ── Content ──────────────────────────────────────────── */}
      <div className={cn("max-w-5xl mx-auto px-4 sm:px-6 py-6", selectedCount > 0 && "pb-28")}>
        {/* Batch notice */}
        {batchNotice && (
          <Alert
            className={cn(
              "mb-4",
              batchNotice.tone === "success"
                ? "border-primary"
                : "border-foreground",
            )}
          >
            {batchNotice.tone === "success" ? (
              <CheckCircle className="h-4 w-4 text-primary" />
            ) : (
              <AlertCircle className="h-4 w-4 text-foreground" />
            )}
            <AlertDescription className="text-sm">
              {batchNotice.message}
            </AlertDescription>
          </Alert>
        )}

        {showLoading ? (
          <div className="space-y-3">
            {[1, 2, 3, 4].map((i) => (
              <div
                key={i}
                className="flex items-center gap-4 rounded-xl border border-border bg-background p-4"
              >
                <Skeleton className="h-5 w-5 rounded" />
                <div className="flex-1 space-y-2">
                  <Skeleton className="h-4 w-64" />
                  <Skeleton className="h-3 w-40" />
                </div>
                <Skeleton className="h-6 w-20" />
              </div>
            ))}
          </div>
        ) : error ? (
          <Alert>
            <AlertCircle className="h-4 w-4" />
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        ) : tasks.length === 0 ? (
          <EmptyState
            icon={PlayCircle}
            title="No generations yet"
            description="Start by processing your first video to create clips."
            action={{ label: "Create New Generation", href: "/" }}
          />
        ) : (
          <>
            {/* ── Table header row ────────────────────────────── */}
            <div className="mb-2 flex items-center gap-4 px-4 py-2">
              <Checkbox
                checked={allVisibleSelected ? true : someSelected ? "indeterminate" : false}
                onCheckedChange={handleToggleAllVisible}
                disabled={activeBatchAction !== null}
                aria-label="Select all generations"
                className="data-[state=indeterminate]:bg-foreground data-[state=indeterminate]:border-foreground"
              />
              <span className="text-xs font-medium uppercase tracking-widest text-muted-foreground">
                {selectedCount > 0 ? `${selectedCount} of ${tasks.length} selected` : "Select"}
              </span>
            </div>

            {/* ── Task list ───────────────────────────────────── */}
            <div className="space-y-2">
              {tasks.map((task) => {
                const isSelected = selectedTaskIds.includes(task.id);

                return (
                  <div
                    key={task.id}
                    className={cn(
                      "group relative flex items-start gap-4 border bg-background p-4 transition-all duration-150",
                      isSelected
                        ? "border-foreground"
                        : "border-border hover:border-primary",
                    )}
                  >
                    {/* Selection indicator bar */}
                    <div
                      className={cn(
                        "absolute left-0 top-3 bottom-3 w-0.5 transition-all duration-150",
                        isSelected ? "bg-foreground" : "bg-transparent",
                      )}
                    />

                    {/* Checkbox */}
                    <div className="pt-0.5 pl-1">
                      <Checkbox
                        checked={isSelected}
                        onCheckedChange={() => handleToggleTask(task.id)}
                        disabled={activeBatchAction !== null}
                        aria-label={
                          isSelected
                            ? `Deselect ${projectTitle(task)}`
                            : `Select ${projectTitle(task)}`
                        }
                      />
                    </div>

                    {/* Thumbnail */}
                    <div className="hidden sm:block flex-shrink-0 w-16 h-16 overflow-hidden border border-border">
                      {youTubeThumbnailUrl(task.source_url) ? (
                        // eslint-disable-next-line @next/next/no-img-element
                        <img
                          src={youTubeThumbnailUrl(task.source_url)!}
                          alt=""
                          className="w-full h-full object-cover"
                        />
                      ) : (
                        <div className="w-full h-full flex items-center justify-center text-muted-foreground">
                          <PlayCircle className="w-6 h-6" />
                        </div>
                      )}
                    </div>

                    {/* Content — links to task detail */}
                    <Link href={projectHref(task)} className="flex-1 min-w-0">
                      <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
                        <div className="min-w-0">
                          <h3 className="truncate text-sm font-semibold text-foreground transition-colors group-hover:text-muted-foreground">
                            {projectTitle(task)}
                          </h3>
                          <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
                            <Badge variant="outline" className="capitalize">
                              {task.task_type === "ranking" ? "Ranking" : "Clipping"}
                            </Badge>
                            {task.task_type !== "ranking" && (
                              <>
                                <Separator orientation="vertical" className="h-3" />
                                <span className="uppercase tracking-wide font-medium text-muted-foreground">
                                  {task.source_type}
                                </span>
                              </>
                            )}
                            <Separator orientation="vertical" className="h-3" />
                            <span className="flex items-center gap-1">
                              <Clock className="w-3 h-3" />
                              {formatDate(task.created_at)}
                            </span>
                            <Separator orientation="vertical" className="h-3" />
                            <span>
                              {task.clips_count} {task.clips_count === 1 ? "clip" : "clips"}
                            </span>
                          </div>
                          {ACTIVE_TASK_STATUSES.includes(task.status) && (
                            <div className="mt-2 max-w-xs">
                              <div className="flex items-center justify-between text-[11px] text-muted-foreground mb-1">
                                <span className="truncate">{task.progress_message || "Waiting in queue"}</span>
                                <span className="tabular-nums ml-2 flex-shrink-0">{task.progress ?? 0}%</span>
                              </div>
                              <div className="h-1 border border-border overflow-hidden">
                                <div
                                  className="h-full bg-primary transition-all duration-700 ease-out"
                                  style={{ width: `${task.progress ?? 0}%` }}
                                />
                              </div>
                            </div>
                          )}
                        </div>

                        <div className="flex-shrink-0">
                          {getStatusBadge(task.status)}
                        </div>
                      </div>
                    </Link>
                  </div>
                );
              })}
            </div>
          </>
        )}
      </div>

      {/* ── Floating batch command bar ────────────────────────── */}
      {selectedCount > 0 && (
        <div
          className="fixed inset-x-0 bottom-0 z-50 flex justify-center px-4 pb-5 pointer-events-none"
          style={{ animation: "command-bar-in 0.25s cubic-bezier(0.16, 1, 0.3, 1) both" }}
        >
          <div
            className="pointer-events-auto flex items-center gap-1 border border-background bg-foreground px-2 py-2"
            style={{ animation: "command-bar-pulse 3s ease-in-out infinite" }}
          >
            {/* Select all checkbox */}
            <div className="flex items-center gap-2.5 pl-2 pr-3">
              <Checkbox
                checked={allVisibleSelected ? true : someSelected ? "indeterminate" : false}
                onCheckedChange={handleToggleAllVisible}
                disabled={activeBatchAction !== null}
                aria-label="Select all"
                className="border-background data-[state=checked]:bg-background data-[state=checked]:text-foreground data-[state=checked]:border-background data-[state=indeterminate]:bg-background data-[state=indeterminate]:border-background"
              />
              <span className="text-sm font-medium text-background tabular-nums">
                {selectedCount}
                <span className="text-background ml-0.5">
                  {" "}selected
                </span>
              </span>
            </div>

            <Separator orientation="vertical" className="h-6 bg-background" />

            {/* Action buttons */}
            <div className="flex items-center gap-0.5 px-1">
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => void handleCancelSelected()}
                    disabled={cancelableCount === 0 || activeBatchAction !== null}
                    className="text-background hover:bg-background hover:text-foreground disabled:opacity-50 disabled:hover:bg-transparent"
                  >
                    {activeBatchAction === "cancel" ? (
                      <Loader2 className="w-4 h-4 animate-spin" />
                    ) : (
                      <PauseCircle className="w-4 h-4" />
                    )}
                    <span className="hidden sm:inline">Cancel</span>
                    {cancelableCount > 0 && (
                      <span className="text-xs">{cancelableCount}</span>
                    )}
                  </Button>
                </TooltipTrigger>
                <TooltipContent side="top" sideOffset={8}>
                  Cancel {cancelableCount} active generation{cancelableCount === 1 ? "" : "s"}
                </TooltipContent>
              </Tooltip>

              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => void handleResumeSelected()}
                    disabled={resumableCount === 0 || activeBatchAction !== null}
                    className="text-background hover:bg-background hover:text-foreground disabled:opacity-50 disabled:hover:bg-transparent"
                  >
                    {activeBatchAction === "resume" ? (
                      <Loader2 className="w-4 h-4 animate-spin" />
                    ) : (
                      <RotateCcw className="w-4 h-4" />
                    )}
                    <span className="hidden sm:inline">Resume</span>
                    {resumableCount > 0 && (
                      <span className="text-xs">{resumableCount}</span>
                    )}
                  </Button>
                </TooltipTrigger>
                <TooltipContent side="top" sideOffset={8}>
                  Resume {resumableCount} failed/cancelled generation{resumableCount === 1 ? "" : "s"}
                </TooltipContent>
              </Tooltip>

              <Separator orientation="vertical" className="h-6 bg-background" />

              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => setShowDeleteDialog(true)}
                    disabled={selectedCount === 0 || activeBatchAction !== null}
                    className="text-background font-bold hover:bg-background hover:text-foreground disabled:opacity-50 disabled:font-normal disabled:hover:bg-transparent"
                  >
                    {activeBatchAction === "delete" ? (
                      <Loader2 className="w-4 h-4 animate-spin" />
                    ) : (
                      <Trash2 className="w-4 h-4" />
                    )}
                    <span className="hidden sm:inline">Delete</span>
                  </Button>
                </TooltipTrigger>
                <TooltipContent side="top" sideOffset={8}>
                  Delete {selectedCount} generation{selectedCount === 1 ? "" : "s"}
                </TooltipContent>
              </Tooltip>
            </div>

            <Separator orientation="vertical" className="h-6 bg-background" />

            {/* Clear selection */}
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  variant="ghost"
                  size="icon-sm"
                  onClick={() => {
                    setSelectedTaskIds([]);
                    setBatchNotice(null);
                  }}
                  disabled={activeBatchAction !== null}
                  className="text-background hover:bg-background hover:text-foreground"
                  aria-label="Clear selection"
                >
                  <X className="w-4 h-4" />
                </Button>
              </TooltipTrigger>
              <TooltipContent side="top" sideOffset={8}>
                Clear selection
              </TooltipContent>
            </Tooltip>
          </div>
        </div>
      )}

      {/* ── Delete confirmation dialog ────────────────────────── */}
      <AlertDialog open={showDeleteDialog} onOpenChange={setShowDeleteDialog}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Move {selectedCount} generation{selectedCount === 1 ? "" : "s"} to Trash?</AlertDialogTitle>
            <AlertDialogDescription>
              {selectedCount === 1 ? "This generation" : "These generations"} will be moved to Trash and can be
              restored later, or permanently deleted from there.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={activeBatchAction === "delete"}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => void handleDeleteSelected()}
              disabled={activeBatchAction === "delete" || selectedCount === 0}
              className="bg-red-600 hover:bg-red-700"
            >
              {activeBatchAction === "delete" ? (
                <>
                  <Loader2 className="w-4 h-4 animate-spin" />
                  Moving...
                </>
              ) : (
                "Move to Trash"
              )}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
