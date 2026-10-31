import { render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";

import { ClipFocus, type WallClip } from "./clip-wall";

vi.mock("next-intl", () => ({ useTranslations: () => (key: string) => key }));
vi.mock("@/components/dynamic-video-player", () => ({ default: () => null }));
vi.mock("@/components/transcript-preview", () => ({ TranscriptPreview: () => null }));
vi.mock("@/components/app/clip-cover", () => ({ ClipCover: () => null, ScoreRing: () => null }));

const clip: WallClip = {
  id: "c1", filename: "a.mp4", start_time: "00:00", end_time: "00:30", duration: 30, text: "",
  relevance_score: 0.9, clip_order: 1, video_url: "/a.mp4", virality_score: 83, hook_score: 0,
  engagement_score: 0, value_score: 0, shareability_score: 0, hook_type: null, hook_title: "T",
  virality_breakdown: {
    hook: 27, narrative_arc: 20, emotional_trigger: 16, pacing: 12, loop_potential: 8,
    penalties_applied: -15, hook_quote: "Never do this", why_it_works: "Contrarian take", editor_fix: "Trim 2s",
  },
};

it("renders the virality breakdown rubric", () => {
  render(
    <ClipFocus clip={clip} taskId="t1" index={0} total={1} busy={false} editable={false}
      onNavigate={() => {}} onClose={() => {}} onDownload={() => {}} hookTypeLabel={() => ""} />,
  );
  expect(screen.getByText("27/30")).toBeInTheDocument();
  expect(screen.getByText("-15")).toBeInTheDocument();
  expect(screen.getByText("Trim 2s")).toBeInTheDocument();
});
