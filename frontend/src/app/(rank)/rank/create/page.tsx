"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import { EmptyState } from "@/components/empty-state";
import { formatSupportMessage, parseApiError } from "@/lib/api-error";
import { toast } from "@/lib/toast";
import {
  ListOrdered,
  Loader2,
  Upload,
  FolderOpen,
  AlertTriangle,
  Shuffle,
  ArrowLeft,
} from "lucide-react";

const ACCEPTED_EXTENSIONS = [".mp4", ".mov", ".mkv", ".webm"];
const MIN_CLIPS = 5;
const TEMPLATE_ID = "ranking_classic";

interface FolderSummary {
  id: string;
  name: string;
  clip_count: number;
}

interface FolderClip {
  id: string;
  file_path: string;
  original_filename: string;
  duration_seconds: number | null;
  saved_text: string | null;
  use_count: number;
}

interface Slot {
  rank: number; // 5..1, fixed label
  clip: FolderClip | null;
  text: string;
}

type Step = "folder" | "select" | "text";

function formatDuration(seconds: number | null) {
  if (seconds === null || !Number.isFinite(seconds)) return "—";
  const mins = Math.floor(seconds / 60);
  const secs = Math.round(seconds % 60);
  return `${mins}:${secs.toString().padStart(2, "0")}`;
}

