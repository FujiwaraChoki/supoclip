"use client";

import { Suspense, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import {
  AlertCircle,
  CheckCircle,
  ExternalLink,
  RefreshCw,
  Trash2,
  TrendingUp,
} from "lucide-react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { Skeleton } from "@/components/ui/skeleton";
import { SocialProviderIcon } from "@/components/social-provider-icon";
import { useSession } from "@/lib/auth-client";
import { cn } from "@/lib/utils";
import {
  PROVIDER_LABELS,
  connectionLabel,
  durationBucketLabel,
  formatDateTime,
  formatMetric,
  formatPercent,
  type PerformanceSummary,
  type SocialConnection,
  type SocialProviderStatus,
} from "@/lib/social";

const SECTION_HEADING = "mb-3 text-sm font-medium uppercase tracking-wide text-muted-foreground";
const TABLE_HEAD = "bg-muted/50 text-left text-xs text-muted-foreground";

function ConnectionStatusBadge({ status }: { status: SocialConnection["status"] }) {
  if (status === "active") {
    return <Badge variant="secondary">Connected</Badge>;
  }
  if (status === "reauth_required") {
    return <Badge variant="destructive">Reconnect needed</Badge>;
  }
  return <Badge variant="outline">{status}</Badge>;
}

function PageSkeleton() {
  return (
    <main className="mx-auto w-full max-w-3xl px-4 py-8 sm:px-8 md:py-10">
      <Skeleton className="h-64 w-full" />
    </main>
  );
}

function SocialSettingsContent() {
  const { data: session, isPending } = useSession();
  const router = useRouter();
  const searchParams = useSearchParams();
  const [providers, setProviders] = useState<SocialProviderStatus[]>([]);
  const [connections, setConnections] = useState<SocialConnection[]>([]);
  const [performance, setPerformance] = useState<PerformanceSummary | null>(null);
  const [isFetching, setIsFetching] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [disconnectingId, setDisconnectingId] = useState<string | null>(null);

  const load = useCallback(async () => {
    setIsFetching(true);
    setError(null);
    try {
      const [providersResponse, connectionsResponse, performanceResponse] = await Promise.all([
        fetch("/api/social/providers", { cache: "no-store" }),
        fetch("/api/social/connections", { cache: "no-store" }),
        fetch("/api/social/performance", { cache: "no-store" }),
      ]);
      if (!providersResponse.ok || !connectionsResponse.ok) {
        throw new Error("Failed to load social accounts");
      }
      const providersData = (await providersResponse.json()) as { providers: SocialProviderStatus[] };
      const connectionsData = (await connectionsResponse.json()) as { connections: SocialConnection[] };
      setProviders(providersData.providers ?? []);
      setConnections(connectionsData.connections ?? []);
      if (performanceResponse.ok) {
        setPerformance((await performanceResponse.json()) as PerformanceSummary);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load social accounts");
    } finally {
      setIsFetching(false);
    }
  }, []);

  useEffect(() => {
    if (session?.user?.id) {
      void load();
    }
  }, [session?.user?.id, load]);

  // Surface the result of an OAuth round-trip, then clean the URL.
  useEffect(() => {
    const connected = searchParams.get("connected");
    const account = searchParams.get("account");
    const oauthError = searchParams.get("error");
    if (!connected && !oauthError) return;
    if (connected) {
      const label = PROVIDER_LABELS[connected as keyof typeof PROVIDER_LABELS] ?? connected;
      setNotice(`${label} connected${account ? ` as ${account}` : ""}.`);
    }
    if (oauthError) {
      setError(oauthError);
    }
    router.replace("/settings/social");
  }, [searchParams, router]);

  const handleDisconnect = async (connection: SocialConnection) => {
    if (!window.confirm(`Disconnect ${connectionLabel(connection)}? Scheduled posts for it will be cancelled.`)) {
      return;
    }
    setDisconnectingId(connection.id);
    setError(null);
    try {
      const response = await fetch(`/api/social/connections/${connection.id}`, { method: "DELETE" });
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(data?.detail || "Failed to disconnect account");
      }
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to disconnect account");
    } finally {
      setDisconnectingId(null);
    }
  };

  if (isPending) {
    return <PageSkeleton />;
  }

  if (!session?.user) {
    return (
      <main className="mx-auto max-w-md px-4 py-24 text-center">
        <p className="mb-4 text-muted-foreground">Sign in to manage your social accounts.</p>
        <Link href="/sign-in">
          <Button>Sign In</Button>
        </Link>
      </main>
    );
  }

  const configuredProviders = providers.filter((provider) => provider.configured);
  const unconfiguredProviders = providers.filter((provider) => !provider.configured);

  return (
    <main className="mx-auto w-full max-w-3xl px-4 py-8 sm:px-8 md:py-10">
      <div className="mb-8">
        <h1 className="font-display text-3xl font-bold tracking-tight">Social accounts</h1>
        <p className="mt-2 text-muted-foreground">
          Connect the accounts you post to. Publish clips straight from a task page, schedule
          them, and let SupoClip learn from how they perform.
        </p>
      </div>

      {error && (
        <Alert variant="destructive" className="mb-6">
          <AlertCircle className="h-4 w-4" />
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
      {notice && (
        <Alert className="mb-6">
          <CheckCircle className="h-4 w-4" />
          <AlertDescription>{notice}</AlertDescription>
        </Alert>
      )}

      {/* Connect */}
      <section className="mb-10">
        <h2 className={SECTION_HEADING}>Connect an account</h2>
        {isFetching ? (
          <Skeleton className="h-20 w-full" />
        ) : (
          <div className="grid gap-3 sm:grid-cols-3">
            {configuredProviders.map((provider) => (
              <a
                key={provider.provider}
                href={`/api/social/connect/${provider.provider}`}
                className="flex items-center justify-between gap-2 rounded-xl border bg-background p-4 transition-colors hover:border-foreground/25"
              >
                <span className="flex items-center gap-2.5 text-sm font-medium">
                  <SocialProviderIcon provider={provider.provider} className="size-5" />
                  {provider.display_name}
                </span>
                <span className="text-xs text-muted-foreground">Connect</span>
              </a>
            ))}
            {unconfiguredProviders.map((provider) => (
              <div
                key={provider.provider}
                className="rounded-xl border border-dashed p-4 text-sm text-muted-foreground"
                title={provider.setup_hint ?? undefined}
              >
                <p className="flex items-center gap-2 font-medium text-foreground">
                  <SocialProviderIcon provider={provider.provider} className="size-4 text-muted-foreground" />
                  {provider.display_name}
                </p>
                <p className="text-xs mt-1">Not configured on this server.</p>
                {provider.setup_hint && <p className="text-xs mt-1">{provider.setup_hint}</p>}
              </div>
            ))}
          </div>
        )}
        <p className="text-xs text-muted-foreground mt-3">
          Instagram needs a professional (Business or Creator) account. TikTok posts stay private until
          TikTok has audited this app. YouTube uploads from an unverified app are private until Google&apos;s
          review completes.
        </p>
      </section>

      {/* Connected */}
      <section className="mb-10">
        <h2 className={SECTION_HEADING}>Connected accounts</h2>
        {isFetching ? (
          <Skeleton className="h-20 w-full" />
        ) : connections.length === 0 ? (
          <p className="text-sm text-muted-foreground">No accounts connected yet.</p>
        ) : (
          <div className="space-y-3">
            {connections.map((connection) => (
              <div
                key={connection.id}
                className="flex items-center justify-between gap-4 rounded-xl border bg-background p-4"
              >
                <div className="flex items-center gap-3 min-w-0">
                  <Avatar className="h-9 w-9">
                    {connection.avatar_url && <AvatarImage src={connection.avatar_url} alt="" />}
                    <AvatarFallback>
                      {(connection.display_name || connection.username || "?").slice(0, 1).toUpperCase()}
                    </AvatarFallback>
                  </Avatar>
                  <div className="min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="font-medium truncate">{connectionLabel(connection)}</span>
                      <Badge variant="outline" className="gap-1.5">
                        <SocialProviderIcon provider={connection.provider} className="size-3" />
                        {PROVIDER_LABELS[connection.provider]}
                      </Badge>
                      <ConnectionStatusBadge status={connection.status} />
                    </div>
                    <p className="text-xs text-muted-foreground mt-1">
                      Connected {formatDateTime(connection.created_at)}
                      {connection.token_expires_at ? ` · Token renews before ${formatDateTime(connection.token_expires_at)}` : ""}
                    </p>
                    {connection.status === "reauth_required" && connection.last_error && (
                      <p className="text-xs text-destructive mt-1">{connection.last_error}</p>
                    )}
                  </div>
                </div>
                <div className="flex items-center gap-1 flex-shrink-0">
                  {connection.status === "reauth_required" && (
                    <a href={`/api/social/connect/${connection.provider}`}>
                      <Button size="sm" variant="outline">
                        <RefreshCw className="w-4 h-4" />
                        Reconnect
                      </Button>
                    </a>
                  )}
                  <Button
                    size="sm"
                    variant="ghost"
                    className="text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
                    disabled={disconnectingId === connection.id}
                    onClick={() => handleDisconnect(connection)}
                  >
                    <Trash2 className="w-4 h-4" />
                    Disconnect
                  </Button>
                </div>
              </div>
            ))}
          </div>
        )}
      </section>

      <Separator className="mb-10" />

      {/* Performance */}
      <section>
        <div className="flex items-center gap-2 mb-2">
          <TrendingUp className="w-5 h-5" />
          <h2 className="text-lg font-semibold">Performance loop</h2>
        </div>
        <p className="text-sm text-muted-foreground mb-4">
          SupoClip pulls views, likes, comments and shares back from each platform and uses them to
          steer future clip selection toward what works for your audience.
        </p>

        {isFetching || !performance ? (
          <Skeleton className="h-32 w-full" />
        ) : (
          <div className="space-y-6">
            <div className="rounded-xl border bg-muted/50 p-4">
              <div className="text-sm">
                {performance.personalization_active ? (
                  <p>
                    Personalized selection is <span className="font-medium">on</span>. New tasks are
                    analyzed with your results from {performance.measured_posts} published clips.
                  </p>
                ) : (
                  <p className="text-muted-foreground">
                    Publish and measure at least {performance.min_posts_for_personalization} clips to
                    turn on personalized selection. {performance.measured_posts} measured so far.
                  </p>
                )}
              </div>
            </div>

            {performance.by_provider.length > 0 && (
              <div>
                <h3 className={cn(SECTION_HEADING, "mb-2")}>By platform</h3>
                <div className="overflow-x-auto rounded-lg border">
                  <table className="w-full text-sm">
                    <thead className={TABLE_HEAD}>
                      <tr>
                        <th className="px-3 py-2 font-medium">Platform</th>
                        <th className="px-3 py-2 font-medium text-right">Posts</th>
                        <th className="px-3 py-2 font-medium text-right">Views</th>
                        <th className="px-3 py-2 font-medium text-right">Avg views</th>
                        <th className="px-3 py-2 font-medium text-right">Likes</th>
                        <th className="px-3 py-2 font-medium text-right">Comments</th>
                      </tr>
                    </thead>
                    <tbody>
                      {performance.by_provider.map((row) => (
                        <tr key={row.provider} className="border-t">
                          <td className="px-3 py-2">
                            <span className="flex items-center gap-2">
                              <SocialProviderIcon provider={row.provider} className="size-3.5" />
                              {PROVIDER_LABELS[row.provider] ?? row.provider}
                            </span>
                          </td>
                          <td className="px-3 py-2 text-right">{row.posts}</td>
                          <td className="px-3 py-2 text-right">{formatMetric(row.total_views)}</td>
                          <td className="px-3 py-2 text-right">{formatMetric(row.avg_views)}</td>
                          <td className="px-3 py-2 text-right">{formatMetric(row.total_likes)}</td>
                          <td className="px-3 py-2 text-right">{formatMetric(row.total_comments)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}

            {performance.by_hook_type.length > 0 && (
              <div>
                <h3 className={cn(SECTION_HEADING, "mb-2")}>By hook type</h3>
                <div className="overflow-x-auto rounded-lg border">
                  <table className="w-full text-sm">
                    <thead className={TABLE_HEAD}>
                      <tr>
                        <th className="px-3 py-2 font-medium">Hook</th>
                        <th className="px-3 py-2 font-medium text-right">Posts</th>
                        <th className="px-3 py-2 font-medium text-right">Median views</th>
                        <th className="px-3 py-2 font-medium text-right">Engagement</th>
                        <th className="px-3 py-2 font-medium text-right">Predicted score</th>
                      </tr>
                    </thead>
                    <tbody>
                      {performance.by_hook_type.map((row) => (
                        <tr key={row.hook_type} className="border-t">
                          <td className="px-3 py-2 capitalize">{row.hook_type}</td>
                          <td className="px-3 py-2 text-right">{row.posts}</td>
                          <td className="px-3 py-2 text-right">{formatMetric(row.median_views)}</td>
                          <td className="px-3 py-2 text-right">{formatPercent(row.avg_engagement_rate)}</td>
                          <td className="px-3 py-2 text-right">
                            {row.avg_predicted_score === null ? "—" : Math.round(row.avg_predicted_score)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}

            {performance.by_duration.length > 0 && (
              <div>
                <h3 className={cn(SECTION_HEADING, "mb-2")}>By clip length</h3>
                <div className="grid gap-2 sm:grid-cols-4">
                  {performance.by_duration.map((row) => (
                    <div key={row.bucket} className="rounded-lg border p-3">
                      <p className="text-xs text-muted-foreground">{durationBucketLabel(row.bucket)}</p>
                      <p className="text-lg font-semibold">{formatMetric(row.avg_views)}</p>
                      <p className="text-xs text-muted-foreground">avg views · {row.posts} posts</p>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {performance.top_posts.length > 0 && (
              <div>
                <h3 className={cn(SECTION_HEADING, "mb-2")}>Top clips</h3>
                <div className="space-y-2">
                  {performance.top_posts.map((post) => (
                    <div key={post.id} className="flex items-center justify-between gap-3 rounded-lg border p-3 text-sm">
                      <div className="min-w-0">
                        <p className="font-medium truncate">
                          {post.hook_title || post.title || "Untitled clip"}
                        </p>
                        <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
                          <SocialProviderIcon provider={post.provider} className="size-3" />
                          {PROVIDER_LABELS[post.provider]} · {post.hook_type || "no hook"} ·{" "}
                          {formatDateTime(post.published_at)}
                        </p>
                      </div>
                      <div className="flex items-center gap-3 flex-shrink-0">
                        <span className="text-sm font-semibold">{formatMetric(post.metrics?.views)} views</span>
                        {post.external_url && (
                          <a
                            href={post.external_url}
                            target="_blank"
                            rel="noreferrer"
                            className="text-muted-foreground hover:text-foreground"
                            aria-label="Open post"
                          >
                            <ExternalLink className="w-4 h-4" />
                          </a>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {performance.total_published === 0 && (
              <p className="text-sm text-muted-foreground">
                Nothing published yet. Open a completed task and use <span className="font-medium">Publish</span> on a clip.
              </p>
            )}
          </div>
        )}
      </section>
    </main>
  );
}

export default function SocialSettingsPage() {
  return (
    <Suspense fallback={<PageSkeleton />}>
      <SocialSettingsContent />
    </Suspense>
  );
}
