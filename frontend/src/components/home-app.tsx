"use client";

import { useState, useEffect } from "react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import Link from "next/link";
import Image from "next/image";
import { ArrowRight, CheckCircle, Loader2, Film, List, Settings, Plus } from "lucide-react";

interface TaskSummary {
  id: string;
  source_title: string;
  source_type: string;
  status: string;
  clips_count: number;
  created_at: string;
}

function statusBadge(status: string) {
  if (status === "completed") {
    return (
      <Badge className="bg-green-100 text-green-800 text-xs">
        <CheckCircle className="w-3 h-3 mr-1" />
        Completed
      </Badge>
    );
  }
  if (status === "processing" || status === "queued") {
    return (
      <Badge className="bg-blue-100 text-blue-800 text-xs">
        <Loader2 className="w-3 h-3 animate-spin" />
        {status === "queued" ? "Queued" : "Processing"}
      </Badge>
    );
  }
  return (
    <Badge variant="outline" className="text-xs">
      {status}
    </Badge>
  );
}

/** Local-first dashboard: recent projects + a way to start a new one. No login, no form here. */
export default function HomeApp() {
  const [tasks, setTasks] = useState<TaskSummary[]>([]);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    const loadTasks = async () => {
      try {
        const response = await fetch("/api/tasks/", { cache: "no-store" });
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

    loadTasks();
  }, []);

  return (
    <div className="min-h-screen bg-white">
      {/* Header */}
      <div className="border-b bg-white">
        <div className="max-w-6xl mx-auto px-6 py-4 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <Image src="/logo.png" alt="SupoClip" width={24} height={24} className="rounded-lg" />
            <h1 className="text-xl font-bold text-black">SupoClip</h1>
          </div>
          <div className="flex items-center gap-2">
            <Link href="/list">
              <Button variant="outline" size="sm">
                <List className="w-4 h-4" />
                All Generations
              </Button>
            </Link>
            <Link href="/settings">
              <Button variant="outline" size="sm">
                <Settings className="w-4 h-4" />
                Settings
              </Button>
            </Link>
          </div>
        </div>
      </div>

      {/* Main content */}
      <div className="max-w-6xl mx-auto px-6 py-10">
        <div className="flex items-center justify-between mb-8">
          <div>
            <h2 className="text-2xl font-bold text-stone-900 mb-1">Your projects</h2>
            <p className="text-stone-500">Paste a YouTube link or upload a video — AI handles the rest.</p>
          </div>
          <Link href="/create">
            <Button size="lg" className="rounded-xl">
              <Plus className="w-4 h-4" />
              New Video
            </Button>
          </Link>
        </div>

        {isLoading && (
          <div className="space-y-3">
            {[0, 1, 2].map((i) => (
              <div key={i} className="p-4 rounded-xl border border-stone-200">
                <div className="flex items-center gap-4">
                  <Skeleton className="w-10 h-10 rounded-lg" />
                  <div className="flex-1">
                    <Skeleton className="h-4 w-48 mb-1.5" />
                    <Skeleton className="h-3 w-32" />
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}

        {!isLoading && tasks.length === 0 && (
          <div className="text-center py-16 rounded-xl border border-dashed border-stone-300">
            <Film className="w-10 h-10 text-stone-300 mx-auto mb-3" />
            <p className="text-stone-500 mb-4">No projects yet.</p>
            <Link href="/create">
              <Button>
                <Plus className="w-4 h-4" />
                Create your first clip
              </Button>
            </Link>
          </div>
        )}

        {!isLoading && tasks.length > 0 && (
          <div className="space-y-3">
            {tasks.map((task) => (
              <Link key={task.id} href={`/tasks/${task.id}`} className="block">
                <div className="flex items-center justify-between p-4 rounded-xl border border-stone-200 bg-stone-50/50 hover:bg-stone-50 transition-colors group">
                  <div className="flex items-center gap-4 min-w-0">
                    <div className="flex-shrink-0 w-10 h-10 rounded-lg bg-stone-900 flex items-center justify-center">
                      <Film className="w-5 h-5 text-white" />
                    </div>
                    <div className="min-w-0">
                      <p className="text-sm font-medium text-stone-900 truncate">{task.source_title}</p>
                      <div className="flex items-center gap-2 text-xs text-stone-500 mt-0.5">
                        <span className="capitalize">{task.source_type}</span>
                        <span>&middot;</span>
                        <span>{new Date(task.created_at).toLocaleDateString()}</span>
                        <span>&middot;</span>
                        <span>
                          {task.clips_count} {task.clips_count === 1 ? "clip" : "clips"}
                        </span>
                      </div>
                    </div>
                  </div>
                  <div className="flex items-center gap-3 flex-shrink-0">
                    {statusBadge(task.status)}
                    <ArrowRight className="w-4 h-4 text-stone-400 group-hover:text-stone-600 transition-colors" />
                  </div>
                </div>
              </Link>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
