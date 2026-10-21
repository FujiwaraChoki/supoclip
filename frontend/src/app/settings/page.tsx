"use client";

import { useState, useEffect } from "react";
import { useTranslations } from "next-intl";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { SubscriptionCancelBanner } from "@/components/subscription-cancel-banner";
import { DeleteAccountSection } from "@/components/delete-account-section";
import { Skeleton } from "@/components/ui/skeleton";
import { Select, SelectContent, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Slider } from "@/components/ui/slider";
import { Switch } from "@/components/ui/switch";
import { useSession } from "@/lib/auth-client";
import { useSocialAccess } from "@/hooks/use-social-access";
import {
  annualMonthlyEquivalent,
  annualMonthsFree,
  getPublicBillingPlans,
  isPaidBillingPlan,
  planPrice,
  type BillingInterval,
  type BillingPlanId,
} from "@/lib/billing-plans";
import { LanguageSwitcher } from "@/components/language-switcher";
import { BillingIntervalToggle, ComingSoonTag, UpgradeNudge, getUpgradeState, useBillingPlanText } from "@/components/billing/upgrade-prompt";
import type { BillingSummary } from "@/hooks/use-billing-summary";
import { startUpgrade } from "@/lib/start-upgrade";
import { CreatorCodeField } from "@/components/billing/creator-code-field";
import { toast } from "sonner";
import { track } from "@/lib/datafast";
import Link from "next/link";
import { AlertCircle, Bot, Check, ChevronRight, Clock, CreditCard, Languages, Loader2, Mail, Megaphone, Share2, Type } from "lucide-react";
import { cn } from "@/lib/utils";
import { FontSelectOption, type FontOption } from "@/components/font-select-option";

interface UserPreferences {
  fontFamily: string;
  fontSize: number;
  fontColor: string;
  notifyOnCompletion: boolean;
}

