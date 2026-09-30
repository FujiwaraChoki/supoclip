"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import { ACTIVE_TASK_STATUSES, RESUMABLE_TASK_STATUSES } from "@/components/app/status-badge";
import { PageLoading } from "@/components/app/page-state";
import { Button } from "@/components/ui/button";

import { Skeleton } from "@/components/ui/skeleton";
import { Input } from "@/components/ui/input";
import { Kbd } from "@/components/ui/kbd";
import { GenerationCard } from "@/components/app/generation-card";
import { fetchGenerations, type GenerationSummary } from "@/lib/generations";
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
  PlayCircle,
  Plus,
  Search,
  AlertCircle,
  CheckCircle,
  Loader2,
  PauseCircle,
  RotateCcw,
  Trash2,
  X,
} from "lucide-react";
import Link from "next/link";

type Task = GenerationSummary;

type BatchAction = "cancel" | "resume" | "delete" | null;

const fetchTasksList = fetchGenerations;

async function buildSupportError(response: Response, fallbackMessage: string) {
  const parsed = await parseApiError(response, fallbackMessage);
  return formatSupportMessage(parsed);
}

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
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState<"all" | "ready" | "active" | "attention">("all");
  const searchRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (event.key === "/" && !target?.closest("input, textarea, select, [contenteditable=true]")) {
        event.preventDefault();
        searchRef.current?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

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

  const refreshTasks = useCallback(async () => {
    const nextTasks = await fetchTasksList();
    setTasks(nextTasks);
    setSelectedTaskIds((current) =>
      current.filter((taskId) => nextTasks.some((task) => task.id === taskId)),
    );
  }, []);

  const hasActiveTasks = tasks.some((task) => ACTIVE_TASK_STATUSES.includes(task.status));
  useEffect(() => {
    if (!session?.user?.id || !hasActiveTasks) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    const refresh = async () => {
      try { await refreshTasks(); }
      catch { /* Preserve the last list during a transient network outage. */ }
      if (!stopped) timer = setTimeout(refresh, 5000);
    };
    timer = setTimeout(refresh, 5000);
    const onFocus = () => { void refreshTasks().catch(() => {}); };
    window.addEventListener("focus", onFocus);
    return () => { stopped = true; clearTimeout(timer); window.removeEventListener("focus", onFocus); };
  }, [session?.user?.id, hasActiveTasks, refreshTasks]);

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

  if (isPending) return <PageLoading />;

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

  /* ── Main render ──────────────────────────────────────────── */

  const query = search.trim().toLowerCase();
  const visibleTasks = tasks.filter((task) => {
    if (query && !task.source_title.toLowerCase().includes(query)) return false;
    if (filter === "ready") return task.status === "completed";
    if (filter === "active") return ACTIVE_TASK_STATUSES.includes(task.status);
    if (filter === "attention") return RESUMABLE_TASK_STATUSES.includes(task.status);
    return true;
  });
  const filters: { id: typeof filter; label: string; count: number }[] = [
    { id: "all", label: "All", count: tasks.length },
    { id: "ready", label: "Ready", count: completedCount },
    { id: "active", label: "In progress", count: activeCount },
    { id: "attention", label: "Needs attention", count: attentionCount },
  ];

  return (
    <main className={cn("mx-auto w-full max-w-7xl px-4 py-8 sm:px-8 md:py-10", selectedCount > 0 && "pb-32")}>
      <div className="flex flex-col gap-5 md:flex-row md:items-end md:justify-between">
        <div>
          <h1 className="font-display text-3xl font-bold tracking-tight">Library</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            {tasks.length} {tasks.length === 1 ? "generation" : "generations"} · hover to preview, select to batch-manage
          </p>
        </div>
        <div className="flex items-center gap-2">
          <div className="relative flex-1 md:w-72 md:flex-none">
            <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              ref={searchRef}
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Search by title"
              aria-label="Search generations"
              className="h-9 rounded-lg bg-background pl-9 pr-8"
            />
            <Kbd className="absolute right-2 top-1/2 -translate-y-1/2">/</Kbd>
          </div>
          <Button asChild size="sm" className="h-9 rounded-lg">
            <Link href="/"><Plus className="size-4" /><span className="hidden sm:inline">New</span></Link>
          </Button>
        </div>
      </div>

      {!isLoading && !error && tasks.length > 0 && (
        <div className="scrollbar-none -mx-4 mt-6 flex items-center gap-1.5 overflow-x-auto px-4 sm:mx-0 sm:px-0" role="tablist" aria-label="Filter generations">
          {filters.map((item) => (
            <button
              key={item.id}
              type="button"
              role="tab"
              aria-selected={filter === item.id}
              onClick={() => setFilter(item.id)}
              className={cn(
                "inline-flex h-8 shrink-0 items-center gap-1.5 rounded-full border px-3 text-xs font-medium transition-colors",
                filter === item.id ? "border-foreground bg-foreground text-background" : "bg-background text-muted-foreground hover:text-foreground",
              )}
            >
              {item.label}
              <span className={cn("tabular-nums", filter === item.id ? "opacity-70" : "opacity-60")}>{item.count}</span>
            </button>
          ))}
          <div className="ml-auto hidden items-center gap-2 pl-4 sm:flex">
            <Checkbox
              id="select-all"
              checked={allVisibleSelected ? true : someSelected ? "indeterminate" : false}
              onCheckedChange={handleToggleAllVisible}
              disabled={activeBatchAction !== null}
              aria-label="Select all generations"
            />
            <label htmlFor="select-all" className="text-xs text-muted-foreground">
              {selectedCount > 0 ? `${selectedCount} of ${tasks.length} selected` : "Select all"}
            </label>
          </div>
        </div>
      )}

      {batchNotice && (
        <Alert className={cn("mt-6", batchNotice.tone === "success" ? "border-emerald-200 bg-emerald-50/50" : "border-red-200 bg-red-50/50")}>
          {batchNotice.tone === "success" ? <CheckCircle className="h-4 w-4 text-emerald-600" /> : <AlertCircle className="h-4 w-4 text-red-600" />}
          <AlertDescription className="text-sm">{batchNotice.message}</AlertDescription>
        </Alert>
      )}

      <div className="mt-6">
        {isLoading ? (
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5">
            {[0, 1, 2, 3, 4].map((i) => <Skeleton key={i} className="aspect-[3/4] rounded-xl" />)}
          </div>
        ) : error ? (
          <Alert>
            <AlertCircle className="h-4 w-4" />
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        ) : tasks.length === 0 ? (
          <div className="flex flex-col items-center rounded-2xl border border-dashed px-6 py-20 text-center">
            <span className="flex size-14 items-center justify-center rounded-2xl bg-muted"><PlayCircle className="size-7 text-muted-foreground" /></span>
            <h2 className="mt-4 text-lg font-semibold">No generations yet</h2>
            <p className="mt-1 max-w-sm text-sm text-muted-foreground">Paste a YouTube link or upload a video and your clips will land here.</p>
            <Button asChild className="mt-6"><Link href="/">Create New Generation</Link></Button>
          </div>
        ) : visibleTasks.length === 0 ? (
          <p className="rounded-2xl border border-dashed px-6 py-16 text-center text-sm text-muted-foreground">
            Nothing matches{query ? ` “${search.trim()}”` : " this filter"}.
          </p>
        ) : (
          <div className="grid grid-cols-2 gap-x-4 gap-y-7 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5">
            {visibleTasks.map((task) => (
              <GenerationCard
                key={task.id}
                generation={task}
                showStatus
                selected={selectedTaskIds.includes(task.id)}
                onToggle={() => handleToggleTask(task.id)}
                selectionDisabled={activeBatchAction !== null}
              />
            ))}
          </div>
        )}
      </div>

      {/* ── Floating batch command bar ────────────────────────── */}
      {selectedCount > 0 && (
        <div
          className="fixed inset-x-0 bottom-[calc(4rem+env(safe-area-inset-bottom))] z-50 flex justify-center px-4 pb-4 pointer-events-none md:bottom-0 md:pb-6 md:pl-64"
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
    </main>
  );
}
