"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import { AppShell } from "@/components/app-shell";
import { Link2, Plus, RefreshCw, Trash2, Users, WandSparkles } from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";

type Account = { id: string; platform: string; display_name: string; status: string };
type Workspace = { id: string; name: string; role: string };
type BrandKit = { id: string; name: string; is_default: boolean; workspace_id?: string | null };
type SourceSubscription = { id: string; provider: string; display_name: string; enabled: boolean };
type Webhook = { id: string; url: string; events: string[] };

async function api(path: string, init?: RequestInit) {
  const response = await fetch(path, {
    ...init,
    headers: init?.body instanceof FormData ? init.headers : { "Content-Type": "application/json", ...init?.headers },
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || data.error || "Request failed");
  return data;
}

function errorMessage(reason: unknown, fallback: string) {
  return reason instanceof Error ? reason.message : fallback;
}

export default function IntegrationsPage() {
  const [isLoading, setIsLoading] = useState(true);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [brandKits, setBrandKits] = useState<BrandKit[]>([]);
  const [sources, setSources] = useState<SourceSubscription[]>([]);
  const [webhooks, setWebhooks] = useState<Webhook[]>([]);
  const [busy, setBusy] = useState("");
  const [workspaceId, setWorkspaceId] = useState("personal");
  const [brandKitId, setBrandKitId] = useState("");
  const [inviteRole, setInviteRole] = useState("editor");
  const [assetType, setAssetType] = useState("logo");

  const load = useCallback(async () => {
    const [accountData, workspaceData, kitData, sourceData, webhookData] = await Promise.all([
      api("/api/social/accounts"), api("/api/workflows/workspaces"),
      api("/api/workflows/brand-kits"), api("/api/workflows/source-subscriptions"),
      api("/api/workflows/webhooks"),
    ]);
    setAccounts(accountData.accounts || []);
    setWorkspaces(workspaceData.workspaces || []);
    setBrandKits(kitData.brand_kits || []);
    const matchingKits = (kitData.brand_kits || []).filter((kit: BrandKit) => (kit.workspace_id || "personal") === workspaceId);
    if (!matchingKits.some((kit: BrandKit) => kit.id === brandKitId)) setBrandKitId(matchingKits[0]?.id || "");
    setSources(sourceData.source_subscriptions || []);
    setWebhooks(webhookData.webhooks || []);
  }, [brandKitId, workspaceId]);

  useEffect(() => {
    load()
      .catch((reason) => toast.error(errorMessage(reason, "Failed to load workflow settings")))
      .finally(() => setIsLoading(false));
  }, [load]);

  const action = async (key: string, work: () => Promise<unknown>, success: string) => {
    setBusy(key);
    try {
      await work();
      toast.success(success);
      await load();
    } catch (reason) {
      toast.error(errorMessage(reason, "Request failed"));
    } finally {
      setBusy("");
    }
  };

  const connect = (platform: string) => action(`connect-${platform}`, async () => {
    const query = workspaceId === "personal" ? "" : `?workspace_id=${encodeURIComponent(workspaceId)}`;
    const result = await api(`/api/social/oauth/${platform}/start${query}`);
    window.location.href = result.authorize_url;
  }, `Opening ${platform}`);

  const createWorkspace = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault(); const form = new FormData(event.currentTarget);
    return action("workspace", () => api("/api/workflows/workspaces", {
      method: "POST", body: JSON.stringify({ name: form.get("name") }),
    }), "Workspace created");
  };

  const createBrandKit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault(); const form = new FormData(event.currentTarget);
    return action("brand", () => api("/api/workflows/brand-kits", {
      method: "POST", body: JSON.stringify({ name: form.get("name"), workspace_id: workspaceId === "personal" ? null : workspaceId,
        is_default: brandKits.filter((kit) => (kit.workspace_id || "personal") === workspaceId).length === 0,
        settings: { primary_color: form.get("color"), font_family: form.get("font") } }),
    }), "Brand kit saved");
  };

  const inviteMember = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault(); const form = new FormData(event.currentTarget);
    if (workspaceId === "personal") return;
    setBusy("invite");
    api(`/api/workflows/workspaces/${workspaceId}/invites`, {
      method: "POST", body: JSON.stringify({ email: form.get("email"), role: form.get("role") }),
    }).then((result) => toast.success(`Invitation link ready: ${window.location.origin}/invites/${result.invite_token}`))
      .catch((reason) => toast.error(errorMessage(reason, "Invitation failed")))
      .finally(() => setBusy(""));
  };

  const uploadBrandAsset = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault(); const form = new FormData(event.currentTarget);
    if (!brandKitId) return;
    const selectedKit = brandKits.find((kit) => kit.id === brandKitId);
    form.set("brand_kit_id", brandKitId);
    if (selectedKit?.workspace_id) form.set("workspace_id", selectedKit.workspace_id);
    return action("asset", () => api("/api/workflows/assets", { method: "POST", body: form }), "Brand asset uploaded");
  };

  const createSource = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault(); const form = new FormData(event.currentTarget);
    return action("source", () => api("/api/workflows/source-subscriptions", {
      method: "POST", body: JSON.stringify({ provider: form.get("provider"),
        external_source_id: form.get("externalId"), source_url: form.get("url"),
        display_name: form.get("name"), workspace_id: workspaceId === "personal" ? null : workspaceId,
        settings: { max_items_per_poll: 1 } }),
    }), "Auto-import source saved");
  };

  const createWebhook = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault(); const form = new FormData(event.currentTarget);
    setBusy("webhook");
    api("/api/workflows/webhooks", { method: "POST",
      body: JSON.stringify({ url: form.get("url"), workspace_id: workspaceId === "personal" ? null : workspaceId,
        events: String(form.get("events") || "task.completed")
        .split(",").map((item) => item.trim()).filter(Boolean) }) })
      .then((result) => { toast.success(`Webhook saved. Signing secret: ${result.secret}`); return load(); })
      .catch((reason) => toast.error(errorMessage(reason, "Webhook setup failed")))
      .finally(() => setBusy(""));
  };

  if (isLoading) {
    return (
      <AppShell breadcrumbs={[{ href: "/settings", label: "Settings" }, { label: "Integrations" }]} className="bg-stone-50 text-stone-950">
        <main className="px-4 py-10">
        <div className="mx-auto max-w-6xl space-y-6">
          <div>
            <Skeleton className="mb-3 h-4 w-20" />
            <Skeleton className="h-8 w-72" />
            <Skeleton className="mt-2 h-4 w-full max-w-xl" />
          </div>
          <Card><CardContent className="pt-6"><Skeleton className="h-10 w-full" /></CardContent></Card>
          <div className="grid gap-6 lg:grid-cols-2">
            {Array.from({ length: 5 }).map((_, index) => (
              <Card key={index} className={index === 4 ? "lg:col-span-2" : undefined}>
                <CardHeader><Skeleton className="h-5 w-44" /></CardHeader>
                <CardContent className="space-y-3">
                  <Skeleton className="h-9 w-full" />
                  <Skeleton className="h-9 w-full" />
                </CardContent>
              </Card>
            ))}
          </div>
        </div>
        </main>
      </AppShell>
    );
  }

  return (
    <AppShell breadcrumbs={[{ href: "/settings", label: "Settings" }, { label: "Integrations" }]} className="bg-stone-50 text-stone-950">
      <main className="px-4 py-10">
      <div className="mx-auto max-w-6xl space-y-6">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="text-3xl font-semibold">Workflows & integrations</h1>
            <p className="mt-1 text-sm text-stone-600">Connect channels, reuse your brand, collaborate, auto-import, and automate delivery.</p>
          </div>
          <Button variant="outline" onClick={() => load()}><RefreshCw className="h-4 w-4" />Refresh</Button>
        </div>

        <Card><CardContent className="grid gap-2 pt-6 sm:grid-cols-[180px_1fr] sm:items-center">
          <Label htmlFor="workspace-select" className="sm:pt-0">Configuration workspace</Label>
          <Select value={workspaceId} onValueChange={setWorkspaceId}>
            <SelectTrigger id="workspace-select" className="w-full bg-white"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="personal">Personal</SelectItem>
              {workspaces.map((workspace) => <SelectItem key={workspace.id} value={workspace.id}>{workspace.name} · {workspace.role}</SelectItem>)}
            </SelectContent>
          </Select>
        </CardContent></Card>

        <div className="grid gap-6 lg:grid-cols-2">
          <Card><CardHeader><CardTitle className="flex items-center gap-2"><Link2 className="h-5 w-5" />Social channels</CardTitle></CardHeader>
            <CardContent className="space-y-4">
              <div className="flex flex-wrap gap-2">{["youtube", "tiktok", "instagram", "facebook"].map((platform) => (
                <Button key={platform} variant="outline" disabled={busy === `connect-${platform}`} onClick={() => connect(platform)}>
                  Connect {platform[0].toUpperCase() + platform.slice(1)}
                </Button>))}</div>
              <div className="space-y-2">{accounts.length === 0 ? <p className="text-sm text-stone-500">No publishing accounts connected yet.</p> : accounts.map((account) => (
                <div key={account.id} className="flex items-center justify-between rounded-lg border bg-white p-3">
                  <div><p className="font-medium">{account.display_name}</p><p className="text-xs uppercase text-stone-500">{account.platform}</p></div>
                  <div className="flex items-center gap-2"><Badge>{account.status}</Badge><Button size="icon" variant="ghost" aria-label={`Disconnect ${account.display_name}`} onClick={() => action(account.id, () => api(`/api/social/accounts/${account.id}`, { method: "DELETE" }), "Account disconnected")}><Trash2 className="h-4 w-4" /></Button></div>
                </div>))}</div>
            </CardContent></Card>

          <Card><CardHeader><CardTitle className="flex items-center gap-2"><Users className="h-5 w-5" />Team workspaces</CardTitle></CardHeader>
            <CardContent className="space-y-4">
              <form className="flex gap-2" onSubmit={createWorkspace}>
                <Input id="workspace-name-input" name="name" required placeholder="Workspace name" aria-label="New workspace name" />
                <Button disabled={busy === "workspace"}><Plus className="h-4 w-4" />Create</Button>
              </form>
              {workspaces.map((workspace) => <div key={workspace.id} className="flex justify-between rounded-lg border bg-white p-3"><span>{workspace.name}</span><Badge variant="outline">{workspace.role}</Badge></div>)}
              {workspaceId !== "personal" && (
                <form className="grid grid-cols-1 gap-2 sm:grid-cols-[1fr_120px_auto]" onSubmit={inviteMember}>
                  <Input name="email" type="email" required placeholder="teammate@example.com" aria-label="Teammate email" />
                  <input type="hidden" name="role" value={inviteRole} />
                  <Select value={inviteRole} onValueChange={setInviteRole}>
                    <SelectTrigger aria-label="Invite role" className="bg-white"><SelectValue /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="editor">Editor</SelectItem>
                      <SelectItem value="member">Member</SelectItem>
                      <SelectItem value="viewer">Viewer</SelectItem>
                      <SelectItem value="admin">Admin</SelectItem>
                    </SelectContent>
                  </Select>
                  <Button variant="outline" disabled={busy === "invite"}>Invite</Button>
                </form>
              )}
            </CardContent></Card>

          <Card><CardHeader><CardTitle className="flex items-center gap-2"><WandSparkles className="h-5 w-5" />Brand kits</CardTitle></CardHeader>
            <CardContent className="space-y-4"><form className="grid gap-3 sm:grid-cols-2" onSubmit={createBrandKit}>
              <div><Label htmlFor="brand-kit-name">Name</Label><Input id="brand-kit-name" name="name" required placeholder="Main brand" /></div>
              <div><Label htmlFor="brand-kit-color">Primary color</Label><Input id="brand-kit-color" name="color" type="color" defaultValue="#111111" /></div>
              <div className="sm:col-span-2"><Label htmlFor="brand-kit-font">Font family</Label><Input id="brand-kit-font" name="font" placeholder="TikTokSans-Regular" /></div>
              <Button className="sm:col-span-2" disabled={busy === "brand"}>Save brand kit</Button>
            </form>{brandKits.filter((kit) => (kit.workspace_id || "personal") === workspaceId).map((kit) => <button type="button" key={kit.id} onClick={() => setBrandKitId(kit.id)} className={`flex w-full justify-between rounded-lg border bg-white p-3 text-left ${brandKitId === kit.id ? "ring-2 ring-stone-900" : ""}`}><span>{kit.name}</span>{kit.is_default && <Badge>Default</Badge>}</button>)}
              {brandKitId && (
                <form className="grid grid-cols-1 gap-2 sm:grid-cols-[140px_1fr_auto]" onSubmit={uploadBrandAsset}>
                  <input type="hidden" name="asset_type" value={assetType} />
                  <Select value={assetType} onValueChange={setAssetType}>
                    <SelectTrigger aria-label="Brand asset type" className="bg-white"><SelectValue /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="logo">Logo</SelectItem>
                      <SelectItem value="music">Music</SelectItem>
                    </SelectContent>
                  </Select>
                  <Input name="file" type="file" required aria-label="Brand asset file" />
                  <Button variant="outline" disabled={busy === "asset"}>Upload</Button>
                </form>
              )}
            </CardContent></Card>

          <Card><CardHeader><CardTitle>Auto-import sources</CardTitle></CardHeader><CardContent className="space-y-4">
            <form className="grid gap-3" onSubmit={createSource}>
              <div><Label htmlFor="source-name">Name</Label><Input id="source-name" name="name" required placeholder="Company channel" /></div>
              <input type="hidden" name="provider" value="youtube" />
              <div><Label htmlFor="source-external-id">YouTube channel ID</Label><Input id="source-external-id" name="externalId" required placeholder="UC…" /></div>
              <div><Label htmlFor="source-url">Channel URL</Label><Input id="source-url" name="url" type="url" required placeholder="https://www.youtube.com/@channel" /></div>
              <Button disabled={busy === "source"}>Watch YouTube channel</Button>
            </form>
            <Button variant="outline" onClick={() => action("poll", () => api("/api/workflows/source-subscriptions/poll", { method: "POST" }), "Source check queued")}>Check now</Button>
            {sources.map((source) => <div key={source.id} className="flex justify-between rounded-lg border bg-white p-3"><span>{source.display_name}</span><Badge variant="outline">{source.provider}</Badge></div>)}
          </CardContent></Card>

          <Card className="lg:col-span-2"><CardHeader><CardTitle>Webhooks & automation</CardTitle></CardHeader><CardContent className="grid gap-5 lg:grid-cols-2">
            <form className="space-y-3" onSubmit={createWebhook}>
              <div><Label htmlFor="webhook-url">HTTPS endpoint</Label><Input id="webhook-url" name="url" type="url" required placeholder="https://example.com/hooks/supoclip" /></div>
              <div><Label htmlFor="webhook-events">Events</Label><Textarea id="webhook-events" name="events" defaultValue="task.completed" /></div>
              <Button disabled={busy === "webhook"}>Create signed webhook</Button>
            </form>
            <div className="space-y-2">{webhooks.length === 0 ? <p className="text-sm text-stone-500">No webhook endpoints.</p> : webhooks.map((hook) => <div key={hook.id} className="rounded-lg border bg-white p-3"><p className="break-all text-sm font-medium">{hook.url}</p><p className="mt-1 text-xs text-stone-500">{hook.events.join(", ")}</p><Button className="mt-2" size="sm" variant="outline" onClick={() => action(`test-${hook.id}`, () => api(`/api/workflows/webhooks/${hook.id}/test`, { method: "POST" }), "Test delivery sent")}>Send test</Button></div>)}</div>
          </CardContent></Card>
        </div>
      </div>
      </main>
    </AppShell>
  );
}
