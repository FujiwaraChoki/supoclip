"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
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
  const t = useTranslations("settings.chatgpt");
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
      setModelsError(error instanceof Error ? error.message : t("loadModelsError"));
    } finally {
      setLoadingModels(false);
    }
  }, [account.id, t]);
  useEffect(() => {
    if (account.connected && account.plan_usage) void loadModels();
  }, [account.connected, account.plan_usage, loadModels]);

  async function action(operation: () => Promise<void>) {
    setBusy(true);
    try { await operation(); } catch (error) { onError(error instanceof Error ? error.message : t("tryAgain")); }
    finally { setBusy(false); }
  }

  return (
    <section className="space-y-4 rounded-2xl border bg-background p-5" aria-label={t("accountLabel", { email: account.email })}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div><h2 className="font-semibold break-all">{account.email}</h2>
          <p className="text-sm text-muted-foreground">{account.enabled ? t("usingPlan") : account.connected ? t("connected") : t("signedOut")}</p>
          <p className="mt-1 text-xs text-muted-foreground">{t("connection", { id: account.id.slice(0, 8) })}</p>
        </div>
        <a className="text-sm underline underline-offset-4" href={USAGE_URL} target="_blank" rel="noopener noreferrer">{t("manageUsage")}</a>
      </div>
      {account.connected && !account.plan_usage && <p className="text-sm">{t("planUsageDisabled")}</p>}
      {account.connected && account.plan_usage && <div className="space-y-2">
        <Label htmlFor={`model-${account.id}`}>{t("modelLabel")}</Label>
        <select id={`model-${account.id}`} className="w-full rounded-md border bg-background px-3 py-2 text-sm" value={model} onChange={(event) => setModel(event.target.value)} disabled={busy || loadingModels}>
          <option value="">{loadingModels ? t("loadingModels") : t("chooseModel")}</option>
          {models.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
        </select>
        {modelsError && <p role="alert" className="text-sm text-destructive">{modelsError} <button className="underline" onClick={() => void loadModels()}>{t("retry")}</button></p>}
        {!loadingModels && !modelsError && models.length === 0 && <p className="text-sm text-muted-foreground">{t("noModels")}</p>}
        <Button disabled={busy || !models.some((item) => item.id === model)} onClick={() => void action(async () => {
          await api(`accounts/${account.id}/activate`, "POST", { model }); await reload();
        })}>{busy && <Loader2 className="mr-2 size-4 animate-spin" />}{t("activate")}</Button>
      </div>}
      <div className="flex flex-wrap gap-3">
        <Button variant="outline" disabled={busy} onClick={() => void action(async () => {
          const data = await api("authorize", "POST", { account_id: account.id }); window.location.assign(data.authorization_url);
        })}>{t("continue")}</Button>
        {account.connected && <Button variant="ghost" disabled={busy} onClick={() => void action(async () => {
          const data = await api(`accounts/${account.id}`, "DELETE"); await reload();
          if (!data.revocation_confirmed) onError(t("revocationUnconfirmed"));
        })}>{t("signOut")}</Button>}
      </div>
    </section>
  );
}

export default function ChatGPTSettingsPage() {
  const t = useTranslations("settings.chatgpt");
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
    void reload().catch((error) => setError(error instanceof Error ? error.message : t("loadConnectionsError"))).finally(() => setLoading(false));
  }, [session?.user?.id, reload, t]);
  const welcomeAccount = accounts.find((account) => account.enabled && !account.welcome_seen);

  return <main className="mx-auto max-w-2xl space-y-6 px-4 py-8 sm:px-6">
    <Link href="/settings" className="inline-flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground"><ArrowLeft className="size-4" />{t("settings")}</Link>
    <div><h1 className="text-2xl font-semibold tracking-tight">{t("title")}</h1>
      <p className="mt-2 text-sm text-muted-foreground">{t("intro")}</p></div>
    <p className="text-sm text-muted-foreground">{t("localDescription")}</p>
    <a className="inline-flex items-center gap-1 text-sm underline underline-offset-4" href={USAGE_URL} target="_blank" rel="noopener noreferrer">{t("manageChatGPTUsage")}<ExternalLink className="size-3" /></a>
    {outcome === "connected" && <Alert><AlertDescription>{t("connectedNotice")}</AlertDescription></Alert>}
    {(outcome === "failed" || outcome === "expired") && <Alert variant="destructive"><AlertDescription>{outcome === "expired" ? t("expiredNotice") : t("failedNotice")}</AlertDescription></Alert>}
    {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
    {isPending || (session && loading) ? <p role="status">{t("loadingConnections")}</p> : !session ? <Link className="underline" href="/sign-in">{t("signIn")}</Link> : <>
      {!local && <Alert><AlertDescription>{t("openLocal")}</AlertDescription></Alert>}
      {accounts.map((account) => <AccountCard key={account.id} account={account} reload={reload} onError={setError} />)}
      <Button disabled={busy || !local || process.env.NEXT_PUBLIC_SELF_HOST === "false"} onClick={async () => {
        setBusy(true); setError(null);
        try { const data = await api("authorize", "POST", {}); window.location.assign(data.authorization_url); }
        catch (error) { setError(error instanceof Error ? error.message : t("connectError")); setBusy(false); }
      }}>{busy && <Loader2 className="mr-2 size-4 animate-spin" />}{accounts.length ? t("addAccount") : t("continue")}</Button>
      <p className="text-xs text-muted-foreground">{t("privacyNotice")}</p>
    </>}
    <Dialog open={Boolean(welcomeAccount)} onOpenChange={(open) => {
      if (!open && welcomeAccount) void api(`accounts/${welcomeAccount.id}/acknowledge`, "POST", {}).then(reload).catch(() => setError(t("savePreferenceError")));
    }}><DialogContent><DialogTitle>{t("welcomeTitle")}</DialogTitle>
      <DialogDescription>{t("welcomeDescription")}</DialogDescription>
      <a href={USAGE_URL} target="_blank" rel="noopener noreferrer" className="text-sm underline">{t("manageUsage")}</a>
      <Button onClick={async () => {
        if (!welcomeAccount) return;
        try { await api(`accounts/${welcomeAccount.id}/acknowledge`, "POST", {}); await reload(); }
        catch { setError(t("savePreferenceError")); }
      }}>{t("gotIt")}</Button>
    </DialogContent></Dialog>
  </main>;
}
