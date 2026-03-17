"use client";

import { useState, useRef, useEffect, useCallback } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Progress } from "@/components/ui/progress";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { Separator } from "@/components/ui/separator";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Skeleton } from "@/components/ui/skeleton";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Slider } from "@/components/ui/slider";
import { signOut, useSession } from "@/lib/auth-client";
import { track } from "@/lib/datafast";
import { formatSupportMessage, parseApiError } from "@/lib/api-error";
import Link from "next/link";
import Image from "next/image";
import { ArrowRight, Youtube, CheckCircle, AlertCircle, Loader2, Palette, Type, Paintbrush, Film, Sparkles, Upload, Monitor, Menu, X, LogOut, List, Shield, Settings } from "lucide-react";
import { Switch } from "@/components/ui/switch";
import LandingPage from "@/components/landing-page";
import { isLandingOnlyModeEnabled } from "@/lib/app-flags";

interface LatestTask {
  id: string;
  source_title: string;
  source_type: string;
  status: string;
  clips_count: number;
  created_at: string;
}

interface BillingSummary {
  monetization_enabled: boolean;
  plan: string;
  subscription_status: string;
  usage_count: number;
  usage_limit: number | null;
  remaining: number | null;
  can_create_task: boolean;
  upgrade_required: boolean;
  reason: string | null;
}

interface FontOption {
  name: string;
  display_name: string;
  format?: string;
}

const extractYouTubeVideoId = (value: string): string | null => {
  const input = value.trim();
  if (!input) return null;

  try {
    const parsed = new URL(input);
    const host = parsed.hostname.replace(/^www\./, "");

    if (host === "youtu.be") {
      const id = parsed.pathname.split("/").filter(Boolean)[0];
      return id && id.length === 11 ? id : null;
    }

    if (host === "youtube.com" || host === "m.youtube.com" || host === "music.youtube.com") {
      const fromSearch = parsed.searchParams.get("v");
      if (fromSearch && fromSearch.length === 11) {
        return fromSearch;
      }

      const pathParts = parsed.pathname.split("/").filter(Boolean);
      const embedId = pathParts[0] === "embed" ? pathParts[1] : null;
      if (embedId && embedId.length === 11) {
        return embedId;
      }
    }
  } catch {
    return null;
  }

  return null;
};

const getYouTubeThumbnailUrl = (value: string): string | null => {
  const videoId = extractYouTubeVideoId(value);
  return videoId ? `https://i.ytimg.com/vi/${videoId}/hqdefault.jpg` : null;
};

