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
import { useSession } from "@/lib/auth-client";
import { formatSupportMessage, parseApiError } from "@/lib/api-error";
import Link from "next/link";
import Image from "next/image";
import { ArrowRight, Youtube, CheckCircle, AlertCircle, Loader2, Palette, Type, Paintbrush, Clock, Film, Sparkles } from "lucide-react";
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

  // Latest task state
  const [latestTask, setLatestTask] = useState<LatestTask | null>(null);
  const [isLoadingLatest, setIsLoadingLatest] = useState(false);
  const [billingSummary, setBillingSummary] = useState<BillingSummary | null>(null);
  const apiUrl = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

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
        const response = await fetch(`${apiUrl}/tasks/`, {
          headers: {
            'user_id': session.user.id,
          },
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
  }, [session?.user?.id, apiUrl]);

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
        const uploadResponse = await fetch(`${apiUrl}/upload`, {
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
          processing_mode: "fast"
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
      <div className="border-b bg-white">
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

            <div className="flex items-center gap-2">
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
          </div>
        </div>
      </div>

      {/* Main Content */}
      <div className="max-w-4xl mx-auto px-4 py-16">
        <div className="max-w-xl mx-auto">
          {/* Latest Generation Preview */}
          {latestTask && (
            <div className="mb-8">
              <div className="flex items-center justify-between mb-4">
                <h2 className="text-lg font-semibold text-black">Latest Generation</h2>
                <Link href="/list">
                  <Button variant="ghost" size="sm" className="text-blue-600 hover:text-blue-700">
                    See All <ArrowRight className="w-4 h-4 ml-1" />
                  </Button>
                </Link>
              </div>

              <Link href={`/tasks/${latestTask.id}`}>
                <Card className="hover:shadow-md transition-shadow cursor-pointer">
                  <CardContent className="p-6">
                    <div className="flex items-start justify-between gap-4">
                      <div className="flex-1 min-w-0">
                        <h3 className="text-lg font-semibold text-black mb-2 truncate">
                          {latestTask.source_title}
                        </h3>
                        <div className="flex flex-wrap items-center gap-3 text-sm text-gray-600">
                          <Badge variant="outline" className="capitalize">
                            {latestTask.source_type}
                          </Badge>
                          <span className="flex items-center gap-1">
                            <Clock className="w-4 h-4" />
                            {new Date(latestTask.created_at).toLocaleDateString()}
                          </span>
                          <span>
                            {latestTask.clips_count} {latestTask.clips_count === 1 ? "clip" : "clips"}
                          </span>
                        </div>
                      </div>
                      <div className="flex-shrink-0">
                        {latestTask.status === "completed" ? (
                          <Badge className="bg-green-100 text-green-800">
                            <CheckCircle className="w-3 h-3 mr-1" />
                            Completed
                          </Badge>
                        ) : latestTask.status === "processing" ? (
                          <Badge className="bg-blue-100 text-blue-800">
                            <Loader2 className="w-3 h-3 mr-1 animate-spin" />
                            Processing
                          </Badge>
                        ) : (
                          <Badge variant="outline">{latestTask.status}</Badge>
                        )}
                      </div>
                    </div>
                  </CardContent>
                </Card>
              </Link>

              <Separator className="my-8" />
            </div>
          )}

          {isLoadingLatest && (
            <div className="mb-8">
              <Skeleton className="h-5 w-32 mb-4" />
              <Card>
                <CardContent className="p-6">
                  <Skeleton className="h-5 w-64 mb-2" />
                  <Skeleton className="h-4 w-48" />
                </CardContent>
              </Card>
              <Separator className="my-8" />
            </div>
          )}

          <div className="mb-8">
            <h2 className="text-2xl font-bold text-black mb-2">
              Video Processing
            </h2>
            <p className="text-gray-600">
              Submit a YouTube URL or upload a video for automated clip generation with customizable fonts
            </p>
          </div>

          <form onSubmit={handleSubmit} className="space-y-6">
            {/* Source Type Selector */}
            <div className="space-y-2">
              <label htmlFor="source-type" className="text-sm font-medium text-black">
                Source Type
              </label>
              <Select value={sourceType} onValueChange={(value: "youtube" | "upload") => {
                setSourceType(value);
                // Reset file input and fileName when switching to YouTube
                if (value === "youtube") {
                  setFileName(null);
                  fileRef.current = null;
                  if (fileInputRef.current) {
                    fileInputRef.current.value = "";
                  }
                }
              }} disabled={isLoading}>
                <SelectTrigger className="w-full">
                  <SelectValue placeholder="Select source type" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="youtube">
                    <div className="flex items-center gap-2">
                      <Youtube className="w-4 h-4" />
                      YouTube URL
                    </div>
                  </SelectItem>
                  <SelectItem value="upload">
                    <div className="flex items-center gap-2">
                      <ArrowRight className="w-4 h-4" />
                      Upload Video
                    </div>
                  </SelectItem>
                </SelectContent>
              </Select>
            </div>

            {/* Dynamic Input Based on Source Type */}
            {sourceType === "youtube" ? (
              <div className="space-y-2">
                <label htmlFor="youtube-url" className="text-sm font-medium text-black">
                  YouTube URL
                </label>
                <Input
                  id="youtube-url"
                  type="url"
                  placeholder="https://www.youtube.com/watch?v=..."
                  value={url}
                  onChange={(e) => setUrl(e.target.value)}
                  disabled={isLoading}
                  className="h-11"
                />
              </div>
            ) : (
              <div className="space-y-2">
                <label htmlFor="video-upload" className="text-sm font-medium text-black">
                  Upload Video
                </label>
                <Input
                  id="video-upload"
                  type="file"
                  accept="video/*"
                  ref={fileInputRef}
                  onChange={handleFileChange}
                  disabled={isLoading}
                  className="h-11"
                // Do not set value prop, keep input uncontrolled
                />
                {fileName && (
                  <div className="text-xs text-gray-600 mt-1">
                    Selected: {fileName}
                  </div>
                )}
              </div>
            )}

            {/* Caption Template Selector */}
            <div className="space-y-2">
              <label className="text-sm font-medium text-black flex items-center gap-2">
                <Sparkles className="w-4 h-4" />
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
              <div className="flex items-center justify-between p-4 border rounded-lg bg-gray-50">
                <div className="flex items-center gap-3">
                  <Film className="w-5 h-5 text-purple-500" />
                  <div>
                    <h3 className="text-sm font-medium text-black">AI B-Roll</h3>
                    <p className="text-xs text-gray-500">Automatically add stock footage from Pexels</p>
                  </div>
                </div>
                <Switch
                  checked={includeBroll}
                  onCheckedChange={setIncludeBroll}
                  disabled={isLoading}
                />
              </div>
            )}

            {/* Font Customization Section */}
            <div className="space-y-4 border rounded-lg p-4 bg-gray-50">
              <div
                className="flex items-center justify-between cursor-pointer"
                onClick={() => setShowAdvancedOptions(!showAdvancedOptions)}
              >
                <div className="flex items-center gap-2">
                  <Paintbrush className="w-4 h-4" />
                  <h3 className="text-sm font-medium text-black">Advanced Font Options</h3>
                </div>
                <button type="button" className="text-xs text-gray-500">
                  {showAdvancedOptions ? "Hide" : "Show"}
                </button>
              </div>

              {showAdvancedOptions && (
                <div className="space-y-4 pt-2">
                  {/* Font Family Selector */}
                  <div className="space-y-2">
                    <label className="text-sm font-medium text-black flex items-center gap-2">
                      <Type className="w-4 h-4" />
                      Font Family
                    </label>
                    <div className="flex items-center justify-between gap-3 text-xs text-gray-600">
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

                  {/* Font Size Slider */}
                  <div className="space-y-2">
                    <label className="text-sm font-medium text-black">
                      Font Size: {fontSize}px
                    </label>
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
                    <label className="text-sm font-medium text-black flex items-center gap-2">
                      <Palette className="w-4 h-4" />
                      Font Color
                    </label>
                    <div className="flex items-center gap-2">
                      <input
                        type="color"
                        value={fontColor}
                        onChange={(e) => setFontColor(e.target.value)}
                        disabled={isLoading}
                        className="w-12 h-8 rounded border border-gray-300 cursor-pointer disabled:cursor-not-allowed"
                      />
                      <Input
                        type="text"
                        value={fontColor}
                        onChange={(e) => setFontColor(e.target.value)}
                        disabled={isLoading}
                        placeholder="#FFFFFF"
                        className="flex-1 h-8"
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
                          className="w-6 h-6 rounded border-2 border-gray-300 cursor-pointer hover:scale-110 transition-transform disabled:cursor-not-allowed"
                          style={{ backgroundColor: color }}
                          title={color}
                        />
                      ))}
                    </div>
                  </div>

                  {/* Preview */}
                  <div className="mt-4 p-3 bg-black rounded-lg">
                    <p
                      style={{
                        color: fontColor,
                        fontSize: `${Math.min(fontSize, 18)}px`,
                        fontFamily: `'${fontFamily}', system-ui, -apple-system, sans-serif`,
                        textAlign: 'center',
                        lineHeight: '1.4'
                      }}
                      className="font-medium"
                    >
                      Preview: Your subtitle will look like this
                    </p>
                  </div>
                </div>
              )}
            </div>

            {isLoading && (
              <div className="space-y-4">
                <div className="space-y-2">
                  <div className="flex justify-between text-sm">
                    <span className="text-gray-600">Processing</span>
                    <span className="text-black">{progress}%</span>
                  </div>
                  <Progress value={progress} className="h-2" />
                </div>

                {/* Detailed Status Display */}
                {currentStep && statusMessage && (
                  <div className="bg-gray-50 rounded-lg p-4 space-y-3">
                    <div className="flex items-center gap-3">
                      {getStepIcon(currentStep)}
                      <div className="flex-1">
                        <p className="text-sm font-medium text-black">{statusMessage}</p>
                        {sourceTitle && (
                          <p className="text-xs text-gray-500 mt-1">Processing: {sourceTitle}</p>
                        )}
                      </div>
                    </div>

                    {/* Step Progress Indicator */}
                    <div className="grid grid-cols-2 gap-2 text-xs">
                      <div className={`flex items-center gap-2 p-2 rounded ${currentStep === 'validation' || currentStep === 'user_check' ? 'bg-blue-100' : progress > 15 ? 'bg-green-100' : 'bg-gray-100'}`}>
                        <CheckCircle className={`w-3 h-3 ${progress > 15 ? 'text-green-500' : 'text-gray-400'}`} />
                        <span className={progress > 15 ? 'text-green-700' : 'text-gray-600'}>Validation</span>
                      </div>
                      <div className={`flex items-center gap-2 p-2 rounded ${currentStep === 'download' || currentStep === 'youtube_info' ? 'bg-green-100' : progress > 30 ? 'bg-green-100' : 'bg-gray-100'}`}>
                        <CheckCircle className={`w-3 h-3 ${progress > 30 ? 'text-green-500' : 'text-gray-400'}`} />
                        <span className={progress > 30 ? 'text-green-700' : 'text-gray-600'}>Download</span>
                      </div>
                      <div className={`flex items-center gap-2 p-2 rounded ${currentStep === 'transcript' ? 'bg-purple-100' : progress > 45 ? 'bg-green-100' : 'bg-gray-100'}`}>
                        <CheckCircle className={`w-3 h-3 ${progress > 45 ? 'text-green-500' : 'text-gray-400'}`} />
                        <span className={progress > 45 ? 'text-green-700' : 'text-gray-600'}>Transcript</span>
                      </div>
                      <div className={`flex items-center gap-2 p-2 rounded ${currentStep === 'ai_analysis' ? 'bg-orange-100' : progress > 60 ? 'bg-green-100' : 'bg-gray-100'}`}>
                        <CheckCircle className={`w-3 h-3 ${progress > 60 ? 'text-green-500' : 'text-gray-400'}`} />
                        <span className={progress > 60 ? 'text-green-700' : 'text-gray-600'}>AI Analysis</span>
                      </div>
                      <div className={`flex items-center gap-2 p-2 rounded ${currentStep === 'clip_generation' ? 'bg-indigo-100' : progress > 75 ? 'bg-green-100' : 'bg-gray-100'}`}>
                        <CheckCircle className={`w-3 h-3 ${progress > 75 ? 'text-green-500' : 'text-gray-400'}`} />
                        <span className={progress > 75 ? 'text-green-700' : 'text-gray-600'}>Create Clips</span>
                      </div>
                      <div className={`flex items-center gap-2 p-2 rounded ${currentStep === 'complete' ? 'bg-green-100' : progress >= 100 ? 'bg-green-100' : 'bg-gray-100'}`}>
                        <CheckCircle className={`w-3 h-3 ${progress >= 100 ? 'text-green-500' : 'text-gray-400'}`} />
                        <span className={progress >= 100 ? 'text-green-700' : 'text-gray-600'}>Complete</span>
                      </div>
                    </div>
                  </div>
                )}
              </div>
            )}

            {error && (
              <Alert className="mt-6 border-red-200 bg-red-50">
                <AlertCircle className="h-4 w-4 text-red-500" />
                <AlertDescription className="text-sm text-red-700">
                  {error}
                </AlertDescription>
              </Alert>
            )}

            <Button
              type="submit"
              className="w-full h-11"
              disabled={
                (sourceType === "youtube" && !url.trim()) ||
                (sourceType === "upload" && !fileRef.current) ||
                (billingSummary?.monetization_enabled && !billingSummary.can_create_task) ||
                isLoading
              }
            >
              {isLoading ? "Processing..." : "Process Video"}
            </Button>

            {((sourceType === "youtube" && url) || (sourceType === "upload" && fileName)) && !isLoading && (
              <Alert className="mt-6">
                <AlertDescription className="text-sm">
                  Ready to process: {sourceType === "youtube"
                    ? (url.length > 50 ? url.substring(0, 50) + "..." : url)
                    : fileName
                  }
                </AlertDescription>
              </Alert>
            )}
          </form>
        </div>
      </div>
    </div>
  );
}
