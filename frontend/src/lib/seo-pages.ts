export interface SeoPageLink {
  href: string;
  label: string;
}

export interface SeoPageSection {
  heading: string;
  paragraphs: string[];
  bullets?: string[];
  /** Contextual internal links rendered under the section. */
  links?: SeoPageLink[];
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
    metaDescription: "Find highlights in long videos, edit captions and framing, and export Shorts, Reels, or TikToks. See real SupoClip output before choosing hosted or self-hosted.",
    eyebrow: "AI Video Clipping",
    heading: "AI video clipper for turning long videos into better shorts",
    summary: "Paste a YouTube, Twitch, or Kick recording link, or upload a video. SupoClip suggests moments from the transcript, adds word-synced captions, and reframes them for short-form video. Review every cut in the editor, then export. Use managed hosting or run the open-source app yourself.",
    keywords: [
      "AI video clipper",
      "AI clip maker",
      "long video to shorts",
      "automatic video clipping",
      "AI shorts generator",
    ],
    publishedAt: "2026-07-27",
    updatedAt: "2026-10-26",
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
        heading: "Find promising moments without scrubbing the whole recording",
        paragraphs: [
          "An AI video clipper reduces the repeated work between a long recording and a finished short: transcribing speech, proposing boundaries, finding a vertical crop, and timing captions. SupoClip analyzes what is said to identify candidates. Start with a podcast answer, interview story, tutorial explanation, or spoken stream reaction. The useful test is whether a new viewer can understand the excerpt without the preceding conversation. A sentence that sounds dramatic but leaves out its explanation still needs an edit.",
          "Candidate scores consider hook strength, engagement, value, and shareability. Use them to decide what to review first; they do not predict views. Transcript-led selection can miss silent gameplay, a visual demonstration, or an important chart that nobody describes aloud. Check those passages in your source yourself. Keep the candidates that contain an opening, enough context, and a complete payoff, even if that means exporting fewer clips.",
        ],
        bullets: [
          "Import supported YouTube, Twitch, and Kick recordings, or upload your video file",
          "Review suggested moments against the source instead of treating a score as approval",
          "Check faces, slides, screen shares, and scene changes in the final crop",
          "Correct words and timing before exporting the destination preset",
        ],
      },
      {
        heading: "Finish the cut, captions, and framing in one workflow",
        paragraphs: [
          "Open a generated clip in the editor and play the opening without the episode around it. If the first sentence depends on an earlier question, move the start to include enough context. Trim repeated setup and stop after the answer resolves. You can adjust cuts and framing rather than accepting the first suggestion unchanged. Watch transitions between speakers and screen shares: a close crop that works on a face can hide the detail a demonstration needs.",
          "Review the transcript for names, numbers, and specialist terms. Choose a caption style, adjust contrast and position, and inspect it at phone size. Keep captions clear of faces and the destination app’s controls. Export using the Shorts, Reels, or TikTok preset, then watch the rendered file all the way through. For YouTube, publishing options also depend on your account and deployment access. Exporting a TikTok or Reels file does not mean those platforms are connected for automatic posting.",
        ],
        links: [
          { href: "/youtube-shorts-clipper", label: "Step-by-step: turn a YouTube video into Shorts" },
          { href: "/blog/self-host-supoclip-docker", label: "Run the pipeline yourself with Docker" },
        ],
      },
      {
        heading: "Choose managed hosting or an open-source setup",
        paragraphs: [
          "Use hosted SupoClip when you want processing managed for you. The paid plans include a monthly allowance measured in source-video minutes; producing more clips from the same recording does not multiply that recording’s duration. Check current plans for output resolution, source limits, and publishing access before choosing. A creator can start with one recording and review its finished clips before making the workflow part of a weekly schedule.",
          "Self-hosting gives technical teams the application source and control over the processing stack. You provide compute, storage, updates, and the transcription and language-model configuration. Local Whisper and Ollama can be used, while external providers bring their own usage costs and data handling. The source is free under AGPL-3.0; managed hosting is paid. Use the real examples above to judge the output, then choose the operating model that fits how you work.",
        ],
        links: [
          { href: "/blog/supoclip-vs-opusclip", label: "How SupoClip compares with OpusClip" },
          { href: "/open-source-video-clipper", label: "Why teams choose an open-source clipper" },
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
      {
        question: "Which video sources does SupoClip accept?",
        answer:
          "YouTube videos, Twitch VODs and clips, Kick VODs and clips, and video files you upload. Live streams aren't supported, so clip the VOD once the stream has ended.",
      },
    ],
  },
  {
    slug: "open-source-video-clipper",
    metaTitle: "Open-Source Video Clipper You Can Self-Host",
    metaDescription: "Self-host SupoClip for AI clip discovery, editable captions, vertical framing, and exports. Compare local and hosted processing, real output, setup, and costs.",
    eyebrow: "Open Source & Self-Hosted",
    heading: "Open-source video clipper with full control of the workflow",
    summary: "Run a complete video clipping app on your own infrastructure: discover moments, edit cuts and captions, adjust vertical framing, and export. Inspect the real output below, understand what stays local, and choose between self-hosting and managed hosting.",
    keywords: [
      "open-source video clipper",
      "self-hosted AI video editor",
      "open-source AI clip maker",
      "self-hosted video clipping",
      "open-source OpusClip alternative",
    ],
    publishedAt: "2026-07-27",
    updatedAt: "2026-10-26",
    tableCaption: "Hosted and self-hosted SupoClip compared",
    tableHeaders: ["Decision", "Managed SupoClip", "Self-hosted SupoClip"],
    tableRows: [
      ["Source access", "Full AGPL-3.0 repository", "Full AGPL-3.0 repository"],
      ["Infrastructure", "Managed for you", "Your server or local environment"],
      ["Model choice", "Managed provider configuration", "Supported hosted providers or Ollama"],
      ["Customization", "App settings; source available separately", "Application and pipeline source code"],
    ],
    sections: [
      {
        heading: "An open-source clipping app you can inspect and change",
        paragraphs: [
          "SupoClip brings source import, transcription, candidate selection, captions, framing, and exports into a browser workflow. The application is available under AGPL-3.0. Self-hosting is useful when you want to adapt the pipeline, choose providers, automate jobs with the REST API or bundled MCP server, or keep the application alongside your own systems. You still review each suggested cut before using it: access to the source does not make an automatic selection editorially correct.",
          "The documented Docker Compose stack includes the frontend, backend, worker, PostgreSQL, and Redis. Begin with the self-hosting guide and a short video you know well. Verify that a task moves through processing, that a finished clip plays, and that captions and framing match the source. The examples above show the kind of output to inspect; they are not a benchmark for your hardware, models, or footage. Do a small first run before processing a backlog.",
        ],
      },
      {
        heading: "Know which processing stays local and what it costs",
        paragraphs: [
          "Running the app on your server does not automatically keep every part of processing on that server. Hosted transcription or language-model providers receive the inputs required for their work. For local speech transcription, configure Whisper; for local clip selection, configure Ollama and a suitable installed model. Check connectivity from the worker as well as from your terminal. Review your selected configuration before processing material that needs to remain on your infrastructure.",
          "The code has no subscription fee for self-hosting, but hardware, electricity, persistent media storage, backups, maintenance, and optional API usage still have costs. Keep secrets private, review updates, monitor workers, and retain backups before upgrades. License terms also matter when you modify or distribute the application or provide a network service. Consult the repository license for the exact requirements. Choose managed hosting if operating this stack would distract from making clips.",
        ],
        bullets: [
          "Provide compute, persistent storage, backups, and room for generated media",
          "Choose local or external transcription and language-model providers",
          "Configure access, protect secrets, and maintain the deployed application",
          "Check jobs, provider usage, and output quality as your workload changes",
        ],
        links: [{ href: "/blog/self-host-supoclip-docker", label: "Production checklist in the self-hosting guide" }],
      },
      {
        heading: "Choose a complete app or integrate a smaller tool",
        paragraphs: [
          "If you need an interface for reviewing clips, timing captions, and correcting crops, evaluate the complete SupoClip app. If you are building a custom processing service, a library may be a better starting point. Automatic silence removal is another distinct editing job; it does not by itself decide which story or explanation makes a good social clip. Compare tools against the task you need to finish instead of assuming every open-source video project replaces the same workflow.",
          "Test a recording you have permission to use and know well. Check complete thoughts, speaker changes, proper nouns, visual context, and final exports. Record setup effort separately from processing time, and include human review and provider costs in your decision. You can use paid hosted SupoClip to focus on creating clips or self-host to control the application and its configuration. Both routes benefit from checking the real output before adopting a repeatable workflow.",
        ],
        links: [
          { href: "/blog/self-host-supoclip-docker", label: "Self-host SupoClip with Docker Compose" },
          { href: "/blog/supoclip-vs-opusclip", label: "Self-hosted SupoClip vs hosted OpusClip" },
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
          "Yes. SupoClip supports Ollama for clip selection, and TRANSCRIPTION_PROVIDER=whisper transcribes locally instead of calling AssemblyAI.",
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
    metaTitle: "YouTube Shorts Clipper: Turn Long Videos into Shorts",
    metaDescription: "Turn a long YouTube video into captioned Shorts. See real examples, select complete moments, edit the vertical crop, and export or publish with SupoClip.",
    eyebrow: "YouTube to Shorts",
    heading: "YouTube Shorts clipper for repurposing long videos",
    summary: "Turn an existing YouTube recording into a shortlist of Shorts. Paste a link, review the suggested moments, correct captions and framing, then export. The examples below show actual clips alongside the source moments so you can judge the result before signing up.",
    keywords: [
      "YouTube Shorts clipper",
      "YouTube video to Shorts",
      "long video to short clips",
      "AI YouTube clip maker",
      "YouTube highlight generator",
    ],
    publishedAt: "2026-07-27",
    updatedAt: "2026-10-26",
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
        heading: "1. Import your YouTube video and choose complete moments",
        paragraphs: [
          "Use a video you own or have permission to repurpose. Copy its YouTube URL, open SupoClip, and paste the link into the source field. Choose your generation options and start processing. Check the source-duration limit shown for your plan before submitting a long recording. If a link is restricted or cannot be downloaded, upload your original video file instead. Wait for the task to finish, then review the proposed clips rather than publishing the full batch automatically.",
          "Choose a candidate that makes sense to someone who has never seen the channel. Keep one useful idea: a question and answer, a specific mistake and lesson, or a short demonstration with its result. In the real examples above, the two clips take different editorial angles from the same recording. Compare them with their source timestamps. If a candidate begins with an unexplained “that” or cuts before the conclusion, adjust its boundaries or leave it out.",
        ],
        bullets: [
          "Start with a recording that fits your plan and that you can repurpose",
          "Keep the first sentence that clearly introduces the subject",
          "Preserve the explanation and any important qualification",
          "End after the payoff, before the next unrelated topic",
        ],
      },
      {
        heading: "2. Make the crop and captions work on a phone",
        paragraphs: [
          "Open the selected clip in the editor and check its vertical framing. Face tracking can keep a speaker visible, but review the whole cut, particularly camera changes and moments with slides or screen recordings. Keep the information the viewer needs inside the frame. A chart without its labels or a product demo without its key control can become meaningless even when the speaker is perfectly centered.",
          "Correct transcript mistakes in names, numbers, and terminology. Pick a caption template and adjust font size, contrast, and position while watching the preview at a realistic mobile size. Keep words away from the bottom description area and side controls of the destination app. The active-word effect should help the viewer follow speech, not compete with a busy scene. Replay both the first and last seconds after making timeline changes.",
        ],
        links: [{ href: "/ai-video-clipper", label: "What the AI clipper automates, stage by stage" }],
      },
      {
        heading: "3. Export, publish, and learn from the result",
        paragraphs: [
          "Choose the YouTube Shorts export preset and render the finished video. Watch the exported file with audio, then once without sound to check that the captions and framing carry the idea. Write a title that describes what the viewer will learn. When YouTube publishing is enabled for your account, Pro supports posting immediately and Creator adds scheduled posting. Availability also depends on the deployment’s social integration; exporting remains a separate workflow.",
          "Keep the original episode link and source timestamp with each clip. Publish distinct takeaways rather than multiple cuts that repeat the same answer. For a weekly batch, finish editorial review before filling the calendar, then confirm that scheduled posts actually appear on the intended channel. Use real audience viewing and responses to choose the next topics. SupoClip’s selection scores help prioritize review; they do not guarantee reach, and no recording is guaranteed to produce a fixed number of useful Shorts.",
        ],
        links: [{ href: "/blog/supoclip-vs-opusclip", label: "Choosing between SupoClip and OpusClip" }],
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
