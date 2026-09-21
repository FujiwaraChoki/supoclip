"use client";

import { useState, useEffect, useRef } from "react";
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
import { Type, Palette, CheckCircle, AlertCircle, Settings, ArrowLeft, Mail, KeyRound, ChevronRight, Mic, Music, SlidersHorizontal, Download, LayoutTemplate, ShieldAlert, Sparkles, ListOrdered } from "lucide-react";
import { ThemeToggle } from "@/components/theme-toggle";
import { RuntimeSettingsForm, type RuntimeSetting } from "@/components/admin/runtime-settings-form";
import { LlmConnectionTest } from "@/components/settings/llm-connection-test";
import { EmptyState } from "@/components/empty-state";
import { useDelayedFlag } from "@/hooks/use-delayed-flag";
import { toast } from "@/lib/toast";
import { PLATFORM_SAFE_ZONES, SAFE_ZONE_PLATFORM_IDS, type SafeZoneSelection } from "@/lib/safe-zones";
import { getDefaultSafeZonePlatform, setDefaultSafeZonePlatform } from "@/lib/safe-zone-settings";

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
  "GPU_ACCELERATION_ENABLED",
]);

const RANKING_SETTING_KEYS = new Set(["RANKING_SFX_OFFSET_PCT", "RANKING_DEFAULT_FRAMING"]);

