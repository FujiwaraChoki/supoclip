"use client";

import { useMemo, useRef, useState } from "react";
import {
  Captions,
  Circle,
  Film,
  Image as ImageIcon,
  LayoutGrid,
  Loader2,
  Music2,
  Plus,
  RectangleHorizontal,
  Search,
  Shapes,
  Trash2,
  Type,
  Upload,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

import { formatTime } from "./editor-utils";
import type { EditorAsset } from "./types";

type PanelTab = "media" | "text" | "captions" | "shapes";

interface MediaPanelProps {
  className?: string;
  assets: EditorAsset[];
  isUploading: boolean;
  uploadProgress: number | null;
  onUpload: (files: File[]) => void;
  onAddAsset: (asset: EditorAsset) => void;
  onDeleteAsset: (asset: EditorAsset) => void;
  onAddText: (preset: "heading" | "subheading" | "body" | "cta") => void;
  onAddCaption: () => void;
  onAddShape: (shape: "rectangle" | "circle" | "line") => void;
}

const TABS: Array<{ id: PanelTab; label: string; icon: typeof Film }> = [
  { id: "media", label: "Media", icon: LayoutGrid },
  { id: "text", label: "Text", icon: Type },
  { id: "captions", label: "Captions", icon: Captions },
  { id: "shapes", label: "Shapes", icon: Shapes },
];

function AssetIcon({ asset }: { asset: EditorAsset }) {
  if (asset.kind === "audio") return <Music2 className="size-4" />;
  if (asset.kind === "image") return <ImageIcon className="size-4" />;
  return <Film className="size-4" />;
}

export function MediaPanel({
  className,
  assets,
  isUploading,
  uploadProgress,
  onUpload,
  onAddAsset,
  onDeleteAsset,
  onAddText,
  onAddCaption,
  onAddShape,
}: MediaPanelProps) {
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [tab, setTab] = useState<PanelTab>("media");
  const [query, setQuery] = useState("");

  const filteredAssets = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    return assets.filter((asset) => !normalized || asset.name.toLowerCase().includes(normalized));
  }, [assets, query]);

  return (
    <aside className={cn("flex h-full min-h-0 w-[260px] shrink-0 border-r border-white/10 bg-[#141414] text-white", className)}>
      <nav className="flex w-[62px] shrink-0 flex-col items-center gap-1 border-r border-white/10 bg-[#0f0f0f] py-3">
        {TABS.map((entry) => {
          const Icon = entry.icon;
          return (
            <button
              key={entry.id}
              type="button"
              className={cn(
                "flex w-[54px] flex-col items-center gap-1 rounded-lg py-2 text-[10px] font-medium text-zinc-500 transition",
                tab === entry.id && "bg-white/10 text-white",
                tab !== entry.id && "hover:bg-white/5 hover:text-zinc-300",
              )}
              onClick={() => setTab(entry.id)}
            >
              <Icon className="size-[18px]" />
              {entry.label}
            </button>
          );
        })}
      </nav>

      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex h-12 shrink-0 items-center border-b border-white/10 px-3">
          <h2 className="text-sm font-semibold">{TABS.find((entry) => entry.id === tab)?.label}</h2>
        </div>

        {tab === "media" ? (
          <div className="flex min-h-0 flex-1 flex-col">
            <div className="space-y-2 border-b border-white/10 p-3">
              <input
                ref={inputRef}
                type="file"
                accept=".mp4,.mov,.webm,.mkv,.jpg,.jpeg,.png,.webp,.mp3,.wav,.m4a,.aac,.ogg,.oga,.weba,.flac"
                multiple
                className="hidden"
                onChange={(event) => {
                  const files = Array.from(event.target.files ?? []);
                  if (files.length) onUpload(files);
                  event.target.value = "";
                }}
              />
              <Button
                type="button"
                className="h-9 w-full bg-white text-black hover:bg-zinc-200"
                disabled={isUploading}
                onClick={() => inputRef.current?.click()}
              >
                {isUploading ? <Loader2 className="animate-spin" /> : <Upload />}
                {isUploading
                  ? uploadProgress === 100
                    ? "Processing media…"
                    : `Uploading ${uploadProgress ?? 0}%`
                  : "Upload media"}
              </Button>
              <div className="relative">
                <Search className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-zinc-600" />
                <Input
                  value={query}
                  placeholder="Search media"
                  className="h-8 border-white/10 bg-white/5 pl-8 text-xs text-white placeholder:text-zinc-600 focus-visible:border-white/25 focus-visible:ring-white/10"
                  onChange={(event) => setQuery(event.target.value)}
                />
              </div>
            </div>

            <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-3">
              {(["generated", "uploaded"] as const).map((source) => {
                const group = filteredAssets.filter((asset) => asset.source === source);
                if (!group.length) return null;
                return (
                  <div key={source} className="space-y-2">
                    <div className="flex items-center justify-between">
                      <h3 className="text-[10px] font-semibold uppercase tracking-[0.16em] text-zinc-500">
                        {source === "generated" ? "AI clips" : "Your uploads"}
                      </h3>
                      <span className="text-[10px] text-zinc-600">{group.length}</span>
                    </div>
                    <div className="grid grid-cols-2 gap-2">
                      {group.map((asset) => (
                        <div
                          key={asset.id}
                          className="group relative overflow-hidden rounded-lg border border-white/10 bg-white/[0.035] transition hover:border-white/25 hover:bg-white/[0.06]"
                          title={`Add ${asset.name} to timeline`}
                          onDoubleClick={() => onAddAsset(asset)}
                        >
                          <div className="relative flex aspect-video items-center justify-center overflow-hidden bg-black/55 text-zinc-500">
                            {asset.kind === "video" ? (
                              <video src={asset.url} preload="metadata" muted className="h-full w-full object-cover" />
                            ) : asset.kind === "image" ? (
                              // eslint-disable-next-line @next/next/no-img-element
                              <img src={asset.url} alt="" className="h-full w-full object-cover" />
                            ) : (
                              <div className="flex size-9 items-center justify-center rounded-full bg-white/10 text-zinc-300">
                                <Music2 className="size-4" />
                              </div>
                            )}
                            {asset.duration ? (
                              <span className="absolute bottom-1 right-1 rounded bg-black/75 px-1 py-0.5 font-mono text-[9px] text-white">
                                {formatTime(asset.duration)}
                              </span>
                            ) : null}
                          </div>
                          <div className="flex min-w-0 items-center gap-1.5 p-2">
                            <span className="text-zinc-500"><AssetIcon asset={asset} /></span>
                            <span className="min-w-0 flex-1 truncate text-[10px] font-medium text-zinc-300">{asset.name}</span>
                          </div>
                          <button
                            type="button"
                            aria-label={`Add ${asset.name}`}
                            className="absolute right-1 top-1 flex size-6 items-center justify-center rounded-md bg-white text-black opacity-0 shadow transition group-hover:opacity-100"
                            onClick={() => onAddAsset(asset)}
                          >
                            <Plus className="size-3.5" />
                          </button>
                          {asset.source === "uploaded" ? (
                            <button
                              type="button"
                              aria-label={`Delete ${asset.name}`}
                              className="absolute left-1 top-1 flex size-6 items-center justify-center rounded-md bg-black/75 text-zinc-300 opacity-0 shadow transition hover:text-red-300 group-hover:opacity-100"
                              onClick={() => onDeleteAsset(asset)}
                            >
                              <Trash2 className="size-3" />
                            </button>
                          ) : null}
                        </div>
                      ))}
                    </div>
                  </div>
                );
              })}

              {!filteredAssets.length ? (
                <div className="rounded-xl border border-dashed border-white/10 p-5 text-center">
                  <Upload className="mx-auto mb-2 size-5 text-zinc-600" />
                  <p className="text-xs font-medium text-zinc-400">No media here yet</p>
                  <p className="mt-1 text-[10px] leading-4 text-zinc-600">Upload video, images, or audio.</p>
                </div>
              ) : null}
            </div>
          </div>
        ) : null}

        {tab === "text" ? (
          <div className="space-y-3 overflow-y-auto p-3">
            <p className="text-[11px] leading-5 text-zinc-500">Add editable text at the playhead, then style it in the inspector.</p>
            {[
              { id: "heading" as const, label: "Add a heading", className: "text-xl font-black" },
              { id: "subheading" as const, label: "Add a subheading", className: "text-base font-bold" },
              { id: "body" as const, label: "Add body text", className: "text-sm font-medium" },
              { id: "cta" as const, label: "CALL TO ACTION", className: "rounded-md bg-white px-2 py-1 text-xs font-black text-black" },
            ].map((preset) => (
              <button
                key={preset.id}
                type="button"
                className="flex min-h-16 w-full items-center justify-center rounded-lg border border-white/10 bg-white/[0.035] px-3 text-center text-white transition hover:border-white/25 hover:bg-white/[0.06]"
                onClick={() => onAddText(preset.id)}
              >
                <span className={preset.className}>{preset.label}</span>
              </button>
            ))}
          </div>
        ) : null}

        {tab === "captions" ? (
          <div className="space-y-4 overflow-y-auto p-3">
            <div className="rounded-xl border border-white/10 bg-white/[0.035] p-4">
              <div className="mb-3 flex size-9 items-center justify-center rounded-lg bg-white/10 text-zinc-300">
                <Captions className="size-4" />
              </div>
              <h3 className="text-sm font-semibold">Dynamic captions</h3>
              <p className="mt-1 text-[11px] leading-5 text-zinc-400">
                Turn the selected clip transcript into a timed, editable caption layer.
              </p>
              <Button
                type="button"
                className="mt-4 h-8 w-full bg-white text-xs text-black hover:bg-zinc-200"
                onClick={onAddCaption}
              >
                <Captions />
                Add captions
              </Button>
            </div>
            <div className="rounded-lg border border-white/10 p-3 text-[10px] leading-4 text-zinc-500">
              Tip: edit wording, font, placement, colors, and stroke from the right inspector.
            </div>
          </div>
        ) : null}

        {tab === "shapes" ? (
          <div className="grid grid-cols-2 gap-2 p-3">
            {[
              { id: "rectangle" as const, label: "Rectangle", icon: RectangleHorizontal },
              { id: "circle" as const, label: "Circle", icon: Circle },
              { id: "line" as const, label: "Line", icon: RectangleHorizontal },
            ].map((shape) => {
              const Icon = shape.icon;
              return (
                <button
                  key={shape.id}
                  type="button"
                  className="flex aspect-square flex-col items-center justify-center gap-2 rounded-lg border border-white/10 bg-white/[0.035] text-zinc-400 transition hover:border-white/25 hover:bg-white/[0.06] hover:text-white"
                  onClick={() => onAddShape(shape.id)}
                >
                  <Icon className={cn("size-8", shape.id === "line" && "h-1")} />
                  <span className="text-[10px] font-medium">{shape.label}</span>
                </button>
              );
            })}
          </div>
        ) : null}
      </div>
    </aside>
  );
}
