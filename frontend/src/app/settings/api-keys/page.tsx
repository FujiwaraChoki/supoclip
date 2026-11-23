"use client";

import { useEffect, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { ArrowUpRight, Copy, Check, Trash2, AlertCircle, Plus, KeyRound } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { authClient, useSession } from "@/lib/auth-client";
import { useCopyText } from "@/lib/copy-text";
import { GITHUB_URL } from "@/lib/site";
import { KeyClientSetup } from "@/components/mcp-setup";
import { AssistantConnect } from "@/components/assistant-connect";
import { apiKeyKeys, useApiKeys, type ApiKey } from "@/lib/queries/settings-api-keys";

const DOCS = [
  { labelKey: "docsMcp", href: `${GITHUB_URL}/blob/main/mcp/README.md` },
  { labelKey: "docsRest", href: `${GITHUB_URL}/blob/main/docs/api-reference.md#authenticating-with-api-keys` },
] as const;

const NO_KEYS: ApiKey[] = [];

function formatDate(value: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString();
}

export default function ApiKeysPage() {
  const t = useTranslations("settings");
  const copyText = useCopyText();
  const newKeyRef = useRef<HTMLElement>(null);
  const { data: session, isPending, refetch } = useSession();
  const queryClient = useQueryClient();
  const keysQuery = useApiKeys(session?.user?.id);
  const keys = keysQuery.data ?? NO_KEYS;
  const isFetching = keysQuery.isPending;
  const [actionError, setError] = useState<string | null>(null);
  const error = actionError ?? (keysQuery.isError ? t("apiKeys.loadFailed") : null);
  const [name, setName] = useState("");
  const [isCreating, setIsCreating] = useState(false);
  const [newKey, setNewKey] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [isSendingVerification, setIsSendingVerification] = useState(false);
  const [verificationSent, setVerificationSent] = useState(false);
  const emailVerified = session?.user?.emailVerified === true;

  useEffect(() => {
    const verificationError = new URLSearchParams(window.location.search).get("error");
    if (verificationError === "TOKEN_EXPIRED" || verificationError === "INVALID_TOKEN") {
      setError(t("apiKeys.invalidLink"));
    }
  }, [t]);

  const handleSendVerification = async () => {
    if (!session?.user.email) return;
    setIsSendingVerification(true);
    setError(null);
    try {
      const result = await authClient.sendVerificationEmail({
        email: session.user.email,
        callbackURL: "/settings/api-keys",
      });
      if (result.error) throw new Error(result.error.message || t("apiKeys.sendFailed"));
      setVerificationSent(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : t("apiKeys.sendFailed"));
    } finally {
      setIsSendingVerification(false);
    }
  };

  const loadKeys = () => queryClient.invalidateQueries({ queryKey: apiKeyKeys.all });

  const handleCreate = async () => {
    if (!emailVerified) return;
    setIsCreating(true);
    setError(null);
    setNewKey(null);
    setCopied(false);
    try {
      const response = await fetch("/api/api-keys", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: name.trim() || t("apiKeys.defaultName") }),
      });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data?.detail || t("apiKeys.createFailed"));
      }
      // Shown once from component state: the plaintext never enters the query cache.
      setNewKey(data.api_key?.key ?? null);
      setName("");
      await loadKeys();
    } catch (err) {
      setError(err instanceof Error ? err.message : t("apiKeys.createFailed"));
    } finally {
      setIsCreating(false);
    }
  };

  const revoke = useMutation({
    mutationFn: async (id: string) => {
      const response = await fetch(`/api/api-keys/${id}`, { method: "DELETE" });
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(data?.detail || t("apiKeys.revokeFailed"));
      }
    },
    onMutate: () => setError(null),
    onSuccess: () => loadKeys(),
    onError: (err) => setError(err instanceof Error ? err.message : t("apiKeys.revokeFailed")),
  });
  const handleRevoke = (id: string) => revoke.mutate(id);

  const handleCopy = async () => {
    if (!newKey) return;
    const outcome = await copyText(newKey, newKeyRef.current);
    if (outcome !== "copied") {
      setCopied(false);
      return;
    }
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const handleCreateOpenChange = (open: boolean) => {
    setCreateOpen(open);
    if (!open) {
      // Shown once: closing the dialog forgets the key.
      setNewKey(null);
      setName("");
      setCopied(false);
    }
  };

  if (isPending) {
    return (
      <div className="min-h-screen bg-background flex items-center justify-center p-4">
        <Skeleton className="h-32 w-full max-w-xl" />
      </div>
    );
  }

  if (!session?.user) {
    return (
      <div className="min-h-screen bg-background">
        <div className="max-w-4xl mx-auto px-4 py-24 text-center">
          <p className="text-muted-foreground mb-4">{t("apiKeys.signIn")}</p>
          <Link href="/sign-in">
            <Button>{t("apiKeys.signInAction")}</Button>
          </Link>
        </div>
      </div>
    );
  }

  return (
    <main className="mx-auto w-full max-w-3xl px-4 py-8 sm:px-8 md:py-10">
      <div className="mb-8">
        <h1 className="font-display text-3xl font-bold tracking-tight">{t("apiKeys.title")}</h1>
        <p className="mt-2 text-muted-foreground">
          {t.rich("apiKeys.intro", {
            brand: (chunks) => <span className="font-medium text-foreground">{chunks}</span>,
          })}
        </p>
        <div className="mt-4 flex flex-wrap gap-2">
          {DOCS.map((doc) => (
            <Button key={doc.href} asChild size="sm" variant="outline">
              <a href={doc.href} target="_blank" rel="noreferrer">
                {t(`apiKeys.${doc.labelKey}`)}
                <ArrowUpRight className="size-3.5 opacity-60" aria-hidden />
              </a>
            </Button>
          ))}
        </div>
      </div>

      {error && (
        <Alert variant="destructive" className="mb-6">
          <AlertCircle className="h-4 w-4" />
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      {!emailVerified && (
        <Alert className="mb-6">
          <AlertDescription>
            <p className="font-medium">{t("apiKeys.verifyTitle")}</p>
            <p className="mt-1">{t("apiKeys.verifyBody", { email: session.user.email })}</p>
            {verificationSent && (
              <p role="status" className="mt-2">{t("apiKeys.verifySent")}</p>
            )}
            <div className="mt-3 flex flex-wrap gap-2">
              <Button variant="outline" onClick={handleSendVerification} disabled={isSendingVerification}>
                {isSendingVerification ? t("apiKeys.sending") : verificationSent ? t("apiKeys.resendVerification") : t("apiKeys.sendVerification")}
              </Button>
              <Button variant="ghost" onClick={() => void refetch()}>
                {t("apiKeys.verified")}
              </Button>
            </div>
          </AlertDescription>
        </Alert>
      )}

      <AssistantConnect />

      <section className="overflow-hidden rounded-xl border bg-card" aria-labelledby="api-keys-title">
        <div className="flex flex-wrap items-start justify-between gap-3 p-5">
          <div className="min-w-0">
            <h2 id="api-keys-title" className="text-sm font-semibold">{t("apiKeys.keysTitle")}</h2>
            <p className="mt-1 text-xs leading-relaxed text-muted-foreground">{t("apiKeys.keysIntro")}</p>
          </div>
          <Button size="sm" onClick={() => setCreateOpen(true)} disabled={!emailVerified}>
            <Plus className="size-4" />
            {t("apiKeys.createKey")}
          </Button>
        </div>

        <div className="border-t">
          {isFetching ? (
            <div className="p-5"><Skeleton className="h-12 w-full" /></div>
          ) : keys.length === 0 ? (
            <p className="px-5 py-8 text-center text-sm text-muted-foreground">{t("apiKeys.empty")}</p>
          ) : (
            <ul className="divide-y">
              {keys.map((key) => (
                <li key={key.id} className="flex items-center gap-3 px-5 py-3.5">
                  <span className="flex size-8 shrink-0 items-center justify-center rounded-md border bg-background text-muted-foreground">
                    <KeyRound className="size-3.5" aria-hidden />
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="truncate text-sm font-medium">{key.name}</span>
                      <code className="hidden shrink-0 rounded bg-muted px-1.5 py-0.5 font-mono text-[11px] text-muted-foreground sm:inline">{key.key_prefix}…</code>
                      {key.revoked && <Badge variant="secondary">{t("apiKeys.revoked")}</Badge>}
                    </div>
                    <p className="mt-0.5 text-xs text-muted-foreground">
                      {t("apiKeys.meta", { created: formatDate(key.created_at), used: formatDate(key.last_used_at) })}
                    </p>
                  </div>
                  {!key.revoked && (
                    <Button
                      size="sm"
                      variant="ghost"
                      className="shrink-0 text-muted-foreground hover:bg-red-50 hover:text-red-600 dark:hover:bg-red-950/50 dark:hover:text-red-400"
                      onClick={() => handleRevoke(key.id)}
                    >
                      <Trash2 className="size-3.5" />
                      <span className="sr-only sm:not-sr-only">{t("apiKeys.revoke")}</span>
                    </Button>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      </section>

      <Dialog open={createOpen} onOpenChange={handleCreateOpenChange}>
        <DialogContent className="sm:max-w-lg">
          {newKey ? (
            <>
              <DialogHeader>
                <DialogTitle>{t("apiKeys.createdTitle")}</DialogTitle>
                <DialogDescription>{t("apiKeys.reveal")}</DialogDescription>
              </DialogHeader>
              <div className="flex min-w-0 items-center gap-2">
                <code ref={newKeyRef} tabIndex={-1} className="min-w-0 flex-1 select-text break-all rounded-md border bg-muted px-3 py-2 font-mono text-sm">
                  {newKey}
                </code>
                <Button size="icon" variant="outline" onClick={handleCopy} aria-label={t("apiKeys.copy")}>
                  {copied ? <Check className="size-4" /> : <Copy className="size-4" />}
                </Button>
              </div>
              <div className="min-w-0 space-y-2 border-t pt-4">
                <p className="text-sm font-medium">{t("apiKeys.useIn")}</p>
                <KeyClientSetup apiKey={newKey} />
              </div>
              <DialogFooter>
                <Button onClick={() => handleCreateOpenChange(false)}>{t("apiKeys.done")}</Button>
              </DialogFooter>
            </>
          ) : (
            <form
              className="grid gap-4"
              onSubmit={(event) => {
                event.preventDefault();
                void handleCreate();
              }}
            >
              <DialogHeader>
                <DialogTitle>{t("apiKeys.createTitle")}</DialogTitle>
                <DialogDescription>{t("apiKeys.createDescription")}</DialogDescription>
              </DialogHeader>
              <div className="grid gap-2">
                <Label htmlFor="key-name">{t("apiKeys.nameLabel")}</Label>
                <Input
                  id="key-name"
                  autoFocus
                  placeholder={t("apiKeys.namePlaceholder")}
                  value={name}
                  maxLength={120}
                  onChange={(e) => setName(e.target.value)}
                  disabled={isCreating}
                />
              </div>
              {error && <p className="text-sm text-destructive">{error}</p>}
              <DialogFooter>
                <Button type="button" variant="ghost" onClick={() => handleCreateOpenChange(false)}>{t("apiKeys.cancel")}</Button>
                <Button type="submit" disabled={isCreating || !emailVerified}>
                  {isCreating ? t("apiKeys.creating") : t("apiKeys.create")}
                </Button>
              </DialogFooter>
            </form>
          )}
        </DialogContent>
      </Dialog>
    </main>
  );
}