const LLM_PROVIDER_SETTING_KEYS = new Set([
  "LLM_PROVIDER_MODE",
  "OLLAMA_BASE_URL",
  "OLLAMA_MODEL",
  "GOOGLE_API_KEY",
  "GEMINI_MODEL",
  "AUTO_GENERATE_METADATA_ENABLED",
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
  const [defaultSafeZonePlatform, setDefaultSafeZonePlatformState] = useState<SafeZoneSelection>("all");
  const [availableFonts, setAvailableFonts] = useState<Array<{ name: string, display_name: string }>>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [isFetching, setIsFetching] = useState(true);
  const showFetching = useDelayedFlag(isFetching);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);
  const [billingSummary, setBillingSummary] = useState<BillingSummary | null>(null);
  const [isBillingActionLoading, setIsBillingActionLoading] = useState(false);
  const [runtimeSettings, setRuntimeSettings] = useState<RuntimeSetting[]>([]);
  const [runtimeSettingsError, setRuntimeSettingsError] = useState<string | null>(null);
  const [llmStatus, setLlmStatus] = useState<{ ollama_connected: boolean; gemini_key_set: boolean } | null>(null);
  const [sfxFiles, setSfxFiles] = useState<SfxFile[]>([]);
  const [sfxError, setSfxError] = useState<string | null>(null);
  const [isUploadingRankingSfx, setIsUploadingRankingSfx] = useState(false);
  const rankingSfxInputRef = useRef<HTMLInputElement | null>(null);
  // Local-first: no login, so there's no real session — every user_id-shaped
  // value downstream just resolves to the single implicit local user.
  const session = { user: { id: LOCAL_USER_ID, name: "Local User", email: "", image: null as string | null } };

  const paidPlans = getPublicBillingPlans();

  // Safe Zone Overlay default platform: purely local (localStorage), not one
  // of the backend runtime settings — it's per-browser view state, not a
  // project or server setting.
  useEffect(() => {
    setDefaultSafeZonePlatformState(getDefaultSafeZonePlatform());
  }, []);

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
      const data = (await response.json()) as {
        settings?: RuntimeSetting[];
        llm_status?: { ollama_connected: boolean; gemini_key_set: boolean };
      };
      setRuntimeSettings(data.settings ?? []);
      setLlmStatus(data.llm_status ?? null);
      setRuntimeSettingsError(null);
    } catch {
      setRuntimeSettingsError("Unable to reach the backend settings API.");
    }
  };

  useEffect(() => {
    loadRuntimeSettings();
  }, []);

  const handleRankingSfxUpload = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    setIsUploadingRankingSfx(true);
    try {
      const formData = new FormData();
      formData.append("file", file);
      const response = await fetch("/api/ranking/settings/sfx", { method: "POST", body: formData });
      if (!response.ok) {
        const data = await response.json().catch(() => null);
        throw new Error(data?.detail || "Failed to upload SFX");
      }
      toast.success("Ranking transition SFX updated.");
      await loadRuntimeSettings();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to upload SFX");
    } finally {
      setIsUploadingRankingSfx(false);
    }
  };

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
  const llmProviderSettings = runtimeSettings.filter((setting) => LLM_PROVIDER_SETTING_KEYS.has(setting.key));
  const rankingSettings = runtimeSettings.filter((setting) => RANKING_SETTING_KEYS.has(setting.key));
  const rankingSfxFilename = runtimeSettings.find((setting) => setting.key === "RANKING_SFX_FILENAME")
    ?.current_value;
  const advancedSettings = runtimeSettings.filter(
    (setting) =>
      !TRANSCRIPTION_SETTING_KEYS.has(setting.key) &&
      !EXPORT_SETTING_KEYS.has(setting.key) &&
      !LLM_PROVIDER_SETTING_KEYS.has(setting.key) &&
      !RANKING_SETTING_KEYS.has(setting.key) &&
      setting.key !== "RANKING_SFX_FILENAME",
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
      const message = billingError instanceof Error ? billingError.message : "Billing action failed";
      setError(message);
      toast.error(message);
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
      toast.success("Preferences saved.");
      setTimeout(() => setSuccess(false), 3000);
    } catch (error) {
      console.error('Error saving preferences:', error);
      const message = error instanceof Error ? error.message : 'Failed to save preferences';
      setError(message);
      toast.error(message);
    } finally {
      setIsLoading(false);
    }
  };

  if (showFetching) {
    return (
      <div className="min-h-screen bg-background flex items-center justify-center p-4">
        <div className="space-y-4">
          <Skeleton className="h-4 w-32 mx-auto" />
          <Skeleton className="h-4 w-48 mx-auto" />
          <Skeleton className="h-4 w-24 mx-auto" />
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-background">
      {/* Header */}
      <div className="border-b bg-background">
        <div className="max-w-7xl mx-auto px-4 py-4">
          <div className="flex justify-between items-center">
            <Link href="/">
              <Button variant="ghost" size="sm">
                <ArrowLeft className="w-4 h-4" />
                Back
              </Button>
            </Link>

            <div className="flex items-center gap-3">
              <ThemeToggle />
              <Avatar className="w-8 h-8">
                <AvatarImage src={session.user.image || ""} />
                <AvatarFallback className="bg-background text-foreground text-sm">
                  {session.user.name?.charAt(0) || session.user.email?.charAt(0) || "U"}
                </AvatarFallback>
              </Avatar>
              <div className="hidden sm:block">
                <p className="text-sm font-medium text-foreground">{session.user.name}</p>
                <p className="text-xs text-muted-foreground">{session.user.email}</p>
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* Main Content */}
      <div className="max-w-4xl mx-auto px-4 py-10">
        <div>
          <div className="mb-8">
            <div className="flex items-center gap-2 mb-2">
              <Settings className="w-6 h-6 text-foreground" />
              <h2 className="text-2xl font-bold text-foreground">
                Settings
              </h2>
            </div>
            <p className="text-muted-foreground">
              Configure your default preferences for video clip generation
            </p>
          </div>

          <Separator className="my-8" />

          <div className="space-y-10">
            {/* Transcription Section */}
            <div className="space-y-4">
              <div>
                <h3 className="text-lg font-semibold text-foreground mb-1 flex items-center gap-2">
                  <Mic className="w-4 h-4" />
                  Transcription
                </h3>
                <p className="text-sm text-muted-foreground">
                  Choose the transcription provider and its API key/model/language. Persisted
                  here, with your .env values as fallback.
                </p>
              </div>
              <div className="rounded-lg border border-border bg-background">
                {runtimeSettingsError ? (
                  <div className="px-4 py-5 text-sm text-foreground font-bold">{runtimeSettingsError}</div>
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
                <h3 className="text-lg font-semibold text-foreground mb-1 flex items-center gap-2">
                  <Music className="w-4 h-4" />
                  Hooks
                </h3>
                <p className="text-sm text-muted-foreground">
                  Sound effects available for the hook&apos;s whoosh/riser (per-hook style is
                  configured when creating or editing a task). Drop{" "}
                  <code className="text-xs">.mp3</code>/<code className="text-xs">.wav</code> files
                  into <code className="text-xs">backend/sfx/</code> to add more.
                </p>
              </div>
              {sfxError ? (
                <p className="text-sm text-foreground font-bold">{sfxError}</p>
              ) : sfxFiles.length === 0 ? (
                <EmptyState
                  icon={Music}
                  title="No SFX files yet"
                  description="Add .mp3/.wav files to backend/sfx/ to make them available here."
                />
              ) : (
                <ul className="divide-y divide-border rounded-lg border border-border bg-background">
                  {sfxFiles.map((sfx) => (
                    <li key={sfx.name} className="flex items-center justify-between gap-3 px-4 py-3">
                      <span className="text-sm text-foreground">{sfx.display_name}</span>
                      <audio controls src={`${process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000"}/sfx/${encodeURIComponent(sfx.name)}`} className="h-8" />
                    </li>
                  ))}
                </ul>
              )}
            </div>

            <Separator />

            {/* Ranking Section — transition SFX, offset, and default framing for the Ranking tool */}
            <div className="space-y-4">
              <div>
                <h3 className="text-lg font-semibold text-foreground mb-1 flex items-center gap-2">
                  <ListOrdered className="w-4 h-4" />
                  Ranking
                </h3>
                <p className="text-sm text-muted-foreground">
                  Defaults for the Ranking tool&apos;s compilation render — the transition sound
                  effect, how early it starts before each cut, and how non-9:16 clips fill the
                  frame by default.
                </p>
              </div>
              <div className="rounded-lg border border-border bg-background px-4 py-4 flex items-center justify-between gap-3">
                <div>
                  <p className="text-sm font-medium text-foreground">Transition SFX</p>
                  <p className="text-xs text-muted-foreground">
                    {rankingSfxFilename ? rankingSfxFilename : "No SFX set — transitions are silent."}
                  </p>
                </div>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={isUploadingRankingSfx}
                  onClick={() => rankingSfxInputRef.current?.click()}
                >
                  {isUploadingRankingSfx ? "Uploading…" : rankingSfxFilename ? "Replace" : "Upload"}
                </Button>
                <input
                  ref={rankingSfxInputRef}
                  type="file"
                  accept="audio/mpeg,audio/wav,audio/mp4,audio/ogg,.mp3,.wav,.m4a,.ogg"
                  className="hidden"
                  onChange={handleRankingSfxUpload}
                />
              </div>
              <div className="rounded-lg border border-border bg-background">
                {runtimeSettingsError ? (
                  <div className="px-4 py-5 text-sm text-foreground font-bold">{runtimeSettingsError}</div>
                ) : (
                  <RuntimeSettingsForm settings={rankingSettings} onSaved={loadRuntimeSettings} />
                )}
              </div>
            </div>

            <Separator />

            {/* Export Section — clip count/duration/mode defaults applied at generation time */}
            <div className="space-y-4">
              <div>
                <h3 className="text-lg font-semibold text-foreground mb-1 flex items-center gap-2">
                  <Download className="w-4 h-4" />
                  Export
                </h3>
                <p className="text-sm text-muted-foreground">
                  Defaults for clip count, duration, processing mode, and rendering. Persisted
                  here, with your .env values as fallback.
                </p>
              </div>
              <div className="rounded-lg border border-border bg-background">
                {runtimeSettingsError ? (
                  <div className="px-4 py-5 text-sm text-foreground font-bold">{runtimeSettingsError}</div>
                ) : (
                  <RuntimeSettingsForm settings={exportSettings} onSaved={loadRuntimeSettings} />
                )}
              </div>
            </div>

            <Separator />

            {/* LLM Provider Section — Ollama (local) primary, Gemini fallback */}
            <div className="space-y-4">
              <div>
                <h3 className="text-lg font-semibold text-foreground mb-1 flex items-center gap-2">
                  <Sparkles className="w-4 h-4" />
                  LLM Provider
                </h3>
                <p className="text-sm text-muted-foreground">
                  Content-policy detection and metadata generation use a local Ollama model by
                  default — free, private, and unlimited. Gemini is an optional fallback for when
                  Ollama is unavailable.
                </p>
              </div>
              <div className="rounded-lg border border-border bg-background">
                {runtimeSettingsError ? (
                  <div className="px-4 py-5 text-sm text-foreground font-bold">{runtimeSettingsError}</div>
                ) : (
                  <>
                    <RuntimeSettingsForm settings={llmProviderSettings} onSaved={loadRuntimeSettings} />
                    <LlmConnectionTest status={llmStatus} />
                  </>
                )}
              </div>
            </div>

            <Separator />

            {/* UI Section — local subtitle appearance */}
            <div className="space-y-8">
              <div className="space-y-6">
                <div>
                  <h3 className="text-lg font-semibold text-foreground mb-1 flex items-center gap-2">
                    <Type className="w-4 h-4" />
                    UI — Subtitle Appearance
                  </h3>
                  <p className="text-sm text-muted-foreground">
                    These settings will be applied to all new video processing tasks
                  </p>
                </div>

                {/* Font Family Selector */}
                <div className="space-y-2">
                  <Label className="text-sm font-medium text-foreground flex items-center gap-2">
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
                  <Label className="text-sm font-medium text-foreground">
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
                  <div className="flex justify-between text-xs text-muted-foreground">
                    <span>12px</span>
                    <span>48px</span>
                  </div>
                </div>

                {/* Font Color Picker */}
                <div className="space-y-2">
                  <Label className="text-sm font-medium text-foreground flex items-center gap-2">
                    <Palette className="w-4 h-4" />
                    Font Color
                  </Label>
                  <div className="flex items-center gap-2">
                    <input
                      type="color"
                      value={fontColor}
                      onChange={(e) => setFontColor(e.target.value)}
                      disabled={isLoading}
                      className="w-12 h-10 rounded border border-border cursor-pointer disabled:cursor-not-allowed"
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
                        className="w-8 h-8 rounded border-2 border-border cursor-pointer hover:scale-110 transition-transform disabled:cursor-not-allowed"
                        style={{ backgroundColor: color }}
                        title={color}
                      />
                    ))}
                  </div>
                </div>

                {/* Preview */}
                <div className="space-y-2">
                  <Label className="text-sm font-medium text-foreground">Preview</Label>
                  <div className="p-6 bg-foreground rounded-lg flex items-center justify-center min-h-[100px]">
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

                {/* Safe Zone Overlay default platform */}
                <div className="space-y-2">
                  <Label className="text-sm font-medium text-foreground">
                    Safe Zone Overlay — Default Platform
                  </Label>
                  <p className="text-xs text-muted-foreground">
                    Used for new projects that haven&apos;t picked their own Safe Zones platform yet.
                  </p>
                  <Select
                    value={defaultSafeZonePlatform}
                    onValueChange={(value) => {
                      const platform = value as SafeZoneSelection;
                      setDefaultSafeZonePlatformState(platform);
                      setDefaultSafeZonePlatform(platform);
                    }}
                  >
                    <SelectTrigger className="w-full">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="all">All</SelectItem>
                      {SAFE_ZONE_PLATFORM_IDS.map((id) => (
                        <SelectItem key={id} value={id}>
                          {PLATFORM_SAFE_ZONES[id].label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              </div>
            </div>

            <Separator />

            {/* Notifications Section */}
            <div className="space-y-6">
              <div>
                <h3 className="text-lg font-semibold text-foreground mb-1">
                  Notifications
                </h3>
                <p className="text-sm text-muted-foreground">
                  Manage how you receive updates about your clips
                </p>
              </div>

              <div className="flex items-center justify-between">
                <Label htmlFor="completion-emails" className="flex items-center gap-2 text-sm font-medium text-foreground cursor-pointer">
                  <Mail className="w-4 h-4" />
                  Completion emails
                  <span className="text-muted-foreground font-normal">— get notified when clips are ready</span>
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
                <h3 className="text-lg font-semibold text-foreground mb-1 flex items-center gap-2">
                  <SlidersHorizontal className="w-4 h-4" />
                  Advanced
                </h3>
                <p className="text-sm text-muted-foreground">
                  LLM provider, API keys, and download/B-roll providers. Persisted here, with
                  your .env values as fallback.
                </p>
              </div>
              <div className="rounded-lg border border-border bg-background">
                {runtimeSettingsError ? (
                  <div className="px-4 py-5 text-sm text-foreground font-bold">{runtimeSettingsError}</div>
                ) : (
                  <RuntimeSettingsForm settings={advancedSettings} onSaved={loadRuntimeSettings} />
                )}
              </div>
            </div>

            <Separator />

            {/* Developer Section */}
            <div className="space-y-6">
              <div>
                <h3 className="text-lg font-semibold text-foreground mb-1">
                  Developer
                </h3>
                <p className="text-sm text-muted-foreground">
                  Programmatic access for tools like the SupoClip MCP server
                </p>
              </div>

              <Link href="/settings/api-keys" className="block">
                <div className="flex items-center justify-between p-4 border border-border rounded-lg hover:border-foreground transition-colors">
                  <div className="flex items-center gap-3">
                    <KeyRound className="w-5 h-5 text-foreground" />
                    <div>
                      <p className="text-sm font-medium text-foreground">API Keys</p>
                      <p className="text-xs text-muted-foreground">Create and manage API keys</p>
                    </div>
                  </div>
                  <ChevronRight className="w-4 h-4 text-muted-foreground" />
                </div>
              </Link>

              <Link href="/settings/templates" className="block">
                <div className="flex items-center justify-between p-4 border border-border rounded-lg hover:border-foreground transition-colors">
                  <div className="flex items-center gap-3">
                    <LayoutTemplate className="w-5 h-5 text-foreground" />
                    <div>
                      <p className="text-sm font-medium text-foreground">Templates</p>
                      <p className="text-xs text-muted-foreground">Reusable project settings bundles</p>
                    </div>
                  </div>
                  <ChevronRight className="w-4 h-4 text-muted-foreground" />
                </div>
              </Link>

              <Link href="/settings/content-policy" className="block">
                <div className="flex items-center justify-between p-4 border border-border rounded-lg hover:border-foreground transition-colors">
                  <div className="flex items-center gap-3">
                    <ShieldAlert className="w-5 h-5 text-foreground" />
                    <div>
                      <p className="text-sm font-medium text-foreground">Content Policy</p>
                      <p className="text-xs text-muted-foreground">Edit flagged-word lists per category</p>
                    </div>
                  </div>
                  <ChevronRight className="w-4 h-4 text-muted-foreground" />
                </div>
              </Link>
            </div>

            <Separator className="mb-4" />

            {/* Success/Error Messages */}
            {success && (
              <Alert className="border-primary">
                <CheckCircle className="h-4 w-4 text-primary" />
                <AlertDescription className="text-sm text-primary">
                  Preferences saved successfully!
                </AlertDescription>
              </Alert>
            )}

            {error && (
              <Alert variant="destructive">
                <AlertCircle className="h-4 w-4" />
                <AlertDescription className="text-sm font-bold">
                  {error}
                </AlertDescription>
              </Alert>
            )}

            {/* Save Button */}
            {billingSummary?.monetization_enabled && (
              <div className="border border-border rounded-lg p-4 space-y-3">
                <div>
                  <h3 className="text-lg font-semibold text-foreground">Billing</h3>
                  {!isPaidBillingPlan(billingSummary.plan) && (
                    <p className="text-sm text-muted-foreground">Video processing requires a paid plan.</p>
                  )}
                  <p className="text-sm text-muted-foreground">
                    {billingSummary.upgrade_required
                      ? "Current plan cannot create generations."
                      : billingSummary.usage_limit === null
                      ? `${billingSummary.usage_count} generations in this billing period`
                      : `${billingSummary.usage_count}/${billingSummary.usage_limit} generations used this period`}
                  </p>
                  <p className="text-sm text-muted-foreground">
                    Plan: {formatBillingPlanName(billingSummary.plan)} ({billingSummary.subscription_status})
                  </p>
                </div>

                {isPaidBillingPlan(billingSummary.plan) ? (
                  billingSummary.subscription_provider === "apple" ? (
                    <p className="rounded-md border border-border bg-background px-3 py-2 text-sm text-muted-foreground">
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
