import assets from "@/lib/article-clip-assets.json";

export interface ArticleClipSource {
  title: string;
  url: string;
  credit: string;
  publishedAt: string;
  license: string;
  licenseUrl: string;
  evidenceUrl: string;
  viewsAtSelection: number;
  checkedAt: string;
}

export interface ArticleClip {
  id: string;
  title: string;
  src: string;
  poster: string;
  durationSeconds: number;
  startSeconds: number;
  sourceRange: string;
  publishedAt: string;
  edits: string;
  attributionFile: string;
  source: ArticleClipSource;
}

export interface ArticleClipGallery {
  afterSection: string;
  heading: string;
  intro: string;
  examples: Array<{ clipId: string; note: string }>;
}

export const articleClips: ArticleClip[] = assets;

export function getArticleClip(id: string) {
  return articleClips.find((clip) => clip.id === id);
}

export const articleClipGalleries: Record<string, ArticleClipGallery> = {
  "podcast-to-shorts": {
    afterSection: "example",
    heading: "From a recorded conversation to a complete answer",
    intro: "These examples use an interview with Blender founder Ton Roosendaal. It is an interview rather than a podcast episode, but the editorial test is the same: can the answer introduce its subject and deliver a useful idea without the surrounding conversation?",
    examples: [
      { clipId: "ton-1", note: "Listen for the subject in the opening sentence, then follow the explanation to its conclusion. Compare the cut with the source to check that the speaker's meaning survives the shorter format." },
      { clipId: "ton-2", note: "Review the second answer as a separate post rather than a continuation. The captions and framing should help a new viewer understand it without having seen the first clip." },
    ],
  },
  "find-best-moments-in-video": {
    afterSection: "example",
    heading: "Two different ideas from Andrew Price's popular talk",
    intro: "The original talk had 3.8 million YouTube views when selected on October 26, 2026. These are fresh SupoClip renders, not screenshots of someone else's Shorts. Play the complete examples and use the source links to inspect their boundaries.",
    examples: [
      { clipId: "price-1", note: "Apply the four checks: identify the opening idea, the context that makes it accurate, the explanation that earns the payoff, and the point where the excerpt ends." },
      { clipId: "price-2", note: "Compare this idea with the first clip. Keeping a distinct takeaway matters more than filling a quota with several versions of the same answer." },
    ],
  },
  "twitch-vod-to-shorts": {
    afterSection: "moments",
    heading: "What a speech-led clip can demonstrate",
    intro: "This source is a recorded interview, not a Twitch broadcast or a gameplay-detection demo. Use it to inspect the spoken-answer workflow described above; a silent play or a webcam-over-game layout needs its own visual review.",
    examples: [
      { clipId: "ton-2", note: "The words carry the idea here, which makes it a useful example of transcript-led selection. On a stream, apply the same context test to a story or explanation, then separately check that the game action remains visible." },
    ],
  },
  "repurpose-webinar-clips": {
    afterSection: "slides",
    heading: "A presentation excerpt with the same framing decisions",
    intro: "Andrew Price's source recording is a conference talk, not a webinar. It provides a real presentation example for checking how a spoken explanation and its visuals survive the move to a vertical clip.",
    examples: [
      { clipId: "price-2", note: "Check what the viewer needs to understand the advice. If the explanation refers to something on screen, compare with the source before deciding that a closer crop is appropriate." },
      { clipId: "price-3", note: "Watch the entire cut rather than judging one still frame. A caption position or crop that works at the start can become distracting when the source composition changes." },
    ],
  },
  "word-by-word-video-captions": {
    afterSection: "style",
    heading: "Read the finished captions while the speaker talks",
    intro: "These complete clips were transcribed and rendered through SupoClip. Play them with sound to check synchronization, then mute the player and judge readability against the changing background.",
    examples: [
      { clipId: "price-1", note: "Follow the active-word highlight through a full sentence. Judge the text at the displayed phone-sized width rather than enlarging the player to make a small font seem readable." },
      { clipId: "ton-1", note: "Use a second speaker and recording to check the same caption style. Reusing a preset still calls for checking names, contrast, line breaks, and placement on each clip." },
    ],
  },
  "schedule-week-of-youtube-shorts": {
    afterSection: "calendar",
    heading: "Three finished clips from the same source recording",
    intro: "This batch comes from one Andrew Price talk. It demonstrates a set of finished assets you could review before assigning publishing dates. These examples have not been posted to a YouTube channel or put on a real schedule.",
    examples: [
      { clipId: "price-1", note: "Give this excerpt its own takeaway and title. Approve the complete render before assigning a slot in your publishing calendar." },
      { clipId: "price-2", note: "Compare the second excerpt with the first and remove it from the batch if it repeats the same point for your audience." },
      { clipId: "price-3", note: "Treat this as a third editorial decision, not an obligation to fill another day. The source and timestamps stay attached so a correction can be checked later." },
    ],
  },
  "open-source-video-clippers": {
    afterSection: "supoclip",
    heading: "Actual output from the local SupoClip pipeline",
    intro: "We generated these clips with the repository's processing service, local Whisper transcription, and an Ollama model for selection. They demonstrate SupoClip on two recordings; they are not a Clips AI or Auto-Editor benchmark.",
    examples: [
      { clipId: "price-1", note: "Use this longer talk to inspect the final selection, captions, and composition. The attribution file records the source and the processing configuration." },
      { clipId: "ton-1", note: "Compare the same workflow on an interview. Different source footage is useful for evaluating output; it does not establish that one tool is faster or better than another." },
    ],
  },
};

export function getArticleVideoObjects(slug: string, siteUrl: string) {
  const gallery = articleClipGalleries[slug];
  if (!gallery) return [];
  return gallery.examples.flatMap(({ clipId }) => {
    const clip = getArticleClip(clipId);
    return clip ? [{
      "@type": "VideoObject",
      name: clip.title,
      description: `SupoClip excerpt from ${clip.source.title}. ${clip.edits}`,
      thumbnailUrl: `${siteUrl}${clip.poster}`,
      contentUrl: `${siteUrl}${clip.src}`,
      uploadDate: clip.publishedAt,
      duration: `PT${clip.durationSeconds}S`,
      inLanguage: "en",
      isBasedOn: clip.source.url,
      license: clip.source.licenseUrl,
      creditText: clip.source.credit,
    }] : [];
  });
}
