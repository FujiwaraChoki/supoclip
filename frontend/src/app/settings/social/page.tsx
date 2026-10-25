"use client";

import { Suspense, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { useRouter, useSearchParams } from "next/navigation";
import {
  AlertCircle,
  CheckCircle,
  ChevronRight,
  Info,
  RefreshCw,
  Trash2,
} from "lucide-react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { ScheduledPosts } from "@/components/scheduled-posts";
import { SocialProviderIcon } from "@/components/social-provider-icon";
import { isSocialPublishingEnabled } from "@/hooks/use-social-access";
import { useSession } from "@/lib/auth-client";
import {
  PROVIDER_LABELS,
  connectionLabel,
  formatDateTime,
  type SocialConnection,
  type SocialProviderStatus,
} from "@/lib/social";

const SECTION_HEADING = "mb-3 text-sm font-medium uppercase tracking-wide text-muted-foreground";

function ConnectionStatusBadge({ status }: { status: SocialConnection["status"] }) {
  const t = useTranslations("social");
  if (status === "active") {
    return <Badge variant="secondary">{t("page.connected")}</Badge>;
  }
  if (status === "reauth_required") {
    return <Badge variant="destructive">{t("page.reconnectNeeded")}</Badge>;
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
  const t = useTranslations("social");
  const { data: session, isPending } = useSession();
  const router = useRouter();
  const searchParams = useSearchParams();
  const [providers, setProviders] = useState<SocialProviderStatus[]>([]);
  const [enabled, setEnabled] = useState<boolean | null>(null);
  const [connections, setConnections] = useState<SocialConnection[]>([]);
  const [isFetching, setIsFetching] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [disconnectingId, setDisconnectingId] = useState<string | null>(null);

  const load = useCallback(async () => {
    setIsFetching(true);
    setError(null);
    try {
      const [providersResponse, connectionsResponse] = await Promise.all([
        fetch("/api/social/providers", { cache: "no-store" }),
        fetch("/api/social/connections", { cache: "no-store" }),
      ]);
      if (!providersResponse.ok) {
        throw new Error(t("page.loadError"));
      }
      const providersData = (await providersResponse.json()) as {
        enabled?: boolean;
        providers: SocialProviderStatus[];
      };
      setEnabled(isSocialPublishingEnabled(providersData));
      const connectionsData = connectionsResponse.ok
        ? ((await connectionsResponse.json()) as { connections: SocialConnection[] })
        : { connections: [] };
      setProviders(providersData.providers ?? []);
      setConnections(connectionsData.connections ?? []);
    } catch (err) {
      setError(err instanceof Error ? err.message : t("page.loadError"));
    } finally {
      setIsFetching(false);
    }
  }, [t]);

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
      setNotice(account ? t("page.connectedAs", { label, account }) : t("page.connectedNotice", { label }));
    }
    if (oauthError) {
      setError(oauthError);
    }
    router.replace("/settings/social");
  }, [searchParams, router, t]);

  const handleDisconnect = async (connection: SocialConnection) => {
    if (!window.confirm(t("page.disconnectConfirm", { account: connectionLabel(connection) }))) {
      return;
    }
    setDisconnectingId(connection.id);
    setError(null);
    try {
      const response = await fetch(`/api/social/connections/${connection.id}`, { method: "DELETE" });
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(data?.detail || t("page.disconnectError"));
      }
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : t("page.disconnectError"));
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
        <p className="mb-4 text-muted-foreground">{t("page.signInBody")}</p>
        <Link href="/sign-in">
          <Button>{t("page.signIn")}</Button>
        </Link>
      </main>
    );
  }

  if (isFetching && enabled === null) {
    return <PageSkeleton />;
  }

  if (enabled !== true) {
    return (
      <main className="mx-auto w-full max-w-3xl px-4 py-8 sm:px-8 md:py-10">
        <h1 className="font-display text-3xl font-bold tracking-tight">{t("page.title")}</h1>
        <p className="mt-2 text-muted-foreground">
          {error ?? t("page.unavailable")}
        </p>
      </main>
    );
  }

  const providerGroups = [
    providers.filter((provider) => provider.configured),
    providers.filter((provider) => !provider.configured),
  ].filter((group) => group.length > 0);

  return (
    <main className="mx-auto w-full max-w-3xl px-4 py-8 sm:px-8 md:py-10">
      <div className="mb-8">
        <h1 className="font-display text-3xl font-bold tracking-tight">{t("page.title")}</h1>
        <p className="mt-2 text-muted-foreground">
          {t("page.intro")}
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
        <h2 className={SECTION_HEADING}>{t("page.connectHeading")}</h2>
        {isFetching ? (
          <Skeleton className="h-20 w-full" />
        ) : (
          <div className="rounded-xl bg-background">
            {providerGroups.map((group) => (
              <div
                key={group[0].configured ? "available" : "upcoming"}
                className={`relative first:rounded-t-xl last:rounded-b-xl [&>div+div]:shadow-[inset_0_1px_0_var(--border)] ${group[0].configured ? "shadow-[inset_0_0_0_1px_var(--border)]" : "bg-muted"}`}
              >
                {!group[0].configured && (
                  <span
                    aria-hidden="true"
                    className="pointer-events-none absolute inset-0 z-10 rounded-[inherit] p-0.5"
                    style={{
                      background: "repeating-linear-gradient(135deg, #fbbf24 0px, #fbbf24 4px, #27272a 4px, #27272a 8px)",
                      mask: "linear-gradient(#fff 0 0) content-box, linear-gradient(#fff 0 0)",
                      maskComposite: "exclude",
                    }}
                  />
                )}
                {group.map((provider) => (
                  <div
                    key={provider.provider}
                    className={`flex items-center gap-3 p-3 ${provider.configured ? "" : "text-muted-foreground"}`}
                  >
                    <span className="flex size-8 shrink-0 items-center justify-center rounded-lg border bg-muted/30">
                      <SocialProviderIcon provider={provider.provider} className="size-5" />
                    </span>
                    <div className="min-w-0 flex-1 self-center">
                      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
                        <p className="text-sm font-medium">{provider.display_name}</p>
                        {!provider.configured && (
                          <Badge variant="secondary" className="font-normal text-muted-foreground">
                            {t("page.comingSoon")}
                          </Badge>
                        )}
                      </div>
                    </div>
                    <div className="flex shrink-0 items-center gap-2">
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <button
                            type="button"
                            aria-label={t("page.platformDetails", { platform: provider.display_name })}
                            className="inline-flex size-7 items-center justify-center rounded-full text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                          >
                            <Info className="size-3.5" aria-hidden="true" />
                          </button>
                        </TooltipTrigger>
                        <TooltipContent className="max-w-xs space-y-2" sideOffset={6}>
                          {!provider.configured && <p>{t("page.verificationNote")}</p>}
                          <p>{t(`page.${provider.provider}Note`)}</p>
                        </TooltipContent>
                      </Tooltip>
                      {provider.configured && (
                        <Button asChild size="sm" variant="outline">
                          <a href={`/api/social/connect/${provider.provider}`}>
                            {t("page.connect")}
                            <ChevronRight className="size-4" />
                          </a>
                        </Button>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            ))}
          </div>
        )}
      </section>

      {/* Connected */}
      <section className="mb-10">
        <h2 className={SECTION_HEADING}>{t("page.connectedHeading")}</h2>
        {isFetching ? (
          <Skeleton className="h-20 w-full" />
        ) : connections.length === 0 ? (
          <p className="text-sm text-muted-foreground">{t("page.none")}</p>
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
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <button
                            type="button"
                            aria-label={t("page.connectionDetails")}
                            className="inline-flex size-6 items-center justify-center rounded-full text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                          >
                            <Info className="size-3.5" aria-hidden="true" />
                          </button>
                        </TooltipTrigger>
                        <TooltipContent className="max-w-xs" sideOffset={6}>
                          {t("page.connectedOn", { date: formatDateTime(connection.created_at) })}
                        </TooltipContent>
                      </Tooltip>
                    </div>
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
                        {t("page.reconnect")}
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
                    {t("page.disconnect")}
                  </Button>
                </div>
              </div>
            ))}
          </div>
        )}
      </section>

      {/* Scheduled */}
      {!isFetching && connections.length > 0 && <ScheduledPosts sectionHeadingClassName={SECTION_HEADING} />}

      <Link
        href="/analytics"
        className="group flex items-center justify-between gap-4 rounded-xl border bg-background p-4 text-sm transition-colors hover:border-foreground/25"
      >
        <span>
          <span className="block font-medium">{t("page.analytics")}</span>
          <span className="block text-xs text-muted-foreground">
            {t("page.analyticsHint")}
          </span>
        </span>
        <ChevronRight className="size-4 text-muted-foreground transition-transform group-hover:translate-x-0.5" />
      </Link>
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
