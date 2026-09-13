"use client";

import { useEffect, useState } from "react";
import { Sparkles } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
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
  onSaved: () => void;
}

const PROVIDER_LABEL: Record<string, string> = {
  ollama: "Local",
  gemini: "Gemini",
};

export function ClipMetadataPanel({
  taskId,
  clipId,
  title,
  description,
  tags,
  provider,
  generationMs,
  onSaved,
}: ClipMetadataPanelProps) {
  const [titleDraft, setTitleDraft] = useState(title ?? "");
  const [descriptionDraft, setDescriptionDraft] = useState(description ?? "");
  const [tagsDraft, setTagsDraft] = useState((tags ?? []).join(", "));
  const [isSaving, setIsSaving] = useState(false);
  const [isRegenerating, setIsRegenerating] = useState(false);

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
        `/api/tasks/${taskId}/clips/${clipId}/metadata/regenerate`,
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

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <label className="text-xs font-medium text-muted-foreground">Title</label>
        {provider && (
          <div className="flex items-center gap-2">
            <Badge variant="secondary">{PROVIDER_LABEL[provider] ?? provider}</Badge>
            {typeof generationMs === "number" && (
              <span className="text-xs text-muted-foreground">{(generationMs / 1000).toFixed(1)}s</span>
            )}
          </div>
        )}
      </div>
      <Input
        value={titleDraft}
        onChange={(e) => setTitleDraft(e.target.value)}
        placeholder="30-60 characters"
        maxLength={80}
      />

      <label className="text-xs font-medium text-muted-foreground">Description</label>
      <Textarea
        value={descriptionDraft}
        onChange={(e) => setDescriptionDraft(e.target.value)}
        placeholder="50-100 characters, no hashtags"
        maxLength={200}
      />

      <label className="text-xs font-medium text-muted-foreground">Tags</label>
      <Input
        value={tagsDraft}
        onChange={(e) => setTagsDraft(e.target.value)}
        placeholder="comma, separated, tags"
      />

      <div className="flex gap-2">
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
