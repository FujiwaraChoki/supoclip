"use client";

import { useRef } from "react";
import { useRouter } from "next/navigation";
import { Plus, Upload, ArrowRight } from "lucide-react";

import { setPendingFile } from "@/lib/pending-file-transfer";
import type { LastOpenedProject } from "@/lib/last-project";

interface HeroActionsProps {
  lastProject: LastOpenedProject | null;
}

/** "Start something" — the home screen's primary, equal-weight entry points
 * into the clipping flow. Drag-and-drop onto the page (handled by the parent,
 * home-app.tsx) uses the same pending-file handoff as "Import Video". */
export function HeroActions({ lastProject }: HeroActionsProps) {
  const router = useRouter();
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const handleImportFile = (files: FileList | null) => {
    const file = files?.[0];
    if (!file) return;
    setPendingFile(file);
    router.push("/create");
  };

  return (
    <section>
      <h2 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-2">
        Start something
      </h2>
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
        <button
          type="button"
          onClick={() => router.push("/create")}
          className="group flex items-center gap-3 p-4 border border-border bg-background hover:border-primary transition-colors text-left"
        >
          <div className="w-9 h-9 flex items-center justify-center bg-foreground shrink-0">
            <Plus className="w-4 h-4 text-background" />
          </div>
          <div className="min-w-0">
            <p className="text-sm font-semibold text-foreground">New Clip</p>
            <p className="text-xs text-muted-foreground">Paste a link or upload a video</p>
          </div>
        </button>

        <button
          type="button"
          onClick={() => fileInputRef.current?.click()}
          className="group flex items-center gap-3 p-4 border border-border bg-background hover:border-primary transition-colors text-left"
        >
          <div className="w-9 h-9 flex items-center justify-center border border-border shrink-0">
            <Upload className="w-4 h-4 text-foreground" />
          </div>
          <div className="min-w-0">
            <p className="text-sm font-semibold text-foreground">Import Video</p>
            <p className="text-xs text-muted-foreground">Pick a file from this device</p>
          </div>
          <input
            ref={fileInputRef}
            type="file"
            accept="video/*"
            className="hidden"
            onChange={(e) => handleImportFile(e.target.files)}
          />
        </button>

        {lastProject && (
          <button
            type="button"
            onClick={() => router.push(`/tasks/${lastProject.id}`)}
            className="group flex items-center gap-3 p-4 border border-border bg-background hover:border-primary transition-colors text-left"
          >
            <div className="w-9 h-9 flex items-center justify-center border border-border shrink-0">
              <ArrowRight className="w-4 h-4 text-foreground" />
            </div>
            <div className="min-w-0">
              <p className="text-sm font-semibold text-foreground">Continue</p>
              <p className="text-xs text-muted-foreground truncate">{lastProject.title}</p>
            </div>
          </button>
        )}
      </div>
    </section>
  );
}
