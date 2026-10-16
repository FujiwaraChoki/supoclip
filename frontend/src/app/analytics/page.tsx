"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { ExternalLink } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { SocialProviderIcon } from "@/components/social-provider-icon";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { ViewsChart, type DailyViews } from "@/components/views-chart";
import { useSocialAccess } from "@/hooks/use-social-access";
import { useSession } from "@/lib/auth-client";
import { cn } from "@/lib/utils";
import {
  PROVIDER_LABELS,
  durationBucketLabel,
  formatDateTime,
  formatMetric,
  formatPercent,
  type PerformanceSummary,
} from "@/lib/social";

const SECTION_HEADING = "mb-3 text-sm font-medium uppercase tracking-wide text-muted-foreground";
const TABLE_HEAD = "bg-muted/50 text-left text-xs text-muted-foreground";
const PAGE = "mx-auto w-full max-w-3xl px-4 py-8 sm:px-8 md:py-10";

function sum(values: (number | null)[]): number {
  return values.reduce<number>((total, value) => total + (value ?? 0), 0);
}

function StatTile({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl border bg-background p-4">
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className="mt-1 text-2xl font-semibold tabular-nums">{value}</p>
    </div>
  );
}

interface ViewsTimeseries {
  days: DailyViews[];
  measured_days: number;
  min_days: number;
}

const RANGES = [7, 30, 90] as const;

