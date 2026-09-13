"use client";

import { useEffect, useState } from "react";
import { Cpu, HardDrive, ListOrdered, Activity } from "lucide-react";

interface SystemStatus {
  queue_depth: number;
  processing_count: number;
  active_jobs: number;
  gpu_enabled: boolean;
  gpu_available: boolean;
  disk_free_bytes: number;
  disk_total_bytes: number;
}

const POLL_INTERVAL_MS = 10000;

function formatBytes(bytes: number): string {
  if (bytes <= 0) return "—";
  const gb = bytes / 1024 ** 3;
  return gb >= 1 ? `${gb.toFixed(1)} GB` : `${(bytes / 1024 ** 2).toFixed(0)} MB`;
}

/** Bottom-of-page operator strip: queue depth, GPU state, disk space, active
 * jobs. Polls its own small endpoint rather than piggybacking on the tasks
 * list, since disk/GPU state isn't part of a task. */
export function StatusStrip() {
  const [status, setStatus] = useState<SystemStatus | null>(null);

  useEffect(() => {
    let cancelled = false;

    const poll = async () => {
      try {
        const response = await fetch("/api/tasks/system-status", { cache: "no-store" });
        if (!response.ok) return;
        const data = await response.json();
        if (!cancelled) setStatus(data);
      } catch (error) {
        console.error("Failed to load system status:", error);
      }
    };

    void poll();
    const interval = setInterval(poll, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, []);

  const diskLowSpace = status ? status.disk_total_bytes > 0 && status.disk_free_bytes / status.disk_total_bytes < 0.1 : false;

  return (
    <div id="system-status-strip" className="border-t border-border bg-background">
      <div className="max-w-6xl mx-auto px-4 py-2 flex flex-wrap items-center gap-x-6 gap-y-1 text-[11px] font-mono text-muted-foreground">
        <span className="flex items-center gap-1.5">
          <ListOrdered className="w-3.5 h-3.5" />
          QUEUE {status ? status.queue_depth : "—"}
        </span>
        <span className="flex items-center gap-1.5">
          <Activity className="w-3.5 h-3.5" />
          ACTIVE {status ? status.active_jobs : "—"}
        </span>
        <span className="flex items-center gap-1.5">
          <Cpu className="w-3.5 h-3.5" />
          GPU {status ? (status.gpu_enabled && status.gpu_available ? "ON" : status.gpu_enabled ? "UNAVAILABLE" : "OFF") : "—"}
        </span>
        <span className={`flex items-center gap-1.5 ${diskLowSpace ? "text-foreground font-bold" : ""}`}>
          <HardDrive className="w-3.5 h-3.5" />
          DISK {status ? `${formatBytes(status.disk_free_bytes)} free` : "—"}
        </span>
      </div>
    </div>
  );
}
