import { GITHUB_URL } from "@/lib/site";

/** Vendor pages behind the OpusClip-alternative articles. Every claim about another product cites one of these. */
export const CLIPPER_SOURCES = {
  supoclip: {
    repo: GITHUB_URL,
    license: `${GITHUB_URL}/blob/main/LICENSE`,
    compose: `${GITHUB_URL}/blob/main/docker-compose.yml`,
    config: `${GITHUB_URL}/blob/main/docs/configuration.md`,
    mcp: `${GITHUB_URL}/blob/main/mcp/README.md`,
  },
  opusclip: {
    pricing: "https://www.opus.pro/pricing",
    credits: "https://help.opus.pro/docs/article/how-are-credits-consumed",
    languages: "https://help.opus.pro/docs/article/video-languages-supported",
    api: "https://help.opus.pro/api-reference/overview",
    tool: "https://www.opus.pro/tools/long-video-to-short-video-ai",
    github: "https://github.com/opus-pro",
  },
  klap: {
    home: "https://klap.app/",
    pricing: "https://klap.app/pricing",
    api: "https://klap.app/api",
  },
  vizard: {
    home: "https://vizard.ai/",
    pricing: "https://vizard.ai/pricing",
    freePlan: "https://help.vizard.ai/en/articles/8767572-what-does-the-free-plan-for-vizard-offer",
    apiPricing: "https://docs.vizard.ai/docs/pricing",
    apiInputs: "https://docs.vizard.ai/reference/submit",
    ios: "https://apps.apple.com/us/app/vizard-ai-video-clip-maker/id6748490660",
  },
  reap: { pricing: "https://reap.video/pricing" },
  quso: { pricing: "https://quso.ai/pricing" },
  choppity: { pricing: "https://www.choppity.com/pricing" },
  twoShort: { pricing: "https://www.2short.ai/pricing" },
  spikes: { pricing: "https://spikes.studio/pricing" },
  kapwing: { pricing: "https://www.kapwing.com/pricing", clipMaker: "https://www.kapwing.com/ai/clip-maker" },
  descript: { pricing: "https://www.descript.com/pricing" },
  riverside: { pricing: "https://riverside.com/pricing", magicClips: "https://riverside.com/magic-clips" },
  capcut: { longToShorts: "https://www.capcut.com/tools/long-video-to-shorts" },
  youtube: { clips: "https://support.google.com/youtube/answer/15824265" },
  eklipse: {
    pricing: "https://eklipse.gg/pricing/",
    limits: "https://eklipse.gg/help/what-are-the-feature-limitations-for-free-vs-premium-users-within-ai-studio/",
  },
  munch: { pricing: "https://www.munchstudio.com/pricing" },
  submagic: { pricing: "https://www.submagic.co/pricing" },
  clipchamp: {
    compare: "https://support.microsoft.com/en-us/clipchamp/feature-comparison-between-clipchamp-for-work-and-personal-versions",
  },
  openshorts: { repo: "https://github.com/mutonby/openshorts" },
  funclip: { repo: "https://github.com/modelscope/FunClip" },
  aiShorts: { repo: "https://github.com/Anil-matcha/AI-Youtube-Shorts-Generator" },
  viralCutter: { repo: "https://github.com/RafaelGodoyEbert/ViralCutter" },
  clipsai: { repo: "https://github.com/ClipsAI/clipsai" },
} as const;
