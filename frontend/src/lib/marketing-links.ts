/**
 * Single registry of indexable marketing pages. The landing page, footer,
 * SEO pages, and blog all link from here so new pages are never orphaned.
 */
export type MarketingLinkGroup = "product" | "guide" | "comparison";

export interface MarketingLink {
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
    related: ["/demo", "/youtube-shorts-clipper", OPUSCLIP_COMPARISON_HREF],
  },
  {
    href: "/youtube-shorts-clipper",
    group: "guide",
    eyebrow: "YouTube to Shorts",
    title: "Repurpose long YouTube videos into Shorts",
    label: "YouTube Shorts clipper",
    description: "A practical workflow for selecting, captioning, reframing, and reviewing clips.",
    related: ["/demo", "/ai-video-clipper", OPUSCLIP_COMPARISON_HREF],
  },
  {
    href: "/open-source-video-clipper",
    group: "guide",
    eyebrow: "Open Source",
    title: "Open-source video clipper you can control",
    label: "Open-source clipper",
    description: "What self-hosting changes about control, model providers, and infrastructure.",
    related: [SELF_HOSTING_GUIDE_HREF, OPUSCLIP_COMPARISON_HREF, "/blog/supoclip-vs-supo-live"],
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
    title: "Best free, self-hosted OpusClip alternative",
    label: "Self-hosted OpusClip alternative",
    description: "When a self-hosted, open-source clipper is the better route than a credit-based tool.",
    related: [OPUSCLIP_COMPARISON_HREF, SELF_HOSTING_GUIDE_HREF, "/open-source-video-clipper"],
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
