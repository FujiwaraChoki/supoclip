"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { toast } from "@/lib/toast";
import { ArrowLeft, Download, Upload, Copy, Trash2, Pencil, LayoutTemplate } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState } from "@/components/empty-state";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { formatSupportMessage, parseApiError } from "@/lib/api-error";

interface TemplateSummary {
  id: string;
  name: string;
  schema_version: number;
  section_count: number;
  created_at: string | null;
  updated_at: string | null;
}

export default function TemplatesSettingsPage() {
  const [templates, setTemplates] = useState<TemplateSummary[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [pendingDeleteId, setPendingDeleteId] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const importInputRef = useRef<HTMLInputElement | null>(null);

  const buildSupportError = useCallback(async (response: Response, fallbackMessage: string) => {
    const parsed = await parseApiError(response, fallbackMessage);
    return formatSupportMessage(parsed);
  }, []);

  const fetchTemplates = useCallback(async () => {
    try {
      const response = await fetch("/api/templates", { cache: "no-store" });
      if (!response.ok) {
        throw new Error(await buildSupportError(response, "Failed to load templates"));
      }
      const data = await response.json();
      setTemplates(data.templates || []);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load templates");
    } finally {
      setIsLoading(false);
    }
  }, [buildSupportError]);

  useEffect(() => {
    void fetchTemplates();
  }, [fetchTemplates]);

  const handleRename = async (id: string) => {
    const name = renameValue.trim();
    if (!name) return;
    setBusyId(id);
    try {
      const response = await fetch(`/api/templates/${id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name }),
      });
      if (!response.ok) throw new Error(await buildSupportError(response, "Failed to rename template"));
      toast.success("Template renamed.");
      setRenamingId(null);
      await fetchTemplates();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to rename template");
    } finally {
      setBusyId(null);
    }
  };

  const handleDuplicate = async (template: TemplateSummary) => {
    setBusyId(template.id);
    try {
      const response = await fetch(`/api/templates/${template.id}/duplicate`, { method: "POST" });
      if (!response.ok) throw new Error(await buildSupportError(response, "Failed to duplicate template"));
      toast.success("Template duplicated.");
      await fetchTemplates();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to duplicate template");
    } finally {
      setBusyId(null);
    }
  };

  const handleDelete = async (id: string) => {
    setBusyId(id);
    try {
      const response = await fetch(`/api/templates/${id}`, { method: "DELETE" });
      if (!response.ok) throw new Error(await buildSupportError(response, "Failed to delete template"));
      toast.success("Template deleted.");
      setPendingDeleteId(null);
      await fetchTemplates();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to delete template");
    } finally {
      setBusyId(null);
    }
  };

  const handleExport = async (template: TemplateSummary) => {
    try {
      const response = await fetch(`/api/templates/${template.id}`, { cache: "no-store" });
      if (!response.ok) throw new Error(await buildSupportError(response, "Failed to export template"));
      const data = await response.json();
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `${template.name.replace(/[^a-z0-9-_]+/gi, "_")}.json`;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      URL.revokeObjectURL(url);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to export template");
    }
  };

  const handleImportFile = async (file: File) => {
    try {
      const text = await file.text();
      const parsed = JSON.parse(text);
      const name = String(parsed.name || file.name.replace(/\.json$/i, "") || "Imported template");
      const response = await fetch("/api/templates", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name,
          settings: parsed.settings || {},
          schema_version: parsed.schema_version,
        }),
      });
      if (!response.ok) throw new Error(await buildSupportError(response, "Failed to import template"));
      toast.success(`Imported "${name}".`);
      await fetchTemplates();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to import template — is this a valid template JSON file?");
    }
  };

  return (
    <div className="min-h-screen bg-background">
      <div className="max-w-4xl mx-auto px-4 sm:px-6 py-8 space-y-6">
        <div className="flex items-center gap-3">
          <Link href="/settings">
            <Button variant="ghost" size="icon">
              <ArrowLeft className="w-4 h-4" />
            </Button>
          </Link>
          <div>
            <h1 className="text-2xl font-bold text-foreground">Templates</h1>
            <p className="text-sm text-muted-foreground">
              Reusable settings bundles — save a project&apos;s settings, then apply them to any other project.
            </p>
          </div>
        </div>

        <div className="flex justify-end">
          <input
            ref={importInputRef}
            type="file"
            accept="application/json"
            className="hidden"
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) void handleImportFile(file);
              e.target.value = "";
            }}
          />
          <Button variant="outline" size="sm" onClick={() => importInputRef.current?.click()}>
            <Upload className="w-4 h-4" />
            Import template
          </Button>
        </div>

        {error && (
          <Card className="border-foreground">
            <CardContent className="pt-6 text-sm text-foreground font-bold">{error}</CardContent>
          </Card>
        )}

        {isLoading ? (
          <div className="space-y-3">
            <Skeleton className="h-20 w-full" />
            <Skeleton className="h-20 w-full" />
          </div>
        ) : templates.length === 0 ? (
          <EmptyState
            icon={LayoutTemplate}
            title="No templates yet"
            description={'Open a project\'s settings and use "Save as Template" to create your first one, or import a template JSON file.'}
          />
        ) : (
          <div className="space-y-3">
            {templates.map((template) => (
              <Card key={template.id}>
                <CardHeader className="pb-2">
                  <div className="flex items-center justify-between gap-3">
                    {renamingId === template.id ? (
                      <div className="flex items-center gap-2 flex-1">
                        <Input
                          value={renameValue}
                          onChange={(e) => setRenameValue(e.target.value)}
                          className="h-8"
                          autoFocus
                          onKeyDown={(e) => {
                            if (e.key === "Enter") void handleRename(template.id);
                            if (e.key === "Escape") setRenamingId(null);
                          }}
                        />
                        <Button size="sm" disabled={busyId === template.id} onClick={() => handleRename(template.id)}>
                          Save
                        </Button>
                        <Button size="sm" variant="ghost" onClick={() => setRenamingId(null)}>
                          Cancel
                        </Button>
                      </div>
                    ) : (
                      <CardTitle className="text-base">{template.name}</CardTitle>
                    )}
                  </div>
                </CardHeader>
                <CardContent className="pt-0">
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <p className="text-xs text-muted-foreground">
                      {template.section_count} section{template.section_count === 1 ? "" : "s"} configured
                      {template.updated_at && (
                        <> &middot; last modified {new Date(template.updated_at).toLocaleString()}</>
                      )}
                    </p>
                    <div className="flex items-center gap-2">
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={busyId === template.id}
                        onClick={() => {
                          setRenamingId(template.id);
                          setRenameValue(template.name);
                        }}
                      >
                        <Pencil className="w-3.5 h-3.5" />
                        Rename
                      </Button>
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={busyId === template.id}
                        onClick={() => handleDuplicate(template)}
                      >
                        <Copy className="w-3.5 h-3.5" />
                        Duplicate
                      </Button>
                      <Button size="sm" variant="outline" onClick={() => handleExport(template)}>
                        <Download className="w-3.5 h-3.5" />
                        Export
                      </Button>
                      <Button
                        size="sm"
                        variant="outline"
                        className="text-foreground font-bold"
                        disabled={busyId === template.id}
                        onClick={() => setPendingDeleteId(template.id)}
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                      </Button>
                    </div>
                  </div>
                </CardContent>
              </Card>
            ))}
          </div>
        )}
      </div>

      <AlertDialog open={!!pendingDeleteId} onOpenChange={(open) => !open && setPendingDeleteId(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete this template?</AlertDialogTitle>
            <AlertDialogDescription>
              This permanently deletes the template. Projects that already used it keep their current settings.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={() => pendingDeleteId && handleDelete(pendingDeleteId)}>
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
