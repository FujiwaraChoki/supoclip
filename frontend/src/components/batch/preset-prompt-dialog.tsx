"use client";

import { useEffect, useState } from "react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { LayoutTemplate } from "lucide-react";

interface TemplateSummary {
  id: string;
  name: string;
}

const LAST_USED_TEMPLATE_KEY = "supoclip:last-used-template-id";

export function getLastUsedTemplateId(): string | null {
  try {
    return localStorage.getItem(LAST_USED_TEMPLATE_KEY);
  } catch {
    return null;
  }
}

function setLastUsedTemplateId(id: string | null) {
  try {
    if (id) localStorage.setItem(LAST_USED_TEMPLATE_KEY, id);
    else localStorage.removeItem(LAST_USED_TEMPLATE_KEY);
  } catch {
    // ignore — this is only a UX convenience default
  }
}

interface PresetPromptDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSelect: (templateId: string | null) => void;
}

/** Shown before "New Clip" / "Batch Add" starts: pick a saved preset, the
 * last-used one (one click), or "use defaults" (no template applied). */
export function PresetPromptDialog({ open, onOpenChange, onSelect }: PresetPromptDialogProps) {
  const [templates, setTemplates] = useState<TemplateSummary[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const lastUsedId = getLastUsedTemplateId();

  useEffect(() => {
    if (!open) return;
    setIsLoading(true);
    fetch("/api/templates", { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : { templates: [] }))
      .then((data) => setTemplates(data.templates || []))
      .catch(() => setTemplates([]))
      .finally(() => setIsLoading(false));
  }, [open]);

  const handleSelect = (templateId: string | null) => {
    setLastUsedTemplateId(templateId);
    onSelect(templateId);
    onOpenChange(false);
  };

  const lastUsed = templates.find((t) => t.id === lastUsedId);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Choose a preset</DialogTitle>
          <DialogDescription>
            Applied to every video in this batch.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-2">
          {lastUsed && (
            <Button
              variant="default"
              className="w-full justify-start"
              onClick={() => handleSelect(lastUsed.id)}
            >
              <LayoutTemplate className="mr-2 size-4" />
              Use last preset: {lastUsed.name}
            </Button>
          )}
          {isLoading ? (
            <p className="text-sm text-muted-foreground">Loading templates...</p>
          ) : (
            templates
              .filter((t) => t.id !== lastUsedId)
              .map((t) => (
                <Button
                  key={t.id}
                  variant="outline"
                  className="w-full justify-start"
                  onClick={() => handleSelect(t.id)}
                >
                  <LayoutTemplate className="mr-2 size-4" />
                  {t.name}
                </Button>
              ))
          )}
          <Button variant="ghost" className="w-full justify-start" onClick={() => handleSelect(null)}>
            Use defaults
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
