import type { BlogPost } from "@/lib/blog-posts";

export interface GuideSection {
  id: string;
  heading: string;
  paragraphs: string[];
  steps?: string[];
  bullets?: string[];
  table?: { caption: string; headers: string[]; rows: string[][] };
  links?: Array<{ href: string; label: string }>;
  example?: "editor";
}

export interface CreatorGuide {
  post: BlogPost;
  intro: string;
  sections: GuideSection[];
  faqs: Array<{ question: string; answer: string }>;
  cta: { title: string; body: string; label: string };
  related: [string, string, string];
}

const publishedAt = "2026-10-26";
const post = (slug: string, title: string, description: string, keywords: string[], readingTime: string): BlogPost => ({
  slug, title, description, keywords, readingTime,
  summary: description,
  eyebrow: "Creator workflow",
  category: "Guide",
  publishedAt,
  updatedAt: publishedAt,
  author: "SupoClip Team",
  language: "en",
});

/** English editorial guides. Explicit language keeps untranslated copy and schema honest. */
export const creatorGuides: CreatorGuide[] = [
  {
    post: {
      ...post("podcast-to-shorts", "How to Turn a Video Podcast into Shorts and Reels", "Turn a video podcast into captioned Shorts and Reels: choose standalone moments, fix speaker framing, review subtitles, and export clips with SupoClip.", ["podcast to shorts", "podcast clips", "podcast to reels", "video podcast clipper"], "7 min read"),
      image: { src: "/clips/articles/ton-source.jpg", alt: "Ton Roosendaal in the source interview used for the SupoClip conversation clipping example", width: 960, height: 540 },
    },
    intro: "A useful podcast clip gives a new viewer a complete idea without asking them to watch the episode first. Start with a question, surprising observation, or specific lesson; keep the explanation that earns the payoff. Here is a repeatable way to do that with a video podcast, from the original recording to the final vertical export.",
    sections: [
      {
        id: "prepare", heading: "1. Start with a recording you can repurpose",
        paragraphs: ["Use the video version of your episode: a YouTube link or a video file you can upload. A clear voice track matters more than an elaborate set. If the audio is muddy, correct it before you ask a transcription system to find useful moments.", "Choose an episode with distinct answers or stories. A conversation about one specific mistake, decision, or result is easier to turn into a standalone clip than a long introduction. Have permission to reuse the recording and any guest material. This workflow assumes video footage; an audio-only episode needs a visual treatment first."],
        bullets: ["Keep an episode title, guest name, and source link with your project notes.", "Check source duration against your hosted plan before submitting a long episode.", "If a source link cannot be imported, upload your own original video file."],
      },
      {
        id: "select", heading: "2. Generate candidates, then choose complete ideas",
        paragraphs: ["Paste the episode link into SupoClip or upload the file. The app transcribes speech and proposes short segments. Treat the scores as a review order: a high score tells you which candidate to inspect first, not how many views it will receive.", "Play each candidate without the rest of the conversation. Can you identify the subject in the opening sentence? Does the answer deliver something specific? A clip that starts with ‘that was the problem’ needs an earlier sentence or a different starting point. Trim greetings and repeated setup, but keep any qualification that changes the speaker's meaning."],
        links: [{ href: "/blog/find-best-moments-in-video", label: "Use the highlight-selection checklist" }],
      },
      {
        id: "example", heading: "A worked example: cutting a recorded interview",
        paragraphs: ["A recorded interview has the same context problem as a video podcast: an answer can depend on a question or an earlier discussion. For the examples below, we ran a Creative Commons interview with Blender founder Ton Roosendaal through SupoClip and rendered complete captioned excerpts.", "Use the source links to compare each cut with the original. Listen for the subject, follow the explanation, and check what was left outside the excerpt. These are examples of the clipping workflow, not a claim that the speaker uses or endorses SupoClip."],
      },
      {
        id: "edit", heading: "3. Review speaker framing and captions together",
        paragraphs: ["Open a chosen clip in the editor. Use vertical framing for Shorts and Reels, then watch the entire cut. A crop that works on the host can miss the guest after a camera change. Where a chart or two-person composition matters, preserve enough of the original frame for it to make sense.", "Correct names, brand spellings, numbers, and punctuation in the transcript. Keep captions away from faces and the platform's controls. Preview at phone size rather than judging readability on a desktop monitor. If the caption style distracts from the answer, simplify the animation and highlight fewer words."],
        links: [{ href: "/blog/word-by-word-video-captions", label: "Style and correct word-by-word captions" }],
      },
      {
        id: "publish", heading: "4. Export each clip with a clear reason to watch",
        paragraphs: ["Export using the preset for your destination. Give each clip a title that describes the actual takeaway. Avoid a dramatic hook that the guest never supports: people should find the promised answer inside the clip.", "Publish the exported file to Reels, or post to YouTube through SupoClip where publishing is enabled for your account. Scheduling depends on your plan and the deployment's publishing access. Keep the episode link in the appropriate description or profile destination so interested viewers can continue watching."],
        bullets: ["Watch the exported file, including the first and last seconds.", "Check that important text survives the destination app's overlays.", "Record which episode and source moment each clip came from.", "Review actual viewing and response data before deciding which topics to clip next."],
        links: [{ href: "/blog/schedule-week-of-youtube-shorts", label: "Build a week of Shorts from one recording" }],
      },
    ],
    faqs: [
      { question: "Can I make Reels from a YouTube podcast?", answer: "Yes. If you have permission to repurpose it, use the YouTube video as your source, create a vertical clip, and download the Reels export. Upload that file to Instagram; this guide does not assume direct Instagram publishing is available." },
      { question: "How many clips should I make from an episode?", answer: "Keep as many as contain distinct, complete ideas worth publishing. There is no fixed number per episode, and repeating the same answer with a different hook does not create a new takeaway." },
      { question: "Can a short clip omit the interviewer's question?", answer: "Yes, when the answer names its subject and makes sense on its own. Otherwise keep a short version of the question or choose an earlier starting point that preserves the context." },
    ],
    cta: { title: "Turn your next podcast episode into clips", body: "Bring one video episode. Review the suggested moments, correct the captions, and export the clips you would actually publish. Hosted SupoClip is paid; self-hosting is another option.", label: "Clip my podcast" },
    related: ["/blog/find-best-moments-in-video", "/blog/word-by-word-video-captions", "/youtube-shorts-clipper"],
  },
  {
    post: post("find-best-moments-in-video", "How to Find the Best Moments in a Long Video", "Choose stronger video highlights with a practical checklist for hooks, context, payoff, and clean cuts. Use AI suggestions without relying on a virality score.", ["find best moments in video", "find video highlights", "AI highlight detection", "choose podcast clips"], "6 min read"),
    intro: "The best moment in a recording is not always the loudest, funniest, or most emotional one. For a short video, it also needs to survive outside its original context. Use the checks below to turn a long recording into a shortlist of moments that a new viewer can understand.",
    sections: [
      {
        id: "brief", heading: "Decide who the clip should help",
        paragraphs: ["Write one sentence before you search: ‘This clip should help a first-time founder handle a difficult customer conversation,’ for example. A narrow audience gives you a useful selection rule. Without one, any surprising line can look like a good highlight.", "Scan chapters, questions, and topic changes for answers to that brief. Useful candidates often include a concrete mistake, a before-and-after explanation, a demonstration, or a disagreement with a clear reason. Mark approximate timestamps; you will refine boundaries later."],
      },
      {
        id: "checklist", heading: "Use four checks before you keep a moment",
        paragraphs: ["Watch a candidate as if it appeared in a feed with no episode title. Each check answers a different question. A compelling opening cannot compensate for a missing explanation, and a complete explanation can still need a cleaner first sentence."],
        table: { caption: "Editorial checklist for a standalone video highlight", headers: ["Check", "Keep it when", "Revise it when"], rows: [
          ["Hook", "The opening names a problem, tension, or specific result", "It begins with greetings or unexplained ‘this’ and ‘that’"],
          ["Context", "The subject and relevant conditions are clear", "You need the previous five minutes to understand it"],
          ["Payoff", "The speaker delivers a reason, example, or useful answer", "The clip makes a promise but cuts before the answer"],
          ["Boundaries", "The clip starts and ends on complete thoughts", "A cut changes the meaning or interrupts the conclusion"],
        ] },
        bullets: ["Listen once without watching: is the argument still coherent?", "Watch once without sound: can the framing and captions carry the topic?", "Check the source around both cuts for a qualification you might have removed."],
      },
      {
        id: "example", heading: "Compare a complete idea with an isolated quote",
        paragraphs: ["A broad statement about practicing a skill can make an appealing quote while leaving out the useful explanation. A stronger cut includes the action the speaker recommends and enough of the reason behind it. The original recording around both boundaries is the reference for whether the shortened version stays accurate.", "The Andrew Price examples below come from the same popular presentation. Apply the checklist to each one: does the excerpt retain the explanation that makes its opening useful? If a candidate needs more context than a short can reasonably hold, leave it in the full recording."],
      },
      {
        id: "ai", heading: "Let AI reduce the search, then make the decision",
        paragraphs: ["An AI video clipper can transcribe the recording, propose candidate segments, and rank them for review. In SupoClip, hook, engagement, value, and shareability scores help organize that shortlist. They are editorial signals, not predicted view counts.", "Transcript-based analysis works best when the interesting moment is expressed in speech. A silent product demonstration, a visual reveal, or a game-winning play may matter without being described aloud. Review those moments yourself instead of assuming a transcript search has found everything."],
        links: [{ href: "/ai-video-clipper", label: "See what the AI video clipper automates" }],
      },
      {
        id: "refine", heading: "Refine the cut before adding effects",
        paragraphs: ["Set the start before the first useful complete thought. Remove repeated setup where you can, then play the ending with the preceding sentence. Stop after the payoff rather than carrying an unrelated next topic into the clip. Preserve pauses that make the answer understandable.", "Only then adjust captions, crop, and hook text. Save a note about why you selected the moment. After publishing, compare the audience's response with that original hypothesis. If viewers leave before the explanation, test a clearer opening; if they watch but do not respond, reconsider whether the takeaway is useful to the audience you chose."],
        links: [{ href: "/blog/podcast-to-shorts", label: "Apply the checklist to a video podcast" }, { href: "/blog/repurpose-webinar-clips", label: "Apply it to a webinar recording" }],
      },
    ],
    faqs: [
      { question: "Does a high virality score guarantee a successful clip?", answer: "No. Scores can help prioritize review, but distribution, audience fit, the final edit, and the recording itself still matter. Judge performance using actual publishing results." },
      { question: "Should every highlight be the same length?", answer: "No. Keep the shortest cut that delivers a complete, accurate idea. Check the destination platform's current format requirements separately." },
      { question: "What if the best moment is mostly visual?", answer: "Inspect it manually. Transcript-led discovery may miss events that are not described in speech. Choose framing that preserves the visual action instead of assuming a face crop is appropriate." },
    ],
    cta: { title: "Find a shortlist in your own recording", body: "Use SupoClip to propose moments, then apply the hook, context, payoff, and boundary checks before exporting.", label: "Find my video highlights" },
    related: ["/blog/podcast-to-shorts", "/blog/repurpose-webinar-clips", "/ai-video-clipper"],
  },
  {
    post: post("twitch-vod-to-shorts", "How to Turn Twitch VODs into TikTok Clips and Shorts", "Repurpose a Twitch VOD into vertical clips: import a past broadcast, find spoken highlights, check gameplay framing, add captions, and export for TikTok or Shorts.", ["Twitch VOD to shorts", "Twitch VOD to TikTok", "Twitch clip maker", "AI stream clipper"], "6 min read"),
    intro: "A long stream contains different kinds of moments: stories, reactions, explanations, and action that happens without a word. They need different editing decisions. Use an AI shortlist for spoken moments, then review the gameplay and framing yourself before turning a Twitch VOD into vertical clips.",
    sections: [
      {
        id: "import", heading: "1. Import a finished broadcast",
        paragraphs: ["Wait for the stream to finish and use a Twitch VOD or clip link, or upload your own recording. SupoClip accepts those sources; it does not clip an ongoing live stream. Retain an original copy of recordings you plan to reuse.", "If a VOD is unavailable, restricted, or cannot be downloaded, use a local recording you have permission to edit. Start with a manageable recording rather than sending your entire archive in one attempt. Check the upload and duration limits shown by the app for your deployment."],
      },
      {
        id: "moments", heading: "2. Separate spoken highlights from silent action",
        paragraphs: ["Submit the recording for analysis and review the proposed moments. Transcript-led selection is useful for a story between matches, an explanation of a strategy, or a reaction where the commentary establishes what happened.", "A silent clutch play or subtle on-screen event may never appear in the transcript. Use timestamps from your own notes to check those passages in the source recording. Do not interpret a missing AI candidate as evidence that nothing interesting happened."],
        table: { caption: "Which stream moments need which kind of review", headers: ["Moment", "What to look for", "Review before exporting"], rows: [
          ["Story or opinion", "An opening and a self-contained payoff", "Remove references to earlier chat that viewers cannot see"],
          ["Spoken reaction", "Enough lead-in to understand the reaction", "Keep the event and the reaction in the same cut"],
          ["Silent gameplay", "The actual play in the source footage", "Select manually; speech analysis may miss it"],
          ["Chat response", "The question plus a clear answer", "Preserve context without exposing private information"],
        ] },
        links: [{ href: "/blog/find-best-moments-in-video", label: "Check whether a highlight stands on its own" }],
      },
      {
        id: "frame", heading: "3. Keep the important action inside the vertical frame",
        paragraphs: ["A face crop is useful when you are talking to the camera. It can be the wrong choice when the viewer needs a minimap, scoreboard, inventory, or a wide view of the play. Watch the entire candidate with the destination shape selected and adjust the framing in the editor.", "If the action does not survive a crop, use framing that preserves more of the source rather than forcing every moment into a close-up. This workflow does not promise an automatic stacked webcam-and-gameplay layout. A readable wide shot can serve the clip better than a large face that hides the event."],
        bullets: ["Check the beginning, the action itself, and every camera or scene change.", "Make sure captions do not cover game UI or the relevant target.", "Preview on a phone; tiny interface text may be unreadable even when it remains in frame."],
      },
      {
        id: "captions", heading: "4. Correct game names and trim the ending",
        paragraphs: ["Automatic captions can miss usernames, character names, abbreviations, and shouted words. Correct those in the transcript and replay the audio. Keep intentional slang when it matters to the speaker's meaning, but do not leave a transcription error just because it sounds plausible.", "Cut after the event or explanation resolves. If the clip ends with ‘wait until you see what happens,’ include the thing the viewer was asked to wait for. A readable hook should describe the actual situation rather than making a claim the footage does not support."],
        links: [{ href: "/blog/word-by-word-video-captions", label: "Make captions readable without hiding the action" }],
      },
      {
        id: "export", heading: "5. Export for TikTok or YouTube Shorts",
        paragraphs: ["Choose the platform export preset and download the rendered video. Upload the TikTok version yourself. For YouTube, you can also use SupoClip publishing where enabled for your account; scheduled posting requires the appropriate plan and access.", "Keep a simple record of the source VOD, timestamp, topic, and published destination. Compare results for stories, reactions, and explanations separately. That tells you which kind of stream moment your audience values instead of treating every clip as the same experiment."],
      },
    ],
    faqs: [
      { question: "Can SupoClip clip my Twitch stream while I am live?", answer: "No. Use a VOD or clip after the broadcast ends, or upload your recording. The workflow described here is for recorded video." },
      { question: "Will AI detect every gaming highlight?", answer: "No. SupoClip uses the transcript to select moments, so important silent gameplay can be missed. Review visual events manually and inspect the final crop." },
      { question: "Does this post clips directly to TikTok?", answer: "This guide uses downloaded TikTok exports. Do not assume direct TikTok publishing is available; check the connected platforms and access shown in your account." },
    ],
    cta: { title: "Give your next VOD a second life", body: "Import a completed stream, review the spoken highlights, and export the moments that work outside the live broadcast.", label: "Clip my Twitch VOD" },
    related: ["/blog/find-best-moments-in-video", "/blog/word-by-word-video-captions", "/youtube-shorts-clipper"],
  },
  {
    post: post("repurpose-webinar-clips", "How to Repurpose a Webinar into Social Media Clips", "Turn a webinar recording into useful social clips. Select audience questions, preserve slide context, correct technical captions, and create a focused publishing plan.", ["repurpose webinar", "webinar to social media clips", "webinar video clips", "webinar repurposing"], "6 min read"),
    intro: "A webinar is organized for people who agreed to spend time on a topic. A social clip must earn that attention from someone who has not seen the invitation, slides, or introduction. Start with the attendee's problem and keep only the context needed to deliver an answer.",
    sections: [
      {
        id: "inventory", heading: "Map the recording to audience questions",
        paragraphs: ["Look for three types of material: an explanation of a common problem, a demonstration of one task, and an answer from the Q&A. Write the question each possible clip answers. ‘How do I hand off a lead without losing context?’ is a better brief than ‘Clip the CRM webinar.’", "Remove housekeeping, introductions, holding screens, and extended sales setup from your shortlist. A product clip can still sell, but it should show a useful outcome before asking the viewer to book a meeting. Check that speakers and any customer examples are approved for public reuse."],
      },
      {
        id: "generate", heading: "Generate candidates from the video file or source link",
        paragraphs: ["Upload your webinar video to SupoClip, or paste a supported YouTube link. Review the transcript-led candidates against your question list. A useful answer may start in Q&A rather than in the section with the most polished slides.", "Trim a candidate until it contains one problem, one explanation, and a conclusion. Keep necessary conditions: an answer about a specific integration or pricing tier can become misleading if you remove the qualification. When an audience question is not audible, make the subject clear in the spoken answer or choose a more complete passage."],
        links: [{ href: "/blog/find-best-moments-in-video", label: "Evaluate hook, context, payoff, and boundaries" }],
      },
      {
        id: "slides", heading: "Choose framing for the information, not just the speaker",
        paragraphs: ["A face-centered crop is a good fit for a direct answer. A product walkthrough needs the interface, and a chart explanation needs its axes and labels. Inspect every shot where the recording moves between a presenter and a screen share.", "If a slide becomes unreadable in vertical framing, keep more of the source, tighten the cut to the relevant explanation, or prepare a separate simplified visual in your normal editor. Do not cover tiny original text with even more tiny captions. A clip should remain useful on the phone where it will be watched."],
        table: { caption: "Framing decisions for webinar clips", headers: ["Source content", "Keep visible", "Common failure"], rows: [
          ["Presenter answer", "Face and enough body language", "Captions covering the speaker's mouth"],
          ["Chart", "Axes, labels, and the change being explained", "Cropping away the scale that gives a number meaning"],
          ["Product demo", "The action and its result", "A cursor or key control outside the crop"],
          ["Panel discussion", "The active speaker and relevant reactions", "A fixed crop that misses the next speaker"],
        ] },
      },
      {
        id: "review", heading: "Check technical language and the final claim",
        paragraphs: ["Correct product names, acronyms, units, dates, and numbers in the captions. Then compare the final clip with the source around its boundaries. A statement such as ‘this works for all accounts’ may have been followed immediately by an exception you need to retain.", "Use a hook that names the viewer's task. An illustrative title such as ‘Why your demo handoff loses context’ gives a clearer reason to watch than ‘Webinar highlights, part 3.’ Treat that as a title-writing example, not a promise that one phrasing will improve performance."],
        links: [{ href: "/blog/word-by-word-video-captions", label: "Review and style the captions" }],
      },
      {
        id: "publish", heading: "Build a sequence with a useful next step",
        paragraphs: ["Publish clips that answer different questions rather than several excerpts that repeat the same pitch. Link each post to the appropriate next step: the full webinar, a relevant product walkthrough, or a signup page. The destination should deliver what the clip suggests.", "Keep source timestamps and approval notes with the final files. Review actual audience responses and downstream actions before investing in another batch. A clip that produces a useful question from the intended buyer can be more valuable than a broadly entertaining clip with no connection to the product."],
        bullets: ["Choose a platform preset and watch the exported file.", "Verify that the linked resource is public and relevant to this particular clip.", "Give each excerpt a distinct question and takeaway.", "Keep an owner responsible for correcting outdated product claims."],
      },
    ],
    faqs: [
      { question: "Can I use a webinar file instead of a public link?", answer: "Yes. Upload the video recording directly. Check the size and duration limits in your app, and confirm you can reuse speaker and customer material publicly." },
      { question: "Should I crop all webinar clips vertically?", answer: "Choose the format for the destination, but preserve the information the answer depends on. A slide or product interface may need more of the source frame than a talking-head shot." },
      { question: "Should every clip include a sales call to action?", answer: "Use a next step that fits the question the clip answers. Some clips naturally lead to a product page; others are better introductions to the full webinar or a detailed guide." },
    ],
    cta: { title: "Find useful clips in your webinar", body: "Upload a recording, review candidate answers, and turn the clearest moments into captioned social videos.", label: "Repurpose my webinar" },
    related: ["/blog/find-best-moments-in-video", "/blog/word-by-word-video-captions", "/blog/schedule-week-of-youtube-shorts"],
  },
  {
    post: {
      ...post("word-by-word-video-captions", "How to Add Word-by-Word Captions to Short Videos", "Create readable word-by-word captions for Shorts, Reels, and TikTok. Correct the transcript, choose a style, check safe areas, and export with SupoClip.", ["word by word captions", "animated video captions", "add captions to shorts", "automatic video subtitles"], "6 min read"),
      image: { src: "/blog/supoclip-editor.webp", alt: "SupoClip editor with caption styles, word timing, and a vertical video preview", width: 1800, height: 1109 },
    },
    intro: "Word-by-word captions highlight speech as it happens. They help only when the words are accurate, easy to read, and placed where the viewer can see them. Build the style around the recording rather than choosing the busiest animation first.",
    sections: [
      {
        id: "transcribe", heading: "1. Generate a clip and inspect its transcript",
        paragraphs: ["Import a supported video link or upload a video to SupoClip. Once a clip has been generated, open it in the editor and inspect the transcript alongside playback. Word-level timestamps connect the text to the spoken audio.", "Start with accuracy. Check names, numbers, product terms, and words spoken over music or another voice. Listen to an uncertain phrase in its surrounding sentence before changing it. Captions should reflect what was actually said; they should not silently rewrite an ambiguous statement into a more confident claim."],
      },
      {
        id: "correct", heading: "2. Correct the words before styling them",
        paragraphs: ["Use the transcript controls to fix a misheard word and replay that portion of the clip. A spelling correction should not require moving an otherwise correct timestamp. If you rewrite a larger portion of the script, check the resulting synchronization rather than assuming the new text will fit the old cadence.", "Remove distracting transcription artifacts, but retain qualifications that matter to meaning. For an illustrative example, ‘fifteen percent’ and ‘fifty percent’ can both look plausible in a transcript; the audio determines which belongs in the finished clip."],
        example: "editor",
      },
      {
        id: "style", heading: "3. Choose a readable caption style",
        paragraphs: ["Start from a caption preset, then adjust the font, size, colors, position, and active-word highlight. Use a small, consistent palette. Highlighting every word with an aggressive color change can compete with a demonstration or a fast-moving scene.", "Check the least convenient frame, not just a clean still. White text that works against a dark shirt can disappear when the camera cuts to a bright slide. A contrasting background or outline can help, but it should leave the subject visible. Save a style only after you have watched it across a representative clip."],
        bullets: ["Use a font that remains legible at phone size.", "Break captions into short, readable groups rather than filling the width.", "Keep essential text clear of faces, charts, and product controls.", "Apply a saved style consistently, then review exceptions on each clip."],
      },
      {
        id: "safe-area", heading: "4. Preview against platform controls",
        paragraphs: ["Short-form apps place interface elements around and over the video. Use the editor's safe-area preview as a guide, then inspect the exported clip in the actual destination app before publishing. There is no single caption position that works for every app, device, and piece of footage.", "Pay particular attention to the bottom description area and the controls along the side. If the source already contains text, move or simplify the captions so two different messages do not overlap. The right position can change when you switch from a face crop to a wider screen recording."],
        table: { caption: "Caption problems to check in playback", headers: ["Problem", "What to inspect", "Adjustment"], rows: [
          ["Words disappear", "Bright and dark scene changes", "Increase contrast or use a readable background"],
          ["Captions feel late", "The spoken word against its highlight", "Review transcript timing and the final render"],
          ["Text hides the action", "Faces, hands, slides, and game UI", "Move captions or reduce the amount on screen"],
          ["Too much movement", "Animation during fast speech", "Choose a quieter style and shorter text groups"],
        ] },
      },
      {
        id: "export", heading: "5. Export and watch without sound",
        paragraphs: ["Choose the destination preset and render the clip. SupoClip's rendered captions are part of the video image, so they remain visible when you upload the file. Watch the exported version with sound to check synchronization, then without sound to check whether the topic and argument remain understandable.", "Burned-in captions cannot be turned off by a viewer like a separate subtitle track. They also do not replace every accessibility feature a platform may offer. Where you provide additional platform captions, check that the two presentations do not create confusing duplicated text."],
        links: [{ href: "/youtube-shorts-clipper", label: "Finish and export a YouTube Short" }, { href: "/blog/podcast-to-shorts", label: "Use captions in a podcast clipping workflow" }],
      },
    ],
    faqs: [
      { question: "Can I correct automatically generated captions?", answer: "Yes. Review the transcript in the clip editor, correct misheard words, and check playback before exporting. Recheck timing after larger script changes." },
      { question: "Do I need a different style for every platform?", answer: "You can reuse a visual style, but inspect placement and readability in each destination. Platform controls and the footage itself can require different caption positions." },
      { question: "Will viewers be able to turn these captions off?", answer: "Captions rendered into the video image stay visible. They are different from a separate subtitle track that a platform can let viewers enable or disable." },
    ],
    cta: { title: "Make your next clip easier to follow", body: "Generate a clip, correct the transcript, and create a caption style that stays readable on a phone.", label: "Create a captioned clip" },
    related: ["/blog/podcast-to-shorts", "/blog/twitch-vod-to-shorts", "/blog/repurpose-webinar-clips"],
  },
  {
    post: post("schedule-week-of-youtube-shorts", "How to Create and Schedule a Week of YouTube Shorts", "Build a weekly Shorts workflow from one recording: choose distinct clips, finish captions, connect YouTube, schedule eligible posts, and verify publication.", ["schedule YouTube Shorts", "week of shorts from one video", "YouTube Shorts workflow", "batch create shorts"], "6 min read"),
    intro: "Batching Shorts works when you finish the editorial decisions before filling a calendar. One recording may contain enough distinct ideas for a week, or it may contain only two worth posting. Let the material determine the number, then build a schedule you can review and maintain.",
    sections: [
      {
        id: "shortlist", heading: "Choose distinct ideas from one recording",
        paragraphs: ["Import a podcast, interview, tutorial, or webinar into SupoClip. Review the suggested candidates and give each useful moment a one-sentence takeaway. If two candidates teach the same lesson, keep the clearer one before looking for another angle.", "Do not set a quota that forces weak clips into the week. A consistent workflow can include days without a post. Keep a small reserve of approved clips so an unavailable source or a last-minute correction does not force you to publish something unfinished."],
        links: [{ href: "/blog/find-best-moments-in-video", label: "Select complete, useful moments" }],
      },
      {
        id: "finish", heading: "Finish the clips before assigning dates",
        paragraphs: ["For each selected moment, check the opening, context, ending, transcript, and vertical framing. Export or complete the render needed for publishing. Watch the finished file rather than relying only on the editor preview.", "Write a title around the specific idea in that clip. Review any AI-drafted title and description for accuracy, especially names, numbers, and claims. A week of clips from one interview should feel like several useful answers, not the same teaser repeated on different days."],
        bullets: ["One distinct takeaway per clip.", "Caption spelling and synchronization checked.", "Framing reviewed through scene changes.", "Final title and description match what the clip actually says."],
      },
      {
        id: "calendar", heading: "Arrange a simple weekly sequence",
        paragraphs: ["The example below is an illustrative planning template, not a tested best-time-to-post recommendation. Replace the topics with the moments your source actually contains. Use your channel's audience data to choose times, then keep the schedule simple enough to maintain."],
        table: { caption: "Example weekly sequence from one source recording", headers: ["Slot", "Editorial role", "Question before approval"], rows: [
          ["Monday", "Introduce a common problem", "Does a new viewer recognize the situation?"],
          ["Tuesday", "Explain a practical step", "Can someone apply the advice?"],
          ["Wednesday", "Show an example", "Is the result visible or clearly explained?"],
          ["Thursday", "Answer an objection", "Have we preserved important conditions?"],
          ["Friday", "Share a different lesson", "Is this distinct from the earlier clips?"],
        ] },
        links: [{ href: "/blog/podcast-to-shorts", label: "Create the batch from a video podcast" }],
      },
      {
        id: "connect", heading: "Connect YouTube and check publishing access",
        paragraphs: ["Where social publishing is enabled for your account, open Settings → Social and connect the intended YouTube account. Confirm the channel before posting, especially when you manage more than one. Use the connection and access status shown in your app as the source of truth.", "On the hosted product, immediate YouTube publishing is available on Pro and scheduled posting is a Creator feature when publishing is enabled. Self-hosted deployments also need the platform integration and publishing access configured. A paid plan alone does not configure a deployment's YouTube credentials."],
        links: [{ href: "/#pricing", label: "Check current hosted plans" }],
      },
      {
        id: "schedule", heading: "Schedule, then verify the actual result",
        paragraphs: ["Open a finished clip's publishing controls, select the connected YouTube account, and review the title and description. Enable scheduling, choose the intended date and time, and double-check the time displayed before submitting. The displayed time should match the publishing time you intended; check your device's timezone if it does not.", "Confirm that the post appears in the scheduled list. After the due time, check its status and the video on the actual channel. If a post fails, read the error and verify the connection before retrying. Check the existing post or platform result first so a retry does not create an accidental duplicate."],
        bullets: ["Use the scheduled-post controls to cancel a post you no longer want to send.", "Keep exported files as a fallback if the publishing connection needs attention.", "For TikTok or Reels, use exports and the destination platform's available publishing workflow.", "Review which topics earn useful viewing and audience responses before filling the next week."],
        links: [{ href: "https://support.google.com/youtube/answer/15424877", label: "YouTube's current Shorts format guidance" }],
      },
    ],
    faqs: [
      { question: "Can I schedule Shorts with SupoClip?", answer: "Where YouTube publishing is enabled for your account, eligible users can schedule finished clips. On the hosted service, scheduling is a Creator feature. Availability also depends on the deployment's publishing configuration." },
      { question: "Do I need seven clips for a weekly workflow?", answer: "No. Schedule only distinct clips that are complete and useful. A smaller batch can be more appropriate than filling every day with repetitive material." },
      { question: "What if scheduling is unavailable in my account?", answer: "Export the finished videos and use the publishing controls available in YouTube Studio. Check your plan, connection, and deployment access before expecting SupoClip's scheduling controls to appear." },
    ],
    cta: { title: "Build your next batch of Shorts", body: "Start with one recording and finish a shortlist of clips. Then publish or schedule through the options available to your account.", label: "Create my Shorts batch" },
    related: ["/blog/podcast-to-shorts", "/youtube-shorts-clipper", "/blog/repurpose-webinar-clips"],
  },
  {
    post: { ...post("open-source-video-clippers", "Open-Source Video Clippers: SupoClip, Clips AI, and Auto-Editor", "Compare an open-source clipping app, a Python clipping library, and a command-line editor. Understand their workflows, setup tradeoffs, and how to test your own footage.", ["open source video clippers", "open source clipping tool", "Clips AI vs SupoClip", "self hosted video clipper"], "6 min read"), eyebrow: "Open-source comparison", category: "Comparison" },
    intro: "Open-source video tools solve different parts of the editing job. A complete clipping app, a Python library, and an editor that removes quiet sections are not interchangeable. Choose based on the workflow you need to finish, then test the same recording before committing your archive to it.",
    sections: [
      {
        id: "scope", heading: "What this comparison establishes",
        paragraphs: ["This is a documentation-based comparison reviewed on October 26, 2026. We build SupoClip, so our product is included with that relationship disclosed. We inspected the projects' official documentation; we did not run Clips AI or Auto-Editor on a shared test recording and make no speed, quality, or engagement ranking.", "The practical distinction is the starting point. SupoClip is an application with a browser workflow. Clips AI provides Python building blocks. Auto-Editor automates editing decisions such as which sections to retain based on signals like audio loudness. Those are useful choices for different jobs."],
      },
      {
        id: "compare", heading: "Compare the kind of tool before comparing features",
        paragraphs: ["Use the table to choose what to evaluate, not as a benchmark. A library may be the right choice inside your own application even when a creator would prefer an interface. An automatic cleanup tool may solve your problem without selecting social highlights at all."],
        table: { caption: "Documented workflow differences; no comparative runtime testing", headers: ["Project", "Documented starting point", "Who should evaluate it"], rows: [
          ["SupoClip", "Browser app with clip discovery, captions, framing, editing, and export", "Creators wanting a complete workflow; teams wanting to self-host an app"],
          ["Clips AI", "Python library for transcript-based clip discovery and aspect-ratio resizing", "Developers assembling a video-processing pipeline"],
          ["Auto-Editor", "Command-line editing based on signals including audio loudness", "Editors automating cleanup or rule-based timeline work"],
        ] },
      },
      {
        id: "supoclip", heading: "SupoClip: a clipping workflow with an interface",
        paragraphs: ["SupoClip accepts supported source links and uploaded video, proposes segments from speech, and gives you an editor for cuts, captions, framing, and exports. You can use the paid hosted service or run the application yourself under AGPL-3.0. The public demo shows actual generated clips with linked source moments.", "Self-hosting means operating the application, worker, database, media storage, and your chosen transcription and model providers. A local Whisper and Ollama configuration is available; using external providers changes where data is processed and what usage costs you incur. Choose the hosted service if your main goal is publishing rather than maintaining that stack."],
        links: [{ href: "/demo", label: "Inspect real SupoClip output" }, { href: "/blog/self-host-supoclip-docker", label: "Read the setup requirements" }],
      },
      {
        id: "alternatives", heading: "Clips AI and Auto-Editor: building blocks for different jobs",
        paragraphs: ["Clips AI's official repository describes a Python library for spoken, narrative video. Its workflow transcribes with WhisperX, finds candidate clips from the transcript, and offers resizing that follows speakers. The documented installation includes Python dependencies, FFmpeg, and libmagic; its resizing example also requires a Hugging Face token for Pyannote. The project lists an MIT license. It is a candidate when you want programmatic control inside your own pipeline.", "Auto-Editor's official documentation describes automatic editing using methods including loudness, motion, and subtitles. Its repository presents a command-line application and lists the Unlicense. Evaluate it for repeatable cleanup or rule-based edits. Detecting a quiet section is a different problem from deciding whether an explanation will make a useful standalone social clip; test the operation you actually need."],
        links: [{ href: "https://github.com/ClipsAI/clipsai", label: "Clips AI official repository and setup" }, { href: "https://auto-editor.com/", label: "Auto-Editor official documentation" }, { href: "https://github.com/WyattBlue/auto-editor", label: "Auto-Editor repository and license" }],
      },
      {
        id: "test", heading: "Run a fair test on your own footage",
        paragraphs: ["Use one recording you know well and have permission to process. Mark a few moments you consider useful before running any tool. Include a speaker change, a difficult proper noun, and a visual event that the transcript does not explain. Those expose different strengths and limitations.", "Keep setup time separate from processing time. Record hardware, model choices, source duration, paid API usage, and how much manual editing was required. Compare only the tasks each tool is designed to perform; do not count missing social-highlight selection as a failed cleanup test."],
        steps: ["Define the job: highlight discovery, captioned social exports, or recording cleanup.", "Record installation steps and the dependencies you actually need.", "Process the same source with documented settings and save the outputs.", "Check complete thoughts, crop stability, subtitle accuracy where applicable, and export usability.", "Add human review time and operating costs before choosing a workflow."],
        links: [{ href: "/open-source-video-clipper", label: "Choose between hosted and self-hosted SupoClip" }],
      },
    ],
    faqs: [
      { question: "Which open-source video clipper is best?", answer: "The answer depends on the job. Evaluate SupoClip for a complete clipping interface, Clips AI for Python integration, and Auto-Editor for automated editing rules. This article does not establish a comparative quality or speed winner." },
      { question: "Does open source mean processing has no cost?", answer: "No. You still provide hardware, storage, operating time, and any external services you choose. Check the project's license and the dependencies required for your particular workflow." },
      { question: "Were all three tools tested on the same video?", answer: "No. This comparison uses official documentation and SupoClip's own implementation and demo. Use the test checklist to evaluate output on your recording; no cross-tool benchmark results are claimed here." },
    ],
    cta: { title: "Try the complete SupoClip workflow", body: "Use the paid hosted app to start with a recording, or follow the self-hosting guide if you want to operate the application yourself.", label: "Start with SupoClip" },
    related: ["/open-source-video-clipper", "/blog/self-host-supoclip-docker", "/blog/supoclip-vs-opusclip"],
  },
];

export function getCreatorGuide(slug: string) {
  return creatorGuides.find((guide) => guide.post.slug === slug);
}
