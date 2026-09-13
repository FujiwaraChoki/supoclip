"use client";

import { useState, useEffect } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { Separator } from "@/components/ui/separator";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Skeleton } from "@/components/ui/skeleton";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Slider } from "@/components/ui/slider";
import { Switch } from "@/components/ui/switch";
import { LOCAL_USER_ID } from "@/lib/local-user";
import { formatBillingPlanName, getPublicBillingPlans, isPaidBillingPlan, type BillingPlanId } from "@/lib/billing-plans";
import { track } from "@/lib/datafast";
import Link from "next/link";
import { Type, Palette, CheckCircle, AlertCircle, Settings, ArrowLeft, Mail, KeyRound, ChevronRight, Mic, Music, SlidersHorizontal, Download } from "lucide-react";
import { RuntimeSettingsForm, type RuntimeSetting } from "@/components/admin/runtime-settings-form";

const TRANSCRIPTION_SETTING_KEYS = new Set([
  "TRANSCRIPTION_PROVIDER",
  "WHISPER_MODEL",
  "WHISPER_LANGUAGE",
  "ASSEMBLY_AI_API_KEY",
]);

const EXPORT_SETTING_KEYS = new Set([
  "MAX_CLIPS",
  "CLIP_DURATION",
  "DEFAULT_PROCESSING_MODE",
  "FAST_MODE_MAX_CLIPS",
]);

