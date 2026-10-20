"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { ArrowLeft, ExternalLink, Loader2 } from "lucide-react";
import { useSession } from "@/lib/auth-client";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Dialog, DialogContent, DialogTitle, DialogDescription } from "@/components/ui/dialog";

const USAGE_URL = "https://chatgpt.com/settings/usage";
type Account = { id: string; email: string; model: string | null; enabled: boolean; connected: boolean; plan_usage: boolean; welcome_seen: boolean };
type Model = { id: string; name: string };

async function api(path: string, method = "GET", body?: object) {
  const response = await fetch(`/api/chatgpt/${path}`, {
    method, cache: "no-store",
    ...(body ? { headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {}),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Unable to update your ChatGPT connection.");
  return data;
}

function AccountCard({ account, reload, onError }: { account: Account; reload: () => Promise<void>; onError: (error: string) => void }) {
  const [models, setModels] = useState<Model[]>([]);
  const [model, setModel] = useState(account.model ?? "");
  const [busy, setBusy] = useState(false);
  const [loadingModels, setLoadingModels] = useState(false);
  const [modelsError, setModelsError] = useState<string | null>(null);
  const loadModels = useCallback(async () => {
    setLoadingModels(true);
    setModelsError(null);
    try {
      const data = await api(`accounts/${account.id}/models`);
      setModels(data.models);
    } catch (error) {
      setModelsError(error instanceof Error ? error.message : "Unable to load models.");
    } finally {
      setLoadingModels(false);
    }
  }, [account.id]);
  useEffect(() => {
    if (account.connected && account.plan_usage) void loadModels();
  }, [account.connected, account.plan_usage, loadModels]);

  async function action(operation: () => Promise<void>) {
    setBusy(true);
    try { await operation(); } catch (error) { onError(error instanceof Error ? error.message : "Please try again."); }
    finally { setBusy(false); }
  }

  return (
    <section className="space-y-4 rounded-2xl border bg-background p-5" aria-label={`ChatGPT account ${account.email}`}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div><h2 className="font-semibold break-all">{account.email}</h2>
          <p className="text-sm text-muted-foreground">{account.enabled ? "Using ChatGPT plan for your clip analysis" : account.connected ? "Connected" : "Signed out"}</p>
          <p className="mt-1 text-xs text-muted-foreground">Connection {account.id.slice(0, 8)}</p>
        </div>
        <a className="text-sm underline underline-offset-4" href={USAGE_URL} target="_blank" rel="noopener noreferrer">Manage usage</a>
      </div>
      {account.connected && !account.plan_usage && <p className="text-sm">ChatGPT plan usage is not enabled. Reconnect and allow plan usage, or keep your configured AI provider.</p>}
      {account.connected && account.plan_usage && <div className="space-y-2">
        <Label htmlFor={`model-${account.id}`}>ChatGPT model</Label>
        <select id={`model-${account.id}`} className="w-full rounded-md border bg-background px-3 py-2 text-sm" value={model} onChange={(event) => setModel(event.target.value)} disabled={busy || loadingModels}>
          <option value="">{loadingModels ? "Loading available models…" : "Choose a model"}</option>
          {models.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
        </select>
        {modelsError && <p role="alert" className="text-sm text-destructive">{modelsError} <button className="underline" onClick={() => void loadModels()}>Retry</button></p>}
        {!loadingModels && !modelsError && models.length === 0 && <p className="text-sm text-muted-foreground">No models are available to this account. Check your ChatGPT plan.</p>}
        <Button disabled={busy || !models.some((item) => item.id === model)} onClick={() => void action(async () => {
          await api(`accounts/${account.id}/activate`, "POST", { model }); await reload();
        })}>{busy && <Loader2 className="mr-2 size-4 animate-spin" />}Use ChatGPT for clip analysis</Button>
      </div>}
      <div className="flex flex-wrap gap-3">
        <Button variant="outline" disabled={busy} onClick={() => void action(async () => {
          const data = await api("authorize", "POST", { account_id: account.id }); window.location.assign(data.authorization_url);
        })}>Continue with ChatGPT</Button>
        {account.connected && <Button variant="ghost" disabled={busy} onClick={() => void action(async () => {
          const data = await api(`accounts/${account.id}`, "DELETE"); await reload();
          if (!data.revocation_confirmed) onError("Signed out locally. Remote revocation was not confirmed; disconnect SupoClip in ChatGPT settings.");
        })}>Sign out of ChatGPT</Button>}
      </div>
    </section>
  );
}

export default function ChatGPTSettingsPage() {
  const { data: session, isPending } = useSession();
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [local, setLocal] = useState(false);
  const [outcome, setOutcome] = useState<string | null>(null);
  const reload = useCallback(async () => {
    const data = await api("status"); setAccounts(data.accounts);
  }, []);
  useEffect(() => {
    setLocal(["localhost", "127.0.0.1"].includes(window.location.hostname));
    const url = new URL(window.location.href);
    const result = url.searchParams.get("chatgpt");
    if (result) {
      setOutcome(result); url.searchParams.delete("chatgpt"); window.history.replaceState(null, "", url.pathname + url.search);
    }
  }, []);
  useEffect(() => {
    if (!session?.user?.id) return;
    void reload().catch((error) => setError(error instanceof Error ? error.message : "Unable to load connections.")).finally(() => setLoading(false));
  }, [session?.user?.id, reload]);
  const welcomeAccount = accounts.find((account) => account.enabled && !account.welcome_seen);

  return <main className="mx-auto max-w-2xl space-y-6 px-4 py-8 sm:px-6">
    <Link href="/settings" className="inline-flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground"><ArrowLeft className="size-4" />Settings</Link>
    <div><h1 className="text-2xl font-semibold tracking-tight">Use your ChatGPT plan</h1>
      <p className="mt-2 text-sm text-muted-foreground">Connect ChatGPT to select and rank highlights using your eligible plan or credits. No OpenAI API key is needed.</p></div>
    <p className="text-sm text-muted-foreground">Available for local self-hosting. Your browser and SupoClip must run on the same computer. Transcription still uses your configured transcription provider.</p>
    <a className="inline-flex items-center gap-1 text-sm underline underline-offset-4" href={USAGE_URL} target="_blank" rel="noopener noreferrer">Manage ChatGPT usage<ExternalLink className="size-3" /></a>
    {outcome === "connected" && <Alert><AlertDescription>ChatGPT connected. Manage its model and clip analysis settings below.</AlertDescription></Alert>}
    {(outcome === "failed" || outcome === "expired") && <Alert variant="destructive"><AlertDescription>{outcome === "expired" ? "This sign-in attempt expired. Start again from this page." : "ChatGPT sign-in did not complete. Try again and allow plan usage if your account is eligible."}</AlertDescription></Alert>}
    {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
    {isPending || (session && loading) ? <p role="status">Loading connections…</p> : !session ? <Link className="underline" href="/sign-in">Sign in to SupoClip to connect ChatGPT</Link> : <>
      {!local && <Alert><AlertDescription>Open your local SupoClip installation to connect ChatGPT.</AlertDescription></Alert>}
      {accounts.map((account) => <AccountCard key={account.id} account={account} reload={reload} onError={setError} />)}
      <Button disabled={busy || !local || process.env.NEXT_PUBLIC_SELF_HOST === "false"} onClick={async () => {
        setBusy(true); setError(null);
        try { const data = await api("authorize", "POST", {}); window.location.assign(data.authorization_url); }
        catch (error) { setError(error instanceof Error ? error.message : "Unable to connect ChatGPT."); setBusy(false); }
      }}>{busy && <Loader2 className="mr-2 size-4 animate-spin" />}{accounts.length ? "Add another ChatGPT account" : "Continue with ChatGPT"}</Button>
      <p className="text-xs text-muted-foreground">Connecting grants no access to your ChatGPT conversations. Eligible analysis uses your shared ChatGPT allowance; SupoClip subscriptions are separate. Signing out restores the configured AI provider for future clips.</p>
    </>}
    <Dialog open={Boolean(welcomeAccount)} onOpenChange={(open) => {
      if (!open && welcomeAccount) void api(`accounts/${welcomeAccount.id}/acknowledge`, "POST", {}).then(reload).catch(() => setError("Unable to save your preference. Please try again."));
    }}><DialogContent><DialogTitle>You’re using your ChatGPT plan</DialogTitle>
      <DialogDescription>Your clip analysis uses your ChatGPT plan or available credits. Manage usage and access in ChatGPT settings.</DialogDescription>
      <a href={USAGE_URL} target="_blank" rel="noopener noreferrer" className="text-sm underline">Manage usage</a>
      <Button onClick={async () => {
        if (!welcomeAccount) return;
        try { await api(`accounts/${welcomeAccount.id}/acknowledge`, "POST", {}); await reload(); }
        catch { setError("Unable to save your preference. Please try again."); }
      }}>Got it</Button>
    </DialogContent></Dialog>
  </main>;
}
