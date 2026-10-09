"use client";

import { useEffect, useState } from "react";
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

const APPLY_PATH = "/settings/creator-program";

const PLATFORMS = [
  { value: "tiktok", label: "TikTok" },
  { value: "youtube", label: "YouTube" },
  { value: "instagram", label: "Instagram" },
  { value: "x", label: "X" },
  { value: "other", label: "Other" },
];

const AUDIENCE_SIZES = [
  { value: "under-1k", label: "Under 1,000" },
  { value: "1k-10k", label: "1,000 – 10,000" },
  { value: "10k-100k", label: "10,000 – 100,000" },
  { value: "100k-plus", label: "100,000+" },
];

const TERMS = [
  "Mark sponsored posts as such (for example #ad).",
  "Don't run ads on the SupoClip name or post your code on coupon or deal sites.",
  "Don't use your own code.",
  "We can end the program or remove a creator at any time.",
];

interface Application {
  slug: string | null;
  status: "pending" | "approved" | "declined" | "revoked";
  decline_reason: string | null;
}

type SlugState = { state: "idle" | "checking" } | { state: "available" } | { state: "unavailable"; reason: string };

function CopyField({ label, value }: { label: string; value: string }) {
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
        <Button size="sm" variant="outline" onClick={copy} aria-label={`Copy ${label.toLowerCase()}`}>
          {copied ? <Check className="size-4" /> : <Copy className="size-4" />}
        </Button>
      </div>
    </div>
  );
}