export default function RankingCreatePage() {
  const router = useRouter();
  const folderInputRef = useRef<HTMLInputElement | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const [step, setStep] = useState<Step>("folder");
  const [existingFolders, setExistingFolders] = useState<FolderSummary[]>([]);
  const [folderId, setFolderId] = useState<string | null>(null);
  const [folderName, setFolderName] = useState<string | null>(null);
  const [pendingFolderName, setPendingFolderName] = useState("");
  const [libraryClips, setLibraryClips] = useState<FolderClip[]>([]);
  const [isUploading, setIsUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState<{ done: number; total: number } | null>(null);

  const [slots, setSlots] = useState<Slot[]>([]);
  const [pickerSlotIndex, setPickerSlotIndex] = useState<number | null>(null);
  const [exportPreset, setExportPreset] = useState("tiktok");
  const [hookWhite, setHookWhite] = useState("");
  const [hookRed, setHookRed] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);

  useEffect(() => {
    (async () => {
      try {
        const response = await fetch("/api/ranking/folders");
        if (!response.ok) return;
        const data = await response.json();
        setExistingFolders(data.folders ?? []);
      } catch {
        // Quick-pick list is a convenience, not required to proceed.
      }
    })();
  }, []);

  const refreshFolderClips = useCallback(async (id: string) => {
    const response = await fetch(`/api/ranking/folders/${id}/clips`);
    if (!response.ok) return;
    const data = await response.json();
    setLibraryClips(data.clips ?? []);
  }, []);

  const uploadFilesToFolder = useCallback(
    async (files: File[], name: string) => {
      const filtered = files.filter((file) =>
        ACCEPTED_EXTENSIONS.some((ext) => file.name.toLowerCase().endsWith(ext))
      );
      if (filtered.length === 0) {
        toast.error("No supported video files found (MP4, MOV, MKV, WEBM).");
        return;
      }
      setIsUploading(true);
      setUploadProgress({ done: 0, total: filtered.length });
      try {
        const formData = new FormData();
        formData.append("folder_name", name);
        filtered.forEach((file) => formData.append("files", file));
        const response = await fetch("/api/ranking/folders/scan", {
          method: "POST",
          body: formData,
        });
        if (!response.ok) {
          const parsed = await parseApiError(response, "Failed to add clips to folder");
          throw new Error(formatSupportMessage(parsed));
        }
        const data = await response.json();
        setFolderId(data.folder_id);
        setFolderName(name);
        setLibraryClips(data.clips ?? []);
        if (data.skipped?.length) {
          toast.warning(`Skipped ${data.skipped.length} unsupported file(s).`);
        }
      } catch (err) {
        toast.error(err instanceof Error ? err.message : "Failed to add clips to folder");
      } finally {
        setIsUploading(false);
        setUploadProgress(null);
      }
    },
    []
  );

  const handleFolderPick = (event: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(event.target.files ?? []);
    event.target.value = "";
    if (files.length === 0) return;
    // webkitRelativePath looks like "MyFolder/clip1.mp4" — the top segment
    // is the folder name the browser's directory picker reports.
    const withPath = files[0] as File & { webkitRelativePath?: string };
    const derivedName = withPath.webkitRelativePath?.split("/")[0] || "";
    if (derivedName) {
      void uploadFilesToFolder(files, derivedName);
    } else {
      setPendingFolderName("");
      setFilesAwaitingName(files);
    }
  };

  const [filesAwaitingName, setFilesAwaitingName] = useState<File[] | null>(null);

  const handleMultiFilePick = (event: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(event.target.files ?? []);
    event.target.value = "";
    if (files.length === 0) return;
    setFilesAwaitingName(files);
  };

  const handleDrop = (event: React.DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    const files = Array.from(event.dataTransfer.files ?? []);
    if (files.length === 0) return;
    setFilesAwaitingName(files);
  };

  const confirmPendingUpload = async () => {
    if (!filesAwaitingName || !pendingFolderName.trim()) return;
    const name = pendingFolderName.trim();
    setFilesAwaitingName(null);
    await uploadFilesToFolder(filesAwaitingName, name);
  };

  const openExistingFolder = async (folder: FolderSummary) => {
    setFolderId(folder.id);
    setFolderName(folder.name);
    await refreshFolderClips(folder.id);
  };

  const meetsMinimum = libraryClips.length >= MIN_CLIPS;

  const startSelection = async () => {
    if (!folderId || !meetsMinimum) return;
    try {
      const response = await fetch(`/api/ranking/folders/${folderId}/select`, { method: "POST" });
      if (!response.ok) {
        const parsed = await parseApiError(response, "Failed to select clips");
        throw new Error(formatSupportMessage(parsed));
      }
      const data = await response.json();
      const picked: FolderClip[] = data.clips ?? [];
      const nextSlots: Slot[] = [5, 4, 3, 2, 1].map((rank, index) => ({
        rank,
        clip: picked[index] ?? null,
        text: picked[index]?.saved_text ?? "",
      }));
      setSlots(nextSlots);
      setStep("select");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to select clips");
    }
  };

  const swapSlot = (index: number, clip: FolderClip) => {
    setSlots((current) =>
      current.map((slot, i) =>
        i === index ? { ...slot, clip, text: clip.saved_text ?? "" } : slot
      )
    );
    setPickerSlotIndex(null);
  };

  const usedClipIds = new Set(slots.map((s) => s.clip?.id).filter(Boolean));

  const goToTextStep = () => setStep("text");

  const handleTextChange = (index: number, value: string) => {
    setSlots((current) => current.map((slot, i) => (i === index ? { ...slot, text: value } : slot)));
  };

  const missingTextRanks = slots.filter((s) => !s.text.trim()).map((s) => s.rank);
  const memoryWarningRanks = slots
    .filter((s) => s.clip && !s.clip.saved_text && !s.text.trim())
    .map((s) => s.rank);

  const handleSubmit = async () => {
    if (missingTextRanks.length > 0 || slots.some((s) => !s.clip)) {
      toast.error("Every rank needs text before rendering.");
      return;
    }
    setIsSubmitting(true);
    try {
      const createResponse = await fetch("/api/ranking/tasks", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      if (!createResponse.ok) {
        const parsed = await parseApiError(createResponse, "Failed to create ranking project");
        throw new Error(formatSupportMessage(parsed));
      }
      const { task_id: taskId } = await createResponse.json();

      // Ranks are added in ascending order (1 first) so the default
      // order_index matches rank order; render_order="descending" on
      // ranking_classic flips *playback* to worst-first without changing
      // which digit each clip displays (see ranking_service.py::_resolve_ranks).
      const ordered = [...slots].sort((a, b) => a.rank - b.rank);
      for (const slot of ordered) {
        if (!slot.clip) continue;
        const addResponse = await fetch(`/api/ranking/tasks/${taskId}/inputs`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            file_path: slot.clip.file_path,
            original_filename: slot.clip.original_filename,
            duration_seconds: slot.clip.duration_seconds,
            folder_clip_id: slot.clip.id,
            rank_text: slot.text.trim(),
            framing: "blur_fill",
          }),
        });
        if (!addResponse.ok) {
          const parsed = await parseApiError(addResponse, `Failed to attach rank ${slot.rank}`);
          throw new Error(formatSupportMessage(parsed));
        }
        const { input_id: inputId } = await addResponse.json();
        await fetch(`/api/ranking/tasks/${taskId}/inputs/${inputId}/rank`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ rank_position: slot.rank }),
        });
      }

      await fetch(`/api/ranking/tasks/${taskId}/settings`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          template_id: TEMPLATE_ID,
          export_preset: exportPreset,
          hook_white: hookWhite.trim(),
          hook_red: hookRed.trim(),
        }),
      });

      const renderResponse = await fetch(`/api/ranking/tasks/${taskId}/render`, { method: "POST" });
      if (!renderResponse.ok) {
        const parsed = await parseApiError(renderResponse, "Failed to start rendering");
        throw new Error(formatSupportMessage(parsed));
      }

      router.push(`/rank/tasks/${taskId}`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to create ranking compilation");
      setIsSubmitting(false);
    }
  };

  return (
    <div className="min-h-screen bg-background">
      <div className="border-b border-border bg-background">
        <div className="max-w-4xl mx-auto px-6 lg:px-12 py-6">
          <h1 className="text-[32px] font-bold leading-[1.15] tracking-[-0.01em] text-foreground">
            New ranking compilation
          </h1>
          <p className="mt-1 text-[15px] text-muted-foreground">
            Pick a folder of clips, choose your 5, add text, and render.
          </p>
        </div>
      </div>

      <div className="max-w-4xl mx-auto px-6 lg:px-12 py-8 space-y-6">
        {step === "folder" && (
          <div className="space-y-6">
            {existingFolders.length > 0 && (
              <div className="space-y-2">
                <p className="text-[13px] font-medium text-foreground">Use an existing folder</p>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                  {existingFolders.map((folder) => (
                    <button
                      key={folder.id}
                      type="button"
                      onClick={() => void openExistingFolder(folder)}
                      className={`flex items-center gap-3 border p-3 text-left ${
                        folderId === folder.id ? "border-foreground" : "border-border hover:border-primary"
                      }`}
                    >
                      <FolderOpen className="w-4 h-4 text-muted-foreground shrink-0" />
                      <div className="min-w-0 flex-1">
                        <p className="text-[14px] text-foreground truncate">{folder.name}</p>
                        <p className="text-[12px] text-muted-foreground">{folder.clip_count} clips</p>
                      </div>
                    </button>
                  ))}
                </div>
              </div>
            )}

            <div
              onDragOver={(e) => e.preventDefault()}
              onDrop={handleDrop}
              className="border border-border p-8 flex flex-col items-center justify-center gap-3 text-center"
            >
              <Upload className="w-6 h-6 text-foreground" />
              <p className="text-[15px] text-foreground">
                Drag and drop a folder&apos;s clips here
              </p>
              <div className="flex items-center gap-2">
                <Button variant="outline" size="sm" onClick={() => folderInputRef.current?.click()}>
                  <FolderOpen className="w-4 h-4" />
                  Choose folder
                </Button>
                <Button variant="outline" size="sm" onClick={() => fileInputRef.current?.click()}>
                  Choose files
                </Button>
              </div>
              <input
                ref={folderInputRef}
                type="file"
                // @ts-expect-error -- non-standard attrs, browser-supported directory picker
                webkitdirectory=""
                directory=""
                multiple
                className="hidden"
                onChange={handleFolderPick}
              />
              <input
                ref={fileInputRef}
                type="file"
                accept="video/mp4,video/quicktime,video/x-matroska,video/webm"
                multiple
                className="hidden"
                onChange={handleMultiFilePick}
              />
              <p className="text-[12px] text-muted-foreground">MP4, MOV, MKV, WEBM · flat folder, no nesting</p>
            </div>

            {filesAwaitingName && (
              <div className="border border-border p-4 space-y-3">
                <p className="text-[13px] text-foreground">
                  {filesAwaitingName.length} file(s) selected. What folder are these from?
                </p>
                <div className="flex gap-2">
                  <Input
                    value={pendingFolderName}
                    onChange={(e) => setPendingFolderName(e.target.value)}
                    placeholder="e.g. Funny football moments"
                    className="flex-1"
                  />
                  <Button onClick={() => void confirmPendingUpload()} disabled={!pendingFolderName.trim()}>
                    Add
                  </Button>
                  <Button variant="ghost" onClick={() => setFilesAwaitingName(null)}>
                    Cancel
                  </Button>
                </div>
              </div>
            )}

            {isUploading && (
              <div className="flex items-center gap-2 text-[13px] text-muted-foreground">
                <Loader2 className="w-4 h-4 animate-spin" />
                Adding {uploadProgress?.total ?? ""} clip(s)…
              </div>
            )}

            {folderId && (
              <div className="space-y-3">
                <div className="flex items-center justify-between">
                  <p className="text-[13px] font-medium text-foreground">
                    {folderName} · {libraryClips.length} clip{libraryClips.length === 1 ? "" : "s"}
                  </p>
                  <div className="flex gap-2">
                    <Button variant="outline" size="sm" onClick={() => folderInputRef.current?.click()}>
                      Add more
                    </Button>
                  </div>
                </div>

                {!meetsMinimum && (
                  <div className="flex items-start gap-2 border border-border bg-foreground text-background px-3 py-2 text-[13px]">
                    <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" />
                    <span>
                      This folder has {libraryClips.length} clip{libraryClips.length === 1 ? "" : "s"} —
                      at least {MIN_CLIPS} are needed to build a ranking.
                    </span>
                  </div>
                )}

                {libraryClips.length === 0 ? (
                  <EmptyState
                    icon={ListOrdered}
                    title="No clips yet"
                    description="Add clips to this folder to get started."
                  />
                ) : (
                  <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
                    {libraryClips.map((clip) => (
                      <div key={clip.id} className="border border-border p-2 space-y-1">
                        <video
                          src={`/api/ranking/folders/clips/${clip.id}/file`}
                          className="w-full aspect-[9/16] object-cover bg-muted"
                          muted
                        />
                        <p className="text-[11px] text-foreground truncate">{clip.original_filename}</p>
                        <div className="flex items-center justify-between">
                          <p className="text-[11px] text-muted-foreground">
                            {formatDuration(clip.duration_seconds)}
                          </p>
                          {clip.use_count > 0 && (
                            <Badge variant="outline" className="text-[10px]">
                              used {clip.use_count}×
                            </Badge>
                          )}
                        </div>
                      </div>
                    ))}
                  </div>
                )}

                <Button className="w-full" disabled={!meetsMinimum} onClick={() => void startSelection()}>
                  <Shuffle className="w-4 h-4" />
                  Auto-select 5 clips
                </Button>
              </div>
            )}
          </div>
        )}

        {step === "select" && (
          <div className="space-y-4">
            <Button variant="ghost" size="sm" onClick={() => setStep("folder")} className="-ml-2">
              <ArrowLeft className="w-4 h-4" />
              Back to folder
            </Button>
            <p className="text-[13px] text-muted-foreground">
              Numbers are just for keeping clips straight — #1 is simply whichever clip plays last.
              Click a slot to swap its clip.
            </p>
            <div className="space-y-2">
              {slots.map((slot, index) => (
                <div key={slot.rank} className="flex items-center gap-3 border border-border p-3">
                  <div
                    className={`flex items-center justify-center w-9 h-9 text-[15px] font-bold shrink-0 ${
                      slot.rank === 1 ? "bg-[#FFD700] text-black" : "bg-primary text-primary-foreground"
                    }`}
                  >
                    {slot.rank}
                  </div>
                  <button
                    type="button"
                    onClick={() => setPickerSlotIndex(index)}
                    className="flex items-center gap-3 flex-1 min-w-0 text-left hover:opacity-80"
                  >
                    {slot.clip ? (
                      <>
                        <video
                          src={`/api/ranking/folders/clips/${slot.clip.id}/file`}
                          className="w-10 h-16 object-cover bg-muted shrink-0"
                          muted
                        />
                        <div className="min-w-0 flex-1">
                          <p className="text-[14px] text-foreground truncate">
                            {slot.clip.original_filename}
                          </p>
                          <p className="text-[12px] text-muted-foreground">
                            {formatDuration(slot.clip.duration_seconds)}
                            {slot.clip.use_count > 0 ? ` · used ${slot.clip.use_count}×` : ""}
                          </p>
                        </div>
                      </>
                    ) : (
                      <p className="text-[13px] text-muted-foreground">No clip selected — click to pick one</p>
                    )}
                  </button>
                </div>
              ))}
            </div>

            {pickerSlotIndex !== null && (
              <div className="border border-border p-4 space-y-2">
                <p className="text-[13px] font-medium text-foreground">
                  Swap rank {slots[pickerSlotIndex].rank}&apos;s clip
                </p>
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 max-h-72 overflow-y-auto">
                  {libraryClips.map((clip) => {
                    const alreadyUsed = usedClipIds.has(clip.id) && clip.id !== slots[pickerSlotIndex].clip?.id;
                    return (
                      <button
                        key={clip.id}
                        type="button"
                        disabled={alreadyUsed}
                        onClick={() => swapSlot(pickerSlotIndex, clip)}
                        className={`border p-2 text-left space-y-1 ${
                          alreadyUsed ? "opacity-40 cursor-not-allowed" : "border-border hover:border-primary"
                        }`}
                      >
                        <p className="text-[11px] text-foreground truncate">{clip.original_filename}</p>
                        <p className="text-[10px] text-muted-foreground">
                          {clip.use_count > 0 ? `used ${clip.use_count}×` : "unused"}
                        </p>
                      </button>
                    );
                  })}
                </div>
                <Button variant="ghost" size="sm" onClick={() => setPickerSlotIndex(null)}>
                  Close
                </Button>
              </div>
            )}

            <Button className="w-full" onClick={goToTextStep} disabled={slots.some((s) => !s.clip)}>
              Continue to text
            </Button>
          </div>
        )}

        {step === "text" && (
          <div className="space-y-4">
            <Button variant="ghost" size="sm" onClick={() => setStep("select")} className="-ml-2">
              <ArrowLeft className="w-4 h-4" />
              Back to selection
            </Button>

            {memoryWarningRanks.length > 0 && (
              <div className="flex items-start gap-2 border border-border bg-foreground text-background px-3 py-2 text-[13px]">
                <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" />
                <span>
                  No saved text yet for rank{memoryWarningRanks.length > 1 ? "s" : ""}{" "}
                  {memoryWarningRanks.join(", ")} — add text below.
                </span>
              </div>
            )}

            <div className="space-y-2">
              {slots.map((slot, index) => (
                <div key={slot.rank} className="flex items-start gap-3 border border-border p-3">
                  <div
                    className={`flex items-center justify-center w-9 h-9 text-[15px] font-bold shrink-0 mt-1 ${
                      slot.rank === 1 ? "bg-[#FFD700] text-black" : "bg-primary text-primary-foreground"
                    }`}
                  >
                    {slot.rank}
                  </div>
                  {slot.clip && (
                    <video
                      src={`/api/ranking/folders/clips/${slot.clip.id}/file`}
                      className="w-10 h-16 object-cover bg-muted shrink-0"
                      muted
                    />
                  )}
                  <Textarea
                    value={slot.text}
                    onChange={(e) => handleTextChange(index, e.target.value)}
                    placeholder="Text for this rank…"
                    rows={2}
                    className="flex-1 resize-none"
                  />
                </div>
              ))}
            </div>

            <div className="space-y-2">
              <p className="text-[13px] font-medium text-foreground">Hook (top of video)</p>
              <div className="flex gap-2">
                <div className="flex-1 space-y-1">
                  <label className="text-[11px] text-muted-foreground">White text</label>
                  <Input
                    value={hookWhite}
                    onChange={(e) => setHookWhite(e.target.value)}
                    placeholder="Ranking Best"
                  />
                </div>
                <div className="flex-1 space-y-1">
                  <label className="text-[11px] text-muted-foreground">Red text</label>
                  <Input
                    value={hookRed}
                    onChange={(e) => setHookRed(e.target.value)}
                    placeholder="Pool Fails"
                  />
                </div>
              </div>
              <p className="text-[12px] text-muted-foreground">
                Renders at the top of the video — white text followed by red text.
              </p>
            </div>

            <div className="flex items-center gap-3">
              <label className="text-[13px] text-foreground">Export preset</label>
              <select
                value={exportPreset}
                onChange={(e) => setExportPreset(e.target.value)}
                className="border border-border bg-background text-[13px] px-2 py-1"
              >
                <option value="tiktok">TikTok</option>
                <option value="reels">Instagram Reels</option>
                <option value="youtube_shorts">YouTube Shorts</option>
                <option value="facebook_reels">Facebook Reels</option>
                <option value="threads">Threads</option>
              </select>
            </div>

            <Button
              className="w-full"
              disabled={missingTextRanks.length > 0 || isSubmitting}
              onClick={() => void handleSubmit()}
            >
              {isSubmitting ? (
                <>
                  <Loader2 className="w-4 h-4 animate-spin" />
                  Starting render…
                </>
              ) : missingTextRanks.length > 0 ? (
                `Add text for rank${missingTextRanks.length > 1 ? "s" : ""} ${missingTextRanks.join(", ")}`
              ) : (
                "Render compilation"
              )}
            </Button>
          </div>
        )}
      </div>
    </div>
  );
}
