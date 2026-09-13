"use client";

import { useState } from "react";
import { Sparkles, Wand2, Check } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { HookTitlePreview } from "@/components/hook-title-preview";
import type { HookStyle, HookTitleVariant, HookType } from "@/lib/hook-style";
import { HOOK_TYPE_LABELS } from "@/lib/hook-style";

type TemplateSummary = { id: string; name: string; font_family?: string; font_size?: number; font_color?: string };

const HOOK_TYPE_OPTIONS = Object.keys(HOOK_TYPE_LABELS) as HookType[];

export function HookVariantCompare({
  taskApiUrl,
  taskId,
  clipId,
  currentHookTitle,
  currentHookType,
  initialVariants,
  hookStyle,
  captionTemplate,
  availableTemplates,
  onApplied,
}: {
  taskApiUrl: string;
  taskId: string;
  clipId: string;
  currentHookTitle: string | null;
  currentHookType: string | null;
  initialVariants: HookTitleVariant[];
  hookStyle: HookStyle;
  captionTemplate: string;
  availableTemplates: TemplateSummary[];
  onApplied?: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [variants, setVariants] = useState<HookTitleVariant[]>(initialVariants);
  const [generating, setGenerating] = useState(false);
  const [applyingId, setApplyingId] = useState<string | null>(null);
  const [customText, setCustomText] = useState("");
  const [hookType, setHookType] = useState<string>(currentHookType || "none");
  const [error, setError] = useState<string | null>(null);

  const handleGenerate = async () => {
    setGenerating(true);
    setError(null);
    try {
      const response = await fetch(`${taskApiUrl}/${taskId}/clips/${clipId}/hook-variants`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ count: 3 }),
      });
      if (!response.ok) {
        throw new Error("Failed to generate hook variants");
      }
      const data = await response.json();
      setVariants(data.variants || []);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to generate hook variants");
    } finally {
      setGenerating(false);
    }
  };

  const applyVariant = async (payload: { variant_id?: string; hook_title?: string }) => {
    setApplyingId(payload.variant_id || "custom");
    setError(null);
    try {
      const response = await fetch(`${taskApiUrl}/${taskId}/clips/${clipId}/hook-variants/select`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...payload, hook_type: hookType }),
      });
      if (!response.ok) {
        throw new Error("Failed to apply hook title");
      }
      setOpen(false);
      onApplied?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to apply hook title");
    } finally {
      setApplyingId(null);
    }
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (next) setError(null);
      }}
    >
      <DialogTrigger asChild>
        <Button size="sm" variant="outline">
          <Sparkles className="w-4 h-4" />
          Compare Hooks
        </Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-2xl max-h-[85vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Compare hook titles</DialogTitle>
        </DialogHeader>

        <div className="space-y-4">
          <div className="rounded-lg border p-3 bg-gray-50">
            <div className="text-xs font-medium text-gray-500 mb-2">Current hook</div>
            <div className="text-sm font-medium text-gray-900">{currentHookTitle || "(no hook title set)"}</div>
          </div>

          <div className="flex items-center justify-between">
            <div className="text-sm font-medium text-gray-900">Generated variants</div>
            <Button size="sm" variant="outline" onClick={handleGenerate} disabled={generating}>
              <Wand2 className="w-4 h-4" />
              {generating ? "Generating..." : variants.length ? "Generate more" : "Generate variants"}
            </Button>
          </div>

          {variants.length === 0 && !generating && (
            <p className="text-xs text-gray-500">
              No variants yet. Generate a few AI-written alternatives, then compare them side-by-side before picking a winner.
            </p>
          )}

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            {variants.map((variant) => (
              <div
                key={variant.id}
                className={`rounded-lg border p-2.5 space-y-2 ${
                  variant.text === currentHookTitle ? "border-gray-900 bg-gray-50" : "border-gray-200"
                }`}
              >
                <HookTitlePreview
                  style={{ ...hookStyle }}
                  captionTemplate={captionTemplate}
                  availableTemplates={availableTemplates}
                  overrideText={variant.text}
                  compact
                />
                <div className="text-sm font-medium text-gray-900">{variant.text}</div>
                <Button
                  size="sm"
                  className="w-full"
                  variant={variant.text === currentHookTitle ? "secondary" : "default"}
                  disabled={applyingId === variant.id || variant.text === currentHookTitle}
                  onClick={() => applyVariant({ variant_id: variant.id })}
                >
                  {variant.text === currentHookTitle ? (
                    <>
                      <Check className="w-4 h-4" />
                      Active
                    </>
                  ) : applyingId === variant.id ? (
                    "Applying..."
                  ) : (
                    "Use this hook"
                  )}
                </Button>
              </div>
            ))}
          </div>

          <div className="rounded-lg border p-3 space-y-2">
            <div className="text-sm font-medium text-gray-900">Write your own</div>
            <Input
              value={customText}
              onChange={(e) => setCustomText(e.target.value)}
              placeholder="Type a custom hook title"
            />
            <Button
              size="sm"
              variant="outline"
              className="w-full"
              disabled={!customText.trim() || applyingId === "custom"}
              onClick={() => applyVariant({ hook_title: customText.trim() })}
            >
              {applyingId === "custom" ? "Applying..." : "Use custom hook"}
            </Button>
          </div>

          <div className="space-y-1.5">
            <label className="text-xs font-medium text-gray-500">Hook type</label>
            <Select value={hookType} onValueChange={setHookType}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {HOOK_TYPE_OPTIONS.map((type) => (
                  <SelectItem key={type} value={type}>
                    {HOOK_TYPE_LABELS[type]}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <p className="text-xs text-gray-500">Applied together with whichever hook you pick above.</p>
          </div>

          {error && <p className="text-xs text-red-600">{error}</p>}
        </div>
      </DialogContent>
    </Dialog>
  );
}