export default function SettingsPage() {
  const [fontFamily, setFontFamily] = useState("TikTokSans-Regular");
  const [fontSize, setFontSize] = useState(24);
  const [fontColor, setFontColor] = useState("#FFFFFF");
  const [completionEmails, setCompletionEmails] = useState(true);
  const [availableFonts, setAvailableFonts] = useState<FontOption[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [isFetching, setIsFetching] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);
  const [billingSummary, setBillingSummary] = useState<BillingSummary | null>(null);
  const [savedSnapshot, setSavedSnapshot] = useState<string | null>(null);
  const [isBillingActionLoading, setIsBillingActionLoading] = useState(false);
  const [billingInterval, setBillingInterval] = useState<BillingInterval>("month");
  const { data: session, isPending } = useSession();
  const socialAccess = useSocialAccess(Boolean(session?.user));
  const t = useTranslations("settings");
  const common = useTranslations("common");
  const tLang = useTranslations("language");
  const plans = useBillingPlanText();

  const paidPlans = getPublicBillingPlans();
  const upgradeState = getUpgradeState(billingSummary);
  const currentPaidPlan = paidPlans.find((plan) => plan.id === billingSummary?.plan);

  // Stripe sends people back here after checkout or a plan change.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const outcome = params.get("billing");
    if (outcome === "success" || outcome === "upgraded") {
      toast.success(outcome === "upgraded" ? t("toasts.upgraded") : t("toasts.ready"));
    }
    if (outcome) {
      params.delete("billing");
      const query = params.toString();
      window.history.replaceState(null, "", `${window.location.pathname}${query ? `?${query}` : ""}${window.location.hash}`);
    }
  }, [t]);

  const handlePlanChange = async (plan: BillingPlanId, interval: BillingInterval) => {
    setIsBillingActionLoading(true);
    try {
      await startUpgrade(plan, "settings", interval);
    } catch (upgradeError) {
      toast.error(upgradeError instanceof Error ? upgradeError.message : plans.t("checkoutError"));
      setIsBillingActionLoading(false);
    }
  };

  // Load available fonts from backend and inject them into the page
  useEffect(() => {
    const loadFonts = async () => {
      try {
        const response = await fetch('/api/fonts', { cache: 'no-store' });
        if (response.ok) {
          const data = await response.json();
          setAvailableFonts(data.fonts || []);

          // Dynamically load fonts using @font-face
          const fontFaceStyles = data.fonts.map((font: { name: string }) => {
            return `
              @font-face {
                font-family: '${font.name}';
                src: url('/api/fonts/${font.name}') format('truetype');
                font-weight: normal;
                font-style: normal;
              }
            `;
          }).join('\n');

          // Inject font styles into the page
          const styleElement = document.createElement('style');
          styleElement.id = 'custom-fonts';
          styleElement.innerHTML = fontFaceStyles;

          // Remove existing custom fonts style if present
          const existingStyle = document.getElementById('custom-fonts');
          if (existingStyle) {
            existingStyle.remove();
          }

          document.head.appendChild(styleElement);
        }
      } catch (error) {
        console.error('Failed to load fonts:', error);
      }
    };

    loadFonts();
  }, []);

  // Load user preferences
  useEffect(() => {
    const loadPreferences = async () => {
      if (!session?.user?.id) return;

      setIsFetching(true);
      try {
        const response = await fetch('/api/preferences');
        if (response.ok) {
          const data: UserPreferences = await response.json();
          setFontFamily(data.fontFamily);
          setFontSize(data.fontSize);
          setFontColor(data.fontColor);
          setCompletionEmails(data.notifyOnCompletion ?? true);
          setSavedSnapshot(JSON.stringify([data.fontFamily, data.fontSize, data.fontColor, data.notifyOnCompletion ?? true]));
        }
      } catch (error) {
        console.error('Failed to load preferences:', error);
      } finally {
        setIsFetching(false);
      }
    };

    loadPreferences();
  }, [session?.user?.id]);

  useEffect(() => {
    const fetchBillingSummary = async () => {
      if (!session?.user?.id) return;

      try {
        const response = await fetch("/api/tasks/billing-summary", {
          cache: "no-store",
        });

        if (!response.ok) {
          return;
        }

        const data: BillingSummary = await response.json();
        setBillingSummary(data);
      } catch (fetchError) {
        console.error("Failed to fetch billing summary:", fetchError);
      }
    };

    fetchBillingSummary();
  }, [session?.user?.id]);

  const handleBillingAction = async (selectedPlan?: BillingPlanId) => {
    if (!billingSummary?.monetization_enabled) return;

    const isPaid = isPaidBillingPlan(billingSummary.plan);

    if (!isPaid) {
      // Checkout goes through startUpgrade so a remembered creator code is applied.
      try {
        setIsBillingActionLoading(true);
        track("billing_checkout_started", { plan: billingSummary.plan, selected_plan: selectedPlan });
        await startUpgrade(selectedPlan ?? "pro", "settings", billingInterval);
      } catch (billingError) {
        setError(billingError instanceof Error ? billingError.message : t("plan.actionFailed"));
        setIsBillingActionLoading(false);
      }
      return;
    }

    try {
      setIsBillingActionLoading(true);
      const response = await fetch("/api/billing/portal", { method: "POST" });
      const responseText = await response.text();
      let data: { url?: string; error?: string } = {};
      if (responseText) {
        try {
          data = JSON.parse(responseText);
        } catch {
          data = { error: responseText };
        }
      }

      if (!response.ok || !data.url) {
        throw new Error(data.error || t("plan.actionFailed"));
      }

      track("billing_portal_opened", { plan: billingSummary.plan });
      window.location.href = data.url;
    } catch (billingError) {
      setError(billingError instanceof Error ? billingError.message : t("plan.actionFailed"));
    } finally {
      setIsBillingActionLoading(false);
    }
  };

  const handleSavePreferences = async () => {
    setIsLoading(true);
    setError(null);
    setSuccess(false);

    try {
      const response = await fetch('/api/preferences', {
        method: 'PATCH',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          fontFamily,
          fontSize,
          fontColor,
          notifyOnCompletion: completionEmails,
        }),
      });

      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.error || t("save.failed"));
      }

      track("preferences_saved");
      setSavedSnapshot(JSON.stringify([fontFamily, fontSize, fontColor, completionEmails]));
      setSuccess(true);
      setTimeout(() => setSuccess(false), 3000);
    } catch (error) {
      console.error('Error saving preferences:', error);
      setError(error instanceof Error ? error.message : t("save.failed"));
    } finally {
      setIsLoading(false);
    }
  };



  if (isPending || (session?.user && isFetching)) {
    return (
      <div role="status" aria-label={common("loading")} className="mx-auto w-full max-w-3xl space-y-6 px-4 py-8 sm:px-8 md:py-10">
        <Skeleton className="h-9 w-40" />
        {[0, 1, 2].map((key) => <Skeleton key={key} className="h-40 w-full rounded-2xl" />)}
      </div>
    );
  }

  if (!session?.user) {
    return (
      <div className="mx-auto max-w-md px-4 py-24 text-center">
        <h1 className="font-display text-3xl font-bold tracking-tight">{t("signInRequired.title")}</h1>
        <p className="mb-8 mt-3 text-muted-foreground">{t("signInRequired.body")}</p>
        <Link href="/sign-in">
          <Button size="lg">{t("signInRequired.action")}</Button>
        </Link>
      </div>
    );
  }

  const dirty = savedSnapshot !== null && savedSnapshot !== JSON.stringify([fontFamily, fontSize, fontColor, completionEmails]);
  // The save bar only appears once there's something to save (and briefly to confirm it).
  const showSaveBar = dirty || isLoading || success;
  const minuteLimit = billingSummary?.minute_limit ?? null;
  const usageMinutes = billingSummary?.usage_minutes ?? 0;
  const usagePct = minuteLimit ? Math.min(100, (usageMinutes / minuteLimit) * 100) : 0;
  // Monthly Stripe subscribers can move to yearly billing for the same plan.
  const canSwitchToYearly =
    currentPaidPlan &&
    billingSummary?.subscription_provider === "stripe" &&
    billingSummary.billing_interval === "month" &&
    !billingSummary.cancel_at;

  return (
    <main className={cn("mx-auto w-full max-w-3xl px-4 pt-8 sm:px-8 md:pt-10", showSaveBar ? "pb-32" : "pb-16")}>
      <h1 className="font-display text-3xl font-bold tracking-tight">{t("title")}</h1>
      <p className="mt-1 text-sm text-muted-foreground">{t("intro")}</p>

      <div className="mt-8 space-y-6">
        <Section icon={<Languages className="size-4" />} title={tLang("label")} description={t("languageDescription")}>
          <LanguageSwitcher />
        </Section>

        <Section icon={<Type className="size-4" />} title={t("captions.title")} description={t("captions.description")}>
          <div className="grid gap-6 sm:grid-cols-[minmax(0,1fr)_150px]">
            <div className="space-y-5">
              <div className="space-y-2">
                <Label className="text-sm">{t("captions.font")}</Label>
                <Select value={fontFamily} onValueChange={setFontFamily} disabled={isLoading}>
                  <SelectTrigger className="w-full"><SelectValue placeholder={t("captions.selectFont")} /></SelectTrigger>
                  <SelectContent>
                    {availableFonts.map((font) => (
                      <FontSelectOption key={font.name} font={font} />
                    ))}
                    {availableFonts.length === 0 && <FontSelectOption font={{ name: "TikTokSans-Regular", display_name: "TikTok Sans Regular", scope: "system" }} />}
                  </SelectContent>
                </Select>
              </div>

              <div className="space-y-3">
                <div className="flex items-center justify-between">
                  <Label className="text-sm">{t("captions.size")}</Label>
                  <span className="text-xs tabular-nums text-muted-foreground">{fontSize}px</span>
                </div>
                <Slider value={[fontSize]} onValueChange={(value) => setFontSize(value[0])} max={48} min={12} step={2} disabled={isLoading} aria-label={t("captions.fontSize")} />
              </div>

              <div className="space-y-2">
                <Label className="text-sm">{t("captions.color")}</Label>
                <div className="flex flex-wrap items-center gap-1.5">
                  {["#FFFFFF", "#000000", "#FFD700", "#FF6B6B", "#4ECDC4", "#45B7D1"].map((color) => (
                    <button
                      key={color}
                      type="button"
                      onClick={() => setFontColor(color)}
                      disabled={isLoading}
                      title={color}
                      aria-label={t("captions.captionColor", { color })}
                      className={cn("size-8 rounded-full border-2 transition-transform hover:scale-110 disabled:cursor-not-allowed", fontColor.toUpperCase() === color ? "border-foreground" : "border-border")}
                      style={{ backgroundColor: color }}
                    />
                  ))}
                  <label className="relative size-8 cursor-pointer overflow-hidden rounded-full border-2 border-dashed border-border" title={t("captions.customColor")}>
                    <input type="color" value={fontColor} onChange={(e) => setFontColor(e.target.value)} disabled={isLoading} className="absolute inset-0 cursor-pointer opacity-0" aria-label={t("captions.customCaptionColor")} />
                    <span className="absolute inset-1 rounded-full bg-[conic-gradient(red,yellow,lime,cyan,blue,magenta,red)]" />
                  </label>
                  <Input
                    type="text"
                    value={fontColor}
                    onChange={(e) => setFontColor(e.target.value)}
                    disabled={isLoading}
                    placeholder="#FFFFFF"
                    aria-label={t("captions.captionColorHex")}
                    className="ml-1 h-8 w-24 font-mono text-xs"
                    pattern="^#[0-9A-Fa-f]{6}$"
                  />
                </div>
              </div>
            </div>

            <div className="relative mx-auto aspect-[9/16] w-full max-w-[150px] overflow-hidden rounded-xl bg-[radial-gradient(circle_at_50%_30%,oklch(0.5_0.05_50),oklch(0.2_0.01_50))]" aria-label={t("captions.preview")}>
              <p
                className="absolute inset-x-2 top-[70%] text-center font-bold leading-snug"
                style={{
                  color: fontColor,
                  fontFamily: `'${fontFamily}', system-ui, sans-serif`,
                  fontSize: `${Math.max(Math.min(fontSize * 0.5, 18), 9)}px`,
                  textShadow: "0 2px 6px rgba(0,0,0,0.8), 0 0 2px rgba(0,0,0,0.9)",
                }}
              >
                {t("captions.sample")}
              </p>
            </div>
          </div>
        </Section>

        <Section icon={<Mail className="size-4" />} title={t("notifications.title")} description={t("notifications.description")}>
          <div className="flex items-center justify-between gap-4">
            <Label htmlFor="completion-emails" className="flex-col items-start gap-0.5">
              <span className="text-sm font-medium">{t("notifications.emails")}</span>
              <span className="text-xs font-normal text-muted-foreground">{t("notifications.emailsHint")}</span>
            </Label>
            <Switch id="completion-emails" checked={completionEmails} onCheckedChange={setCompletionEmails} disabled={isLoading} />
          </div>
        </Section>

        {billingSummary?.monetization_enabled && (
          <Section id="plan" icon={<CreditCard className="size-4" />} title={t("plan.title")} description={isPaidBillingPlan(billingSummary.plan) ? t("plan.paidDescription") : t("plan.freeDescription")}>
            <div className="space-y-4">
              <div className="rounded-xl bg-muted/50 p-4">
                <div className="flex items-baseline justify-between gap-3">
                  <p className="font-semibold">
                    {plans.planName(billingSummary.plan)}
                    <span className="ml-2 text-xs font-normal capitalize text-muted-foreground">{billingSummary.subscription_status}</span>
                  </p>
                  <p className="text-sm tabular-nums text-muted-foreground">
                    {minuteLimit === null ? t("plan.usageMinutes", { used: usageMinutes }) : t("plan.usageMinutesOf", { used: usageMinutes, limit: minuteLimit })}
                  </p>
                </div>
                {minuteLimit !== null && (
                  <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-background"><div className="h-full rounded-full bg-brand" style={{ width: `${usagePct}%` }} /></div>
                )}
                <p className="mt-2 text-xs text-muted-foreground">
                  {!upgradeState?.isPaid
                    ? t("plan.noneYet")
                    : minuteLimit === null
                    ? t("plan.minutesThisMonth", { used: usageMinutes })
                    : upgradeState.atLimit
                    ? (upgradeState.resetsOn
                      ? t("plan.allUsedRefresh", { limit: plans.formatMinutes(minuteLimit), date: upgradeState.resetsOn })
                      : t("plan.allUsedPeriod", { limit: plans.formatMinutes(minuteLimit) }))
                    : (upgradeState.resetsOn
                      ? t("plan.leftRefresh", { left: plans.formatMinutes(Math.max(minuteLimit - usageMinutes, 0)), date: upgradeState.resetsOn })
                      : t("plan.leftPeriod", { left: plans.formatMinutes(Math.max(minuteLimit - usageMinutes, 0)) }))}
                </p>
                {upgradeState?.isPaid && billingSummary.output_short_side && (
                  <p className="mt-1 text-xs text-muted-foreground">
                    {billingSummary.legacy_hd
                      ? t("plan.clipsResolutionLegacy", { resolution: `${billingSummary.output_short_side}p` })
                      : t("plan.clipsResolution", { resolution: `${billingSummary.output_short_side}p` })}
                    {billingSummary.billing_interval === "year" ? ` · ${t("plan.billedYearly")}` : ""}
                  </p>
                )}
              </div>

              {billingSummary.cancel_at && billingSummary.subscription_provider === "stripe" && (
                <SubscriptionCancelBanner
                  cancelAt={billingSummary.cancel_at}
                  onRestarted={() => setBillingSummary((prev) => (prev ? { ...prev, cancel_at: null } : prev))}
                />
              )}

              {isPaidBillingPlan(billingSummary.plan) ? (
                billingSummary.subscription_provider === "apple" ? (
                  <p className="rounded-lg border px-3 py-2 text-sm text-muted-foreground">{t("plan.appStore")}</p>
                ) : (
                  <div className="space-y-3">
                    {upgradeState?.nextPlan && (
                      <UpgradeNudge
                        title={
                          currentPaidPlan
                            ? t(
                                upgradeState.nextPlan.resolution !== currentPaidPlan.resolution && !billingSummary.legacy_hd
                                  ? "plan.upgradeMultiplierHd"
                                  : "plan.upgradeMultiplier",
                                {
                                  multiplier: formatRatio(upgradeState.nextPlan.minuteLimit / currentPaidPlan.minuteLimit),
                                  resolution: upgradeState.nextPlan.resolution,
                                  plan: plans.planName(upgradeState.nextPlan.id),
                                },
                              )
                            : t("plan.upgradeMore", { plan: plans.planName(upgradeState.nextPlan.id) })
                        }
                        description={t("plan.upgradeDescription", {
                          price: upgradeState.nextPlan.priceMonthly,
                          minutes: plans.formatMinutes(upgradeState.nextPlan.minuteLimit),
                          resolution: upgradeState.nextPlan.resolution,
                          length: plans.formatMinutes(upgradeState.nextPlan.youtubeMaxMinutes),
                        })}
                        action={t("plan.upgrade")}
                        onAction={() => handlePlanChange(upgradeState.nextPlan!.id, billingSummary.billing_interval ?? "month")}
                        loading={isBillingActionLoading}
                      />
                    )}
                    {canSwitchToYearly && annualMonthsFree(currentPaidPlan) > 0 && (
                      <UpgradeNudge
                        title={t("plan.switchYearlyTitle", { count: annualMonthsFree(currentPaidPlan) })}
                        description={t("plan.switchYearlyDescription", {
                          price: currentPaidPlan.priceAnnual,
                          monthly: annualMonthlyEquivalent(currentPaidPlan),
                        })}
                        action={t("plan.switch")}
                        onAction={() => handlePlanChange(currentPaidPlan.id, "year")}
                        loading={isBillingActionLoading}
                      />
                    )}
                    {billingSummary.subscription_provider === "affiliate" ? (
                      <p className="rounded-lg border px-3 py-2 text-sm text-muted-foreground">{t("plan.affiliate")}</p>
                    ) : (
                      <Button type="button" variant="outline" onClick={() => handleBillingAction()} disabled={isBillingActionLoading}>
                        {isBillingActionLoading ? t("plan.loading") : t("plan.manage")}
                      </Button>
                    )}
                  </div>
                )
              ) : (
                <div className="grid gap-3 sm:grid-cols-2">
                  <BillingIntervalToggle
                    className="justify-self-start sm:col-span-2"
                    value={billingInterval}
                    onChange={setBillingInterval}
                    plan={paidPlans[0]}
                  />
                  {paidPlans.map((plan) => (
                    <button
                      key={plan.id}
                      type="button"
                      onClick={() => handleBillingAction(plan.id)}
                      disabled={isBillingActionLoading}
                      className={cn(
                        "rounded-xl border p-4 text-left transition-colors hover:border-foreground/30 disabled:opacity-60",
                        plan.highlighted && "border-foreground ring-1 ring-foreground",
                      )}
                    >
                      <span className="flex items-center justify-between gap-2">
                        <span className="text-sm font-semibold">{plans.planName(plan.id)}</span>
                        {plan.highlighted && <span className="text-xs text-muted-foreground">{plans.t("mostPopular")}</span>}
                      </span>
                      <span className="mt-1 block text-2xl font-bold tracking-tight">
                        ${planPrice(plan, billingInterval)}
                        <span className="text-xs font-normal text-muted-foreground">
                          {billingInterval === "year" ? plans.t("perYear") : plans.t("perMonth")}
                        </span>
                      </span>
                      {billingInterval === "year" && (
                        <span className="block text-xs text-muted-foreground">
                          {t("plan.monthlyEquivalentYearly", { price: annualMonthlyEquivalent(plan) })}
                        </span>
                      )}
                      <span className="mt-3 block space-y-1.5">
                        {plans.highlights(plan).map((highlight) => (
                          <span key={highlight} className="flex items-start gap-1.5 text-xs text-muted-foreground">
                            <Check className="mt-0.5 size-3 shrink-0" />{highlight}
                          </span>
                        ))}
                        {plans.comingSoon(plan) && (
                          <span className="flex items-start gap-1.5 text-xs text-muted-foreground/80">
                            <Clock className="mt-0.5 size-3 shrink-0" />
                            <span>{plans.comingSoon(plan)} <ComingSoonTag /></span>
                          </span>
                        )}
                      </span>
                      <span className={cn("mt-4 flex h-9 items-center justify-center rounded-lg text-sm font-medium", plan.highlighted ? "bg-primary text-primary-foreground" : "bg-muted")}>
                        {isBillingActionLoading ? t("plan.openingCheckout") : t("plan.choose", { name: plans.planName(plan.id) })}
                      </span>
                    </button>
                  ))}
                  <CreatorCodeField className="justify-self-start sm:col-span-2" />
                </div>
              )}
            </div>
          </Section>
        )}

        <Link href="/settings/api-keys" className="group flex items-center gap-4 rounded-2xl border bg-background p-5 transition-colors hover:border-foreground/25">
          <span className="flex size-9 items-center justify-center rounded-lg bg-muted"><Bot className="size-4" /></span>
          <span className="min-w-0 flex-1">
            <span className="block text-sm font-semibold">{t("links.agentsTitle")}</span>
            <span className="block text-xs text-muted-foreground">{t("links.agentsDescription")}</span>
          </span>
          <ChevronRight className="size-4 text-muted-foreground transition-transform group-hover:translate-x-0.5" />
        </Link>

        {socialAccess === true && (
          <Link href="/settings/social" className="group flex items-center gap-4 rounded-2xl border bg-background p-5 transition-colors hover:border-foreground/25">
            <span className="flex size-9 items-center justify-center rounded-lg bg-muted"><Share2 className="size-4" /></span>
            <span className="min-w-0 flex-1">
              <span className="block text-sm font-semibold">{t("links.socialTitle")}</span>
              <span className="block text-xs text-muted-foreground">{t("links.socialDescription")}</span>
            </span>
            <ChevronRight className="size-4 text-muted-foreground transition-transform group-hover:translate-x-0.5" />
          </Link>
        )}

        <Link href="/settings/creator-program" className="group flex items-center gap-4 rounded-2xl border bg-background p-5 transition-colors hover:border-foreground/25">
          <span className="flex size-9 items-center justify-center rounded-lg bg-muted"><Megaphone className="size-4" /></span>
          <span className="min-w-0 flex-1">
            <span className="block text-sm font-semibold">{t("links.creatorTitle")}</span>
            <span className="block text-xs text-muted-foreground">{t("links.creatorDescription")}</span>
          </span>
          <ChevronRight className="size-4 text-muted-foreground transition-transform group-hover:translate-x-0.5" />
        </Link>

        <DeleteAccountSection
          email={session.user.email}
          hasAppStoreSubscription={
            billingSummary?.subscription_provider === "apple" &&
            ["active", "trialing", "past_due"].includes(billingSummary.subscription_status)
          }
        />

        {error && (
          <Alert className="border-red-200 bg-red-50">
            <AlertCircle className="h-4 w-4 text-red-500" />
            <AlertDescription className="text-sm text-red-700">{error}</AlertDescription>
          </Alert>
        )}
      </div>

      {showSaveBar && (
        <div className="fixed inset-x-0 bottom-[calc(4rem+env(safe-area-inset-bottom))] z-30 flex justify-center px-4 pb-4 pointer-events-none md:bottom-0 md:pb-6 md:pl-64">
          <div className="pointer-events-auto flex w-full max-w-3xl animate-in fade-in slide-in-from-bottom-4 duration-200 items-center justify-between gap-4 rounded-2xl border bg-background/95 px-4 py-3 shadow-lg backdrop-blur max-md:mr-14">
            <p role="status" className="flex items-center gap-2 text-sm text-muted-foreground">
              {success ? <><Check className="size-4 text-emerald-600" />{t("save.saved")}</>
                : <><span className="size-2 rounded-full bg-brand" />{t("save.unsaved")}</>}
            </p>
            <Button onClick={handleSavePreferences} disabled={isLoading}>
              {isLoading ? <><Loader2 className="size-4 animate-spin" />{t("save.saving")}</> : t("save.button")}
            </Button>
          </div>
        </div>
      )}
    </main>
  );
}

function Section({ id, icon, title, description, children }: { id?: string; icon: React.ReactNode; title: string; description: string; children: React.ReactNode }) {
  return (
    <section id={id} className="scroll-mt-6 rounded-2xl border bg-background">
      <div className="flex items-start gap-3 border-b px-5 py-4">
        <span className="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-lg bg-muted">{icon}</span>
        <div>
          <h2 className="text-sm font-semibold">{title}</h2>
          <p className="text-xs text-muted-foreground">{description}</p>
        </div>
      </div>
      <div className="p-5">{children}</div>
    </section>
  );
}

/** 2.5 → "2.5", 3 → "3". */
function formatRatio(ratio: number) {
  const rounded = Math.round(ratio * 10) / 10;
  return Number.isInteger(rounded) ? String(rounded) : rounded.toFixed(1);
}
