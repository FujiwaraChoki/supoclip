export interface Clip {
  id: string;
  filename: string;
  file_path: string;
  start_time: string;
  end_time: string;
  duration: number;
  text: string;
  relevance_score: number;
  reasoning: string;
  clip_order: number;
  created_at: string;
  video_url: string;
  // Virality scores
  virality_score: number;
  hook_score: number;
  engagement_score: number;
  value_score: number;
  shareability_score: number;
  hook_type: string | null;
  hook_title: string | null;
}

export const EXPORT_PRESETS = [
  { value: "original", label: "Original" },
  { value: "tiktok", label: "TikTok" },
  { value: "reels", label: "Reels" },
  { value: "shorts", label: "Shorts" },
] as const;

export const formatDuration = (seconds: number) => {
  const mins = Math.floor(seconds / 60);
  const secs = Math.floor(seconds % 60);
  return `${mins}:${secs.toString().padStart(2, "0")}`;
};

// Neutral → amber → green. A weak score is not an error, so nothing renders red.
export const getScoreColor = (score: number) => {
  if (score >= 0.8) return "bg-green-50 text-green-800 border-green-200";
  if (score >= 0.6) return "bg-amber-50 text-amber-800 border-amber-200";
  return "bg-stone-100 text-stone-700 border-stone-200";
};

export const getViralityColor = (score: number) => {
  if (score >= 80) return "text-green-700";
  if (score >= 60) return "text-amber-700";
  return "text-stone-600";
};

export const getViralityBgColor = (score: number) => {
  if (score >= 80) return "bg-green-800";
  if (score >= 60) return "bg-amber-700";
  if (score >= 40) return "bg-stone-600";
  return "bg-stone-500";
};

export const getHookTypeLabel = (hookType: string | null) => {
  const labels: Record<string, string> = {
    question: "Question Hook",
    statement: "Bold Statement",
    statistic: "Data/Stats",
    story: "Story Hook",
    contrast: "Contrast Hook",
    none: "No Hook",
  };
  return labels[hookType || "none"] || hookType || "None";
};
