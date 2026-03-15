"use client";

import { useEffect, useState } from "react";
import { Card, CardContent } from "@/components/ui/card";
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
import { useSession } from "@/lib/auth-client";
import { formatSupportMessage, parseApiError } from "@/lib/api-error";
import { cn } from "@/lib/utils";
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
  status: string;
  clips_count: number;
  created_at: string;
  updated_at: string;
}

type BatchAction = "cancel" | "resume" | "delete" | null;

const ACTIVE_TASK_STATUSES = ["queued", "processing"];
const RESUMABLE_TASK_STATUSES = ["cancelled", "error"];

async function fetchTasksList() {
  const response = await fetch("/api/tasks/", {
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
    dotClass: "bg-emerald-500",
    bgClass: "bg-emerald-50 border-emerald-200/60",
    textClass: "text-emerald-800",
  },
  processing: {
    label: "Processing",
    dotClass: "bg-blue-500 animate-pulse",
    bgClass: "bg-blue-50 border-blue-200/60",
    textClass: "text-blue-800",
  },
  queued: {
    label: "Queued",
    dotClass: "bg-amber-500",
    bgClass: "bg-amber-50 border-amber-200/60",
    textClass: "text-amber-800",
  },
  error: {
    label: "Error",
    dotClass: "bg-red-500",
    bgClass: "bg-red-50 border-red-200/60",
    textClass: "text-red-800",
  },
  cancelled: {
    label: "Cancelled",
    dotClass: "bg-stone-400",
    bgClass: "bg-stone-100 border-stone-200/60",
    textClass: "text-stone-600",
  },
};

export default function ListPage() {
  const { data: session, isPending } = useSession();
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
        setBatchNotice({ tone: "success", message: labels.success(fulfilled.length) });
      } else {
        const firstFailure = rejected[0]?.reason;
        const firstError =
          firstFailure instanceof Error
            ? firstFailure.message
            : typeof firstFailure === "string"
              ? firstFailure
              : labels.fallback;
        setBatchNotice({
          tone: "error",
          message: labels.partial(fulfilled.length, rejected.length, firstError),
        });
      }
    } catch (refreshError) {
      console.error("Error refreshing task list:", refreshError);
      setBatchNotice({
        tone: "error",
        message:
          refreshError instanceof Error
            ? refreshError.message
            : "The batch action finished, but the list could not be refreshed.",
      });
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
        fallback: "Failed to delete generation",
        success: (count) => `${count} generation${count === 1 ? "" : "s"} deleted.`,
        partial: (s, f, err) => `${s} deleted, ${f} failed. ${err}`,
      },
    );

    setShowDeleteDialog(false);
  };

  /* ── Loading / Auth gates ─────────────────────────────────── */

  if (isPending) {
    return (
      <div className="min-h-screen bg-white flex items-center justify-center p-4">
        <div className="space-y-4">
          <Skeleton className="h-4 w-32 mx-auto" />
          <Skeleton className="h-4 w-48 mx-auto" />
          <Skeleton className="h-4 w-24 mx-auto" />
        </div>
      </div>
    );
  }

  if (!session?.user) {
    return (
      <div className="min-h-screen bg-white">
        <div className="max-w-4xl mx-auto px-4 py-24 text-center">
          <h1 className="text-3xl font-bold text-black mb-4">Sign In Required</h1>
          <p className="text-gray-600 mb-8">
            You need to be signed in to view your generations.
          </p>
          <Link href="/sign-in">
            <Button size="lg">Sign In</Button>
          </Link>
        </div>
      </div>
    );
  }

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
          "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-medium",
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
    <div className="min-h-screen bg-stone-50/50">
      {/* ── Page header ──────────────────────────────────────── */}
      <div className="border-b border-stone-200 bg-white">
        <div className="max-w-5xl mx-auto px-4 sm:px-6 py-5">
          <div className="flex items-center gap-3 mb-4">
            <Link href="/">
              <Button variant="ghost" size="sm" className="text-stone-500 hover:text-stone-900">
                <ArrowLeft className="w-4 h-4" />
                Back
              </Button>
            </Link>
          </div>

          <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
            <div>
              <h1 className="font-[var(--font-syne)] text-2xl font-bold tracking-tight text-stone-950">
                Generations
              </h1>
              <p className="mt-1 text-sm text-stone-500">
                {tasks.length} total &middot; manage and review your clips
              </p>
            </div>

            {!isLoading && !error && tasks.length > 0 && (
              <div className="flex items-center gap-2">
                {completedCount > 0 && (
                  <span className="inline-flex items-center gap-1.5 rounded-full bg-emerald-50 border border-emerald-200/60 px-2.5 py-1 text-xs font-medium text-emerald-800">
                    <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" />
                    {completedCount} done
                  </span>
                )}
                {activeCount > 0 && (
                  <span className="inline-flex items-center gap-1.5 rounded-full bg-blue-50 border border-blue-200/60 px-2.5 py-1 text-xs font-medium text-blue-800">
                    <span className="h-1.5 w-1.5 rounded-full bg-blue-500 animate-pulse" />
                    {activeCount} active
                  </span>
                )}
                {attentionCount > 0 && (
                  <span className="inline-flex items-center gap-1.5 rounded-full bg-red-50 border border-red-200/60 px-2.5 py-1 text-xs font-medium text-red-700">
                    <span className="h-1.5 w-1.5 rounded-full bg-red-500" />
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
                ? "border-emerald-200 bg-emerald-50/50"
                : "border-red-200 bg-red-50/50",
            )}
          >
            {batchNotice.tone === "success" ? (
              <CheckCircle className="h-4 w-4 text-emerald-600" />
            ) : (
              <AlertCircle className="h-4 w-4 text-red-600" />
            )}
            <AlertDescription className="text-sm">
              {batchNotice.message}
            </AlertDescription>
          </Alert>
        )}

        {isLoading ? (
          <div className="space-y-3">
            {[1, 2, 3, 4].map((i) => (
              <div
                key={i}
                className="flex items-center gap-4 rounded-xl border border-stone-200 bg-white p-4"
              >
                <Skeleton className="h-5 w-5 rounded" />
                <div className="flex-1 space-y-2">
                  <Skeleton className="h-4 w-64" />
                  <Skeleton className="h-3 w-40" />
                </div>
                <Skeleton className="h-6 w-20 rounded-full" />
              </div>
            ))}
          </div>
        ) : error ? (
          <Alert>
            <AlertCircle className="h-4 w-4" />
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        ) : tasks.length === 0 ? (
          <Card className="border-stone-200">
            <CardContent className="p-12 text-center">
              <div className="w-16 h-16 bg-stone-100 rounded-2xl flex items-center justify-center mx-auto mb-4">
                <PlayCircle className="w-8 h-8 text-stone-400" />
              </div>
              <h2 className="text-xl font-semibold text-stone-950 mb-2">No generations yet</h2>
              <p className="text-stone-500 mb-6 text-sm">
                Start by processing your first video to create clips.
              </p>
              <Link href="/">
                <Button>Create New Generation</Button>
              </Link>
            </CardContent>
          </Card>
        ) : (
          <>
            {/* ── Table header row ────────────────────────────── */}
            <div className="mb-2 flex items-center gap-4 px-4 py-2">
              <Checkbox
                checked={allVisibleSelected ? true : someSelected ? "indeterminate" : false}
                onCheckedChange={handleToggleAllVisible}
                disabled={activeBatchAction !== null}
                aria-label="Select all generations"
                className="data-[state=indeterminate]:bg-stone-400 data-[state=indeterminate]:border-stone-400"
              />
              <span className="text-xs font-medium uppercase tracking-widest text-stone-400">
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
                      "group relative flex items-start gap-4 rounded-xl border bg-white p-4 transition-all duration-150",
                      isSelected
                        ? "border-stone-900/20 bg-stone-50 shadow-sm ring-1 ring-stone-900/5"
                        : "border-stone-200 hover:border-stone-300 hover:shadow-sm",
                    )}
                  >
                    {/* Selection indicator bar */}
                    <div
                      className={cn(
                        "absolute left-0 top-3 bottom-3 w-0.5 rounded-full transition-all duration-150",
                        isSelected ? "bg-stone-900" : "bg-transparent",
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
                            ? `Deselect ${task.source_title}`
                            : `Select ${task.source_title}`
                        }
                      />
                    </div>

                    {/* Content — links to task detail */}
                    <Link href={`/tasks/${task.id}`} className="flex-1 min-w-0">
                      <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
                        <div className="min-w-0">
                          <h3 className="truncate text-sm font-semibold text-stone-950 transition-colors group-hover:text-stone-600">
                            {task.source_title}
                          </h3>
                          <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-stone-400">
                            <span className="uppercase tracking-wide font-medium text-stone-500">
                              {task.source_type}
                            </span>
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
            className="pointer-events-auto flex items-center gap-1 rounded-2xl border border-stone-800 bg-stone-950 px-2 py-2 shadow-2xl"
            style={{ animation: "command-bar-pulse 3s ease-in-out infinite" }}
          >
            {/* Select all checkbox */}
            <div className="flex items-center gap-2.5 pl-2 pr-3">
              <Checkbox
                checked={allVisibleSelected ? true : someSelected ? "indeterminate" : false}
                onCheckedChange={handleToggleAllVisible}
                disabled={activeBatchAction !== null}
                aria-label="Select all"
                className="border-stone-600 data-[state=checked]:bg-white data-[state=checked]:text-stone-950 data-[state=checked]:border-white data-[state=indeterminate]:bg-stone-500 data-[state=indeterminate]:border-stone-500"
              />
              <span className="text-sm font-medium text-white tabular-nums">
                {selectedCount}
                <span className="text-stone-400 ml-0.5">
                  {" "}selected
                </span>
              </span>
            </div>

            <Separator orientation="vertical" className="h-6 bg-stone-700" />

            {/* Action buttons */}
            <div className="flex items-center gap-0.5 px-1">
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => void handleCancelSelected()}
                    disabled={cancelableCount === 0 || activeBatchAction !== null}
                    className="text-stone-300 hover:text-white hover:bg-stone-800 disabled:text-stone-600 disabled:hover:bg-transparent"
                  >
                    {activeBatchAction === "cancel" ? (
                      <Loader2 className="w-4 h-4 animate-spin" />
                    ) : (
                      <PauseCircle className="w-4 h-4" />
                    )}
                    <span className="hidden sm:inline">Cancel</span>
                    {cancelableCount > 0 && (
                      <span className="text-xs text-stone-500">{cancelableCount}</span>
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
                    className="text-stone-300 hover:text-white hover:bg-stone-800 disabled:text-stone-600 disabled:hover:bg-transparent"
                  >
                    {activeBatchAction === "resume" ? (
                      <Loader2 className="w-4 h-4 animate-spin" />
                    ) : (
                      <RotateCcw className="w-4 h-4" />
                    )}
                    <span className="hidden sm:inline">Resume</span>
                    {resumableCount > 0 && (
                      <span className="text-xs text-stone-500">{resumableCount}</span>
                    )}
                  </Button>
                </TooltipTrigger>
                <TooltipContent side="top" sideOffset={8}>
                  Resume {resumableCount} failed/cancelled generation{resumableCount === 1 ? "" : "s"}
                </TooltipContent>
              </Tooltip>

              <Separator orientation="vertical" className="h-6 bg-stone-700" />

              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => setShowDeleteDialog(true)}
                    disabled={selectedCount === 0 || activeBatchAction !== null}
                    className="text-red-400 hover:text-red-300 hover:bg-red-950/50 disabled:text-stone-600 disabled:hover:bg-transparent"
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

            <Separator orientation="vertical" className="h-6 bg-stone-700" />

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
                  className="text-stone-400 hover:text-white hover:bg-stone-800 rounded-xl"
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
            <AlertDialogTitle>Delete {selectedCount} generation{selectedCount === 1 ? "" : "s"}?</AlertDialogTitle>
            <AlertDialogDescription>
              This will permanently remove {selectedCount === 1 ? "this generation" : "these generations"} and all
              associated clips. This cannot be undone.
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
                  Deleting...
                </>
              ) : (
                "Delete"
              )}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
