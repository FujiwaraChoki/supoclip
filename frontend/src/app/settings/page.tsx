"use client";

import { useState, useEffect } from "react";
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
import { formatBillingPlanName, formatMinutes, getPublicBillingPlans, isPaidBillingPlan, type BillingPlanId } from "@/lib/billing-plans";
import { UpgradeNudge, getUpgradeState } from "@/components/billing/upgrade-prompt";
import { startUpgrade } from "@/lib/start-upgrade";
import { CreatorCodeField } from "@/components/billing/creator-code-field";
import { toast } from "sonner";
import { track } from "@/lib/datafast";
import Link from "next/link";
import { AlertCircle, Bot, Check, ChevronRight, CreditCard, Loader2, Mail, Megaphone, Type } from "lucide-react";
import { cn } from "@/lib/utils";
import { FontSelectOption, type FontOption } from "@/components/font-select-option";

interface UserPreferences {
  fontFamily: string;
  fontSize: number;
  fontColor: string;
  notifyOnCompletion: boolean;
}

interface BillingSummary {
  monetization_enabled: boolean;
  plan: string;
  subscription_status: string;
  subscription_provider: string | null;
  cancel_at?: string | null;
  period_end?: string | null;
  usage_count: number;
  usage_limit: number | null;
  remaining: number | null;
  upgrade_required: boolean;
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
  const { data: session, isPending } = useSession();

  const paidPlans = getPublicBillingPlans();
  const upgradeState = getUpgradeState(billingSummary);
  const currentPaidPlan = paidPlans.find((plan) => plan.id === billingSummary?.plan);

