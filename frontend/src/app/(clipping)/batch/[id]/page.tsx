"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { ArrowLeft, CheckCircle2, Circle, Loader2, Pause, Play, RotateCcw, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/progress";
import { toast } from "@/lib/toast";
import { notifyBatchComplete } from "@/lib/notifications";
import { formatSupportMessage, parseApiError } from "@/lib/api-error";

interface BatchItem {
  id: string;
  source_filename: string;
  task_id: string | null;
  status: "pending" | "processing" | "done" | "error" | "skipped";
  progress_percent: number;
  current_stage: string | null;
  error_message: string | null;
  retry_count: number;
}

interface BatchQueue {
  id: string;
  status: "queued" | "running" | "paused" | "completed" | "cancelled";
  started_at: string | null;
  completed_at: string | null;
}

const STATUS_ICON: Record<BatchItem["status"], typeof Circle> = {
  pending: Circle,
  processing: Loader2,
  done: CheckCircle2,
  error: X,
  skipped: Circle,
};

export default function BatchQueueDetailPage() {
  const params = useParams<{ id: string }>();
  const [queue, setQueue] = useState<BatchQueue | null>(null);
  const [items, setItems] = useState<BatchItem[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const notifiedRef = useRef(false);

  const buildSupportError = useCallback(async (response: Response, fallbackMessage: string) => {
    const parsed = await parseApiError(response, fallbackMessage);
    return formatSupportMessage(parsed);
  }, []);

  const fetchStatus = useCallback(async () => {
    try {
      const response = await fetch(`/api/batch-queue/${params.id}`, { cache: "no-store" });
      if (!response.ok) throw new Error(await buildSupportError(response, "Failed to load batch"));
      const data = await response.json();
      setQueue(data.batch_queue);
      setItems(data.items || []);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to load batch");
    } finally {
      setIsLoading(false);
    }
  }, [params.id, buildSupportError]);

  useEffect(() => {
    void fetchStatus();
    const interval = setInterval(fetchStatus, 2000);
    return () => clearInterval(interval);
  }, [fetchStatus]);

  useEffect(() => {
    if (!queue || notifiedRef.current) return;
    if (queue.status === "completed" || queue.status === "cancelled") {
      notifiedRef.current = true;
      const succeeded = items.filter((i) => i.status === "done").length;
      const failed = items.filter((i) => i.status === "error").length;
      if (queue.status === "completed") {
        if (failed > 0) {
          toast.error(`Batch finished: ${succeeded}/${items.length} succeeded, ${failed} failed.`);
        } else {
          toast.success(`Batch finished: all ${items.length} videos processed.`);
        }
      }
      notifyBatchComplete({ succeeded, failed, total: items.length });
    }
  }, [queue, items]);

  const handlePause = async () => {
    const response = await fetch(`/api/batch-queue/${params.id}/pause`, { method: "POST" });
    if (response.ok) toast.success("Batch paused.");
    await fetchStatus();
  };

  const handleResume = async (skipFailed = false) => {
    const response = await fetch(`/api/batch-queue/${params.id}/resume`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ skip_failed: skipFailed }),
    });
    if (response.ok) toast.success("Batch resumed.");
    await fetchStatus();
  };

  const handleCancel = async () => {
    const response = await fetch(`/api/batch-queue/${params.id}/cancel`, { method: "POST" });
    if (response.ok) toast.success("Batch cancelled.");
    await fetchStatus();
  };

  const handleRetryItem = async (itemId: string) => {
    const response = await fetch(`/api/batch-queue/${params.id}/items/${itemId}/retry`, {
      method: "POST",
    });
    if (response.ok) toast.success("Item queued for retry.");
    await fetchStatus();
  };

  if (isLoading) {
    return <div className="mx-auto max-w-3xl px-6 py-12 lg:px-12">Loading...</div>;
  }
  if (!queue) {
    return <div className="mx-auto max-w-3xl px-6 py-12 lg:px-12">Batch not found.</div>;
  }

  const done = items.filter((i) => i.status === "done" || i.status === "skipped").length;
  const failed = items.filter((i) => i.status === "error").length;
  const overallPercent = items.length > 0 ? Math.round(((done + failed) / items.length) * 100) : 0;

  return (
    <div className="mx-auto max-w-3xl px-6 py-12 lg:px-12">
      <div className="mb-8 flex items-center gap-3">
        <Link href="/list">
          <Button variant="ghost" size="icon">
            <ArrowLeft className="size-4" />
          </Button>
        </Link>
        <div>
          <h1 className="text-h1 font-bold tracking-[-0.01em]">Batch Processing</h1>
          <p className="text-small text-muted-foreground">
            {done + failed} of {items.length} done — {queue.status}
          </p>
        </div>
      </div>

      <Card className="mb-6">
        <CardContent className="space-y-3 pt-6">
          <Progress value={overallPercent} />
          <div className="flex gap-2">
            {queue.status === "running" && (
              <Button variant="outline" size="sm" onClick={handlePause}>
                <Pause className="mr-1 size-3.5" />
                Pause batch
              </Button>
            )}
            {queue.status === "paused" && (
              <Button variant="outline" size="sm" onClick={() => handleResume(false)}>
                <Play className="mr-1 size-3.5" />
                Resume batch
              </Button>
            )}
            {(queue.status === "running" || queue.status === "paused") && (
              <Button variant="outline" size="sm" onClick={handleCancel}>
                <X className="mr-1 size-3.5" />
                Cancel batch
              </Button>
            )}
            {failed > 0 && (queue.status === "completed" || queue.status === "paused") && (
              <Button variant="outline" size="sm" onClick={() => handleResume(true)}>
                Resume, skipping failed
              </Button>
            )}
          </div>
        </CardContent>
      </Card>

      <div className="space-y-3">
        {items.map((item) => {
          const Icon = STATUS_ICON[item.status];
          return (
            <Card key={item.id}>
              <CardHeader className="flex flex-row items-center justify-between py-3">
                <CardTitle className="flex items-center gap-2 text-h3">
                  <Icon className={`size-4 ${item.status === "processing" ? "animate-spin" : ""}`} />
                  {item.source_filename}
                </CardTitle>
                <Badge variant={item.status === "error" ? "destructive" : "secondary"}>
                  {item.status}
                </Badge>
              </CardHeader>
              <CardContent className="pb-3">
                {item.status === "processing" && (
                  <div className="space-y-1">
                    <Progress value={item.progress_percent} />
                    <p className="text-caption text-muted-foreground">{item.current_stage ?? "starting"}</p>
                  </div>
                )}
                {item.status === "error" && (
                  <div className="flex items-center justify-between gap-2">
                    <p className="text-small text-muted-foreground">{item.error_message}</p>
                    <Button variant="outline" size="sm" onClick={() => handleRetryItem(item.id)}>
                      <RotateCcw className="mr-1 size-3.5" />
                      Retry
                    </Button>
                  </div>
                )}
                {item.task_id && item.status === "done" && (
                  <Link href={`/tasks/${item.task_id}`} className="text-small underline text-muted-foreground">
                    View project
                  </Link>
                )}
              </CardContent>
            </Card>
          );
        })}
      </div>
    </div>
  );
}
