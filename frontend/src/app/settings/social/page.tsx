"use client";

import { Suspense, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { useRouter, useSearchParams } from "next/navigation";
import {
  AlertCircle,
  CheckCircle,
  ChevronRight,
  RefreshCw,
  Trash2,
} from "lucide-react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
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

  const configuredProviders = providers.filter((provider) => provider.configured);
  const unconfiguredProviders = providers.filter((provider) => !provider.configured);

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
                <span className="text-xs text-muted-foreground">{t("page.connect")}</span>
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
                <p className="text-xs mt-1">{t("page.notConfigured")}</p>
                {provider.setup_hint && <p className="text-xs mt-1">{provider.setup_hint}</p>}
              </div>
            ))}
          </div>
        )}
        <p className="text-xs text-muted-foreground mt-3">
          {t("page.instagramNote")}
        </p>
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
                    </div>
                    <p className="text-xs text-muted-foreground mt-1">
                      {t("page.connectedOn", { date: formatDateTime(connection.created_at) })}
                      {connection.token_expires_at ? t("page.tokenRenews", { date: formatDateTime(connection.token_expires_at) }) : ""}
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