  // Stripe sends people back here after checkout or a plan change.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const outcome = params.get("billing");
    if (outcome === "success" || outcome === "upgraded") {
      toast.success(outcome === "upgraded" ? "Your plan has been upgraded. Enjoy the extra room!" : "You're all set. Time to make some clips!");
    }
    if (outcome) {
      params.delete("billing");
      const query = params.toString();
      window.history.replaceState(null, "", `${window.location.pathname}${query ? `?${query}` : ""}${window.location.hash}`);
    }
  }, []);

  const handleUpgradeToNextPlan = async () => {
    if (!upgradeState?.nextPlan) return;
    setIsBillingActionLoading(true);
    try {
      await startUpgrade(upgradeState.nextPlan.id, "settings");
    } catch (upgradeError) {
      toast.error(upgradeError instanceof Error ? upgradeError.message : "We couldn't open checkout. Please try again.");
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
        await startUpgrade(selectedPlan ?? "pro", "settings");
      } catch (billingError) {
        setError(billingError instanceof Error ? billingError.message : "Billing action failed");
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
        throw new Error(data.error || "Unable to open billing");
      }

      track("billing_portal_opened", { plan: billingSummary.plan });
      window.location.href = data.url;
    } catch (billingError) {
      setError(billingError instanceof Error ? billingError.message : "Billing action failed");
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
        throw new Error(errorData.error || 'Failed to save preferences');
      }

      track("preferences_saved");
      setSavedSnapshot(JSON.stringify([fontFamily, fontSize, fontColor, completionEmails]));
      setSuccess(true);
      setTimeout(() => setSuccess(false), 3000);
    } catch (error) {
      console.error('Error saving preferences:', error);
      setError(error instanceof Error ? error.message : 'Failed to save preferences');
    } finally {
      setIsLoading(false);
    }
  };



  if (isPending || (session?.user && isFetching)) {
    return (
      <div role="status" aria-label="Loading" className="mx-auto w-full max-w-3xl space-y-6 px-4 py-8 sm:px-8 md:py-10">
        <Skeleton className="h-9 w-40" />
        {[0, 1, 2].map((key) => <Skeleton key={key} className="h-40 w-full rounded-2xl" />)}
      </div>
    );
  }

  if (!session?.user) {
    return (
      <div className="mx-auto max-w-md px-4 py-24 text-center">
        <h1 className="font-display text-3xl font-bold tracking-tight">Sign In Required</h1>
        <p className="mb-8 mt-3 text-muted-foreground">You need to sign in to access your settings</p>
        <Link href="/sign-in">
          <Button size="lg">Sign In</Button>
        </Link>
      </div>
    );
  }

  const dirty = savedSnapshot !== null && savedSnapshot !== JSON.stringify([fontFamily, fontSize, fontColor, completionEmails]);
  // The save bar only appears once there's something to save (and briefly to confirm it).
  const showSaveBar = dirty || isLoading || success;
  const usagePct = billingSummary?.usage_limit ? Math.min(100, (billingSummary.usage_count / billingSummary.usage_limit) * 100) : 0;

  return (
    <main className={cn("mx-auto w-full max-w-3xl px-4 pt-8 sm:px-8 md:pt-10", showSaveBar ? "pb-32" : "pb-16")}>
      <h1 className="font-display text-3xl font-bold tracking-tight">Settings</h1>
      <p className="mt-1 text-sm text-muted-foreground">Defaults for new generations, notifications and your plan.</p>

      <div className="mt-8 space-y-6">
        <Section icon={<Type className="size-4" />} title="Caption defaults" description="Applied to every new generation. You can still change them per video.">
          <div className="grid gap-6 sm:grid-cols-[minmax(0,1fr)_150px]">
            <div className="space-y-5">
              <div className="space-y-2">
                <Label className="text-sm">Font</Label>
                <Select value={fontFamily} onValueChange={setFontFamily} disabled={isLoading}>
                  <SelectTrigger className="w-full"><SelectValue placeholder="Select font" /></SelectTrigger>
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
                  <Label className="text-sm">Size</Label>
                  <span className="text-xs tabular-nums text-muted-foreground">{fontSize}px</span>
                </div>
                <Slider value={[fontSize]} onValueChange={(value) => setFontSize(value[0])} max={48} min={12} step={2} disabled={isLoading} aria-label="Font size" />
              </div>

              <div className="space-y-2">
                <Label className="text-sm">Color</Label>
                <div className="flex flex-wrap items-center gap-1.5">
                  {["#FFFFFF", "#000000", "#FFD700", "#FF6B6B", "#4ECDC4", "#45B7D1"].map((color) => (
                    <button
                      key={color}
                      type="button"
                      onClick={() => setFontColor(color)}
                      disabled={isLoading}
                      title={color}
                      aria-label={`Caption color ${color}`}
                      className={cn("size-8 rounded-full border-2 transition-transform hover:scale-110 disabled:cursor-not-allowed", fontColor.toUpperCase() === color ? "border-foreground" : "border-border")}
                      style={{ backgroundColor: color }}
                    />
                  ))}
                  <label className="relative size-8 cursor-pointer overflow-hidden rounded-full border-2 border-dashed border-border" title="Custom color">
                    <input type="color" value={fontColor} onChange={(e) => setFontColor(e.target.value)} disabled={isLoading} className="absolute inset-0 cursor-pointer opacity-0" aria-label="Custom caption color" />
                    <span className="absolute inset-1 rounded-full bg-[conic-gradient(red,yellow,lime,cyan,blue,magenta,red)]" />
                  </label>
                  <Input
                    type="text"
                    value={fontColor}
                    onChange={(e) => setFontColor(e.target.value)}
                    disabled={isLoading}
                    placeholder="#FFFFFF"
                    aria-label="Caption color hex"
                    className="ml-1 h-8 w-24 font-mono text-xs"
                    pattern="^#[0-9A-Fa-f]{6}$"
                  />
                </div>
              </div>
            </div>

            <div className="relative mx-auto aspect-[9/16] w-full max-w-[150px] overflow-hidden rounded-xl bg-[radial-gradient(circle_at_50%_30%,oklch(0.5_0.05_50),oklch(0.2_0.01_50))]" aria-label="Caption preview">
              <p
                className="absolute inset-x-2 top-[70%] text-center font-bold leading-snug"
                style={{
                  color: fontColor,
                  fontFamily: `'${fontFamily}', system-ui, sans-serif`,
                  fontSize: `${Math.max(Math.min(fontSize * 0.5, 18), 9)}px`,
                  textShadow: "0 2px 6px rgba(0,0,0,0.8), 0 0 2px rgba(0,0,0,0.9)",
                }}
              >
                Your subtitle will look like this
              </p>
            </div>
          </div>
        </Section>

        <Section icon={<Mail className="size-4" />} title="Notifications" description="How we let you know your clips are ready.">
          <div className="flex items-center justify-between gap-4">
            <Label htmlFor="completion-emails" className="flex-col items-start gap-0.5">
              <span className="text-sm font-medium">Completion emails</span>
              <span className="text-xs font-normal text-muted-foreground">Get an email as soon as a generation finishes.</span>
            </Label>
            <Switch id="completion-emails" checked={completionEmails} onCheckedChange={setCompletionEmails} disabled={isLoading} />
          </div>
        </Section>

        {billingSummary?.monetization_enabled && (
          <Section id="plan" icon={<CreditCard className="size-4" />} title="Plan & billing" description={isPaidBillingPlan(billingSummary.plan) ? "Manage your subscription and invoices." : "Pick the plan that fits how much you clip."}>
            <div className="space-y-4">
              <div className="rounded-xl bg-muted/50 p-4">
                <div className="flex items-baseline justify-between gap-3">
                  <p className="font-semibold">
                    {formatBillingPlanName(billingSummary.plan)}
                    <span className="ml-2 text-xs font-normal capitalize text-muted-foreground">{billingSummary.subscription_status}</span>
                  </p>
                  <p className="text-sm tabular-nums text-muted-foreground">
                    {billingSummary.usage_limit === null ? `${billingSummary.usage_count} this period` : `${billingSummary.usage_count} / ${billingSummary.usage_limit}`}
                  </p>
                </div>
                {billingSummary.usage_limit !== null && (
                  <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-background"><div className="h-full rounded-full bg-brand" style={{ width: `${usagePct}%` }} /></div>
                )}
                <p className="mt-2 text-xs text-muted-foreground">
                  {!upgradeState?.isPaid
                    ? "No videos included yet. Choose a plan below to start clipping."
                    : billingSummary.usage_limit === null
                    ? `${billingSummary.usage_count} videos clipped this period`
                    : upgradeState.atLimit
                    ? `All ${billingSummary.usage_limit} videos used${upgradeState.resetsOn ? `, refreshes on ${upgradeState.resetsOn}` : " this period"}`
                    : `${billingSummary.usage_count} of ${billingSummary.usage_limit} videos used${upgradeState.resetsOn ? ` · refreshes on ${upgradeState.resetsOn}` : " this period"}`}
                </p>
              </div>

              {billingSummary.cancel_at && billingSummary.subscription_provider === "stripe" && (
                <SubscriptionCancelBanner
                  cancelAt={billingSummary.cancel_at}
                  onRestarted={() => setBillingSummary((prev) => (prev ? { ...prev, cancel_at: null } : prev))}
                />
              )}

              {isPaidBillingPlan(billingSummary.plan) ? (
                billingSummary.subscription_provider === "apple" ? (
                  <p className="rounded-lg border px-3 py-2 text-sm text-muted-foreground">Managed through the App Store</p>
                ) : (
                  <div className="space-y-3">
                    {upgradeState?.nextPlan && (
                      <UpgradeNudge
                        title={
                          currentPaidPlan
                            ? `Get ${Math.max(2, Math.round(upgradeState.nextPlan.generationLimit / currentPaidPlan.generationLimit))}× more videos with ${upgradeState.nextPlan.name}`
                            : `Do more with ${upgradeState.nextPlan.name}`
                        }
                        description={`$${upgradeState.nextPlan.priceMonthly}/mo · ${upgradeState.nextPlan.generationLimit} videos · YouTube up to ${formatMinutes(upgradeState.nextPlan.youtubeMaxMinutes)}`}
                        action="Upgrade"
                        onAction={handleUpgradeToNextPlan}
                        loading={isBillingActionLoading}
                      />
                    )}
                    {billingSummary.subscription_provider === "affiliate" ? (
                      <p className="rounded-lg border px-3 py-2 text-sm text-muted-foreground">Free through the creator program</p>
                    ) : (
                      <Button type="button" variant="outline" onClick={() => handleBillingAction()} disabled={isBillingActionLoading}>
                        {isBillingActionLoading ? "Loading..." : "Manage Billing"}
                      </Button>
                    )}
                  </div>
                )
              ) : (
                <div className="grid gap-3 sm:grid-cols-2">
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
                        <span className="text-sm font-semibold">{plan.name}</span>
                        {plan.highlighted && <span className="text-xs text-muted-foreground">Most popular</span>}
                      </span>
                      <span className="mt-1 block text-2xl font-bold tracking-tight">${plan.priceMonthly}<span className="text-xs font-normal text-muted-foreground">/mo</span></span>
                      <span className="mt-3 block space-y-1.5">
                        {plan.highlights.map((highlight) => (
                          <span key={highlight} className="flex items-start gap-1.5 text-xs text-muted-foreground">
                            <Check className="mt-0.5 size-3 shrink-0" />{highlight}
                          </span>
                        ))}
                      </span>
                      <span className={cn("mt-4 flex h-9 items-center justify-center rounded-lg text-sm font-medium", plan.highlighted ? "bg-primary text-primary-foreground" : "bg-muted")}>
                        {isBillingActionLoading ? "Opening checkout…" : `Choose ${plan.name}`}
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
            <span className="block text-sm font-semibold">Agents &amp; API</span>
            <span className="block text-xs text-muted-foreground">API keys for AI agents (MCP) and the REST API</span>
          </span>
          <ChevronRight className="size-4 text-muted-foreground transition-transform group-hover:translate-x-0.5" />
        </Link>

        <Link href="/settings/creator-program" className="group flex items-center gap-4 rounded-2xl border bg-background p-5 transition-colors hover:border-foreground/25">
          <span className="flex size-9 items-center justify-center rounded-lg bg-muted"><Megaphone className="size-4" /></span>
          <span className="min-w-0 flex-1">
            <span className="block text-sm font-semibold">Creator program</span>
            <span className="block text-xs text-muted-foreground">Make videos about SupoClip, get Pro free and a code for your audience</span>
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
              {success ? <><Check className="size-4 text-emerald-600" />Preferences saved successfully!</>
                : <><span className="size-2 rounded-full bg-brand" />Unsaved changes</>}
            </p>
            <Button onClick={handleSavePreferences} disabled={isLoading}>
              {isLoading ? <><Loader2 className="size-4 animate-spin" />Saving...</> : "Save Preferences"}
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
