export interface SeoPageSection {
  heading: string;
  paragraphs: string[];
  bullets?: string[];
}

export interface SeoPageFaq {
  question: string;
  answer: string;
}

export interface SeoPage {
  slug: string;
  metaTitle: string;
  metaDescription: string;
  eyebrow: string;
  heading: string;
  summary: string;
  keywords: string[];
  publishedAt: string;
  updatedAt: string;
  tableCaption: string;
  tableHeaders: [string, string, string];
  tableRows: Array<[string, string, string]>;
  sections: SeoPageSection[];
  faqs: SeoPageFaq[];
}

export const seoPages: SeoPage[] = [
  {
    slug: "ai-video-clipper",
    metaTitle: "AI Video Clipper for Shorts, Reels & TikTok",
    metaDescription:
      "Use SupoClip to find highlights in long videos, score promising moments, create face-aware vertical crops, add captions, and export social-ready clips.",
    eyebrow: "AI Video Clipping",
    heading: "AI video clipper for turning long videos into better shorts",
    summary:
      "SupoClip combines transcription, AI-assisted highlight detection, virality scoring, face-aware reframing, word-synced captions, and export presets in one open-source workflow.",
    keywords: [
      "AI video clipper",
      "AI clip maker",
      "long video to shorts",
      "automatic video clipping",
      "AI shorts generator",
    ],
    publishedAt: "2026-07-27",
    updatedAt: "2026-07-27",
    tableCaption: "SupoClip AI clipping capability matrix",
    tableHeaders: ["Workflow stage", "Manual approach", "SupoClip approach"],
    tableRows: [
      ["Find moments", "Review the full recording", "Transcribe and score candidate segments"],
      ["Reframe", "Crop each shot by hand", "Track faces for vertical 9:16 framing"],
      ["Caption", "Time captions manually", "Generate word-synced animated subtitles"],
      ["Export", "Configure every platform separately", "Use presets for Shorts, Reels, and TikTok"],
    ],
    sections: [
      {
        heading: "What an AI video clipper should automate",
        paragraphs: [
          "The time-consuming part of short-form production is not one edit. It is repeating the same review, reframing, captioning, and export work for every source video. A useful AI clipper reduces that repetition while keeping the creator in control of the final cut.",
          "SupoClip starts from the source transcript, identifies candidate segments, and scores them for qualities such as hook strength, engagement, value, and shareability. The score is a review aid, not a promise that a clip will go viral.",
        ],
        bullets: [
          "YouTube URLs and uploaded video files",
          "AI-assisted candidate selection",
          "Face-centered vertical crops",
          "Editable caption styles and export formats",
        ],
      },
      {
        heading: "How the SupoClip workflow works",
        paragraphs: [
          "Paste a YouTube URL or upload a source file. SupoClip transcribes the recording, proposes clip boundaries, and presents the strongest candidates for review. Selected moments can then be rendered as vertical, captioned videos for short-form platforms.",
          "Because SupoClip is open source, teams can inspect the pipeline, choose supported hosted or local language models, and adapt processing to their own infrastructure. A hosted version is also available for creators who do not want to manage deployment.",
        ],
      },
      {
        heading: "Who this is built for",
        paragraphs: [
          "The workflow fits podcasters, educators, founders, agencies, and video teams that already produce long-form recordings and need a repeatable way to publish more short-form clips.",
          "It is especially useful when ownership matters: self-hosters retain control of the application and can modify the source instead of depending entirely on a closed editing platform.",
        ],
      },
    ],
    faqs: [
      {
        question: "What is an AI video clipper?",
        answer:
          "An AI video clipper analyzes a longer recording and assists with finding short segments, reframing them, adding captions, and preparing exports for short-form platforms.",
      },
      {
        question: "Can SupoClip turn YouTube videos into Shorts?",
        answer:
          "Yes. SupoClip accepts YouTube URLs, identifies candidate moments, and can render vertical, captioned clips suitable for YouTube Shorts.",
      },
      {
        question: "Is SupoClip open source?",
        answer:
          "Yes. The SupoClip codebase is available under the AGPL-3.0 license and can be self-hosted or modified.",
      },
    ],
  },
  {
    slug: "open-source-video-clipper",
    metaTitle: "Open-Source Video Clipper You Can Self-Host",
    metaDescription:
      "SupoClip is an open-source AI video clipper for self-hosted highlight detection, face-aware vertical crops, captions, and short-form exports.",
    eyebrow: "Open Source & Self-Hosted",
    heading: "Open-source video clipper with full control of the workflow",
    summary:
      "Run AI-assisted video clipping on your own infrastructure, inspect the source, choose your model provider, and customize how clips are scored, captioned, framed, and exported.",
    keywords: [
      "open-source video clipper",
      "self-hosted AI video editor",
      "open-source AI clip maker",
      "self-hosted video clipping",
      "open-source OpusClip alternative",
    ],
    publishedAt: "2026-07-27",
    updatedAt: "2026-07-27",
    tableCaption: "Hosted-only and open-source clipping workflows",
    tableHeaders: ["Decision", "Hosted-only tool", "Self-hosted SupoClip"],
    tableRows: [
      ["Source access", "Unavailable", "Full AGPL-3.0 repository"],
      ["Infrastructure", "Vendor controlled", "Your server or local environment"],
      ["Model choice", "Vendor selected", "Supported hosted providers or Ollama"],
      ["Customization", "Product settings", "Application and pipeline source code"],
    ],
    sections: [
      {
        heading: "Why self-host a video clipping pipeline",
        paragraphs: [
          "Self-hosting is useful when a team needs control over deployment, provider choice, processing limits, or product customization. Instead of sending every workflow decision through a closed platform, the application can run alongside the rest of the team's infrastructure.",
          "SupoClip includes a Next.js frontend, FastAPI backend, background workers, PostgreSQL, and Redis. The repository includes a Docker Compose setup so the complete application can be deployed as one stack.",
        ],
      },
      {
        heading: "What remains your responsibility",
        paragraphs: [
          "Open source does not mean that every dependency is free. Self-hosters provide their own compute, storage, transcription service, and language-model configuration. The tradeoff is operational responsibility in exchange for source-level control.",
          "Teams should also review the AGPL-3.0 license, secure their environment variables, keep dependencies updated, and monitor the worker and storage layers in production.",
        ],
        bullets: [
          "Provision compute and persistent storage",
          "Configure transcription and model providers",
          "Protect secrets and authenticated routes",
          "Monitor processing jobs and generated media",
        ],
      },
      {
        heading: "Start with Docker, customize when needed",
        paragraphs: [
          "The quickest setup is to clone the repository, copy the environment template, add the required provider credentials, and start the stack with Docker Compose. Developers can then change the scoring logic, caption presets, interface, or deployment model as their workflow evolves.",
          "Creators who prefer not to operate infrastructure can use the hosted SupoClip application while keeping the open-source repository available as a migration path.",
        ],
      },
    ],
    faqs: [
      {
        question: "Is SupoClip free to self-host?",
        answer:
          "The source code is free under the AGPL-3.0 license. You remain responsible for infrastructure and any paid transcription or AI-provider usage.",
      },
      {
        question: "Can SupoClip use a local language model?",
        answer:
          "Yes. SupoClip supports Ollama configuration in addition to supported hosted model providers.",
      },
      {
        question: "Does SupoClip require Docker?",
        answer:
          "Docker Compose is the documented full-stack path. Developers can run the Next.js frontend, FastAPI API, worker, PostgreSQL, and Redis separately when their environment requires it.",
      },
    ],
  },
  {
    slug: "youtube-shorts-clipper",
    metaTitle: "YouTube Shorts Clipper for Long Videos",
    metaDescription:
      "Turn long YouTube videos into vertical, captioned Shorts with AI-assisted highlight detection, virality scoring, and face-aware 9:16 crops.",
    eyebrow: "YouTube to Shorts",
    heading: "YouTube Shorts clipper for repurposing long videos",
    summary:
      "Paste a YouTube URL, review AI-selected moments, and create captioned vertical clips without manually scanning and reframing the entire recording.",
    keywords: [
      "YouTube Shorts clipper",
      "YouTube video to Shorts",
      "long video to short clips",
      "AI YouTube clip maker",
      "YouTube highlight generator",
    ],
    publishedAt: "2026-07-27",
    updatedAt: "2026-07-27",
    tableCaption: "Long-video to YouTube Shorts workflow",
    tableHeaders: ["Step", "Input", "Output"],
    tableRows: [
      ["1. Import", "YouTube URL", "Source metadata and video"],
      ["2. Analyze", "Transcript", "Candidate segments and scores"],
      ["3. Format", "Selected moment", "Face-aware 9:16 clip"],
      ["4. Finish", "Caption and export settings", "Platform-ready short video"],
    ],
    sections: [
      {
        heading: "How to turn a YouTube video into Shorts",
        paragraphs: [
          "Start with a video you own or have permission to repurpose. Paste its YouTube URL into SupoClip, choose the output and caption options, and begin processing. The application transcribes the source and proposes short segments for review.",
          "Choose the moments that make sense without the surrounding episode. Strong Shorts usually establish context quickly, deliver one clear idea, and end without relying on footage that was removed from the clip.",
        ],
        bullets: [
          "Use a clear spoken hook near the beginning",
          "Keep one idea per short clip",
          "Review names, numbers, and caption timing",
          "Confirm that vertical framing follows the active speaker",
        ],
      },
      {
        heading: "Captions and vertical reframing",
        paragraphs: [
          "SupoClip uses word-level timing to create synchronized captions and can track faces when converting landscape footage to a vertical frame. Caption templates provide a consistent starting point, while font, color, and output settings can be adjusted before rendering.",
          "Automated crops still deserve a visual review, especially when multiple people appear, screen shares are important, or the speaker moves quickly through the frame.",
        ],
      },
      {
        heading: "Build a repeatable repurposing workflow",
        paragraphs: [
          "Treat each long video as a source library rather than a one-time upload. Process the recording, group clips by theme, and connect each Short back to the longer video or a relevant product page.",
          "Track which topics earn watch time and engagement, then use those results to guide future recordings. SupoClip's scores help prioritize candidates; publishing data should determine what the audience actually values.",
        ],
      },
    ],
    faqs: [
      {
        question: "Can I paste a YouTube URL into SupoClip?",
        answer:
          "Yes. SupoClip supports YouTube URLs as a source for transcription, candidate selection, and clip generation.",
      },
      {
        question: "Does SupoClip add captions to Shorts?",
        answer:
          "Yes. It supports word-synced subtitles and configurable caption templates for generated clips.",
      },
      {
        question: "Does every AI-selected clip perform well?",
        answer:
          "No. AI scoring helps prioritize candidates, but creators should review context, accuracy, framing, and fit for their audience before publishing.",
      },
    ],
  },
];

export function getSeoPage(slug: string) {
  return seoPages.find((page) => page.slug === slug);
}
