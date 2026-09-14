"use client";

import { useEffect, useState } from "react";
import { Copy, Sparkles } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { toast } from "@/lib/toast";
import { formatSupportMessage, parseApiError } from "@/lib/api-error";

interface ClipMetadataPanelProps {
  taskId: string;
  clipId: string;
  title: string | null | undefined;
  description: string | null | undefined;
  tags: string[] | undefined;
  provider: "ollama" | "gemini" | null | undefined;
  generationMs: number | null | undefined;
  stale?: boolean;
  onSaved: () => void;
}

const PROVIDER_LABEL: Record<string, string> = {
  ollama: "Local",
  gemini: "Gemini",
};

const QUALITY_OPTIONS = [
  { value: "fast", label: "Fast (3B)" },
  { value: "balanced", label: "Balanced (3B)" },
  { value: "high", label: "High quality (7B)" },
  { value: "gemini", label: "Gemini" },
];

async function copyToClipboard(text: string, label: string) {
  try {
    await navigator.clipboard.writeText(text);
    toast.success(`${label} copied.`);
  } catch {
    toast.error(`Failed to copy ${label.toLowerCase()}.`);
  }
}

export function ClipMetadataPanel({
  taskId,
  clipId,
  title,
  description,
  tags,
  provider,
  generationMs,
  stale,
  onSaved,
}: ClipMetadataPanelProps) {
  const [titleDraft, setTitleDraft] = useState(title ?? "");
  const [descriptionDraft, setDescriptionDraft] = useState(description ?? "");
  const [tagsDraft, setTagsDraft] = useState((tags ?? []).join(", "));
  const [isSaving, setIsSaving] = useState(false);
  const [isRegenerating, setIsRegenerating] = useState(false);
  const [quality, setQuality] = useState("balanced");

  useEffect(() => {
    setTitleDraft(title ?? "");
    setDescriptionDraft(description ?? "");
    setTagsDraft((tags ?? []).join(", "));
  }, [clipId, title, description, tags]);

  const buildSupportError = async (response: Response, fallbackMessage: string) => {
    const parsed = await parseApiError(response, fallbackMessage);
    return formatSupportMessage(parsed);
  };

  const handleSave = async () => {
    setIsSaving(true);
    try {
      const response = await fetch(`/api/tasks/${taskId}/clips/${clipId}/metadata`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          title: titleDraft,
          description: descriptionDraft,
          tags: tagsDraft.split(",").map((t) => t.trim()).filter(Boolean),
        }),
      });
      if (!response.ok) throw new Error(await buildSupportError(response, "Failed to save metadata"));
      toast.success("Metadata saved.");
      onSaved();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to save metadata");
    } finally {
      setIsSaving(false);
    }
  };

  const handleRegenerate = async () => {
    setIsRegenerating(true);
    try {
      const response = await fetch(
        `/api/tasks/${taskId}/clips/${clipId}/metadata/regenerate?quality=${quality}`,
        { method: "POST" }
      );
      if (!response.ok) throw new Error(await buildSupportError(response, "Failed to regenerate metadata"));
      toast.success("Metadata regenerated.");
      onSaved();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to regenerate metadata");
    } finally {
      setIsRegenerating(false);
    }
  };

  const formattedAll = `Title: ${titleDraft}\nDescription: ${descriptionDraft}\nTags: ${tagsDraft}`;

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <label className="text-xs font-medium text-muted-foreground">Title</label>
          {stale && (
            <Badge variant="outline" className="text-amber-600 border-amber-600">
              Stale
            </Badge>
          )}
        </div>
        {provider && (
          <div className="flex items-center gap-2">
            <Badge variant="secondary">{PROVIDER_LABEL[provider] ?? provider}</Badge>
            {typeof generationMs === "number" && (
              <span className="text-xs text-muted-foreground">{(generationMs / 1000).toFixed(1)}s</span>
            )}
          </div>
        )}
      </div>
      <div className="flex gap-2">
        <Input
          value={titleDraft}
          onChange={(e) => setTitleDraft(e.target.value)}
          placeholder="30-60 characters"
          maxLength={80}
        />
        <Button
          size="icon"
          variant="ghost"
          className="shrink-0"
          onClick={() => copyToClipboard(titleDraft, "Title")}
          disabled={!titleDraft}
          title="Copy title"
        >
          <Copy className="size-3.5" />
        </Button>
      </div>

      <label className="text-xs font-medium text-muted-foreground">Description</label>
      <div className="flex gap-2">
        <Textarea
          value={descriptionDraft}
          onChange={(e) => setDescriptionDraft(e.target.value)}
          placeholder="50-100 characters, no hashtags"
          maxLength={200}
        />
        <Button
          size="icon"
          variant="ghost"
          className="shrink-0"
          onClick={() => copyToClipboard(descriptionDraft, "Description")}
          disabled={!descriptionDraft}
          title="Copy description"
        >
          <Copy className="size-3.5" />
        </Button>
      </div>

      <label className="text-xs font-medium text-muted-foreground">Tags</label>
      <div className="flex gap-2">
        <Input
          value={tagsDraft}
          onChange={(e) => setTagsDraft(e.target.value)}
          placeholder="comma, separated, tags"
        />
        <Button
          size="icon"
          variant="ghost"
          className="shrink-0"
          onClick={() => copyToClipboard(tagsDraft, "Tags")}
          disabled={!tagsDraft}
          title="Copy tags"
        >
          <Copy className="size-3.5" />
        </Button>
      </div>

      <Button
        size="sm"
        variant="outline"
        className="w-full"
        onClick={() => copyToClipboard(formattedAll, "All metadata")}
      >
        <Copy className="mr-1 size-3.5" />
        Copy all
      </Button>

      <div className="flex gap-2">
        <Select value={quality} onValueChange={setQuality}>
          <SelectTrigger className="w-36 shrink-0">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {QUALITY_OPTIONS.map((opt) => (
              <SelectItem key={opt.value} value={opt.value}>
                {opt.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Button size="sm" variant="outline" className="flex-1" onClick={handleRegenerate} disabled={isRegenerating}>
          <Sparkles className="mr-1 size-3.5" />
          {isRegenerating ? "Regenerating..." : "Regenerate"}
        </Button>
        <Button size="sm" className="flex-1" onClick={handleSave} disabled={isSaving}>
          {isSaving ? "Saving..." : "Save"}
        </Button>
      </div>
    </div>
  );
}
