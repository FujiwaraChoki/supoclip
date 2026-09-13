"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import { UploadCloud } from "lucide-react";

import { HomeTopBar } from "@/components/home/home-top-bar";
import { HeroActions } from "@/components/home/hero-actions";
import { RecentProjects, type TaskSummary } from "@/components/home/recent-projects";
import { ToolsGrid } from "@/components/home/tools-grid";
import { StatusStrip } from "@/components/home/status-strip";
import { ActivityFeed } from "@/components/home/activity-feed";
import { setPendingFile } from "@/lib/pending-file-transfer";
import { getLastOpenedProject, type LastOpenedProject } from "@/lib/last-project";
import { ResumeBatchPrompt } from "@/components/batch/resume-batch-prompt";

/** The home screen: an operations-dashboard launchpad for the multi-tool
 * platform (see CLAUDE.md's "Home Screen" section), not a marketing page or
 * a bare project list. Sections top to bottom: top bar, hero CTAs, recent
 * projects, tools grid, activity, system status strip. */
export default function HomeApp() {
  const router = useRouter();
  const [tasks, setTasks] = useState<TaskSummary[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [lastProject, setLastProject] = useState<LastOpenedProject | null>(null);
  const [isDraggingFile, setIsDraggingFile] = useState(false);
  const dragDepthRef = useRef(0);

  useEffect(() => {
    setLastProject(getLastOpenedProject());
  }, []);

  useEffect(() => {
    const loadTasks = async () => {
      try {
        const response = await fetch("/api/tasks/?limit=20", { cache: "no-store" });
        if (response.ok) {
          const data = await response.json();
          setTasks(data.tasks || []);
        }
      } catch (error) {
        console.error("Failed to load tasks:", error);
      } finally {
        setIsLoading(false);
      }
    };

    void loadTasks();
  }, []);

  const processingCount = tasks.filter((task) => task.status === "processing" || task.status === "queued").length;

  // Page-level drag-and-drop: dropping a file anywhere on home hands it off
  // to /create the same way "Import Video" does (see pending-file-transfer.ts).
  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    if (e.dataTransfer.types.includes("Files")) setIsDraggingFile(true);
  }, []);

  const handleDragEnter = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    if (!e.dataTransfer.types.includes("Files")) return;
    dragDepthRef.current += 1;
    setIsDraggingFile(true);
  }, []);

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    dragDepthRef.current = Math.max(0, dragDepthRef.current - 1);
    if (dragDepthRef.current === 0) setIsDraggingFile(false);
  }, []);

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setIsDraggingFile(false);
      dragDepthRef.current = 0;
      const file = e.dataTransfer.files?.[0];
      if (!file) return;
      setPendingFile(file);
      router.push("/create");
    },
    [router],
  );

  return (
    <div
      className="min-h-screen bg-background relative"
      onDragOver={handleDragOver}
      onDragEnter={handleDragEnter}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
    >
      <ResumeBatchPrompt />
      {isDraggingFile && (
        <div className="fixed inset-0 z-50 bg-background border-4 border-dashed border-primary flex flex-col items-center justify-center gap-3 pointer-events-none">
          <UploadCloud className="w-10 h-10 text-primary" />
          <p className="text-sm font-semibold text-foreground">Drop to start a new clip</p>
        </div>
      )}

      <HomeTopBar />

      <div className="max-w-6xl mx-auto px-4 py-6 flex flex-col gap-8">
        <HeroActions lastProject={lastProject} />
        <RecentProjects tasks={tasks} isLoading={isLoading} />
        <ToolsGrid processingCount={processingCount} />
        <ActivityFeed tasks={tasks} />
      </div>

      <StatusStrip />
    </div>
  );
}
