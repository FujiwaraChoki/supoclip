"use client";

import { useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { AlertCircle, Check, Copy, Loader2 } from "lucide-react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { useSession } from "@/lib/auth-client";
import { appStoreRedeemUrl } from "@/lib/site";

const APPLY_PATH = "/settings/creator-program";

const PLATFORMS = ["tiktok", "youtube", "instagram", "x", "other"] as const;

const AUDIENCE_SIZES = ["under-1k", "1k-10k", "10k-100k", "100k-plus"] as const;

const TERM_KEYS = ["termSponsored", "termAds", "termOwn", "termEnd"] as const;

const AUDIENCE_KEYS = {
  "under-1k": "under1k",
  "1k-10k": "from1k",
  "10k-100k": "from10k",
  "100k-plus": "from100k",
} as const;

interface Application {
  slug: string | null;
  status: "pending" | "approved" | "declined" | "revoked";
  decline_reason: string | null;
  app_store_code_added_at: string | null;
}

type SlugState = { state: "idle" | "checking" } | { state: "available" } | { state: "unavailable"; reason: string };

function CopyField({ label, value, copyLabel }: { label: string; value: string; copyLabel: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    await navigator.clipboard.writeText(value);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };
  return (
    <div>
      <p className="mb-1.5 text-xs font-medium text-muted-foreground">{label}</p>
      <div className="flex items-center gap-2">
        <code className="min-w-0 flex-1 truncate rounded-lg border bg-muted/40 px-3 py-2 text-sm">{value}</code>
        <Button size="sm" variant="outline" onClick={copy} aria-label={copyLabel}>
          {copied ? <Check className="size-4" /> : <Copy className="size-4" />}
        </Button>
      </div>
    </div>
  );
}

export default function CreatorProgramPage() {
  const t = useTranslations("settings");
  const { data: session, isPending } = useSession();
  const [application, setApplication] = useState<Application | null>(null);
  const [canApplyAt, setCanApplyAt] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [slug, setSlug] = useState("");
  const [slugState, setSlugState] = useState<SlugState>({ state: "idle" });
  const [platform, setPlatform] = useState("");
  const [profileUrl, setProfileUrl] = useState("");
  const [audienceSize, setAudienceSize] = useState("");
  const [videoUrl, setVideoUrl] = useState("");
  const [promotionPlan, setPromotionPlan] = useState("");
  const [acceptTerms, setAcceptTerms] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);

  useEffect(() => {
    if (!session?.user?.id) return;
    fetch("/api/affiliates", { cache: "no-store" })
      .then(async (response) => {
        if (!response.ok) throw new Error(t("creator.loadFailed"));
        const data = await response.json();
        setApplication(data.application);
        setCanApplyAt(data.can_apply_at);
      })
      .catch((err) => setError(err instanceof Error ? err.message : t("creator.loadFailed")))
      .finally(() => setIsLoading(false));
  }, [session?.user?.id, t]);

  useEffect(() => {
    const value = slug.trim();
    if (!value) {
      setSlugState({ state: "idle" });
      return;
    }
    setSlugState({ state: "checking" });
    const controller = new AbortController();
    const timer = setTimeout(async () => {
      try {
        const response = await fetch(`/api/affiliates/slug?slug=${encodeURIComponent(value)}`, { signal: controller.signal });
        const data = await response.json();
        setSlugState(data.available ? { state: "available" } : { state: "unavailable", reason: data.reason || t("creator.notAvailable") });
      } catch {
        if (!controller.signal.aborted) setSlugState({ state: "idle" });
      }
    }, 400);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [slug, t]);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setIsSubmitting(true);
    setError(null);
    try {
      const response = await fetch("/api/affiliates", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          slug,
          platform,
          profile_url: profileUrl,
          audience_size: audienceSize,
          video_url: videoUrl,
          promotion_plan: promotionPlan,
          accept_terms: acceptTerms,
        }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.error || t("creator.submitFailed"));
      setApplication(data.application);
      setCanApplyAt(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : t("creator.submitFailed"));
    } finally {
      setIsSubmitting(false);
    }
  };

  if (isPending || (session?.user && isLoading)) {
    return (
      <main className="mx-auto w-full max-w-3xl px-4 py-8 sm:px-8 md:py-10">
        <Skeleton className="h-64 w-full" />
      </main>
    );
  }

  if (!session?.user) {
    return (
      <main className="mx-auto max-w-md px-4 py-24 text-center">
        <p className="mb-4 text-muted-foreground">{t("creator.signIn")}</p>
        <div className="flex justify-center gap-2">
          <Link href={`/sign-up?next=${encodeURIComponent(APPLY_PATH)}`}><Button>{t("creator.createAccount")}</Button></Link>
          <Link href={`/sign-in?next=${encodeURIComponent(APPLY_PATH)}`}><Button variant="outline">{t("creator.signInAction")}</Button></Link>
        </div>
      </main>
    );
  }

  const canApply = canApplyAt !== null && new Date(canApplyAt).getTime() <= Date.now();
  const origin = typeof window === "undefined" ? "" : window.location.origin;
  const canSubmit = slugState.state === "available" && platform && profileUrl.trim() && audienceSize && acceptTerms && !isSubmitting;

  return (
    <main className="mx-auto w-full max-w-3xl px-4 py-8 sm:px-8 md:py-10">
      <div className="mb-8">
        <h1 className="font-display text-3xl font-bold tracking-tight">{t("creator.title")}</h1>
        <p className="mt-2 text-muted-foreground">
          {t.rich("creator.intro", {
            em: (chunks) => <span className="font-medium text-foreground">{chunks}</span>,
          })}{" "}
          <Link href="/affiliate" className="underline underline-offset-4 hover:text-foreground">{t("creator.howItWorks")}</Link>
        </p>
      </div>

      {error && (
        <Alert variant="destructive" className="mb-6">
          <AlertCircle className="h-4 w-4" />
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      {application?.status === "approved" && application.slug && (
        <section className="space-y-4 rounded-2xl border bg-background p-5">
          <div className="flex items-center gap-2">
            <h2 className="text-sm font-semibold">{t("creator.approvedTitle")}</h2>
            <Badge variant="outline">{t("creator.approvedBadge")}</Badge>
          </div>
          <p className="text-sm text-muted-foreground">{t("creator.approvedBody")}</p>
          <CopyField label={t("creator.yourLink")} copyLabel={t("creator.copyLink")} value={`${origin}/?ref=${application.slug}`} />
          <CopyField label={t("creator.yourCode")} copyLabel={t("creator.copyCode")} value={application.slug.toUpperCase()} />
          {application.app_store_code_added_at ? (
            <CopyField label={t("creator.iphoneLink")} copyLabel={t("creator.copyIphone")} value={appStoreRedeemUrl(application.slug)} />
          ) : (
            <p className="text-xs text-muted-foreground">{t("creator.iphonePending")}</p>
          )}
        </section>
      )}

      {application?.status === "pending" && (
        <section className="rounded-2xl border bg-background p-5">
          <div className="flex items-center gap-2">
            <h2 className="text-sm font-semibold">{t("creator.pendingTitle")}</h2>
            <Badge>{t("creator.pendingBadge")}</Badge>
          </div>
          <p className="mt-2 text-sm text-muted-foreground">
            {t.rich("creator.pendingBody", {
              code: application.slug?.toUpperCase() ?? "",
              mono: (chunks) => <span className="font-mono text-foreground">{chunks}</span>,
            })}
          </p>
        </section>
      )}

      {application?.status === "revoked" && (
        <section className="rounded-2xl border bg-background p-5">
          <h2 className="text-sm font-semibold">{t("creator.revokedTitle")}</h2>
          <p className="mt-2 text-sm text-muted-foreground">{t("creator.revokedBody")}</p>
        </section>
      )}

      {application?.status === "declined" && !canApply && (
        <section className="rounded-2xl border bg-background p-5">
          <h2 className="text-sm font-semibold">{t("creator.declinedTitle")}</h2>
          {application.decline_reason && <p className="mt-2 text-sm text-muted-foreground">{application.decline_reason}</p>}
          {canApplyAt && (
            <p className="mt-2 text-sm text-muted-foreground">{t("creator.applyAgain", { date: new Date(canApplyAt).toLocaleDateString() })}</p>
          )}
        </section>
      )}

      {canApply && (
        <form onSubmit={submit} className="space-y-5 rounded-2xl border bg-background p-5">
          <div>
            <Label htmlFor="slug">{t("creator.codeLabel")}</Label>
            <Input
              id="slug"
              value={slug}
              onChange={(event) => setSlug(event.target.value.replace(/\s+/g, ""))}
              placeholder={t("creator.codePlaceholder")}
              maxLength={20}
              autoComplete="off"
              className="mt-1.5 font-mono"
            />
            <p className="mt-1.5 flex items-center gap-1.5 text-xs text-muted-foreground">
              {slugState.state === "checking" && <><Loader2 className="size-3 animate-spin" /> {t("creator.checking")}</>}
              {slugState.state === "available" && (
                <span className="text-green-700">
                  {t("creator.available", { origin, slug: slug.trim().toLowerCase(), code: slug.trim().toUpperCase() })}
                </span>
              )}
              {slugState.state === "unavailable" && <span className="text-red-600">{slugState.reason}</span>}
              {slugState.state === "idle" && t("creator.codeHint")}
            </p>
          </div>

          <div className="grid gap-5 sm:grid-cols-2">
            <div>
              <Label>{t("creator.platformLabel")}</Label>
              <Select value={platform} onValueChange={setPlatform}>
                <SelectTrigger className="mt-1.5 w-full"><SelectValue placeholder={t("creator.platformPlaceholder")} /></SelectTrigger>
                <SelectContent>
                  {PLATFORMS.map((value) => <SelectItem key={value} value={value}>{t(`creator.platforms.${value}`)}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
            <div>
              <Label>{t("creator.followersLabel")}</Label>
              <Select value={audienceSize} onValueChange={setAudienceSize}>
                <SelectTrigger className="mt-1.5 w-full"><SelectValue placeholder={t("creator.followersPlaceholder")} /></SelectTrigger>
                <SelectContent>
                  {AUDIENCE_SIZES.map((value) => <SelectItem key={value} value={value}>{t(`creator.audience.${AUDIENCE_KEYS[value]}`)}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
          </div>

          <div>
            <Label htmlFor="profile-url">{t("creator.profileLabel")}</Label>
            <Input id="profile-url" type="url" value={profileUrl} onChange={(event) => setProfileUrl(event.target.value)} placeholder={t("creator.profilePlaceholder")} className="mt-1.5" />
          </div>

          <div>
            <Label htmlFor="video-url">{t("creator.videoLabel")} <span className="font-normal text-muted-foreground">{t("creator.optional")}</span></Label>
            <Input id="video-url" type="url" value={videoUrl} onChange={(event) => setVideoUrl(event.target.value)} placeholder={t("creator.videoPlaceholder")} className="mt-1.5" />
          </div>

          <div>
            <Label htmlFor="plan">{t("creator.planLabel")} <span className="font-normal text-muted-foreground">{t("creator.optional")}</span></Label>
            <Textarea id="plan" value={promotionPlan} onChange={(event) => setPromotionPlan(event.target.value)} maxLength={1000} rows={3} className="mt-1.5" />
          </div>

          <div className="rounded-xl bg-muted/40 p-4">
            <p className="text-xs font-medium">{t("creator.termsTitle")}</p>
            <ul className="mt-2 list-disc space-y-1 pl-4 text-xs text-muted-foreground">
              {TERM_KEYS.map((key) => <li key={key}>{t(`creator.${key}`)}</li>)}
            </ul>
            <label className="mt-3 flex items-center gap-2 text-sm">
              <Checkbox checked={acceptTerms} onCheckedChange={(checked) => setAcceptTerms(checked === true)} />
              {t("creator.agree")}
            </label>
          </div>

          <Button type="submit" disabled={!canSubmit}>
            {isSubmitting ? t("creator.submitting") : t("creator.submit")}
          </Button>
        </form>
      )}
    </main>
  );
}
