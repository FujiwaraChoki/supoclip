"use client";

import { Suspense, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import {
  AlertCircle,
  ArrowLeft,
  CheckCircle,
  ExternalLink,
  Link2,
  RefreshCw,
  Share2,
  Sparkles,
  Trash2,
  TrendingUp,
} from "lucide-react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { Skeleton } from "@/components/ui/skeleton";
import { useSession } from "@/lib/auth-client";
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

function ConnectionStatusBadge({ status }: { status: SocialConnection["status"] }) {
  if (status === "active") {
    return (
      <Badge variant="outline" className="bg-green-50 text-green-700 border-green-200">
        Connected
      </Badge>
    );
  }
  if (status === "reauth_required") {
    return (
      <Badge variant="outline" className="bg-amber-50 text-amber-700 border-amber-200">
        Reconnect needed
      </Badge>
    );
  }
  return <Badge variant="secondary">{status}</Badge>;
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
          <p className="text-gray-600 mb-4">Sign in to manage your social accounts.</p>
          <Link href="/sign-in">
            <Button>Sign In</Button>
          </Link>
        </div>
      </div>
    );
  }

  const configuredProviders = providers.filter((provider) => provider.configured);
  const unconfiguredProviders = providers.filter((provider) => !provider.configured);

  return (
    <div className="min-h-screen bg-white">
      <div className="border-b bg-white">
        <div className="max-w-7xl mx-auto px-4 py-4">
          <Link href="/settings">
            <Button variant="ghost" size="sm">
              <ArrowLeft className="w-4 h-4" />
              Settings
            </Button>
          </Link>
        </div>
      </div>

      <div className="max-w-4xl mx-auto px-4 py-16">
        <div className="max-w-2xl mx-auto">
          <div className="mb-8">
            <div className="flex items-center gap-2 mb-2">
              <Share2 className="w-6 h-6 text-black" />
              <h1 className="text-2xl font-bold text-black">Social Accounts</h1>
            </div>
            <p className="text-gray-600">
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
            <Alert className="mb-6 border-green-200 bg-green-50">
              <CheckCircle className="h-4 w-4 text-green-700" />
              <AlertDescription className="text-green-900">{notice}</AlertDescription>
            </Alert>
          )}

          {/* Connect */}
          <section className="mb-10">
            <h2 className="text-sm font-medium text-gray-500 uppercase tracking-wide mb-3">
              Connect an account
            </h2>
            {isFetching ? (
              <Skeleton className="h-20 w-full" />
            ) : (
              <div className="grid gap-3 sm:grid-cols-3">
                {configuredProviders.map((provider) => (
                  <a
                    key={provider.provider}
                    href={`/api/social/connect/${provider.provider}`}
                    className="flex items-center justify-between gap-2 rounded-lg border p-4 hover:bg-gray-50 transition-colors"
                  >
                    <span className="flex items-center gap-2 text-sm font-medium text-black">
                      <Link2 className="w-4 h-4" />
                      {provider.display_name}
                    </span>
                    <ExternalLink className="w-4 h-4 text-gray-400" />
                  </a>
                ))}
                {unconfiguredProviders.map((provider) => (
                  <div
                    key={provider.provider}
                    className="rounded-lg border border-dashed p-4 text-sm text-gray-500"
                    title={provider.setup_hint ?? undefined}
                  >
                    <p className="font-medium text-gray-700">{provider.display_name}</p>
                    <p className="text-xs mt-1">Not configured on this server.</p>
                    {provider.setup_hint && <p className="text-xs mt-1 text-gray-400">{provider.setup_hint}</p>}
                  </div>
                ))}
              </div>
            )}
            <p className="text-xs text-gray-500 mt-3">
              Instagram needs a professional (Business or Creator) account. TikTok posts stay private until
              TikTok has audited this app. YouTube uploads from an unverified app are private until Google&apos;s
              review completes.
            </p>
          </section>

          {/* Connected */}
          <section className="mb-10">
            <h2 className="text-sm font-medium text-gray-500 uppercase tracking-wide mb-3">
              Connected accounts
            </h2>
            {isFetching ? (
              <Skeleton className="h-20 w-full" />
            ) : connections.length === 0 ? (
              <p className="text-gray-500 text-sm">No accounts connected yet.</p>
            ) : (
              <div className="space-y-3">
                {connections.map((connection) => (
                  <div
                    key={connection.id}
                    className="flex items-center justify-between gap-4 p-4 border rounded-lg"
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
                          <span className="font-medium text-black truncate">{connectionLabel(connection)}</span>
                          <Badge variant="outline">{PROVIDER_LABELS[connection.provider]}</Badge>
                          <ConnectionStatusBadge status={connection.status} />
                        </div>
                        <p className="text-xs text-gray-400 mt-1">
                          Connected {formatDateTime(connection.created_at)}
                          {connection.token_expires_at ? ` · Token renews before ${formatDateTime(connection.token_expires_at)}` : ""}
                        </p>
                        {connection.status === "reauth_required" && connection.last_error && (
                          <p className="text-xs text-amber-700 mt-1">{connection.last_error}</p>
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
                        className="text-red-600 hover:text-red-700 hover:bg-red-50"
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
              <TrendingUp className="w-5 h-5 text-black" />
              <h2 className="text-lg font-semibold text-black">Performance loop</h2>
            </div>
            <p className="text-sm text-gray-600 mb-4">
              SupoClip pulls views, likes, comments and shares back from each platform and uses them to
              steer future clip selection toward what works for your audience.
            </p>

            {isFetching || !performance ? (
              <Skeleton className="h-32 w-full" />
            ) : (
              <div className="space-y-6">
                <div
                  className={`flex items-start gap-3 rounded-lg border p-4 ${
                    performance.personalization_active ? "border-green-200 bg-green-50" : "bg-gray-50"
                  }`}
                >
                  <Sparkles
                    className={`w-5 h-5 mt-0.5 ${
                      performance.personalization_active ? "text-green-700" : "text-gray-400"
                    }`}
                  />
                  <div className="text-sm">
                    {performance.personalization_active ? (
                      <p className="text-green-900">
                        Personalized selection is <span className="font-medium">on</span>. New tasks are
                        analyzed with your results from {performance.measured_posts} published clips.
                      </p>
                    ) : (
                      <p className="text-gray-700">
                        Publish and measure at least {performance.min_posts_for_personalization} clips to
                        turn on personalized selection. {performance.measured_posts} measured so far.
                      </p>
                    )}
                  </div>
                </div>

                {performance.by_provider.length > 0 && (
                  <div>
                    <h3 className="text-sm font-medium text-gray-500 uppercase tracking-wide mb-2">
                      By platform
                    </h3>
                    <div className="overflow-x-auto rounded-lg border">
                      <table className="w-full text-sm">
                        <thead className="bg-gray-50 text-left text-xs text-gray-500">
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
                              <td className="px-3 py-2">{PROVIDER_LABELS[row.provider] ?? row.provider}</td>
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
                    <h3 className="text-sm font-medium text-gray-500 uppercase tracking-wide mb-2">
                      By hook type
                    </h3>
                    <div className="overflow-x-auto rounded-lg border">
                      <table className="w-full text-sm">
                        <thead className="bg-gray-50 text-left text-xs text-gray-500">
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
                    <h3 className="text-sm font-medium text-gray-500 uppercase tracking-wide mb-2">
                      By clip length
                    </h3>
                    <div className="grid gap-2 sm:grid-cols-4">
                      {performance.by_duration.map((row) => (
                        <div key={row.bucket} className="rounded-lg border p-3">
                          <p className="text-xs text-gray-500">{durationBucketLabel(row.bucket)}</p>
                          <p className="text-lg font-semibold text-black">{formatMetric(row.avg_views)}</p>
                          <p className="text-xs text-gray-400">avg views · {row.posts} posts</p>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {performance.top_posts.length > 0 && (
                  <div>
                    <h3 className="text-sm font-medium text-gray-500 uppercase tracking-wide mb-2">
                      Top clips
                    </h3>
                    <div className="space-y-2">
                      {performance.top_posts.map((post) => (
                        <div key={post.id} className="flex items-center justify-between gap-3 rounded-lg border p-3 text-sm">
                          <div className="min-w-0">
                            <p className="font-medium text-black truncate">
                              {post.hook_title || post.title || "Untitled clip"}
                            </p>
                            <p className="text-xs text-gray-500">
                              {PROVIDER_LABELS[post.provider]} · {post.hook_type || "no hook"} ·{" "}
                              {formatDateTime(post.published_at)}
                            </p>
                          </div>
                          <div className="flex items-center gap-3 flex-shrink-0">
                            <span className="text-sm font-semibold">{formatMetric(post.metrics?.views)} views</span>
                            {post.external_url && (
                              <a href={post.external_url} target="_blank" rel="noreferrer" className="text-blue-600">
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
                  <p className="text-sm text-gray-500">
                    Nothing published yet. Open a completed task and use <span className="font-medium">Publish</span> on a clip.
                  </p>
                )}
              </div>
            )}
          </section>
        </div>
      </div>
    </div>
  );
}

export default function SocialSettingsPage() {
  return (
    <Suspense
      fallback={
        <div className="min-h-screen bg-white flex items-center justify-center p-4">
          <Skeleton className="h-32 w-full max-w-xl" />
        </div>
      }
    >
      <SocialSettingsContent />
    </Suspense>
  );
}
