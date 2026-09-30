"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { ArrowUpRight, Bot, Braces, Copy, Check, Trash2, AlertCircle, Plus } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { useSession } from "@/lib/auth-client";
import { GITHUB_URL } from "@/lib/site";

const DOCS = [
  {
    title: "Connect an agent",
    description: "Set up the MCP server in Claude, Cursor and other MCP clients.",
    href: `${GITHUB_URL}/blob/main/mcp/README.md`,
    icon: Bot,
  },
  {
    title: "REST API reference",
    description: "Endpoints, authentication and examples for direct API access.",
    href: `${GITHUB_URL}/blob/main/docs/api-reference.md#authenticating-with-api-keys`,
    icon: Braces,
  },
];

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
  const { data: session, isPending } = useSession();
  const [keys, setKeys] = useState<ApiKey[]>([]);
  const [isFetching, setIsFetching] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [isCreating, setIsCreating] = useState(false);
  const [newKey, setNewKey] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

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
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create API key");
    } finally {
      setIsCreating(false);
    }
  };

  const handleRevoke = async (id: string) => {
    setError(null);
    try {
      const response = await fetch(`/api/api-keys/${id}`, { method: "DELETE" });
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(data?.detail || "Failed to revoke API key");
      }
      await loadKeys();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to revoke API key");
    }
  };

  const handleCopy = async () => {
    if (!newKey) return;
    await navigator.clipboard.writeText(newKey);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  if (isPending) {
    return (
      <div className="min-h-screen bg-white flex items-center justify-center p-4">
        <Skeleton className="h-32 w-full max-w-xl" />
      </div>
    );
  }

  if (!session?.user) {
    return (
      <div className="min-h-screen bg-white">
        <div className="max-w-4xl mx-auto px-4 py-24 text-center">
          <p className="text-gray-600 mb-4">Sign in to manage your API keys.</p>
          <Link href="/sign-in">
            <Button>Sign In</Button>
          </Link>
        </div>
      </div>
    );
  }

  return (
    <main className="mx-auto w-full max-w-3xl px-4 py-8 sm:px-8 md:py-10">
        <div>
          <div className="mb-8">
            <h1 className="font-display text-3xl font-bold tracking-tight">Agents &amp; API</h1>
            <p className="mt-2 text-muted-foreground">
              Let Claude, Cursor and other AI agents clip videos for you through the{" "}
              <span className="font-medium text-foreground">SupoClip MCP server</span>, or call the REST API
              from your own code. Treat keys like passwords; they grant full access to your account.
            </p>
          </div>

          <div className="mb-8 grid gap-3 sm:grid-cols-2">
            {DOCS.map((doc) => (
              <a
                key={doc.href}
                href={doc.href}
                target="_blank"
                rel="noreferrer"
                className="group flex items-start gap-3 rounded-xl border bg-background p-4 transition-colors hover:border-foreground/25"
              >
                <span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-muted"><doc.icon className="size-4" /></span>
                <span className="min-w-0 flex-1">
                  <span className="flex items-center gap-1 text-sm font-semibold">
                    {doc.title}
                    <ArrowUpRight className="size-3.5 text-muted-foreground transition-transform group-hover:-translate-y-0.5 group-hover:translate-x-0.5" />
                  </span>
                  <span className="mt-0.5 block text-xs text-muted-foreground">{doc.description}</span>
                </span>
              </a>
            ))}
          </div>

          {error && (
            <Alert variant="destructive" className="mb-6">
              <AlertCircle className="h-4 w-4" />
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          )}

          {/* One-time key reveal */}
          {newKey && (
            <Alert className="mb-6 border-green-200 bg-green-50">
              <AlertDescription>
                <p className="font-medium text-green-900 mb-2">
                  Copy your new key now — it won&apos;t be shown again.
                </p>
                <div className="flex items-center gap-2">
                  <code className="flex-1 px-3 py-2 bg-white border rounded text-sm break-all">
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
          <div className="mb-10 p-4 border rounded-lg">
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
            <h2 className="text-sm font-medium text-gray-500 uppercase tracking-wide mb-3">
              Your keys
            </h2>
            {isFetching ? (
              <Skeleton className="h-20 w-full" />
            ) : keys.length === 0 ? (
              <p className="text-gray-500 text-sm">No API keys yet.</p>
            ) : (
              <div className="space-y-3">
                {keys.map((key) => (
                  <div
                    key={key.id}
                    className="flex items-center justify-between p-4 border rounded-lg"
                  >
                    <div className="min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="font-medium text-black truncate">{key.name}</span>
                        {key.revoked && <Badge variant="secondary">Revoked</Badge>}
                      </div>
                      <p className="text-xs text-gray-500 font-mono mt-1">
                        {key.key_prefix}…
                      </p>
                      <p className="text-xs text-gray-400 mt-1">
                        Created {formatDate(key.created_at)} · Last used{" "}
                        {formatDate(key.last_used_at)}
                      </p>
                    </div>
                    {!key.revoked && (
                      <Button
                        size="sm"
                        variant="ghost"
                        className="text-red-600 hover:text-red-700 hover:bg-red-50"
                        onClick={() => handleRevoke(key.id)}
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
    </main>
  );
}
