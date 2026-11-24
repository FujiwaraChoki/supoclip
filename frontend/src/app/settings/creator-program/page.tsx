"use client";

import { useEffect, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
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
import { CreatorCountrySelect } from "@/components/settings/creator-country-select";
import { CreatorEarnings } from "@/components/settings/creator-earnings";
import { COMMISSION_HOLD_DAYS, COMMISSION_MONTHS, COMMISSION_PERCENT, isPayoutCountry } from "@/lib/creator-commission";
import { useCopyText } from "@/lib/copy-text";
import { appStoreRedeemUrl } from "@/lib/site";
import {
  creatorProgramKeys,
  useCreatorProgram,
  type CreatorApplication as Application,
  type CreatorProgramState,
} from "@/lib/queries/settings-creator";

const APPLY_PATH = "/settings/creator-program";

const PLATFORMS = ["tiktok", "youtube", "instagram", "x", "other"] as const;

const AUDIENCE_SIZES = ["under-1k", "1k-10k", "10k-100k", "100k-plus"] as const;

const TERM_KEYS = ["termCommission", "termSponsored", "termAds", "termOwn", "termTax", "termEnd"] as const;

const AUDIENCE_KEYS = {
  "under-1k": "under1k",
  "1k-10k": "from1k",
  "10k-100k": "from10k",
  "100k-plus": "from100k",
} as const;

type SlugState = { state: "idle" | "checking" } | { state: "available" } | { state: "unavailable"; reason: string };

function CopyField({ label, value, copyLabel }: { label: string; value: string; copyLabel: string }) {
  const copyText = useCopyText();
  const valueRef = useRef<HTMLElement>(null);
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    const outcome = await copyText(value, valueRef.current);
    if (outcome !== "copied") {
      setCopied(false);
      return;
    }
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };
  return (
    <div>
      <p className="mb-1.5 text-xs font-medium text-muted-foreground">{label}</p>
      <div className="flex items-center gap-2">
        <code ref={valueRef} tabIndex={-1} className="min-w-0 flex-1 select-text truncate rounded-lg border bg-muted/40 px-3 py-2 text-sm">{value}</code>
        <Button size="sm" variant="outline" onClick={copy} aria-label={copyLabel}>
          {copied ? <Check className="size-4" /> : <Copy className="size-4" />}
        </Button>
      </div>
    </div>
  );
}

/** Creators who applied before the form asked where they live set it once here. */
function CountryPrompt({ onSaved }: { onSaved: (country: string) => void }) {
  const t = useTranslations("settings");
  const [country, setCountry] = useState("");
  const save = useMutation({
    mutationFn: async () => {
      const response = await fetch("/api/affiliates/country", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ country }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.error || t("creator.countryPrompt.failed"));
      return data.country as string;
    },
    onSuccess: onSaved,
  });
  return (
    <section className="space-y-3 rounded-2xl border bg-background p-5">
      <h2 className="text-sm font-semibold">{t("creator.countryPrompt.title")}</h2>
      <p className="text-sm text-muted-foreground">{t("creator.countryPrompt.body")}</p>
      <div className="flex flex-col gap-2 sm:flex-row sm:items-end">
        <div className="sm:w-72">
          <CreatorCountrySelect value={country} onChange={setCountry} />
        </div>
        <Button onClick={() => save.mutate()} disabled={!country || save.isPending}>
          {save.isPending && <Loader2 className="size-4 animate-spin" />}
          {t("creator.countryPrompt.save")}
        </Button>
      </div>
      {save.isError && <p className="text-sm text-red-600">{save.error.message}</p>}
    </section>
  );
}

export default function CreatorProgramPage() {
  const t = useTranslations("settings");
  const { data: session, isPending } = useSession();
  const queryClient = useQueryClient();
  const userId = session?.user?.id;
  const program = useCreatorProgram(userId, t("creator.loadFailed"));
  const application = program.data?.application ?? null;
  const canApplyAt = program.data?.can_apply_at ?? null;
  const isLoading = program.isPending;
  const [submitError, setError] = useState<string | null>(null);
  const error = submitError ?? (program.isError ? program.error.message || t("creator.loadFailed") : null);

  const [slug, setSlug] = useState("");
  const [slugState, setSlugState] = useState<SlugState>({ state: "idle" });
  const [platform, setPlatform] = useState("");
  const [profileUrl, setProfileUrl] = useState("");
  const [audienceSize, setAudienceSize] = useState("");
  const [country, setCountry] = useState("");
  const [videoUrl, setVideoUrl] = useState("");
  const [promotionPlan, setPromotionPlan] = useState("");
  const [acceptTerms, setAcceptTerms] = useState(false);

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

  const apply = useMutation({
    mutationFn: async () => {
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
          country,
          accept_terms: acceptTerms,
        }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.error || t("creator.submitFailed"));
      return data.application as Application;
    },
    onMutate: () => setError(null),
    onSuccess: (submitted) => {
      queryClient.setQueryData<CreatorProgramState>(creatorProgramKeys.detail(userId ?? ""), {
        application: submitted,
        can_apply_at: null,
      });
    },
    onError: (err) => setError(err instanceof Error ? err.message : t("creator.submitFailed")),
  });
  const isSubmitting = apply.isPending;

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    apply.mutate();
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
  const canSubmit = slugState.state === "available" && platform && profileUrl.trim() && audienceSize && country && acceptTerms && !isSubmitting;
  const setSavedCountry = (saved: string) =>
    queryClient.setQueryData<CreatorProgramState>(creatorProgramKeys.detail(userId ?? ""), (state) =>
      state?.application ? { ...state, application: { ...state.application, country: saved } } : state,
    );

  return (
    <main className="mx-auto w-full max-w-3xl px-4 py-8 sm:px-8 md:py-10">
      <div className="mb-8">
        <h1 className="font-display text-3xl font-bold tracking-tight">{t("creator.title")}</h1>
        <p className="mt-2 text-muted-foreground">
          {t.rich("creator.intro", {
            percent: COMMISSION_PERCENT,
            months: COMMISSION_MONTHS,
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

      {application?.status === "approved" && application.slug && (
        <div className="mt-6">
          {application.country === null ? (
            <CountryPrompt onSaved={setSavedCountry} />
          ) : isPayoutCountry(application.country) ? (
            <CreatorEarnings />
          ) : (
            <section className="rounded-2xl border bg-background p-5">
              <h2 className="text-sm font-semibold">{t("creator.noCommission.title")}</h2>
              <p className="mt-2 text-sm text-muted-foreground">{t("creator.noCommission.body")}</p>
            </section>
          )}
        </div>
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
                <span className="text-green-700 dark:text-green-400">
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
            <Label htmlFor="creator-country">{t("creator.countryLabel")}</Label>
            <CreatorCountrySelect id="creator-country" value={country} onChange={setCountry} />
            <p className="mt-1.5 text-xs text-muted-foreground">{t("creator.countryHint")}</p>
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
              {TERM_KEYS.map((key) => (
                <li key={key}>{t(`creator.${key}`, { percent: COMMISSION_PERCENT, months: COMMISSION_MONTHS, days: COMMISSION_HOLD_DAYS })}</li>
              ))}
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
