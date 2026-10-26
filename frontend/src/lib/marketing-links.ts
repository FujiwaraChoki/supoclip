/**
 * Single registry of indexable marketing pages. The landing page, footer,
 * SEO pages, and blog all link from here so new pages are never orphaned.
 */
export type MarketingLinkGroup = "product" | "guide" | "comparison";

export interface MarketingLink {
  /** English-only editorial destinations keep their English navigation labels. */
  language?: "en";
  href: string;
  group: MarketingLinkGroup;
  eyebrow: string;
  title: string;
  /** Short footer label. */
  label: string;
  description: string;
  /** Hand-picked next reads, in priority order. */
  related: string[];
}

export const SELF_HOSTING_GUIDE_HREF = "/blog/self-host-supoclip-docker";
export const OPUSCLIP_COMPARISON_HREF = "/blog/supoclip-vs-opusclip";

export const marketingLinks: MarketingLink[] = [
  {
    href: "/demo",
    group: "product",
    eyebrow: "Demo",
    title: "See real clips SupoClip made from a long video",
    label: "Demo",
    description: "Watch the output, then jump to each moment in the original source video.",
    related: ["/ai-video-clipper", "/youtube-shorts-clipper", SELF_HOSTING_GUIDE_HREF],
  },
  {
    href: "/ai-video-clipper",
    group: "guide",
    eyebrow: "AI Video Clipping",
    title: "AI video clipper for Shorts, Reels, and TikTok",
    label: "AI video clipper",
    description: "How SupoClip finds moments, scores candidates, reframes faces, and adds captions.",
    related: ["/blog/find-best-moments-in-video", "/blog/podcast-to-shorts", "/youtube-shorts-clipper"],
  },
  {
    href: "/youtube-shorts-clipper",
    group: "guide",
    eyebrow: "YouTube to Shorts",
    title: "Repurpose long YouTube videos into Shorts",
    label: "YouTube Shorts clipper",
    description: "A practical workflow for selecting, captioning, reframing, and reviewing clips.",
    related: ["/demo", "/blog/schedule-week-of-youtube-shorts", "/blog/word-by-word-video-captions"],
  },
  {
    href: "/open-source-video-clipper",
    group: "guide",
    eyebrow: "Open Source",
    title: "Open-source video clipper you can control",
    label: "Open-source clipper",
    description: "What self-hosting changes about control, model providers, and infrastructure.",
    related: ["/blog/open-source-video-clippers", SELF_HOSTING_GUIDE_HREF, OPUSCLIP_COMPARISON_HREF],
  },
  {
    href: SELF_HOSTING_GUIDE_HREF,
    group: "guide",
    eyebrow: "Self-Hosting Guide",
    title: "Self-host SupoClip with Docker Compose",
    label: "Self-hosting guide",
    description: "Requirements, environment variables, first boot, a fully local setup, and production checks.",
    related: ["/open-source-video-clipper", OPUSCLIP_COMPARISON_HREF, "/youtube-shorts-clipper"],
  },
  {
    href: OPUSCLIP_COMPARISON_HREF,
    group: "comparison",
    eyebrow: "Comparison",
    title: "SupoClip vs OpusClip: a sourced comparison",
    label: "SupoClip vs OpusClip",
    description: "Pricing model, deployment, watermarks, and workflow, with every claim linked to a source.",
    related: [SELF_HOSTING_GUIDE_HREF, "/blog/best-free-opusclip-alternative", "/ai-video-clipper"],
  },
  {
    href: "/blog/best-free-opusclip-alternative",
    group: "comparison",
    eyebrow: "Comparison",
    title: "Free OpusClip alternative: costs and tradeoffs",
    label: "Self-hosted OpusClip alternative",
    description: "Understand the compute, provider costs, and maintenance behind a self-hosted workflow.",
    related: [OPUSCLIP_COMPARISON_HREF, SELF_HOSTING_GUIDE_HREF, "/blog/supoclip-vs-supo-live"],
  },
  {
    href: "/blog/supoclip-vs-supo-live",
    group: "comparison",
    eyebrow: "Comparison",
    title: "SupoClip vs supo.live: open-source control",
    label: "SupoClip vs supo.live",
    description: "Compare self-hosting, AI clipping, and editing workflows before choosing a video tool.",
    related: ["/open-source-video-clipper", SELF_HOSTING_GUIDE_HREF, OPUSCLIP_COMPARISON_HREF],
  },
  {
    href: "/blog/podcast-to-shorts", language: "en", group: "guide", eyebrow: "Podcast clips",
    title: "Turn a video podcast into Shorts and Reels", label: "Podcast to Shorts",
    description: "Choose complete ideas, correct captions, and follow real clips back to the source.",
    related: ["/blog/find-best-moments-in-video", "/blog/word-by-word-video-captions", "/youtube-shorts-clipper"],
  },
  {
    href: "/blog/find-best-moments-in-video", language: "en", group: "guide", eyebrow: "Highlight selection",
    title: "Find the best moments in a long video", label: "Find video highlights",
    description: "Use a practical checklist for hooks, context, payoff, and clean boundaries.",
    related: ["/blog/podcast-to-shorts", "/blog/repurpose-webinar-clips", "/ai-video-clipper"],
  },
  {
    href: "/blog/twitch-vod-to-shorts", language: "en", group: "guide", eyebrow: "Stream clips",
    title: "Turn Twitch VODs into TikTok clips and Shorts", label: "Twitch VOD to Shorts",
    description: "Review spoken highlights, preserve gameplay context, and export vertical clips.",
    related: ["/blog/find-best-moments-in-video", "/blog/word-by-word-video-captions", "/youtube-shorts-clipper"],
  },
  {
    href: "/blog/repurpose-webinar-clips", language: "en", group: "guide", eyebrow: "Webinar repurposing",
    title: "Repurpose a webinar into social media clips", label: "Webinar clips",
    description: "Turn audience questions and useful answers into clips with readable slides and captions.",
    related: ["/blog/find-best-moments-in-video", "/blog/word-by-word-video-captions", "/blog/schedule-week-of-youtube-shorts"],
  },
  {
    href: "/blog/word-by-word-video-captions", language: "en", group: "guide", eyebrow: "Caption editing",
    title: "Add readable word-by-word captions to clips", label: "Word-by-word captions",
    description: "Correct the transcript, choose a style, and keep subtitles clear of platform controls.",
    related: ["/blog/podcast-to-shorts", "/blog/twitch-vod-to-shorts", "/blog/repurpose-webinar-clips"],
  },
  {
    href: "/blog/schedule-week-of-youtube-shorts", language: "en", group: "guide", eyebrow: "Publishing workflow",
    title: "Create and schedule a week of YouTube Shorts", label: "Schedule YouTube Shorts",
    description: "Build a distinct batch, review each clip, and verify scheduled publication.",
    related: ["/blog/podcast-to-shorts", "/youtube-shorts-clipper", "/blog/repurpose-webinar-clips"],
  },
  {
    href: "/blog/open-source-video-clippers", language: "en", group: "comparison", eyebrow: "Open-source comparison",
    title: "Compare SupoClip, Clips AI, and Auto-Editor", label: "Open-source video clippers",
    description: "Choose an app, Python library, or command-line tool for the editing job you need.",
    related: ["/open-source-video-clipper", SELF_HOSTING_GUIDE_HREF, OPUSCLIP_COMPARISON_HREF],
  },
];

export function getMarketingLink(href: string) {
  return marketingLinks.find((link) => link.href === href);
}

export function getMarketingLinks(group: MarketingLinkGroup) {
  return marketingLinks.filter((link) => link.group === group);
}

export function getRelatedMarketingLinks(href: string): MarketingLink[] {
  return (getMarketingLink(href)?.related ?? [])
    .map(getMarketingLink)
    .filter((link): link is MarketingLink => Boolean(link));
}
