"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { AppShell } from "@/components/app-shell";
import Image from "next/image";
import { Download, Languages, Loader2, Plus, Send, Sparkles, Trash2, Video } from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";

type Clip = { id: string; filename: string; text: string; duration: number; virality_score: number; video_url: string };
type Account = { id: string; platform: string; display_name: string };
type Variant = { id: string; variant_type: string; language?: string; status: string; file_url?: string; metadata?: { ai_voice?: boolean } };
type BRollResult = { id: string; thumbnail: string; duration: number; source_url: string; creator?: string };
type BRollItem = { id: string; prompt: string; start_seconds: number; end_seconds: number; sort_order: number };
type Collection = { id: string; name: string; clip_count: number; workspace_id?: string | null };

async function api(path: string, init?: RequestInit) {
  const response = await fetch(path, { ...init, headers: { "Content-Type": "application/json", ...init?.headers } });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || data.error || "Request failed");
  return data;
}

export default function TaskWorkflowsPage() {
  const params = useParams<{ id: string }>();
  const taskId = params.id;
  const [isLoading, setIsLoading] = useState(true);
  const [clips, setClips] = useState<Clip[]>([]);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [clipId, setClipId] = useState("");
  const [variants, setVariants] = useState<Variant[]>([]);
  const [language, setLanguage] = useState("Spanish");
  const [variantKind, setVariantKind] = useState("translated");
  const [voice, setVoice] = useState("alloy");
  const [brollQuery, setBrollQuery] = useState("");
  const [brollStart, setBrollStart] = useState("0");
  const [brollResults, setBrollResults] = useState<BRollResult[]>([]);
  const [brollItems, setBrollItems] = useState<BRollItem[]>([]);
  const [accountId, setAccountId] = useState("");
  const [scheduledFor, setScheduledFor] = useState("");
  const [collections, setCollections] = useState<Collection[]>([]);
  const [collectionId, setCollectionId] = useState("");
  const [collectionName, setCollectionName] = useState("");
  const [workspaceId, setWorkspaceId] = useState<string | null>(null);
  const [busy, setBusy] = useState("");
  // Tracks whichever clip is currently selected so a variants/B-roll response that
  // resolves after the user has already switched clips gets dropped instead of
  // overwriting the newer clip's state.
  const selectedClipRef = useRef("");

  const loadVariants = useCallback(async (selected: string) => {
    if (!selected) return;
    const data = await api(`/api/clip-workflows/clips/${selected}/variants`);
    if (selectedClipRef.current !== selected) return;
    setVariants(data.variants || []);
  }, []);

  const loadBroll = useCallback(async (selected: string) => {
    if (!selected) return;
    const data = await api(`/api/clip-workflows/clips/${selected}/broll`);
    if (selectedClipRef.current !== selected) return;
    setBrollItems(data.items || []);
  }, []);

  const selectClip = useCallback((id: string) => {
    selectedClipRef.current = id;
    setClipId(id);
    loadVariants(id);
    loadBroll(id);
  }, [loadBroll, loadVariants]);

  useEffect(() => {
    Promise.all([api(`/api/tasks/${taskId}/clips`), api("/api/social/accounts"), api("/api/workflows/collections"), api(`/api/tasks/${taskId}`)])
      .then(([clipData, accountData, collectionData, taskData]) => {
        const nextClips = clipData.clips || [];
        const nextWorkspaceId = taskData.workspace_id || null;
        const matchingCollections = (collectionData.collections || []).filter((collection: Collection) => (collection.workspace_id || null) === nextWorkspaceId);
        setWorkspaceId(nextWorkspaceId);
        setClips(nextClips); setAccounts(accountData.accounts || []);
        if (nextClips[0]) selectClip(nextClips[0].id);
        if (accountData.accounts?.[0]) setAccountId(accountData.accounts[0].id);
        setCollections(matchingCollections);
        if (matchingCollections[0]) setCollectionId(matchingCollections[0].id);
      })
      .catch((reason) => toast.error(reason instanceof Error ? reason.message : "Unable to load workflows"))
      .finally(() => setIsLoading(false));
  }, [selectClip, taskId]);

  const action = async (key: string, work: () => Promise<unknown>, success: string | ((result: unknown) => string)) => {
    setBusy(key);
    try {
      const result = await work();
      toast.success(typeof success === "function" ? success(result) : success);
      await Promise.all([loadVariants(clipId), loadBroll(clipId)]);
    } catch (reason) {
      toast.error(reason instanceof Error ? reason.message : "Request failed");
    } finally {
      setBusy("");
    }
  };

  const localize = () => action("localize", () => api(`/api/clip-workflows/clips/${clipId}/variants`, {
    method: "POST", body: JSON.stringify({ language, kind: variantKind, voice }),
  }), variantKind === "dubbed" ? "Dubbed variant generated with AI voice disclosure" : "Translated caption variant generated");

  const searchBroll = () => action("search", async () => {
    const data = await api(`/api/clip-workflows/clips/${clipId}/broll/search?query=${encodeURIComponent(brollQuery)}`);
    setBrollResults(data.results || []);
  }, "B-roll suggestions loaded");

  const addBroll = (result: BRollResult) => action(`broll-${result.id}`, () => api(`/api/clip-workflows/clips/${clipId}/broll`, {
    method: "POST", body: JSON.stringify({ prompt: brollQuery, source_url: result.source_url,
      start_seconds: Number(brollStart), duration: Math.min(5, result.duration || 3) }),
  }), "B-roll added to the clip workflow");

  const updateBroll = (item: BRollItem, patch: Record<string, number | string>) => action(`broll-update-${item.id}`, () =>
    api(`/api/clip-workflows/clips/${clipId}/broll/${item.id}`, { method: "PATCH", body: JSON.stringify(patch) }),
  "B-roll timing updated");

  const removeBroll = (item: BRollItem) => action(`broll-delete-${item.id}`, () =>
    api(`/api/clip-workflows/clips/${clipId}/broll/${item.id}`, { method: "DELETE" }),
  "B-roll item removed");

  const publish = (now: boolean) => action("publish", async () => {
    if (!accountId) throw new Error("Connect a publishing account first");
    const draft = await api("/api/social/posts", { method: "POST", body: JSON.stringify({ clip_id: clipId,
      social_account_id: accountId, scheduled_for: now || !scheduledFor ? null : new Date(scheduledFor).toISOString(),
      generate_copy: true }) });
    if (now) return api(`/api/social/posts/${draft.id}/publish`, { method: "POST" });
    return draft;
  }, now
    ? (result) => (result as { status?: string })?.status === "publishing" ? "Clip uploaded and processing on the channel" : "Clip published"
    : "Clip scheduled with generated social copy");

  const exportTask = (type: string) => action(`export-${type}`, async () => {
    toast.message(`Preparing ${type.toUpperCase()} download…`);
    const result = await api("/api/workflows/exports", { method: "POST",
      body: JSON.stringify({ task_id: taskId, export_type: type }) });
    window.location.href = `/api${result.file_url}`;
  }, `${type.toUpperCase()} export ready`);

  const createCollection = () => action("collection-create", async () => {
    if (!collectionName.trim()) throw new Error("Enter a collection name");
    const result = await api("/api/workflows/collections", { method: "POST", body: JSON.stringify({ name: collectionName, workspace_id: workspaceId }) });
    setCollections((current) => [...current, { id: result.id, name: result.name, clip_count: 0, workspace_id: workspaceId }]);
    setCollectionId(result.id); setCollectionName("");
  }, "Collection created");

  const addToCollection = () => action("collection-add", () => api(`/api/workflows/collections/${collectionId}/clips`, {
    method: "POST", body: JSON.stringify({ clip_id: clipId }),
  }), "Clip added to collection");

  if (isLoading) {
    return (
      <AppShell breadcrumbs={[{ href: `/tasks/${taskId}`, label: "Task" }, { label: "Workflows" }]} className="bg-stone-50 text-stone-950">
        <main className="px-4 py-10">
        <div className="mx-auto max-w-6xl space-y-6">
          <div>
            <Skeleton className="mb-3 h-4 w-28" />
            <Skeleton className="h-8 w-64" />
            <Skeleton className="mt-2 h-4 w-96 max-w-full" />
          </div>
          <Card><CardContent className="pt-6"><Skeleton className="mb-2 h-4 w-24" /><Skeleton className="h-10 w-full" /></CardContent></Card>
          <div className="grid gap-6 lg:grid-cols-2">
            {Array.from({ length: 4 }).map((_, index) => (
              <Card key={index}>
                <CardHeader><Skeleton className="h-5 w-40" /></CardHeader>
                <CardContent className="space-y-3">
                  <Skeleton className="h-9 w-full" />
                  <Skeleton className="h-9 w-full" />
                  <Skeleton className="h-9 w-2/3" />
                </CardContent>
              </Card>
            ))}
          </div>
        </div>
        </main>
      </AppShell>
    );
  }

  return <AppShell breadcrumbs={[{ href: `/tasks/${taskId}`, label: "Task" }, { label: "Workflows" }]} className="bg-stone-50 text-stone-950"><main className="px-4 py-10"><div className="mx-auto max-w-6xl space-y-6">
    <div><h1 className="text-3xl font-semibold">Clip workflows</h1><p className="mt-1 text-sm text-stone-600">Localize, add B-roll, export, and publish—without changing the advanced editor project.</p></div>
    <Card><CardContent className="pt-6"><Label htmlFor="clip-select">Working clip</Label><Select value={clipId} onValueChange={selectClip}><SelectTrigger id="clip-select"><SelectValue placeholder="Select a clip" /></SelectTrigger><SelectContent>{clips.map((clip) => <SelectItem key={clip.id} value={clip.id}>{clip.filename} · {clip.duration.toFixed(0)}s · score {clip.virality_score}</SelectItem>)}</SelectContent></Select></CardContent></Card>
    <div className="grid gap-6 lg:grid-cols-2">
      <Card><CardHeader><CardTitle className="flex items-center gap-2"><Languages className="h-5 w-5" />Translation & dubbing</CardTitle></CardHeader><CardContent className="space-y-4">
        <div><Label htmlFor="target-language">Target language</Label><Input id="target-language" value={language} onChange={(event) => setLanguage(event.target.value)} /></div><div className="grid grid-cols-2 gap-3"><div><Label htmlFor="variant-kind">Output</Label><Select value={variantKind} onValueChange={setVariantKind}><SelectTrigger id="variant-kind"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="translated">Translated captions</SelectItem><SelectItem value="dubbed">AI dubbing</SelectItem></SelectContent></Select></div><div><Label htmlFor="voice-select">Voice</Label><Select value={voice} onValueChange={setVoice}><SelectTrigger id="voice-select"><SelectValue /></SelectTrigger><SelectContent>{["alloy", "echo", "fable", "onyx", "nova", "shimmer"].map((item) => <SelectItem key={item} value={item}>{item}</SelectItem>)}</SelectContent></Select></div></div>
        <Button disabled={!clipId || busy === "localize"} onClick={localize}>{busy === "localize" ? <Loader2 className="h-4 w-4 animate-spin" /> : <Sparkles className="h-4 w-4" />}Generate variant</Button>
        <div className="space-y-2">{variants.map((variant) => <div key={variant.id} className="flex items-center justify-between rounded-lg border bg-white p-3"><div><span className="font-medium capitalize">{variant.variant_type}</span>{variant.language && <span className="text-sm text-stone-500"> · {variant.language}</span>}{variant.metadata?.ai_voice && <Badge className="ml-2">AI voice</Badge>}</div>{variant.file_url && <Button asChild size="sm" variant="outline"><a href={`/api${variant.file_url}`}><Download className="h-4 w-4" />Download</a></Button>}</div>)}</div>
      </CardContent></Card>
      <Card><CardHeader><CardTitle className="flex items-center gap-2"><Video className="h-5 w-5" />Editable B-roll workflow</CardTitle></CardHeader><CardContent className="space-y-4"><div className="flex gap-2"><Input value={brollQuery} onChange={(event) => setBrollQuery(event.target.value)} placeholder="Search: busy city, coffee…" aria-label="B-roll search query" /><Button variant="outline" disabled={!brollQuery || busy === "search"} onClick={searchBroll}>Search</Button></div><div><Label htmlFor="broll-start">Insert at second</Label><Input id="broll-start" type="number" min="0" value={brollStart} onChange={(event) => setBrollStart(event.target.value)} /></div>
        <div className="grid grid-cols-2 gap-3">{brollResults.map((result) => <button key={result.id} className="overflow-hidden rounded-lg border bg-white text-left" onClick={() => addBroll(result)}><Image src={result.thumbnail} alt="B-roll suggestion" width={320} height={180} unoptimized className="aspect-video w-full object-cover" /><span className="block p-2 text-xs"><Plus className="mr-1 inline h-3 w-3" />Add {result.duration}s · {result.creator || "Pexels"}</span></button>)}</div>
        {brollItems.length > 0 && <div className="space-y-2"><Label>Timeline items</Label>{brollItems.map((item) => <div key={`${item.id}-${item.start_seconds}-${item.end_seconds}`} className="grid grid-cols-1 gap-2 rounded-lg border bg-white p-3 sm:grid-cols-[1fr_80px_80px_auto] sm:items-end"><div><p className="truncate text-sm font-medium">{item.prompt || "B-roll"}</p><p className="text-xs text-stone-500">Editable, non-destructive insert</p></div><div><Label htmlFor={`broll-start-${item.id}`} className="text-xs">Start</Label><Input id={`broll-start-${item.id}`} type="number" min="0" step="0.1" defaultValue={item.start_seconds} onBlur={(event) => { const value = Number(event.target.value); if (value !== item.start_seconds) updateBroll(item, { start_seconds: value }); }} /></div><div><Label htmlFor={`broll-duration-${item.id}`} className="text-xs">Duration</Label><Input id={`broll-duration-${item.id}`} type="number" min="0.5" max="15" step="0.5" defaultValue={Math.max(0.5, item.end_seconds - item.start_seconds)} onBlur={(event) => { const value = Number(event.target.value); if (value !== item.end_seconds - item.start_seconds) updateBroll(item, { duration: value }); }} /></div><Button size="icon" variant="ghost" aria-label="Remove B-roll item" disabled={busy === `broll-delete-${item.id}`} onClick={() => removeBroll(item)}><Trash2 className="h-4 w-4" /></Button></div>)}</div>}
        <Button variant="outline" disabled={brollItems.length === 0 || busy === "render"} onClick={() => action("render", () => api(`/api/clip-workflows/clips/${clipId}/broll/render`, { method: "POST" }), "B-roll variant rendered")}>Render B-roll variant</Button>
      </CardContent></Card>
      <Card><CardHeader><CardTitle className="flex items-center gap-2"><Send className="h-5 w-5" />Publish & schedule</CardTitle></CardHeader><CardContent className="space-y-4">{accounts.length ? <><div><Label htmlFor="channel-select">Channel</Label><Select value={accountId} onValueChange={setAccountId}><SelectTrigger id="channel-select"><SelectValue /></SelectTrigger><SelectContent>{accounts.map((account) => <SelectItem key={account.id} value={account.id}>{account.display_name} · {account.platform}</SelectItem>)}</SelectContent></Select></div><div><Label htmlFor="schedule-input">Schedule</Label><Input id="schedule-input" type="datetime-local" value={scheduledFor} onChange={(event) => setScheduledFor(event.target.value)} /></div><div className="flex gap-2"><Button onClick={() => publish(true)}>Publish now</Button><Button variant="outline" disabled={!scheduledFor} onClick={() => publish(false)}>Schedule</Button></div><p className="text-xs text-stone-500">A grounded title, description, and hashtags are generated from the clip transcript.</p></> : <div><p className="text-sm text-stone-500">Connect a channel before publishing.</p><Button asChild className="mt-3" variant="outline"><Link href="/settings/integrations">Connect channels</Link></Button></div>}</CardContent></Card>
      <Card><CardHeader><CardTitle>Professional exports</CardTitle></CardHeader><CardContent><p className="mb-4 text-sm text-stone-600">Export every clip in this generation with metadata and timeline handoff files.</p><div className="flex flex-wrap gap-2">{["zip", "srt", "csv", "fcpxml", "edl"].map((type) => <Button key={type} variant="outline" disabled={busy === `export-${type}`} onClick={() => exportTask(type)}><Download className="h-4 w-4" />{type.toUpperCase()}</Button>)}</div></CardContent></Card>
      <Card className="lg:col-span-2"><CardHeader><CardTitle>Collections</CardTitle></CardHeader><CardContent className="grid gap-4 lg:grid-cols-2"><div className="flex gap-2"><Input value={collectionName} onChange={(event) => setCollectionName(event.target.value)} placeholder="Campaign highlights" aria-label="New collection name" /><Button variant="outline" onClick={createCollection}><Plus className="h-4 w-4" />Create</Button></div><div className="flex gap-2"><Select value={collectionId} onValueChange={setCollectionId}><SelectTrigger><SelectValue placeholder="Choose collection" /></SelectTrigger><SelectContent>{collections.map((collection) => <SelectItem key={collection.id} value={collection.id}>{collection.name} · {collection.clip_count} clips</SelectItem>)}</SelectContent></Select><Button disabled={!collectionId} onClick={addToCollection}>Add clip</Button></div></CardContent></Card>
    </div>
  </div></main></AppShell>;
}