export default function Home() {
  const [url, setUrl] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [progress, setProgress] = useState(0);
  const [statusMessage, setStatusMessage] = useState("");
  const [currentStep, setCurrentStep] = useState("");
  const [sourceType, setSourceType] = useState<"youtube" | "upload">("youtube");
  const [fileName, setFileName] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sourceTitle, setSourceTitle] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const { data: session, isPending } = useSession();
  const isAdmin = Boolean((session?.user as { is_admin?: boolean } | undefined)?.is_admin);

  // Font customization states
  const [fontFamily, setFontFamily] = useState("TikTokSans-Regular");
  const [fontSize, setFontSize] = useState(24);
  const [fontColor, setFontColor] = useState("#FFFFFF");
  const [availableFonts, setAvailableFonts] = useState<FontOption[]>([]);
  const [showAdvancedOptions, setShowAdvancedOptions] = useState(true);
  const [fontSearch, setFontSearch] = useState("");
  const [fontLoadError, setFontLoadError] = useState<string | null>(null);
  const [isUploadingFont, setIsUploadingFont] = useState(false);
  const fontUploadInputRef = useRef<HTMLInputElement | null>(null);

  // Caption template and B-roll states
  const [captionTemplate, setCaptionTemplate] = useState("default");
  const [availableTemplates, setAvailableTemplates] = useState<Array<{ id: string, name: string, description: string, animation: string, font_family?: string, font_size?: number, font_color?: string }>>([]);
  const [includeBroll, setIncludeBroll] = useState(false);
  const [brollAvailable, setBrollAvailable] = useState(false);
  const [outputFormat, setOutputFormat] = useState<"vertical" | "original">("vertical");
  const [addSubtitles, setAddSubtitles] = useState(true);

  // Latest task state
  const [latestTask, setLatestTask] = useState<LatestTask | null>(null);
  const [isLoadingLatest, setIsLoadingLatest] = useState(false);
  const [billingSummary, setBillingSummary] = useState<BillingSummary | null>(null);
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const apiUrl = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';
  const taskApiUrl = "/api/tasks";
  const youtubeThumbnailUrl = sourceType === "youtube" ? getYouTubeThumbnailUrl(url) : null;

  const refreshFonts = useCallback(async () => {
    try {
      setFontLoadError(null);
      const response = await fetch("/api/fonts", {
        cache: "no-store",
      });
      if (!response.ok) {
        throw new Error(`Failed to load fonts (${response.status})`);
      }

      const data = await response.json();
      const fonts: FontOption[] = data.fonts || [];
      setAvailableFonts(fonts);

      const fontFaceStyles = fonts.map((font) => {
        const format = font.format === "otf" ? "opentype" : "truetype";
        return `
          @font-face {
            font-family: '${font.name}';
            src: url('/api/fonts/${font.name}') format('${format}');
            font-weight: normal;
            font-style: normal;
          }
        `;
      }).join("\n");

      const styleElement = document.createElement("style");
      styleElement.id = "custom-fonts";
      styleElement.innerHTML = fontFaceStyles;

      const existingStyle = document.getElementById("custom-fonts");
      if (existingStyle) {
        existingStyle.remove();
      }

      document.head.appendChild(styleElement);
    } catch (error) {
      console.error("Failed to load fonts:", error);
      setFontLoadError("Could not load fonts right now.");
    }
  }, []);

  useEffect(() => {
    void refreshFonts();
  }, [refreshFonts]);

  // Load caption templates and check B-roll availability
  useEffect(() => {
    const loadTemplates = async () => {
      try {
        const response = await fetch(`${apiUrl}/caption-templates`);
        if (response.ok) {
          const data = await response.json();
          setAvailableTemplates(data.templates || []);
        }
      } catch (error) {
        console.error('Failed to load caption templates:', error);
      }
    };

    const checkBrollStatus = async () => {
      try {
        const response = await fetch(`${apiUrl}/broll/status`);
        if (response.ok) {
          const data = await response.json();
          setBrollAvailable(data.configured || false);
        }
      } catch (error) {
        console.error('Failed to check B-roll status:', error);
      }
    };

    loadTemplates();
    checkBrollStatus();
  }, [apiUrl]);

  // Load user preferences as defaults
  useEffect(() => {
    const loadUserPreferences = async () => {
      if (!session?.user?.id) return;

      try {
        const response = await fetch('/api/preferences');
        if (response.ok) {
          const data = await response.json();
          setFontFamily(data.fontFamily || "TikTokSans-Regular");
          setFontSize(data.fontSize || 24);
          setFontColor(data.fontColor || "#FFFFFF");
        }
      } catch (error) {
        console.error('Failed to load user preferences:', error);
      }
    };

    loadUserPreferences();
  }, [session?.user?.id]);

  // Load latest task
  useEffect(() => {
    const fetchLatestTask = async () => {
      if (!session?.user?.id) return;

      try {
        setIsLoadingLatest(true);
        const response = await fetch(`${taskApiUrl}/`, {
          cache: "no-store",
        });

        if (response.ok) {
          const data = await response.json();
          if (data.tasks && data.tasks.length > 0) {
            setLatestTask(data.tasks[0]); // Get the first (latest) task
          }
        }
      } catch (error) {
        console.error('Failed to load latest task:', error);
      } finally {
        setIsLoadingLatest(false);
      }
    };

    fetchLatestTask();
  }, [session?.user?.id, taskApiUrl]);

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
      } catch (error) {
        console.error("Failed to load billing summary:", error);
      }
    };

    fetchBillingSummary();
  }, [session?.user?.id, apiUrl]);

  // Always treat file input as uncontrolled, and store file in a ref
  const fileRef = useRef<File | null>(null);

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0] || null;
    fileRef.current = file;
    setFileName(file ? file.name : null);
  };

  const handleTemplateChange = (templateId: string) => {
    setCaptionTemplate(templateId);

    const selectedTemplate = availableTemplates.find((template) => template.id === templateId);
    if (!selectedTemplate) {
      return;
    }

    if (selectedTemplate.font_family) {
      setFontFamily(selectedTemplate.font_family);
    }
    if (typeof selectedTemplate.font_size === "number") {
      setFontSize(selectedTemplate.font_size);
    }
    if (selectedTemplate.font_color) {
      setFontColor(selectedTemplate.font_color);
    }
  };

  const handleFontUpload = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) {
      return;
    }

    const isSupported = file.name.toLowerCase().endsWith(".ttf") || file.name.toLowerCase().endsWith(".otf");
    if (!isSupported) {
      setError("Only .ttf and .otf files are supported for custom fonts.");
      return;
    }

    try {
      setIsUploadingFont(true);
      setError(null);
      const formData = new FormData();
      formData.append("file", file);

      const response = await fetch("/api/fonts/upload", {
        method: "POST",
        body: formData,
      });

      if (!response.ok) {
        const parsed = await parseApiError(response, "Failed to upload font");
        setError(formatSupportMessage(parsed));
        return;
      }

      const data = await response.json();
      if (data?.font?.name) {
        setFontFamily(data.font.name);
      }
      await refreshFonts();
    } catch (uploadError) {
      console.error("Failed to upload font:", uploadError);
      setError("Failed to upload font. Please try again.");
    } finally {
      setIsUploadingFont(false);
    }
  };

  const filteredFonts = availableFonts.filter((font) => {
    const keyword = fontSearch.toLowerCase().trim();
    if (!keyword) {
      return true;
    }

    return font.display_name.toLowerCase().includes(keyword) || font.name.toLowerCase().includes(keyword);
  });

  const canUploadCustomFonts =
    !billingSummary?.monetization_enabled ||
    (billingSummary.plan === "pro" && ["active", "trialing"].includes(billingSummary.subscription_status));

  const handleSignOut = async () => {
    await signOut();
    window.location.href = "/sign-in";
  };

  const getStepIcon = (step: string) => {
    const iconMap: Record<string, React.ReactElement> = {
      validation: <Loader2 className="w-4 h-4 animate-spin text-blue-500" />,
      user_check: <Loader2 className="w-4 h-4 animate-spin text-blue-500" />,
      source_analysis: <Loader2 className="w-4 h-4 animate-spin text-blue-500" />,
      youtube_info: <Youtube className="w-4 h-4 text-red-500" />,
      database_save: <Loader2 className="w-4 h-4 animate-spin text-blue-500" />,
      download: <Loader2 className="w-4 h-4 animate-spin text-green-500" />,
      transcript: <Loader2 className="w-4 h-4 animate-spin text-purple-500" />,
      ai_analysis: <Loader2 className="w-4 h-4 animate-spin text-orange-500" />,
      clip_generation: <Loader2 className="w-4 h-4 animate-spin text-indigo-500" />,
      save_clips: <Loader2 className="w-4 h-4 animate-spin text-pink-500" />,
      complete: <CheckCircle className="w-4 h-4 text-green-500" />,
    };
    return iconMap[step] || <Loader2 className="w-4 h-4 animate-spin text-gray-500" />;
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();

    if (sourceType === "upload" && !fileRef.current) return;
    if (sourceType === "youtube" && !url.trim()) return;
    if (!session?.user?.id) return;
    if (billingSummary?.monetization_enabled && !billingSummary.can_create_task) {
      setError(billingSummary.reason || "Active subscription required to continue processing.");
      return;
    }

    setIsLoading(true);
    setProgress(0);
    setError(null);
    setStatusMessage("");
    setCurrentStep("");
    setSourceTitle(null);

    const normalizedColor = /^#[0-9A-Fa-f]{6}$/.test(fontColor)
      ? fontColor
      : "#FFFFFF";

    try {
      let videoUrl = url;

      // If uploading file, upload it first
      if (sourceType === "upload" && fileRef.current) {
        setStatusMessage("Uploading video file...");
        setProgress(5);

        const formData = new FormData();
        formData.append("video", fileRef.current);
        const uploadResponse = await fetch("/api/upload", {
          method: "POST",
          body: formData
        });

        if (!uploadResponse.ok) {
          const uploadError = await parseApiError(
            uploadResponse,
            `Upload error: ${uploadResponse.status}`
          );
          throw new Error(formatSupportMessage(uploadError));
        }

        const uploadResult = await uploadResponse.json();
        videoUrl = uploadResult.video_path;
      }

      // Step 1: Start the task (using new refactored endpoint)
      const startResponse = await fetch("/api/tasks/create", {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          source: {
            url: videoUrl,
            title: null
          },
          font_options: {
            font_family: fontFamily,
            font_size: fontSize,
            font_color: normalizedColor
          },
          caption_template: captionTemplate,
          include_broll: includeBroll,
          processing_mode: "fast",
          output_format: outputFormat,
          add_subtitles: addSubtitles
        }),
      });

      if (!startResponse.ok) {
        const startError = await parseApiError(
          startResponse,
          `API error: ${startResponse.status}`
        );
        throw new Error(formatSupportMessage(startError));
      }

      const startResult = await startResponse.json();
      const taskIdFromStart = startResult.task_id;
      track("task_created", {
        source_type: sourceType,
        caption_template: captionTemplate,
        include_broll: includeBroll,
        output_format: outputFormat,
        add_subtitles: addSubtitles,
        processing_mode: "fast",
      });
      // Redirect immediately to the task page
      window.location.href = `/tasks/${taskIdFromStart}`;

    } catch (error) {
      console.error('Error processing video:', error);
      setError(error instanceof Error ? error.message : 'Failed to process video. Please try again.');
    } finally {
      setIsLoading(false);
      setProgress(0);
      setStatusMessage("");
      setCurrentStep("");
      setFileName(null);
      fileRef.current = null;
      setUrl("");
      if (fileInputRef.current) {
        fileInputRef.current.value = "";
      }
    }
  };

  if (isPending) {
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

  if (isLandingOnlyModeEnabled || !session?.user) {
    return <LandingPage />;
  }

  return (
    <div className="min-h-screen bg-white">
      {/* Header */}
      <div className="border-b bg-white relative">
        <div className="max-w-7xl mx-auto px-4 py-4">
          <div className="flex justify-between items-center">
            <div className="flex items-center gap-3">
              <Image
                src="/logo.png"
                alt="SupoClip"
                width={24}
                height={24}
                className="rounded-lg"
              />
              <h1 className="text-xl font-bold text-black">SupoClip</h1>
            </div>

            {/* Desktop nav */}
            <div className="hidden md:flex items-center gap-2">
              {billingSummary?.monetization_enabled && (
                <div className="flex items-center gap-2 mr-1">
                  <Badge
                    className={`text-[10px] px-1.5 py-0 h-5 ${
                      billingSummary.plan === "pro"
                        ? "bg-stone-900 text-white"
                        : "bg-stone-100 text-stone-600 border border-stone-200"
                    }`}
                  >
                    {billingSummary.plan === "pro" ? "Pro" : "Free"}
                  </Badge>
                  <div className="flex items-center gap-1.5">
                    <div className="w-16 h-1.5 bg-stone-200 rounded-full overflow-hidden">
                      <div
                        className={`h-full rounded-full transition-all duration-500 ${
                          billingSummary.usage_limit &&
                          billingSummary.usage_count / billingSummary.usage_limit > 0.8
                            ? "bg-red-500"
                            : "bg-stone-900"
                        }`}
                        style={{
                          width: billingSummary.usage_limit
                            ? `${Math.min((billingSummary.usage_count / billingSummary.usage_limit) * 100, 100)}%`
                            : "0%",
                        }}
                      />
                    </div>
                    <span className="text-[11px] text-stone-500 tabular-nums whitespace-nowrap">
                      {billingSummary.usage_limit
                        ? `${billingSummary.usage_count}/${billingSummary.usage_limit}`
                        : `${billingSummary.usage_count}`}
                    </span>
                  </div>
                </div>
              )}
              <Link href="/list">
                <Button variant="outline" size="sm">
                  All Generations
                </Button>
              </Link>
              {isAdmin && (
                <Link href="/admin">
                  <Button variant="outline" size="sm">
                    Admin
                  </Button>
                </Link>
              )}
              <Button variant="outline" size="sm" onClick={handleSignOut}>
                Sign Out
              </Button>
              <Link href="/settings" className="flex items-center gap-3 hover:bg-gray-50 rounded-lg px-3 py-2 transition-colors cursor-pointer">
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
              </Link>
            </div>

            {/* Mobile hamburger */}
            <div className="flex items-center gap-2 md:hidden">
              {billingSummary?.monetization_enabled && (
                <Badge
                  className={`text-[10px] px-1.5 py-0 h-5 ${
                    billingSummary.plan === "pro"
                      ? "bg-stone-900 text-white"
                      : "bg-stone-100 text-stone-600 border border-stone-200"
                  }`}
                >
                  {billingSummary.plan === "pro" ? "Pro" : "Free"}
                </Badge>
              )}
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setMobileMenuOpen(!mobileMenuOpen)}
                className="p-2"
                aria-label="Toggle menu"
              >
                {mobileMenuOpen ? <X className="w-5 h-5" /> : <Menu className="w-5 h-5" />}
              </Button>
            </div>
          </div>
        </div>

        {/* Mobile menu dropdown */}
        {mobileMenuOpen && (
          <div className="md:hidden border-t bg-white absolute left-0 right-0 z-50 shadow-lg">
            <div className="px-4 py-3 space-y-1">
              {/* User info */}
              <Link
                href="/settings"
                onClick={() => setMobileMenuOpen(false)}
                className="flex items-center gap-3 rounded-lg px-3 py-2.5 hover:bg-gray-50 transition-colors"
              >
                <Avatar className="w-8 h-8">
                  <AvatarImage src={session.user.image || ""} />
                  <AvatarFallback className="bg-gray-100 text-black text-sm">
                    {session.user.name?.charAt(0) || session.user.email?.charAt(0) || "U"}
                  </AvatarFallback>
                </Avatar>
                <div className="min-w-0">
                  <p className="text-sm font-medium text-black truncate">{session.user.name}</p>
                  <p className="text-xs text-gray-500 truncate">{session.user.email}</p>
                </div>
              </Link>

              <Separator />

              {/* Usage bar (mobile) */}
              {billingSummary?.monetization_enabled && (
                <div className="flex items-center gap-2 px-3 py-2">
                  <div className="flex-1 h-1.5 bg-stone-200 rounded-full overflow-hidden">
                    <div
                      className={`h-full rounded-full transition-all duration-500 ${
                        billingSummary.usage_limit &&
                        billingSummary.usage_count / billingSummary.usage_limit > 0.8
                          ? "bg-red-500"
                          : "bg-stone-900"
                      }`}
                      style={{
                        width: billingSummary.usage_limit
                          ? `${Math.min((billingSummary.usage_count / billingSummary.usage_limit) * 100, 100)}%`
                          : "0%",
                      }}
                    />
                  </div>
                  <span className="text-xs text-stone-500 tabular-nums whitespace-nowrap">
                    {billingSummary.usage_limit
                      ? `${billingSummary.usage_count}/${billingSummary.usage_limit}`
                      : `${billingSummary.usage_count}`}
                  </span>
                </div>
              )}

              {/* Nav links */}
              <Link
                href="/list"
                onClick={() => setMobileMenuOpen(false)}
                className="flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm text-stone-700 hover:bg-gray-50 transition-colors"
              >
                <List className="w-4 h-4 text-stone-400" />
                All Generations
              </Link>
              {isAdmin && (
                <Link
                  href="/admin"
                  onClick={() => setMobileMenuOpen(false)}
                  className="flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm text-stone-700 hover:bg-gray-50 transition-colors"
                >
                  <Shield className="w-4 h-4 text-stone-400" />
                  Admin
                </Link>
              )}
              <Link
                href="/settings"
                onClick={() => setMobileMenuOpen(false)}
                className="flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm text-stone-700 hover:bg-gray-50 transition-colors"
              >
                <Settings className="w-4 h-4 text-stone-400" />
                Settings
              </Link>

              <Separator />

              <button
                onClick={() => {
                  setMobileMenuOpen(false);
                  handleSignOut();
                }}
                className="flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm text-red-600 hover:bg-red-50 transition-colors w-full text-left"
              >
                <LogOut className="w-4 h-4" />
                Sign Out
              </button>
            </div>
          </div>
        )}
      </div>

      {/* Main Content */}
      <div className="max-w-6xl mx-auto px-6 py-10">
        {/* Latest Generation Banner */}
        {latestTask && (
          <Link href={`/tasks/${latestTask.id}`} className="block mb-8">
            <div className="flex items-center justify-between p-4 rounded-xl border border-stone-200 bg-stone-50/50 hover:bg-stone-50 transition-colors group">
              <div className="flex items-center gap-4 min-w-0">
                <div className="flex-shrink-0 w-10 h-10 rounded-lg bg-stone-900 flex items-center justify-center">
                  <Film className="w-5 h-5 text-white" />
                </div>
                <div className="min-w-0">
                  <p className="text-sm font-medium text-stone-900 truncate">
                    {latestTask.source_title}
                  </p>
                  <div className="flex items-center gap-2 text-xs text-stone-500 mt-0.5">
                    <span className="capitalize">{latestTask.source_type}</span>
                    <span>&middot;</span>
                    <span>{new Date(latestTask.created_at).toLocaleDateString()}</span>
                    <span>&middot;</span>
                    <span>{latestTask.clips_count} {latestTask.clips_count === 1 ? "clip" : "clips"}</span>
                  </div>
                </div>
              </div>
              <div className="flex items-center gap-3 flex-shrink-0">
                {latestTask.status === "completed" ? (
                  <Badge className="bg-green-100 text-green-800 text-xs">
                    <CheckCircle className="w-3 h-3 mr-1" />
                    Completed
                  </Badge>
                ) : latestTask.status === "processing" ? (
                  <Badge className="bg-blue-100 text-blue-800 text-xs">
                    <Loader2 className="w-3 h-3 animate-spin" />
                    Processing
                  </Badge>
                ) : (
                  <Badge variant="outline" className="text-xs">{latestTask.status}</Badge>
                )}
                <ArrowRight className="w-4 h-4 text-stone-400 group-hover:text-stone-600 transition-colors" />
              </div>
            </div>
          </Link>
        )}

        {isLoadingLatest && (
          <div className="mb-8 p-4 rounded-xl border border-stone-200">
            <div className="flex items-center gap-4">
              <Skeleton className="w-10 h-10 rounded-lg" />
              <div>
                <Skeleton className="h-4 w-48 mb-1.5" />
                <Skeleton className="h-3 w-32" />
              </div>
            </div>
          </div>
        )}

        {/* Two Column Layout */}
        <div className="flex flex-col lg:flex-row gap-10 items-start">
          {/* Left Column — Form */}
          <div className="flex-1 min-w-0">
            <div className="mb-8">
              <h2 className="text-2xl font-bold text-stone-900 mb-2">
                Create New Clip
              </h2>
              <p className="text-stone-500">
                Paste a YouTube link or upload a video — AI handles the rest.
              </p>
            </div>

            <form onSubmit={handleSubmit} className="space-y-6">
              {/* Source Type Tabs */}
              <div className="space-y-3">
                <div className="flex gap-2">
                  <button
                    type="button"
                    onClick={() => {
                      setSourceType("youtube");
                      setFileName(null);
                      fileRef.current = null;
                      if (fileInputRef.current) fileInputRef.current.value = "";
                    }}
                    disabled={isLoading}
                    className={`flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-all ${
                      sourceType === "youtube"
                        ? "bg-stone-900 text-white shadow-sm"
                        : "bg-stone-100 text-stone-600 hover:bg-stone-200"
                    }`}
                  >
                    <Youtube className="w-4 h-4" />
                    YouTube URL
                  </button>
                  <button
                    type="button"
                    onClick={() => setSourceType("upload")}
                    disabled={isLoading}
                    className={`flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-all ${
                      sourceType === "upload"
                        ? "bg-stone-900 text-white shadow-sm"
                        : "bg-stone-100 text-stone-600 hover:bg-stone-200"
                    }`}
                  >
                    <Upload className="w-4 h-4" />
                    Upload Video
                  </button>
                </div>

                {/* URL / Upload Input */}
                {sourceType === "youtube" ? (
                  <div className="relative">
                    <Youtube className="absolute left-4 top-1/2 -translate-y-1/2 w-5 h-5 text-stone-400" />
                    <Input
                      id="youtube-url"
                      type="url"
                      placeholder="https://www.youtube.com/watch?v=..."
                      value={url}
                      onChange={(e) => setUrl(e.target.value)}
                      disabled={isLoading}
                      className="h-14 pl-12 text-base rounded-xl border-stone-300 focus:border-stone-500 placeholder:text-stone-400"
                    />
                  </div>
                ) : (
                  <div
                    className="relative border-2 border-dashed border-stone-300 rounded-xl p-8 text-center hover:border-stone-400 transition-colors cursor-pointer"
                    onClick={() => !isLoading && fileInputRef.current?.click()}
                  >
                    <input
                      id="video-upload"
                      type="file"
                      accept="video/*"
                      ref={fileInputRef}
                      onChange={handleFileChange}
                      disabled={isLoading}
                      className="hidden"
                    />
                    <Upload className="w-8 h-8 text-stone-400 mx-auto mb-3" />
                    {fileName ? (
                      <p className="text-sm font-medium text-stone-900">{fileName}</p>
                    ) : (
                      <>
                        <p className="text-sm font-medium text-stone-700">Drop a video file here or click to browse</p>
                        <p className="text-xs text-stone-400 mt-1">MP4, MOV, AVI up to 500MB</p>
                      </>
                    )}
                  </div>
                )}
              </div>

              {/* Caption & Style Section */}
              <Card className="border-stone-200">
                <CardContent className="px-4 pt-0 pb-2.5 space-y-2.5">
                  <div className="flex items-center gap-2 text-sm font-medium text-stone-900">
                    <Sparkles className="w-4 h-4" />
                    Style & Captions
                  </div>

                  {/* Caption Template Selector */}
                  <div className="space-y-2">
                    <label className="text-sm text-stone-600">
                      Caption Style
                    </label>
                    <Select value={captionTemplate} onValueChange={handleTemplateChange} disabled={isLoading}>
                      <SelectTrigger className="w-full h-11">
                        <SelectValue>
                          {availableTemplates.find(t => t.id === captionTemplate)?.name || "Select style"}
                        </SelectValue>
                      </SelectTrigger>
                      <SelectContent>
                        {availableTemplates.length > 0 ? (
                          availableTemplates.map((template) => (
                            <SelectItem key={template.id} value={template.id} className="py-3">
                              <span className="font-medium">{template.name}</span>
                              <span className="text-xs text-gray-500 ml-2">{template.description}</span>
                            </SelectItem>
                          ))
                        ) : (
                          <SelectItem value="default">Default</SelectItem>
                        )}
                      </SelectContent>
                    </Select>
                  </div>

                  {/* B-Roll Toggle */}
                  {brollAvailable && (
                    <div className="flex items-center justify-between p-3 border rounded-lg bg-stone-50">
                      <div className="flex items-center gap-3">
                        <Film className="w-4 h-4 text-purple-500" />
                        <div>
                          <h3 className="text-sm font-medium text-stone-900">AI B-Roll</h3>
                          <p className="text-xs text-stone-500">Auto-add stock footage from Pexels</p>
                        </div>
                      </div>
                      <Switch
                        checked={includeBroll}
                        onCheckedChange={setIncludeBroll}
                        disabled={isLoading}
                      />
                    </div>
                  )}

                  {/* Output format */}
                  <div className="flex items-center justify-between p-3 border rounded-lg bg-stone-50">
                    <div className="flex items-center gap-3">
                      <Monitor className="w-4 h-4 text-blue-500" />
                      <div>
                        <h3 className="text-sm font-medium text-stone-900">Wide format</h3>
                        <p className="text-xs text-stone-500">Keep original aspect ratio instead of 9:16 vertical</p>
                      </div>
                    </div>
                    <Switch
                      checked={outputFormat === "original"}
                      onCheckedChange={(checked) => setOutputFormat(checked ? "original" : "vertical")}
                      disabled={isLoading}
                    />
                  </div>

                  {/* Add subtitles */}
                  <div className="flex items-center justify-between p-3 border rounded-lg bg-stone-50">
                    <div className="flex items-center gap-3">
                      <Type className="w-4 h-4 text-emerald-500" />
                      <div>
                        <h3 className="text-sm font-medium text-stone-900">Add subtitles</h3>
                        <p className="text-xs text-stone-500">Burn captions onto clips (disable for faster processing)</p>
                      </div>
                    </div>
                    <Switch
                      checked={addSubtitles}
                      onCheckedChange={setAddSubtitles}
                      disabled={isLoading}
                    />
                  </div>
                </CardContent>
              </Card>

              {/* Font Customization Section */}
              <div
                className={`transition-all duration-500 ease-in-out overflow-hidden ${
                  addSubtitles
                    ? "max-h-[800px] opacity-100"
                    : "max-h-0 opacity-0 pointer-events-none"
                }`}
              >
              <Card className="border-stone-200">
                <CardContent className="px-4 pt-0 pb-2.5 space-y-2.5">
                  <div
                    className="flex items-center justify-between cursor-pointer"
                    onClick={() => setShowAdvancedOptions(!showAdvancedOptions)}
                  >
                    <div className="flex items-center gap-2 text-sm font-medium text-stone-900">
                      <Paintbrush className="w-4 h-4" />
                      Font Customization
                    </div>
                    <button type="button" className="text-xs text-stone-500 hover:text-stone-700 transition-colors">
                      {showAdvancedOptions ? "Hide" : "Show"}
                    </button>
                  </div>

                  {showAdvancedOptions && (
                    <div className="space-y-5 pt-1">
                      {/* Font Family Selector */}
                      <div className="space-y-2">
                        <label className="text-sm text-stone-600 flex items-center gap-2">
                          <Type className="w-3.5 h-3.5" />
                          Font Family
                        </label>
                        <div className="flex items-center justify-between gap-3 text-xs text-stone-500">
                          <span>{availableFonts.length} font{availableFonts.length === 1 ? "" : "s"} available</span>
                          <input
                            ref={fontUploadInputRef}
                            type="file"
                            accept=".ttf,.otf"
                            onChange={handleFontUpload}
                            className="hidden"
                          />
                          <Button
                            type="button"
                            variant="outline"
                            size="sm"
                            disabled={isLoading || isUploadingFont || !canUploadCustomFonts}
                            onClick={() => fontUploadInputRef.current?.click()}
                          >
                            {isUploadingFont ? "Uploading..." : "Upload Font"}
                          </Button>
                        </div>
                        {!canUploadCustomFonts && (
                          <p className="text-xs text-amber-700">Custom font upload is available on Pro plans.</p>
                        )}
                        <Input
                          type="text"
                          value={fontSearch}
                          onChange={(e) => setFontSearch(e.target.value)}
                          placeholder="Search fonts"
                          disabled={isLoading}
                        />
                        <Select value={fontFamily} onValueChange={setFontFamily} disabled={isLoading}>
                          <SelectTrigger className="w-full">
                            <SelectValue placeholder="Select font" />
                          </SelectTrigger>
                          <SelectContent>
                            {filteredFonts.map((font) => (
                              <SelectItem key={font.name} value={font.name}>
                                <span style={{ fontFamily: `'${font.name}', system-ui, sans-serif` }}>
                                  {font.display_name}
                                </span>
                              </SelectItem>
                            ))}
                            {availableFonts.length === 0 && (
                              <SelectItem value="TikTokSans-Regular">TikTok Sans Regular</SelectItem>
                            )}
                            {availableFonts.length > 0 && filteredFonts.length === 0 && (
                              <SelectItem value="__no_match__" disabled>
                                No fonts match your search
                              </SelectItem>
                            )}
                          </SelectContent>
                        </Select>
                        {fontLoadError && (
                          <p className="text-xs text-amber-700">{fontLoadError}</p>
                        )}
                      </div>

                      {/* Font Size & Color Row */}
                      <div className="grid grid-cols-2 gap-4">
                        {/* Font Size Slider */}
                        <div className="space-y-2">
                          <label className="text-sm text-stone-600">
                            Size: {fontSize}px
                          </label>
                          <div className="px-1">
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
                          <div className="flex justify-between text-xs text-stone-400">
                            <span>12px</span>
                            <span>48px</span>
                          </div>
                        </div>

                        {/* Font Color Picker */}
                        <div className="space-y-2">
                          <label className="text-sm text-stone-600 flex items-center gap-1.5">
                            <Palette className="w-3.5 h-3.5" />
                            Color
                          </label>
                          <div className="flex items-center gap-2">
                            <input
                              type="color"
                              value={fontColor}
                              onChange={(e) => setFontColor(e.target.value)}
                              disabled={isLoading}
                              className="w-10 h-8 rounded border border-stone-300 cursor-pointer disabled:cursor-not-allowed"
                            />
                            <Input
                              type="text"
                              value={fontColor}
                              onChange={(e) => setFontColor(e.target.value)}
                              disabled={isLoading}
                              placeholder="#FFFFFF"
                              className="flex-1 h-8 text-xs"
                              pattern="^#[0-9A-Fa-f]{6}$"
                            />
                          </div>
                          <div className="flex gap-1.5 mt-1">
                            {["#FFFFFF", "#000000", "#FFD700", "#FF6B6B", "#4ECDC4", "#45B7D1"].map((color) => (
                              <button
                                key={color}
                                type="button"
                                onClick={() => setFontColor(color)}
                                disabled={isLoading}
                                className="w-5 h-5 rounded border-2 border-stone-300 cursor-pointer hover:scale-110 transition-transform disabled:cursor-not-allowed"
                                style={{ backgroundColor: color }}
                                title={color}
                              />
                            ))}
                          </div>
                        </div>
                      </div>
                    </div>
                  )}
                </CardContent>
              </Card>
              </div>

              {isLoading && (
                <div className="space-y-4">
                  <div className="space-y-2">
                    <div className="flex justify-between text-sm">
                      <span className="text-stone-600">Processing</span>
                      <span className="text-stone-900 font-medium">{progress}%</span>
                    </div>
                    <Progress value={progress} className="h-2" />
                  </div>

                  {currentStep && statusMessage && (
                    <div className="bg-stone-50 rounded-xl p-4 space-y-3 border border-stone-200">
                      <div className="flex items-center gap-3">
                        {getStepIcon(currentStep)}
                        <div className="flex-1">
                          <p className="text-sm font-medium text-stone-900">{statusMessage}</p>
                          {sourceTitle && (
                            <p className="text-xs text-stone-500 mt-1">Processing: {sourceTitle}</p>
                          )}
                        </div>
                      </div>

                      <div className="grid grid-cols-2 gap-2 text-xs">
                        <div className={`flex items-center gap-2 p-2 rounded-lg ${currentStep === 'validation' || currentStep === 'user_check' ? 'bg-blue-100' : progress > 15 ? 'bg-green-100' : 'bg-stone-100'}`}>
                          <CheckCircle className={`w-3 h-3 ${progress > 15 ? 'text-green-500' : 'text-stone-400'}`} />
                          <span className={progress > 15 ? 'text-green-700' : 'text-stone-600'}>Validation</span>
                        </div>
                        <div className={`flex items-center gap-2 p-2 rounded-lg ${currentStep === 'download' || currentStep === 'youtube_info' ? 'bg-green-100' : progress > 30 ? 'bg-green-100' : 'bg-stone-100'}`}>
                          <CheckCircle className={`w-3 h-3 ${progress > 30 ? 'text-green-500' : 'text-stone-400'}`} />
                          <span className={progress > 30 ? 'text-green-700' : 'text-stone-600'}>Download</span>
                        </div>
                        <div className={`flex items-center gap-2 p-2 rounded-lg ${currentStep === 'transcript' ? 'bg-purple-100' : progress > 45 ? 'bg-green-100' : 'bg-stone-100'}`}>
                          <CheckCircle className={`w-3 h-3 ${progress > 45 ? 'text-green-500' : 'text-stone-400'}`} />
                          <span className={progress > 45 ? 'text-green-700' : 'text-stone-600'}>Transcript</span>
                        </div>
                        <div className={`flex items-center gap-2 p-2 rounded-lg ${currentStep === 'ai_analysis' ? 'bg-orange-100' : progress > 60 ? 'bg-green-100' : 'bg-stone-100'}`}>
                          <CheckCircle className={`w-3 h-3 ${progress > 60 ? 'text-green-500' : 'text-stone-400'}`} />
                          <span className={progress > 60 ? 'text-green-700' : 'text-stone-600'}>AI Analysis</span>
                        </div>
                        <div className={`flex items-center gap-2 p-2 rounded-lg ${currentStep === 'clip_generation' ? 'bg-indigo-100' : progress > 75 ? 'bg-green-100' : 'bg-stone-100'}`}>
                          <CheckCircle className={`w-3 h-3 ${progress > 75 ? 'text-green-500' : 'text-stone-400'}`} />
                          <span className={progress > 75 ? 'text-green-700' : 'text-stone-600'}>Create Clips</span>
                        </div>
                        <div className={`flex items-center gap-2 p-2 rounded-lg ${currentStep === 'complete' ? 'bg-green-100' : progress >= 100 ? 'bg-green-100' : 'bg-stone-100'}`}>
                          <CheckCircle className={`w-3 h-3 ${progress >= 100 ? 'text-green-500' : 'text-stone-400'}`} />
                          <span className={progress >= 100 ? 'text-green-700' : 'text-stone-600'}>Complete</span>
                        </div>
                      </div>
                    </div>
                  )}
                </div>
              )}

              {error && (
                <Alert className="border-red-200 bg-red-50">
                  <AlertCircle className="h-4 w-4 text-red-500" />
                  <AlertDescription className="text-sm text-red-700">
                    {error}
                  </AlertDescription>
                </Alert>
              )}

              <p className="text-xs text-stone-500">
                Completion emails use your user preference in{" "}
                <Link href="/settings" className="font-medium text-stone-700 underline underline-offset-2">
                  Settings
                </Link>.
              </p>

              <Button
                type="submit"
                className="w-full h-12 text-base rounded-xl"
                disabled={
                  (sourceType === "youtube" && !url.trim()) ||
                  (sourceType === "upload" && !fileRef.current) ||
                  (billingSummary?.monetization_enabled && !billingSummary.can_create_task) ||
                  isLoading
                }
              >
                {isLoading ? "Processing..." : "Process Video"}
              </Button>
            </form>
          </div>

          {/* Right Column — Phone Preview */}
          <div
            className={`hidden lg:block flex-shrink-0 overflow-hidden transition-all duration-500 ease-in-out ${
              sourceType === "upload"
                ? "w-0 opacity-0"
                : "w-[340px] opacity-100"
            }`}
          >
            <div
              className={`w-[340px] transition-all duration-500 ease-in-out ${
                sourceType === "upload"
                  ? "translate-x-6 scale-[0.97] opacity-0"
                  : "translate-x-0 scale-100 opacity-100"
              }`}
            >
            <div className="lg:sticky lg:top-8">
              <div className="flex items-center justify-center gap-2 mb-5 text-sm text-stone-400">
                <Monitor className="w-4 h-4" />
                <span>Live Preview</span>
              </div>

              {/* Phone Frame — realistic iPhone style */}
              <div className="mx-auto" style={{ maxWidth: "300px" }}>
                <div
                  className="relative bg-stone-950"
                  style={{ borderRadius: "3rem", padding: "12px" }}
                >
                  {/* Screen with inner radius */}
                  <div
                    className="relative overflow-hidden bg-black"
                    style={{ borderRadius: "2.25rem", height: "580px" }}
                  >
                    {/* Status bar */}
                    <div className="absolute top-0 left-0 right-0 z-20 px-6 pt-3 flex justify-between items-center">
                      <span className="text-white text-xs font-semibold">9:41</span>
                      {/* Dynamic Island */}
                      <div className="absolute top-2.5 left-1/2 -translate-x-1/2 w-24 h-7 bg-black rounded-full" />
                      <div className="flex items-center gap-1">
                        {/* Signal */}
                        <svg width="16" height="12" viewBox="0 0 16 12" className="text-white">
                          <rect x="0" y="8" width="3" height="4" rx="0.5" fill="currentColor" />
                          <rect x="4.5" y="5" width="3" height="7" rx="0.5" fill="currentColor" />
                          <rect x="9" y="2" width="3" height="10" rx="0.5" fill="currentColor" />
                          <rect x="13.5" y="0" width="3" height="12" rx="0.5" fill="currentColor" opacity="0.3" />
                        </svg>
                        {/* WiFi */}
                        <svg width="14" height="12" viewBox="0 0 14 12" className="text-white ml-0.5">
                          <path d="M7 10.5a1.5 1.5 0 100 3 1.5 1.5 0 000-3z" fill="currentColor" />
                          <path d="M3.5 8.5a5 5 0 017 0" stroke="currentColor" strokeWidth="1.5" fill="none" strokeLinecap="round" />
                          <path d="M1 5.5a8.5 8.5 0 0112 0" stroke="currentColor" strokeWidth="1.5" fill="none" strokeLinecap="round" />
                        </svg>
                        {/* Battery */}
                        <svg width="26" height="12" viewBox="0 0 26 12" className="text-white ml-0.5">
                          <rect x="0" y="1" width="22" height="10" rx="2" stroke="currentColor" strokeWidth="1" fill="none" />
                          <rect x="2" y="3" width="16" height="6" rx="1" fill="currentColor" />
                          <rect x="23" y="4" width="2" height="4" rx="0.5" fill="currentColor" opacity="0.4" />
                        </svg>
                      </div>
                    </div>

                    {/* Video background */}
                    {youtubeThumbnailUrl ? (
                      <div
                        className="absolute inset-0 bg-cover bg-center scale-105 blur-sm"
                        style={{ backgroundImage: `url(${youtubeThumbnailUrl})` }}
                      />
                    ) : (
                      <div className="absolute inset-0 bg-gradient-to-b from-stone-600 via-stone-500 to-stone-700" />
                    )}
                    <div className="absolute inset-0 bg-black/20" />
                    {/* Bottom gradient for readability over lower UI */}
                    <div className="absolute inset-x-0 bottom-0 h-60 bg-gradient-to-t from-black/70 via-black/30 to-transparent z-[1]" />

                    {/* TikTok-style top navigation */}
                    <div className="absolute top-12 left-0 right-0 z-10 flex justify-center items-center gap-5">
                      <span className="text-white/50 text-xs font-medium">Following</span>
                      <span className="text-white text-xs font-semibold relative">
                        For You
                        <div className="absolute -bottom-1.5 left-1/2 -translate-x-1/2 w-6 h-0.5 bg-white rounded-full" />
                      </span>
                    </div>

                    {/* Right side action buttons — TikTok style */}
                    <div className="absolute right-3 space-y-5 z-10" style={{ bottom: "260px" }}>
                      {/* Profile */}
                      <div className="flex flex-col items-center gap-1">
                        <div className="w-9 h-9 rounded-full bg-white/20 border-2 border-white/40" />
                        <div className="w-4 h-4 rounded-full bg-red-500 -mt-3 border border-black flex items-center justify-center">
                          <span className="text-white text-[7px] font-bold">+</span>
                        </div>
                      </div>
                      {/* Heart */}
                      <div className="flex flex-col items-center gap-0.5">
                        <svg width="26" height="26" viewBox="0 0 24 24" fill="white" className="opacity-90">
                          <path d="M12 21.35l-1.45-1.32C5.4 15.36 2 12.28 2 8.5 2 5.42 4.42 3 7.5 3c1.74 0 3.41.81 4.5 2.09C13.09 3.81 14.76 3 16.5 3 19.58 3 22 5.42 22 8.5c0 3.78-3.4 6.86-8.55 11.54L12 21.35z"/>
                        </svg>
                        <span className="text-white text-[10px] font-semibold">24.5K</span>
                      </div>
                      {/* Comment */}
                      <div className="flex flex-col items-center gap-0.5">
                        <svg width="24" height="24" viewBox="0 0 24 24" fill="white" className="opacity-90">
                          <path d="M20 2H4c-1.1 0-2 .9-2 2v18l4-4h14c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2z"/>
                        </svg>
                        <span className="text-white text-[10px] font-semibold">482</span>
                      </div>
                      {/* Share */}
                      <div className="flex flex-col items-center gap-0.5">
                        <svg width="24" height="24" viewBox="0 0 24 24" fill="white" className="opacity-90">
                          <path d="M18 16.08c-.76 0-1.44.3-1.96.77L8.91 12.7c.05-.23.09-.46.09-.7s-.04-.47-.09-.7l7.05-4.11c.54.5 1.25.81 2.04.81 1.66 0 3-1.34 3-3s-1.34-3-3-3-3 1.34-3 3c0 .24.04.47.09.7L8.04 9.81C7.5 9.31 6.79 9 6 9c-1.66 0-3 1.34-3 3s1.34 3 3 3c.79 0 1.5-.31 2.04-.81l7.12 4.16c-.05.21-.08.43-.08.65 0 1.61 1.31 2.92 2.92 2.92s2.92-1.31 2.92-2.92-1.31-2.92-2.92-2.92z"/>
                        </svg>
                        <span className="text-white text-[10px] font-semibold">Share</span>
                      </div>
                    </div>

                    {/* Subtitle area — positioned above creator info */}
                    <div className="absolute left-0 right-0 z-10" style={{ bottom: "195px" }}>
                      <div className="mx-4">
                        <p
                          style={{
                            color: fontColor,
                            fontSize: `${Math.max(Math.min(fontSize * 0.6, 22), 11)}px`,
                            fontFamily: `'${fontFamily}', system-ui, -apple-system, sans-serif`,
                            textAlign: 'center',
                            lineHeight: '1.5',
                            textShadow: '0 2px 8px rgba(0,0,0,0.8), 0 0px 2px rgba(0,0,0,0.9)',
                          }}
                          className="font-bold"
                        >
                          Your subtitle will look like this
                        </p>
                      </div>
                    </div>

                    {/* Bottom left — creator info */}
                    <div className="absolute left-3 z-10 max-w-[60%]" style={{ bottom: "110px" }}>
                      <p className="text-white text-xs font-bold mb-1">@creator_name</p>
                      <p className="text-white/80 text-[10px] leading-snug">
                        Check out this amazing clip generated by AI
                      </p>
                      <div className="flex items-center gap-1.5 mt-2">
                        <svg width="10" height="10" viewBox="0 0 24 24" fill="white" className="opacity-70">
                          <path d="M12 3v10.55c-.59-.34-1.27-.55-2-.55-2.21 0-4 1.79-4 4s1.79 4 4 4 4-1.79 4-4V7h4V3h-6z"/>
                        </svg>
                        <span className="text-white/70 text-[9px]">Original Sound - creator_name</span>
                      </div>
                    </div>

                    {/* Bottom nav bar */}
                    <div className="absolute bottom-0 left-0 right-0 z-20 bg-black px-2 pt-2 pb-5">
                      <div className="flex items-center justify-around">
                        <div className="flex flex-col items-center gap-0.5">
                          <svg width="18" height="18" viewBox="0 0 24 24" fill="white">
                            <path d="M10 20v-6h4v6h5v-8h3L12 3 2 12h3v8z"/>
                          </svg>
                          <span className="text-white text-[8px]">Home</span>
                        </div>
                        <div className="flex flex-col items-center gap-0.5">
                          <svg width="18" height="18" viewBox="0 0 24 24" fill="white" opacity="0.5">
                            <path d="M15.5 14h-.79l-.28-.27C15.41 12.59 16 11.11 16 9.5 16 5.91 13.09 3 9.5 3S3 5.91 3 9.5 5.91 16 9.5 16c1.61 0 3.09-.59 4.23-1.57l.27.28v.79l5 4.99L20.49 19l-4.99-5z"/>
                          </svg>
                          <span className="text-white/50 text-[8px]">Discover</span>
                        </div>
                        <div className="relative -mt-3">
                          <div className="w-10 h-7 rounded-lg bg-white flex items-center justify-center">
                            <span className="text-black text-lg font-bold leading-none">+</span>
                          </div>
                        </div>
                        <div className="flex flex-col items-center gap-0.5">
                          <svg width="18" height="18" viewBox="0 0 24 24" fill="white" opacity="0.5">
                            <path d="M20 2H4c-1.1 0-2 .9-2 2v18l4-4h14c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2z"/>
                          </svg>
                          <span className="text-white/50 text-[8px]">Inbox</span>
                        </div>
                        <div className="flex flex-col items-center gap-0.5">
                          <div className="w-5 h-5 rounded-full bg-white/30" />
                          <span className="text-white/50 text-[8px]">Me</span>
                        </div>
                      </div>
                      {/* Home indicator */}
                      <div className="w-28 h-1 bg-white/40 rounded-full mx-auto mt-2" />
                    </div>
                  </div>
                </div>

                {/* Caption info below phone */}
                <div className="mt-6 space-y-3 px-2">
                  <div className="flex items-center justify-between text-xs text-stone-500">
                    <span>Font</span>
                    <span className="text-stone-700 font-medium">
                      {availableFonts.find(f => f.name === fontFamily)?.display_name || fontFamily}
                    </span>
                  </div>
                  <Separator />
                  <div className="flex items-center justify-between text-xs text-stone-500">
                    <span>Size</span>
                    <span className="text-stone-700 font-medium">{fontSize}px</span>
                  </div>
                  <Separator />
                  <div className="flex items-center justify-between text-xs text-stone-500">
                    <span>Color</span>
                    <div className="flex items-center gap-2">
                      <div className="w-3 h-3 rounded-full border border-stone-300" style={{ backgroundColor: fontColor }} />
                      <span className="text-stone-700 font-medium">{fontColor}</span>
                    </div>
                  </div>
                  <Separator />
                  <div className="flex items-center justify-between text-xs text-stone-500">
                    <span>Template</span>
                    <span className="text-stone-700 font-medium">
                      {availableTemplates.find(t => t.id === captionTemplate)?.name || "Default"}
                    </span>
                  </div>
                </div>
              </div>
            </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