function ViewsSection() {
  const [range, setRange] = useState<(typeof RANGES)[number]>(30);
  const [series, setSeries] = useState<ViewsTimeseries | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetch(`/api/social/performance/timeseries?days=${range}`, { cache: "no-store" })
      .then(async (response) => (response.ok ? ((await response.json()) as ViewsTimeseries) : null))
      .then((data) => {
        if (!cancelled && data) setSeries(data);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [range]);

  return (
    <section>
      <div className="mb-3 flex items-center justify-between gap-3">
        <h2 className="text-sm font-medium uppercase tracking-wide text-muted-foreground">Daily views</h2>
        <ToggleGroup
          type="single"
          size="sm"
          variant="outline"
          value={String(range)}
          onValueChange={(value) => value && setRange(Number(value) as (typeof RANGES)[number])}
          aria-label="Time range"
        >
          {RANGES.map((option) => (
            <ToggleGroupItem key={option} value={String(option)} className="px-2.5 text-xs">
              {option}d
            </ToggleGroupItem>
          ))}
        </ToggleGroup>
      </div>
      <div className="rounded-xl border bg-background p-4">
        {!series ? (
          <Skeleton className="h-[200px] w-full" />
        ) : series.measured_days < series.min_days ? (
          <p className="py-10 text-center text-sm text-muted-foreground">
            Your views chart appears after {series.min_days} days of results.{" "}
            {series.measured_days} {series.measured_days === 1 ? "day" : "days"} so far.
          </p>
        ) : (
          <ViewsChart days={series.days} />
        )}
      </div>
    </section>
  );
}

export default function AnalyticsPage() {
  const { data: session, isPending } = useSession();
  const [performance, setPerformance] = useState<PerformanceSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const socialAccess = useSocialAccess(Boolean(session?.user?.id));

  useEffect(() => {
    if (!session?.user?.id || !socialAccess) return;
    fetch("/api/social/performance", { cache: "no-store" })
      .then(async (response) => {
        if (!response.ok) throw new Error("Failed to load analytics");
        setPerformance((await response.json()) as PerformanceSummary);
      })
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load analytics"));
  }, [session?.user?.id, socialAccess]);

  if (isPending) {
    return (
      <main className={PAGE}>
        <Skeleton className="h-64 w-full" />
      </main>
    );
  }

  if (!session?.user) {
    return (
      <main className="mx-auto max-w-md px-4 py-24 text-center">
        <p className="mb-4 text-muted-foreground">Sign in to see how your clips perform.</p>
        <Link href="/sign-in">
          <Button>Sign In</Button>
        </Link>
      </main>
    );
  }

  return (
    <main className={PAGE}>
      <div className="mb-8">
        <h1 className="font-display text-3xl font-bold tracking-tight">Analytics</h1>
        <p className="mt-2 text-muted-foreground">
          How your published clips perform. SupoClip pulls views, likes and comments back from each
          platform and uses them to steer future clip selection toward what works for your audience.
        </p>
      </div>

      {socialAccess === false ? (
        <p className="text-sm text-muted-foreground">
          Analytics comes with social publishing, which isn&apos;t available on your account yet.
        </p>
      ) : error ? (
        <p className="text-sm text-destructive">{error}</p>
      ) : !performance ? (
        <div className="space-y-4">
          <Skeleton className="h-20 w-full" />
          <Skeleton className="h-40 w-full" />
        </div>
      ) : performance.total_published === 0 ? (
        <div className="rounded-xl border border-dashed p-8 text-center text-sm text-muted-foreground">
          <p>Nothing published yet.</p>
          <p className="mt-1">
            Connect an account on the{" "}
            <Link href="/settings/social" className="font-medium text-foreground underline underline-offset-4">
              Social
            </Link>{" "}
            page, then use Publish on any clip. Results show up here within an hour.
          </p>
        </div>
      ) : (
        <div className="space-y-10">
          <section className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <StatTile label="Published clips" value={formatMetric(performance.total_published)} />
            <StatTile label="Views" value={formatMetric(sum(performance.by_provider.map((row) => row.total_views)))} />
            <StatTile label="Likes" value={formatMetric(sum(performance.by_provider.map((row) => row.total_likes)))} />
            <StatTile label="Comments" value={formatMetric(sum(performance.by_provider.map((row) => row.total_comments)))} />
          </section>

          <ViewsSection />

          <section className="rounded-xl border bg-muted/50 p-4 text-sm">
            {performance.personalization_active ? (
              <p>
                Personalized clip selection is <span className="font-medium">on</span>. New generations
                use your results from {performance.measured_posts} published clips.
              </p>
            ) : (
              <p className="text-muted-foreground">
                Personalized clip selection turns on after {performance.min_posts_for_personalization} measured
                clips. {performance.measured_posts} measured so far.
              </p>
            )}
          </section>

          {performance.top_posts.length > 0 && (
            <section>
              <h2 className={SECTION_HEADING}>Top clips</h2>
              <div className="divide-y rounded-xl border bg-background">
                {performance.top_posts.map((post) => (
                  <div key={post.id} className="flex items-center gap-3 px-4 py-3 text-sm">
                    <SocialProviderIcon provider={post.provider} className="text-muted-foreground" />
                    <div className="min-w-0 flex-1">
                      {post.task_id ? (
                        <Link href={`/tasks/${post.task_id}`} className="block truncate font-medium hover:underline underline-offset-4">
                          {post.hook_title || post.title || "Untitled clip"}
                        </Link>
                      ) : (
                        <p className="truncate font-medium">{post.hook_title || post.title || "Untitled clip"}</p>
                      )}
                      <p className="truncate text-xs text-muted-foreground">
                        {post.hook_type ? `${post.hook_type} hook · ` : ""}
                        {formatDateTime(post.published_at)}
                      </p>
                    </div>
                    <span className="shrink-0 tabular-nums font-semibold">
                      {formatMetric(post.metrics?.views)} {post.metrics?.views === 1 ? "view" : "views"}
                    </span>
                    {post.external_url && (
                      <Button asChild size="icon" variant="ghost" className="size-8 text-muted-foreground">
                        <a href={post.external_url} target="_blank" rel="noreferrer" aria-label="Open post">
                          <ExternalLink className="size-4" />
                        </a>
                      </Button>
                    )}
                  </div>
                ))}
              </div>
            </section>
          )}

          {performance.by_provider.length > 0 && (
            <section>
              <h2 className={SECTION_HEADING}>By platform</h2>
              <div className="overflow-x-auto rounded-xl border bg-background">
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
                  <tbody className="tabular-nums">
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
            </section>
          )}

          {performance.by_hook_type.length > 0 && (
            <section>
              <h2 className={SECTION_HEADING}>By hook type</h2>
              <div className="overflow-x-auto rounded-xl border bg-background">
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
                  <tbody className="tabular-nums">
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
            </section>
          )}

          {performance.by_duration.length > 0 && (
            <section>
              <h2 className={SECTION_HEADING}>By clip length</h2>
              <div className={cn("grid gap-3", performance.by_duration.length > 2 ? "sm:grid-cols-4" : "sm:grid-cols-2")}>
                {performance.by_duration.map((row) => (
                  <div key={row.bucket} className="rounded-xl border bg-background p-4">
                    <p className="text-xs text-muted-foreground">{durationBucketLabel(row.bucket)}</p>
                    <p className="mt-1 text-2xl font-semibold tabular-nums">{formatMetric(row.avg_views)}</p>
                    <p className="text-xs text-muted-foreground">
                      avg views · {row.posts} {row.posts === 1 ? "post" : "posts"}
                    </p>
                  </div>
                ))}
              </div>
            </section>
          )}
        </div>
      )}
    </main>
  );
}