type SfxFile = { name: string; display_name: string };

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
  const [availableFonts, setAvailableFonts] = useState<Array<{ name: string, display_name: string }>>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [isFetching, setIsFetching] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);
  const [billingSummary, setBillingSummary] = useState<BillingSummary | null>(null);
  const [isBillingActionLoading, setIsBillingActionLoading] = useState(false);
  const [runtimeSettings, setRuntimeSettings] = useState<RuntimeSetting[]>([]);
  const [runtimeSettingsError, setRuntimeSettingsError] = useState<string | null>(null);
  const [sfxFiles, setSfxFiles] = useState<SfxFile[]>([]);
  const [sfxError, setSfxError] = useState<string | null>(null);
  // Local-first: no login, so there's no real session — every user_id-shaped
  // value downstream just resolves to the single implicit local user.
  const session = { user: { id: LOCAL_USER_ID, name: "Local User", email: "", image: null as string | null } };

  const paidPlans = getPublicBillingPlans();

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

  const loadRuntimeSettings = async () => {
    try {
      const response = await fetch("/api/admin/runtime-settings", { cache: "no-store" });
      if (!response.ok) {
        setRuntimeSettingsError("Unable to load runtime settings.");
        return;
      }
      const data = (await response.json()) as { settings?: RuntimeSetting[] };
      setRuntimeSettings(data.settings ?? []);
      setRuntimeSettingsError(null);
    } catch {
      setRuntimeSettingsError("Unable to reach the backend settings API.");
    }
  };

  useEffect(() => {
    loadRuntimeSettings();
  }, []);

  useEffect(() => {
    const loadSfx = async () => {
      try {
        const apiUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
        const response = await fetch(`${apiUrl}/sfx`, { cache: "no-store" });
        if (!response.ok) {
          setSfxError("Unable to load the SFX library.");
          return;
        }
        const data = (await response.json()) as { sfx?: SfxFile[] };
        setSfxFiles(data.sfx ?? []);
      } catch {
        setSfxError("Unable to reach the backend SFX API.");
      }
    };

    loadSfx();
  }, []);

  const transcriptionSettings = runtimeSettings.filter((setting) =>
    TRANSCRIPTION_SETTING_KEYS.has(setting.key),
  );
  const exportSettings = runtimeSettings.filter((setting) => EXPORT_SETTING_KEYS.has(setting.key));
  const advancedSettings = runtimeSettings.filter(
    (setting) => !TRANSCRIPTION_SETTING_KEYS.has(setting.key) && !EXPORT_SETTING_KEYS.has(setting.key),
  );

  const handleBillingAction = async (selectedPlan?: BillingPlanId) => {
    if (!billingSummary?.monetization_enabled) return;

    const isPaid = isPaidBillingPlan(billingSummary.plan);
    const route = isPaid ? "/api/billing/portal" : "/api/billing/checkout";
    const body = !isPaid && selectedPlan ? JSON.stringify({ plan: selectedPlan }) : undefined;

    try {
      setIsBillingActionLoading(true);
      const response = await fetch(route, {
        method: "POST",
        ...(body
          ? {
              headers: { "Content-Type": "application/json" },
              body,
            }
          : {}),
      });
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

      track(isPaid ? "billing_portal_opened" : "billing_checkout_started", {
        plan: billingSummary.plan,
        selected_plan: selectedPlan,
      });
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
      setSuccess(true);
      setTimeout(() => setSuccess(false), 3000);
    } catch (error) {
      console.error('Error saving preferences:', error);
      setError(error instanceof Error ? error.message : 'Failed to save preferences');
    } finally {
      setIsLoading(false);
    }
  };

  if (isFetching) {
    return (
      <div className="min-h-screen bg-white flex items-center justify-center p-4">
        <div className="space-y-4">
          <Skeleton className="h-4 w-32 mx-auto" />
          <Skeleton className="h-4 w-48 mx-auto" />
          <Skeleton className="h-4 w-24 mx-auto" />
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-white">
      {/* Header */}
      <div className="border-b bg-white">
        <div className="max-w-7xl mx-auto px-4 py-4">
          <div className="flex justify-between items-center">
            <Link href="/">
              <Button variant="ghost" size="sm">
                <ArrowLeft className="w-4 h-4" />
                Back
              </Button>
            </Link>

            <div className="flex items-center gap-3">
              <Avatar className="w-8 h-8">
                <AvatarImage src={session.user.image || ""} />
                <AvatarFallback className="bg-gray-100 text-black text-sm">
                  {session.user.name?.charAt(0) || session.user.email?.charAt(0) || "U"}
                </AvatarFallback>
              </Avatar>
              <div className="hidden sm:block">
                <p className="text-sm font-medium text-black">{session.user.name}</p>
                <p className="text-xs text-gray-500">{session.user.email}</p>
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* Main Content */}
      <div className="max-w-4xl mx-auto px-4 py-16">
        <div>
          <div className="mb-8">
            <div className="flex items-center gap-2 mb-2">
              <Settings className="w-6 h-6 text-black" />
              <h2 className="text-2xl font-bold text-black">
                Settings
              </h2>
            </div>
            <p className="text-gray-600">
              Configure your default preferences for video clip generation
            </p>
          </div>

          <Separator className="my-8" />

          <div className="space-y-10">
            {/* Transcription Section */}
            <div className="space-y-4">
              <div>
                <h3 className="text-lg font-semibold text-black mb-1 flex items-center gap-2">
                  <Mic className="w-4 h-4" />
                  Transcription
                </h3>
                <p className="text-sm text-gray-600">
                  Choose the transcription provider and its API key/model/language. Persisted
                  here, with your .env values as fallback.
                </p>
              </div>
              <div className="rounded-lg border border-gray-200 bg-white">
                {runtimeSettingsError ? (
                  <div className="px-4 py-5 text-sm text-red-700">{runtimeSettingsError}</div>
                ) : (
                  <RuntimeSettingsForm
                    settings={transcriptionSettings}
                    onSaved={loadRuntimeSettings}
                  />
                )}
              </div>
            </div>

            <Separator />

            {/* Hooks Section — sound effects for the AI-written hook overlay */}
            <div className="space-y-4">
              <div>
                <h3 className="text-lg font-semibold text-black mb-1 flex items-center gap-2">
                  <Music className="w-4 h-4" />
                  Hooks
                </h3>
                <p className="text-sm text-gray-600">
                  Sound effects available for the hook&apos;s whoosh/riser (per-hook style is
                  configured when creating or editing a task). Drop{" "}
                  <code className="text-xs">.mp3</code>/<code className="text-xs">.wav</code> files
                  into <code className="text-xs">backend/sfx/</code> to add more.
                </p>
              </div>
              {sfxError ? (
                <p className="text-sm text-red-700">{sfxError}</p>
              ) : sfxFiles.length === 0 ? (
                <p className="text-sm text-gray-500">
                  No SFX files yet — add some to <code className="text-xs">backend/sfx/</code>.
                </p>
              ) : (
                <ul className="divide-y divide-gray-200 rounded-lg border border-gray-200 bg-white">
                  {sfxFiles.map((sfx) => (
                    <li key={sfx.name} className="flex items-center justify-between gap-3 px-4 py-3">
                      <span className="text-sm text-black">{sfx.display_name}</span>
                      <audio controls src={`${process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000"}/sfx/${encodeURIComponent(sfx.name)}`} className="h-8" />
                    </li>
                  ))}
                </ul>
              )}
            </div>

            <Separator />

            {/* Export Section — clip count/duration/mode defaults applied at generation time */}
            <div className="space-y-4">
              <div>
                <h3 className="text-lg font-semibold text-black mb-1 flex items-center gap-2">
                  <Download className="w-4 h-4" />
                  Export
                </h3>
                <p className="text-sm text-gray-600">
                  Defaults for clip count, duration, and processing mode. Persisted here, with
                  your .env values as fallback.
                </p>
              </div>
              <div className="rounded-lg border border-gray-200 bg-white">
                {runtimeSettingsError ? (
                  <div className="px-4 py-5 text-sm text-red-700">{runtimeSettingsError}</div>
                ) : (
                  <RuntimeSettingsForm settings={exportSettings} onSaved={loadRuntimeSettings} />
                )}
              </div>
            </div>

            <Separator />

            {/* UI Section — local subtitle appearance */}
            <div className="space-y-8">
              <div className="space-y-6">
                <div>
                  <h3 className="text-lg font-semibold text-black mb-1 flex items-center gap-2">
                    <Type className="w-4 h-4" />
                    UI — Subtitle Appearance
                  </h3>
                  <p className="text-sm text-gray-600">
                    These settings will be applied to all new video processing tasks
                  </p>
                </div>

                {/* Font Family Selector */}
                <div className="space-y-2">
                  <Label className="text-sm font-medium text-black flex items-center gap-2">
                    <Type className="w-4 h-4" />
                    Font Family
                  </Label>
                  <Select value={fontFamily} onValueChange={setFontFamily} disabled={isLoading}>
                    <SelectTrigger className="w-full">
                      <SelectValue placeholder="Select font" />
                    </SelectTrigger>
                    <SelectContent>
                      {availableFonts.map((font) => (
                        <SelectItem key={font.name} value={font.name}>
                          {font.display_name}
                        </SelectItem>
                      ))}
                      {availableFonts.length === 0 && (
                        <SelectItem value="TikTokSans-Regular">TikTok Sans Regular</SelectItem>
                      )}
                    </SelectContent>
                  </Select>
                </div>

                {/* Font Size Slider */}
                <div className="space-y-2">
                  <Label className="text-sm font-medium text-black">
                    Font Size: {fontSize}px
                  </Label>
                  <div className="px-2">
                    <Slider
                      value={[fontSize]}
                      onValueChange={(value) => setFontSize(value[0])}
                      max={48}
                      min={12}
                      step={2}
                      disabled={isLoading}
                      className="w-full"
                    />
                  </div>
                  <div className="flex justify-between text-xs text-gray-500">
                    <span>12px</span>
                    <span>48px</span>
                  </div>
                </div>

                {/* Font Color Picker */}
                <div className="space-y-2">
                  <Label className="text-sm font-medium text-black flex items-center gap-2">
                    <Palette className="w-4 h-4" />
                    Font Color
                  </Label>
                  <div className="flex items-center gap-2">
                    <input
                      type="color"
                      value={fontColor}
                      onChange={(e) => setFontColor(e.target.value)}
                      disabled={isLoading}
                      className="w-12 h-10 rounded border border-gray-300 cursor-pointer disabled:cursor-not-allowed"
                    />
                    <Input
                      type="text"
                      value={fontColor}
                      onChange={(e) => setFontColor(e.target.value)}
                      disabled={isLoading}
                      placeholder="#FFFFFF"
                      className="flex-1 h-10"
                      pattern="^#[0-9A-Fa-f]{6}$"
                    />
                  </div>
                  <div className="flex gap-2 mt-2">
                    {["#FFFFFF", "#000000", "#FFD700", "#FF6B6B", "#4ECDC4", "#45B7D1"].map((color) => (
                      <button
                        key={color}
                        type="button"
                        onClick={() => setFontColor(color)}
                        disabled={isLoading}
                        className="w-8 h-8 rounded border-2 border-gray-300 cursor-pointer hover:scale-110 transition-transform disabled:cursor-not-allowed"
                        style={{ backgroundColor: color }}
                        title={color}
                      />
                    ))}
                  </div>
                </div>

                {/* Preview */}
                <div className="space-y-2">
                  <Label className="text-sm font-medium text-black">Preview</Label>
                  <div className="p-6 bg-black rounded-lg flex items-center justify-center min-h-[100px]">
                    <p
                      style={{
                        color: fontColor,
                        fontSize: `${Math.min(fontSize, 32)}px`,
                        fontFamily: `'${fontFamily}', system-ui, -apple-system, sans-serif`,
                        textAlign: 'center',
                        lineHeight: '1.4'
                      }}
                      className="font-medium"
                    >
                      Your subtitle will look like this
                    </p>
                  </div>
                </div>
              </div>
            </div>

            <Separator />

            {/* Notifications Section */}
            <div className="space-y-6">
              <div>
                <h3 className="text-lg font-semibold text-black mb-1">
                  Notifications
                </h3>
                <p className="text-sm text-gray-600">
                  Manage how you receive updates about your clips
                </p>
              </div>

              <div className="flex items-center justify-between">
                <Label htmlFor="completion-emails" className="flex items-center gap-2 text-sm font-medium text-black cursor-pointer">
                  <Mail className="w-4 h-4" />
                  Completion emails
                  <span className="text-gray-500 font-normal">— get notified when clips are ready</span>
                </Label>
                <Switch
                  id="completion-emails"
                  checked={completionEmails}
                  onCheckedChange={setCompletionEmails}
                  disabled={isLoading}
                />
              </div>
            </div>

            <Separator />

            {/* Advanced Section — the rest of the runtime settings (LLM/API keys/providers) */}
            <div className="space-y-4">
              <div>
                <h3 className="text-lg font-semibold text-black mb-1 flex items-center gap-2">
                  <SlidersHorizontal className="w-4 h-4" />
                  Advanced
                </h3>
                <p className="text-sm text-gray-600">
                  LLM provider, API keys, and download/B-roll providers. Persisted here, with
                  your .env values as fallback.
                </p>
              </div>
              <div className="rounded-lg border border-gray-200 bg-white">
                {runtimeSettingsError ? (
                  <div className="px-4 py-5 text-sm text-red-700">{runtimeSettingsError}</div>
                ) : (
                  <RuntimeSettingsForm settings={advancedSettings} onSaved={loadRuntimeSettings} />
                )}
              </div>
            </div>

            <Separator />

            {/* Developer Section */}
            <div className="space-y-6">
              <div>
                <h3 className="text-lg font-semibold text-black mb-1">
                  Developer
                </h3>
                <p className="text-sm text-gray-600">
                  Programmatic access for tools like the SupoClip MCP server
                </p>
              </div>

              <Link href="/settings/api-keys" className="block">
                <div className="flex items-center justify-between p-4 border rounded-lg hover:bg-gray-50 transition-colors">
                  <div className="flex items-center gap-3">
                    <KeyRound className="w-5 h-5 text-black" />
                    <div>
                      <p className="text-sm font-medium text-black">API Keys</p>
                      <p className="text-xs text-gray-500">Create and manage API keys</p>
                    </div>
                  </div>
                  <ChevronRight className="w-4 h-4 text-gray-400" />
                </div>
              </Link>
            </div>

            <Separator className="mb-4" />

            {/* Success/Error Messages */}
            {success && (
              <Alert className="border-green-200 bg-green-50">
                <CheckCircle className="h-4 w-4 text-green-500" />
                <AlertDescription className="text-sm text-green-700">
                  Preferences saved successfully!
                </AlertDescription>
              </Alert>
            )}

            {error && (
              <Alert className="border-red-200 bg-red-50">
                <AlertCircle className="h-4 w-4 text-red-500" />
                <AlertDescription className="text-sm text-red-700">
                  {error}
                </AlertDescription>
              </Alert>
            )}

            {/* Save Button */}
            {billingSummary?.monetization_enabled && (
              <div className="border rounded-lg p-4 bg-gray-50 space-y-3">
                <div>
                  <h3 className="text-lg font-semibold text-black">Billing</h3>
                  {!isPaidBillingPlan(billingSummary.plan) && (
                    <p className="text-sm text-gray-600">Video processing requires a paid plan.</p>
                  )}
                  <p className="text-sm text-gray-600">
                    {billingSummary.upgrade_required
                      ? "Current plan cannot create generations."
                      : billingSummary.usage_limit === null
                      ? `${billingSummary.usage_count} generations in this billing period`
                      : `${billingSummary.usage_count}/${billingSummary.usage_limit} generations used this period`}
                  </p>
                  <p className="text-sm text-gray-500">
                    Plan: {formatBillingPlanName(billingSummary.plan)} ({billingSummary.subscription_status})
                  </p>
                </div>

                {isPaidBillingPlan(billingSummary.plan) ? (
                  billingSummary.subscription_provider === "apple" ? (
                    <p className="rounded-md border border-gray-200 bg-white px-3 py-2 text-sm text-gray-600">
                      Managed through the App Store
                    </p>
                  ) : (
                    <Button
                      type="button"
                      variant="outline"
                      onClick={() => handleBillingAction()}
                      disabled={isBillingActionLoading}
                      className="w-full"
                    >
                      {isBillingActionLoading ? "Loading..." : "Manage Billing"}
                    </Button>
                  )
                ) : (
                  <div className="grid gap-2 sm:grid-cols-2">
                    {paidPlans.map((plan) => (
                      <Button
                        key={plan.id}
                        type="button"
                        variant={plan.highlighted ? "default" : "outline"}
                        onClick={() => handleBillingAction(plan.id)}
                        disabled={isBillingActionLoading}
                        className="h-auto min-h-12 flex-col gap-0.5 py-2"
                      >
                        <span>{isBillingActionLoading ? "Loading..." : plan.cta}</span>
                        <span className="text-xs font-normal opacity-80">
                          ${plan.priceMonthly}/mo · {plan.generationLimit} generations
                        </span>
                      </Button>
                    ))}
                  </div>
                )}
              </div>
            )}

            <Button
              onClick={handleSavePreferences}
              disabled={isLoading}
              className="w-full h-11"
            >
              {isLoading ? "Saving..." : "Save Preferences"}
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}
