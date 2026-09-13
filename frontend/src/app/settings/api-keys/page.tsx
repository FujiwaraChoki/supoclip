"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { ArrowLeft, KeyRound, Copy, Check, Trash2, AlertCircle, Plus } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState } from "@/components/empty-state";
import { useDelayedFlag } from "@/hooks/use-delayed-flag";
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
import { LOCAL_USER_ID } from "@/lib/local-user";
import { toast } from "@/lib/toast";

interface ApiKey {
  id: string;
  name: string;
  key_prefix: string;
  created_at: string | null;
  last_used_at: string | null;
  revoked: boolean;
}

function formatDate(value: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString();
}

export default function ApiKeysPage() {
  // Local-first: no login, so there's no real session — kept as a constant so
  // the existing "session?.user?.id" checks keep working.
  const session = { user: { id: LOCAL_USER_ID } };
  const [keys, setKeys] = useState<ApiKey[]>([]);
  const [isFetching, setIsFetching] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [isCreating, setIsCreating] = useState(false);
  const [newKey, setNewKey] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [revokeTarget, setRevokeTarget] = useState<ApiKey | null>(null);
  const showFetching = useDelayedFlag(isFetching);

  const loadKeys = useCallback(async () => {
    setIsFetching(true);
    setError(null);
    try {
      const response = await fetch("/api/api-keys", { cache: "no-store" });
      if (!response.ok) throw new Error("Failed to load API keys");
      const data = await response.json();
      setKeys(data.api_keys || []);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load API keys");
    } finally {
      setIsFetching(false);
    }
  }, []);

  useEffect(() => {
    if (session?.user?.id) {
      loadKeys();
    }
  }, [session?.user?.id, loadKeys]);

  const handleCreate = async () => {
    setIsCreating(true);
    setError(null);
    setNewKey(null);
    setCopied(false);
    try {
      const response = await fetch("/api/api-keys", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: name.trim() || "API Key" }),
      });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data?.detail || "Failed to create API key");
      }
      setNewKey(data.api_key?.key ?? null);
      setName("");
      await loadKeys();
      toast.success("API key created.");
    } catch (err) {
      const message = err instanceof Error ? err.message : "Failed to create API key";
      setError(message);
      toast.error(message);
    } finally {
      setIsCreating(false);
    }
  };

  const handleRevoke = async () => {
    if (!revokeTarget) return;
    setError(null);
    try {
      const response = await fetch(`/api/api-keys/${revokeTarget.id}`, { method: "DELETE" });
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(data?.detail || "Failed to revoke API key");
      }
      await loadKeys();
      toast.success(`"${revokeTarget.name}" revoked.`);
    } catch (err) {
      const message = err instanceof Error ? err.message : "Failed to revoke API key";
      setError(message);
      toast.error(message);
    } finally {
      setRevokeTarget(null);
    }
  };

  const handleCopy = async () => {
    if (!newKey) return;
    await navigator.clipboard.writeText(newKey);
    setCopied(true);
    toast.success("API key copied to clipboard.");
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="min-h-screen bg-background">
      {/* Header */}
      <div className="border-b bg-background">
        <div className="max-w-7xl mx-auto px-4 py-4">
          <Link href="/settings">
            <Button variant="ghost" size="sm">
              <ArrowLeft className="w-4 h-4" />
              Settings
            </Button>
          </Link>
        </div>
      </div>

      {/* Main Content */}
      <div className="max-w-4xl mx-auto px-4 py-10">
        <div className="max-w-xl mx-auto">
          <div className="mb-8">
            <div className="flex items-center gap-2 mb-2">
              <KeyRound className="w-6 h-6 text-foreground" />
              <h1 className="text-2xl font-bold text-foreground">API Keys</h1>
            </div>
            <p className="text-muted-foreground">
              Create keys for programmatic access — for example the{" "}
              <span className="font-medium">SupoClip MCP server</span>. Treat keys
              like passwords; they grant full access to your account.
            </p>
          </div>

          {error && (
            <Alert variant="destructive" className="mb-6">
              <AlertCircle className="h-4 w-4" />
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          )}

          {/* One-time key reveal */}
          {newKey && (
            <Alert className="mb-6 border-primary">
              <AlertDescription>
                <p className="font-medium text-primary mb-2">
                  Copy your new key now — it won&apos;t be shown again.
                </p>
                <div className="flex items-center gap-2">
                  <code className="flex-1 px-3 py-2 bg-background border border-border rounded text-sm break-all">
                    {newKey}
                  </code>
                  <Button size="sm" variant="outline" onClick={handleCopy}>
                    {copied ? <Check className="w-4 h-4" /> : <Copy className="w-4 h-4" />}
                  </Button>
                </div>
              </AlertDescription>
            </Alert>
          )}

          {/* Create form */}
          <div className="mb-10 p-4 border border-border rounded-lg">
            <Label htmlFor="key-name" className="mb-2 block">
              Create a new key
            </Label>
            <div className="flex gap-2">
              <Input
                id="key-name"
                placeholder="e.g. My laptop MCP"
                value={name}
                maxLength={120}
                onChange={(e) => setName(e.target.value)}
                disabled={isCreating}
              />
              <Button onClick={handleCreate} disabled={isCreating}>
                <Plus className="w-4 h-4" />
                {isCreating ? "Creating…" : "Create"}
              </Button>
            </div>
          </div>

          {/* Key list */}
          <div>
            <h2 className="text-sm font-medium text-muted-foreground uppercase tracking-wide mb-3">
              Your keys
            </h2>
            {showFetching ? (
              <Skeleton className="h-20 w-full" />
            ) : keys.length === 0 ? (
              <EmptyState
                icon={KeyRound}
                title="No API keys yet"
                description="Create one above to access SupoClip programmatically."
              />
            ) : (
              <div className="space-y-3">
                {keys.map((key) => (
                  <div
                    key={key.id}
                    className="flex items-center justify-between p-4 border border-border rounded-lg"
                  >
                    <div className="min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="font-medium text-foreground truncate">{key.name}</span>
                        {key.revoked && <Badge variant="secondary">Revoked</Badge>}
                      </div>
                      <p className="text-xs text-muted-foreground font-mono mt-1">
                        {key.key_prefix}…
                      </p>
                      <p className="text-xs text-muted-foreground mt-1">
                        Created {formatDate(key.created_at)} · Last used{" "}
                        {formatDate(key.last_used_at)}
                      </p>
                    </div>
                    {!key.revoked && (
                      <Button
                        size="sm"
                        variant="ghost"
                        className="text-foreground font-bold hover:bg-background"
                        onClick={() => setRevokeTarget(key)}
                      >
                        <Trash2 className="w-4 h-4" />
                        Revoke
                      </Button>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>

      <AlertDialog open={!!revokeTarget} onOpenChange={(open) => !open && setRevokeTarget(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Revoke this API key?</AlertDialogTitle>
            <AlertDialogDescription>
              {revokeTarget && `"${revokeTarget.name}" `}
              will stop working immediately for any client using it. This cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={() => void handleRevoke()} className="bg-destructive text-background hover:bg-destructive/90">
              Revoke
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