export default function CreatorProgramPage() {
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
        if (!response.ok) throw new Error("Failed to load your application");
        const data = await response.json();
        setApplication(data.application);
        setCanApplyAt(data.can_apply_at);
      })
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load your application"))
      .finally(() => setIsLoading(false));
  }, [session?.user?.id]);

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
        setSlugState(data.available ? { state: "available" } : { state: "unavailable", reason: data.reason || "Not available" });
      } catch {
        if (!controller.signal.aborted) setSlugState({ state: "idle" });
      }
    }, 400);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [slug]);

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
      if (!response.ok) throw new Error(data.error || "Failed to submit your application");
      setApplication(data.application);
      setCanApplyAt(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to submit your application");
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
        <p className="mb-4 text-muted-foreground">Create a free account or sign in to apply to the creator program.</p>
        <div className="flex justify-center gap-2">
          <Link href={`/sign-up?next=${encodeURIComponent(APPLY_PATH)}`}><Button>Create account</Button></Link>
          <Link href={`/sign-in?next=${encodeURIComponent(APPLY_PATH)}`}><Button variant="outline">Sign in</Button></Link>
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
        <h1 className="font-display text-3xl font-bold tracking-tight">Creator program</h1>
        <p className="mt-2 text-muted-foreground">
          Making videos about SupoClip? Get your own code: your audience gets their{" "}
          <span className="font-medium text-foreground">first month free, then 20% off forever</span>, and you get SupoClip Pro for free.{" "}
          <Link href="/affiliate" className="underline underline-offset-4 hover:text-foreground">How it works</Link>
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
            <h2 className="text-sm font-semibold">You&apos;re in</h2>
            <Badge variant="outline">Approved</Badge>
          </div>
          <p className="text-sm text-muted-foreground">Share your link or code. Your account has Pro for free while you&apos;re in the program.</p>
          <CopyField label="Your link" value={`${origin}/?ref=${application.slug}`} />
          <CopyField label="Your code" value={application.slug.toUpperCase()} />
        </section>
      )}

      {application?.status === "pending" && (
        <section className="rounded-2xl border bg-background p-5">
          <div className="flex items-center gap-2">
            <h2 className="text-sm font-semibold">Application received</h2>
            <Badge>Under review</Badge>
          </div>
          <p className="mt-2 text-sm text-muted-foreground">
            We&apos;re reviewing your application for the code <span className="font-mono text-foreground">{application.slug?.toUpperCase()}</span>. You&apos;ll get an email either way.
          </p>
        </section>
      )}

      {application?.status === "revoked" && (
        <section className="rounded-2xl border bg-background p-5">
          <h2 className="text-sm font-semibold">Your creator code is no longer active</h2>
          <p className="mt-2 text-sm text-muted-foreground">Contact us if you think this is a mistake.</p>
        </section>
      )}

      {application?.status === "declined" && !canApply && (
        <section className="rounded-2xl border bg-background p-5">
          <h2 className="text-sm font-semibold">We couldn&apos;t approve your application this time</h2>
          {application.decline_reason && <p className="mt-2 text-sm text-muted-foreground">{application.decline_reason}</p>}
          {canApplyAt && (
            <p className="mt-2 text-sm text-muted-foreground">You can apply again on {new Date(canApplyAt).toLocaleDateString()}.</p>
          )}
        </section>
      )}

      {canApply && (
        <form onSubmit={submit} className="space-y-5 rounded-2xl border bg-background p-5">
          <div>
            <Label htmlFor="slug">Your code</Label>
            <Input
              id="slug"
              value={slug}
              onChange={(event) => setSlug(event.target.value.replace(/\s+/g, ""))}
              placeholder="maya"
              maxLength={20}
              autoComplete="off"
              className="mt-1.5 font-mono"
            />
            <p className="mt-1.5 flex items-center gap-1.5 text-xs text-muted-foreground">
              {slugState.state === "checking" && <><Loader2 className="size-3 animate-spin" /> Checking…</>}
              {slugState.state === "available" && (
                <span className="text-green-700">
                  Available. Your link will be {origin}/?ref={slug.trim().toLowerCase()} and your code {slug.trim().toUpperCase()}.
                </span>
              )}
              {slugState.state === "unavailable" && <span className="text-red-600">{slugState.reason}</span>}
              {slugState.state === "idle" && "3–20 letters, numbers or hyphens."}
            </p>
          </div>

          <div className="grid gap-5 sm:grid-cols-2">
            <div>
              <Label>Where you post</Label>
              <Select value={platform} onValueChange={setPlatform}>
                <SelectTrigger className="mt-1.5 w-full"><SelectValue placeholder="Pick a platform" /></SelectTrigger>
                <SelectContent>
                  {PLATFORMS.map((option) => <SelectItem key={option.value} value={option.value}>{option.label}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
            <div>
              <Label>Followers</Label>
              <Select value={audienceSize} onValueChange={setAudienceSize}>
                <SelectTrigger className="mt-1.5 w-full"><SelectValue placeholder="Pick a range" /></SelectTrigger>
                <SelectContent>
                  {AUDIENCE_SIZES.map((option) => <SelectItem key={option.value} value={option.value}>{option.label}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
          </div>

          <div>
            <Label htmlFor="profile-url">Profile link</Label>
            <Input id="profile-url" type="url" value={profileUrl} onChange={(event) => setProfileUrl(event.target.value)} placeholder="https://www.tiktok.com/@you" className="mt-1.5" />
          </div>

          <div>
            <Label htmlFor="video-url">A video you made about SupoClip <span className="font-normal text-muted-foreground">(optional)</span></Label>
            <Input id="video-url" type="url" value={videoUrl} onChange={(event) => setVideoUrl(event.target.value)} placeholder="https://" className="mt-1.5" />
          </div>

          <div>
            <Label htmlFor="plan">How will you promote SupoClip? <span className="font-normal text-muted-foreground">(optional)</span></Label>
            <Textarea id="plan" value={promotionPlan} onChange={(event) => setPromotionPlan(event.target.value)} maxLength={1000} rows={3} className="mt-1.5" />
          </div>

          <div className="rounded-xl bg-muted/40 p-4">
            <p className="text-xs font-medium">Program terms</p>
            <ul className="mt-2 list-disc space-y-1 pl-4 text-xs text-muted-foreground">
              {TERMS.map((term) => <li key={term}>{term}</li>)}
            </ul>
            <label className="mt-3 flex items-center gap-2 text-sm">
              <Checkbox checked={acceptTerms} onCheckedChange={(checked) => setAcceptTerms(checked === true)} />
              I agree to the program terms
            </label>
          </div>

          <Button type="submit" disabled={!canSubmit}>
            {isSubmitting ? "Submitting…" : "Apply"}
          </Button>
        </form>
      )}
    </main>
  );
}
