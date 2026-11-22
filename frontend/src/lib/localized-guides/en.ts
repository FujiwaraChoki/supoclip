import type { ArticleShellExtras, ClipExamplesChrome, Gta6ArticleCopy } from "./types";

export const enShellExtras: ArticleShellExtras = {
  seeHostedPlans: "See hosted plans",
  faqTocLabel: "FAQ",
};

export const enClipExamplesChrome: ClipExamplesChrome = {
  eyebrow: "Made with SupoClip",
  fullClipMeta: (durationSeconds, sourceRange) => `${durationSeconds}s full clip · source ${sourceRange}`,
  videoAriaLabel: (clipTitle, sourceTitle) => `${clipTitle} — SupoClip example from ${sourceTitle}`,
  sourcePrefix: "Source:",
  watchOriginal: "Watch the original moment",
  detailsSummary: "Source, license, and edits",
  viewsNote: (formattedViews, checkedAt) => `${formattedViews} YouTube views when selected on ${checkedAt}.`,
  licenseRecord: "Source and license record",
  noEndorsement: "These editorial examples do not imply endorsement by the speakers or source publisher.",
  downloadAttribution: "Download attribution and processing notes",
  footerNote:
    "These are complete example clips, encoded at 540×960 for this page. Source timestamps refer to the original recording; removed pauses can make the final clip shorter than that span.",
};

export const enEditorFigure = {
  ariaLabel: "Open the full-size SupoClip editor screenshot",
  alt: "SupoClip caption editor showing style controls, a word-level transcript, and safe-area preview",
  caption:
    "Editor screenshot using a sample video. The caption controls and transcript sit beside the preview; open the image to inspect them at full size.",
};

export const enGta6Copy: Gta6ArticleCopy = {
  post: {
    title: "How to Clip GTA 6 (and What It Really Pays)",
    description:
      "Clip GTA 6 streams into TikToks and Shorts: what clipping campaigns pay, Rockstar's rules, and a step-by-step workflow from VOD to captioned vertical clips.",
    eyebrow: "GTA 6 clipping",
    category: "Guide",
    readingTime: "10 min read",
    summary:
      "Everyone is clipping GTA 6. Here is what it pays, which clips get taken down, and the workflow from a four-hour stream to vertical clips worth posting.",
    imageAlt: "Illustration of a GTA-style sunset stream with a webcam inset turning into three vertical captioned clips",
  },
  breadcrumb: "How to clip GTA 6",
  toc: {
    whyNow: "Why GTA 6",
    whatItPays: "What it pays",
    rules: "The rules",
    workflow: "The workflow",
    launchWeek: "Launch week",
    mistakes: "Mistakes",
    faq: "FAQ",
  },
  faqs: [
    {
      question: "Can you really make money clipping GTA 6?",
      answer:
        "Some clippers do, mostly through campaigns that pay per 1,000 approved views, often around $1. Most clips earn very little and a few carry the month, campaign budgets run out, and many campaigns cap what a single clip can earn. Treat it as paid practice at editing, not a guaranteed income.",
    },
    {
      question: "Is it legal to post GTA 6 clips?",
      answer:
        "Rockstar's video policy allows fan videos of its games for non-commercial use and lets Take-Two remove footage at any time. It specifically targets the ending, big story reveals, isolated cutscenes and pre-release footage. Monetizing is not explicitly granted, so read the policy and your campaign's rules before you count on the money.",
    },
    {
      question: "Can SupoClip clip a GTA 6 stream while it's live?",
      answer:
        "No. SupoClip works on recorded video: a Twitch or Kick VOD or clip, a YouTube video, or a file you upload. Clip the VOD once the stream ends.",
    },
    {
      question: "Will the AI find every good GTA 6 moment?",
      answer:
        "No. SupoClip picks moments from what is said, so narrated chases, reactions and opinions land on its shortlist. A silent near-miss or physics glitch may not. Note those timestamps while you watch and cut them by hand.",
    },
    {
      question: "How many GTA 6 clips do I get from one stream?",
      answer:
        "SupoClip's shortlist grows with the length of the video, roughly one candidate per five minutes up to a cap. Keep only the ones that work on their own; a few finished clips beat twenty rough ones.",
    },
    {
      question: "What about the in-game radio music?",
      answer:
        "GTA radio stations play licensed songs. They can get a clip muted or claimed on TikTok and YouTube, separate from Rockstar's own policy. Cut around songs or lower the music where you can.",
    },
  ],
  cta: {
    title: "Clip your first GTA 6 stream",
    body: "Paste a finished Twitch, Kick or YouTube VOD. Review the shortlist, fix the framing and captions, and post the clips you'd actually watch.",
    label: "Clip a GTA 6 VOD",
  },
  heroCaption: "Original illustration. SupoClip is not affiliated with Rockstar Games or Take-Two Interactive.",
  intro: [
    "Your feed is full of them: a streamer's face on top, a GTA chase underneath, captions bouncing word by word, and someone in the comments asking how much the clipper got paid. Clipping GTA 6 is the obvious play of the season, and it does pay some people. It pays the ones who treat it like editing, not like a lottery ticket.",
    "This guide is the full workflow: where clips come from, what campaigns actually pay, which rules get videos taken down, and how to go from a four-hour VOD to a handful of vertical clips with SupoClip. The visual tools along the way are interactive; change their numbers to your own.",
  ],
  whyNow: {
    heading: "Why everyone is clipping GTA 6 right now",
    p1LinkText: "Grand Theft Auto VI",
    p1AfterLinkBeforeDate: " launches on ",
    p1Date: "November 19, 2026",
    p1AfterDate:
      ", on PlayStation 5 and Xbox Series X|S, with a PC version planned later. It is set in the state of Leonida, around Vice City, and follows Lucia and Jason. Launch week will likely be one of the most-streamed weeks of the year, and most people will see it as short clips rather than four-hour VODs.",
    p2BeforeStrong: "That is the opportunity in one sentence: ",
    p2Strong: "streams are long, feeds want 30 to 60 seconds",
    p2AfterStrong:
      ". Someone has to find the moment, cut it so a stranger understands it, frame it for a phone and caption it. Streamers, brands and agencies pay clippers to do that, and creators who play the game themselves do it to grow their own channels.",
  },
  whatItPays: {
    heading: "What clipping GTA 6 actually pays",
    lead: "There are two ways GTA 6 clips turn into money, and they work very differently.",
    li1Label: "Clipping campaigns.",
    li1BeforeLink: " A streamer or brand funds a budget on a marketplace such as ",
    li1LinkText: "Whop Content Rewards",
    li1AfterLink:
      ", sets a rate per 1,000 views and rules for the footage. You post clips on your own accounts, submit the links, and get paid for views that pass review. Whop puts the average around $1 per 1,000 views; individual GTA campaigns set their own rate, often a cap per clip, and stop paying when the budget is gone.",
    li2Label: "Your own channel.",
    li2BeforeLink:
      " If you play and commentate yourself, clips drive people to your streams and can earn through platform programs. YouTube's ",
    li2LinkText: "monetization policies",
    li2AfterLink:
      " exclude reused content without meaningful commentary or editing, so a raw re-upload of somebody else's gameplay is the wrong plan.",
    mathIntro:
      "The math for campaigns is simple and humbling. At $1 per 1,000 views, 100,000 approved views is $100. Try your own numbers below, including what the SupoClip plan you would need costs:",
  },
  rules: {
    heading: "The rules that get GTA 6 clips taken down",
    p1BeforeLink: "Rockstar publishes a ",
    p1LinkText: "policy on posting its copyrighted material",
    p1BetweenLinkAndEm: ". It allows fan videos for ",
    p1Em: "non-commercial",
    p1AfterEm:
      " use, which it defines as not making money through the footage, and keeps the right to have anything removed. It specifically calls out videos of the ending or big story reveals, edited compilations of cutscenes, and pre-release footage. Cutscenes shown as part of a longer, narrated playthrough are fine.",
    p2: "On top of that, each campaign has its own rules: which streams you may use, required tags or links, minimum clip length, and banned tricks like bought views. Break them and the views don't count. Read them before you cut, not after.",
  },
  workflow: {
    heading: "The workflow: from a GTA 6 VOD to finished clips",
    lead: "Here is the whole process at a glance. The split matters: SupoClip does the repetitive work (transcribing, shortlisting, cropping, timing captions) so your attention goes to the decisions only you can make.",
    s1Heading: "1. Pick a VOD you're allowed to clip",
    s1Body:
      "Use your own stream, or the VODs a campaign names. Wait until the broadcast ends: SupoClip works on recordings, not live streams. Paste a Twitch or Kick VOD or clip link, a YouTube link, or upload the file. Hosted plans are metered in minutes of source video, so a four-hour VOD uses 240 minutes, whether you keep three clips or ten.",
    s2Heading: "2. Let SupoClip shortlist, then look for what it can't hear",
    s2Body:
      "SupoClip transcribes the stream and proposes moments, each with hook, engagement, value and shareability scores. Use the scores to decide what to watch first, not as a view forecast. Because the shortlist comes from speech, it is strong on narrated chases, reactions, opinions and answers to chat, and blind to a silent near-miss.",
    s3Heading: "3. Frame the streamer and the gameplay",
    s3BodyBefore:
      "Most GTA streams put the webcam in a corner. Cropped naively to 9:16, you get either a giant face or a sliver of the game. SupoClip detects a steady corner webcam and stacks it above the gameplay:",
    s3BodyAfter:
      "Watch the whole clip at phone size anyway. Check that the minimap or the car you're chasing survives the crop, and that captions don't cover the action. If the moment is all gameplay, a wider frame beats a big face.",
    s4Heading: "4. Cut for a stranger",
    s4Body:
      "The viewer has not seen the last three hours. Start on the situation, keep only the context they need, and stop right after the payoff. Open the clip in the studio to trim or split it, or type an instruction in the AI edit field, like “cut the dead air before the chase”, and review the change before you keep it.",
    s5Heading: "5. Fix the captions",
    s5Body:
      "Captions are word-synced, but transcription has never heard of half of GTA 6. Correct character and place names (Lucia, Jason, Leonida, Vice City), usernames and shouted lines. Each clip also gets a short hook title at the top for its first seconds; make sure it describes what actually happens, because a promise the clip doesn't keep loses viewers and campaign approvals.",
    editorAlt: "SupoClip caption editor with style controls, a word-level transcript and a safe-area preview",
    editorCaption: "The SupoClip editor, shown with a sample video: transcript, caption style and preview side by side.",
    s6Heading: "6. Export, publish and submit",
    s6Body:
      "Export in 9:16 for TikTok, Shorts or Reels, or publish to connected TikTok and YouTube accounts straight from SupoClip. On Creator you can schedule posts ahead, which helps you keep a steady pace through launch week. For campaigns, submit each link as soon as it is live, and keep a simple log: source VOD, timestamp, kind of moment, views.",
    scheduleLinkLabel: "How to schedule a week of Shorts from one recording",
  },
  launchWeek: {
    heading: "A launch-week plan",
    lead: "The first days after launch are the most crowded and the most watched. Speed matters, but a rough clip posted fast still loses to a clean one posted an hour later. A realistic rhythm:",
  },
  mistakes: {
    heading: "Mistakes that cost GTA 6 clippers views",
    items: [
      {
        label: "Spoilers for reach.",
        body: "They spike, then get reported and removed, and can sink the account with them.",
      },
      {
        label: "Clips that need the stream.",
        body: "“He finally did it” means nothing to a stranger. Say what “it” is.",
      },
      {
        label: "Ending before the payoff.",
        body: "If you ask people to wait for something, show it.",
      },
      {
        label: "Unreadable framing.",
        body: "Tiny HUD text and a crop that loses the car. Preview on a phone.",
      },
      {
        label: "Licensed music.",
        body: "A radio hit under your clip can mute it or hand the revenue to someone else.",
      },
      {
        label: "Volume over quality.",
        body: "Twenty near-identical clips from one chase compete with each other. Post the best one.",
      },
    ],
    moreBeforeFirst: "More on spotting standalone moments in ",
    moreFirstLink: "how to find the best moments in a long video",
    moreBetween: ", and on stream footage in general in ",
    moreSecondLink: "turning Twitch VODs into TikTok clips and Shorts",
    moreAfter: ".",
  },
  sources: {
    heading: "Sources",
    gtaSix: "Rockstar Games: Grand Theft Auto VI",
    rockstarPolicy: "Rockstar Games: Policy on posting copyrighted Rockstar Games material",
    whopContentRewards: "Whop: Content Rewards",
    youtubeMonetization: "YouTube Help: Channel monetization policies",
    checkedNote:
      "Checked November 17, 2026. Campaign rates and platform rules change; check the current versions before you rely on them.",
  },
  calculator: {
    eyebrow: "Interactive estimate",
    title: "What a month of GTA 6 clips could pay",
    description:
      "Move the sliders to your own assumptions. Payout per clip is approved views × the campaign rate, stopped at the per-clip cap.",
    clipsPerWeekLabel: "Clips posted per week",
    clipsPerWeekHint: "Count only clips you would actually be proud to post.",
    viewsPerClipLabel: "Approved views per clip (average)",
    viewsPerClipHint: "Campaigns pay on views that pass their review, inside their tracking window.",
    rateLabel: "Rate per 1,000 views",
    rateHint: "Set by each campaign. Around $1 is a common reference point.",
    capLabel: "Cap per clip",
    capNone: "No cap",
    capHint: "Many campaigns stop paying a clip past a set amount. 0 means no cap.",
    vodHoursLabel: "Stream hours you clip per month",
    vodHoursUnit: "h",
    vodHoursHint: "Hosted SupoClip meters minutes of source video, not clips.",
    payoutLabel: "Estimated payout",
    payoutDetail: (clipsPerMonth, formattedViews) => `${clipsPerMonth} clips · ${formattedViews} views a month`,
    perClipLabel: "Per clip",
    cappedDetail: "Every clip hits the cap at this view count.",
    uncappedDetail: "Below the per-clip cap.",
    afterPlanLabel: "After SupoClip plan",
    planFitsDetail: (minutes, planName, formattedPrice, planMinutes) =>
      `${minutes} source minutes fits ${planName} (${formattedPrice}/mo, ${planMinutes} min).`,
    planOverDetail: (minutes) =>
      `${minutes} minutes is more than Creator's 1,500. Clip fewer hours, or self-host.`,
    averageLead: "An average hides the shape.",
    averageBody:
      " One clip at 200,000 views and nineteen at 500 average out to about 10,500 views per clip, but at a $150 cap that month pays roughly $160, not the $210 an even spread suggests. Expect most clips to earn little and a few to carry the month.",
    disclaimer:
      "Estimate only, before taxes and fees. It is not a forecast of what you will earn, and campaign budgets can run out before your views are counted.",
  },
  workflowDiagram: {
    caption: "The GTA 6 clipping workflow · you decide, SupoClip does the repetitive part",
    youLabel: "You",
    appLabel: "SupoClip",
    steps: [
      { title: "Pick a VOD", you: "Your own stream, or one a campaign lets you clip", app: "Imports Twitch, Kick and YouTube links or a file" },
      { title: "Shortlist", you: "Watch candidates, note silent plays", app: "Transcribes speech and ranks spoken moments" },
      { title: "Frame", you: "Check every scene change at phone size", app: "Stacks a corner webcam above the gameplay" },
      { title: "Cut", you: "Hook in the first seconds, end on the payoff", app: "Trim, split, and AI edits in the studio" },
      { title: "Caption", you: "Fix names: Lucia, Jason, Leonida, Vice City", app: "Word-synced captions and a hook title" },
      { title: "Publish", you: "Submit links to the campaign, log results", app: "Exports 9:16, posts to TikTok and YouTube" },
    ],
  },
  reactionLayout: {
    svgTitle: "From a 16:9 stream to a stacked 9:16 clip",
    svgDesc:
      "On the left, a widescreen stream with the webcam in the top-right corner. On the right, the vertical clip: the webcam fills the top third and the gameplay fills the bottom two thirds.",
    sourceTitle: "Stream VOD · 16:9",
    sourceSubtitle: "Webcam fixed in an upper corner",
    captionsHere: "captions sit here",
    outputTitle: "Clip · 9:16 · 1080×1920",
    figcaption:
      "When SupoClip finds a steady face in an upper corner of the frame, it places the streamer in the top third and the gameplay underneath. No steady webcam, or a webcam that moves? It falls back to its other layouts, so watch the result.",
  },
  clipAnatomy: {
    eyebrow: "Anatomy of one clip",
    title: "A 43-second GTA 6 chase, cut to hold attention",
    hookBarNote: "Hook title on screen for the first ~4 s",
    secondsUnit: "s",
    parts: [
      { label: "Hook", from: 0, to: 3, note: "Say or show the situation: “six stars, one minute in”." },
      { label: "Context", from: 3, to: 10, note: "Only what a stranger needs to follow it." },
      { label: "Build", from: 10, to: 32, note: "The chase, the heist, the argument with chat." },
      { label: "Payoff", from: 32, to: 40, note: "The crash, the escape, the reaction." },
      { label: "Out", from: 40, to: 43, note: "Stop. No “anyway, so…”" },
    ],
  },
  rulesBoard: {
    caption: "What you can post · a summary, not legal advice",
    groups: [
      {
        status: "ok",
        label: "Usually fine",
        items: [
          "Your own gameplay with your commentary",
          "Story moments inside a narrated playthrough",
          "Streams whose owner runs a clipping campaign, within its rules",
        ],
      },
      {
        status: "warn",
        label: "Check first",
        items: [
          "Monetizing: Rockstar’s policy is written for non-commercial use",
          "Licensed radio songs: can be muted or claimed",
          "Someone else’s stream with no campaign or permission",
        ],
      },
      {
        status: "no",
        label: "Gets removed",
        items: [
          "The ending or a big story reveal",
          "Isolated cutscenes and cinematic compilations",
          "Leaks, cheats and unauthorized mods",
        ],
      },
    ],
  },
  launchWeekTable: {
    caption: "A launch-week plan for GTA 6 clips (launch: November 19, 2026)",
    headers: ["When", "Focus", "What to do"],
    rows: [
      [
        "Before Nov 19",
        "Set up",
        "Connect TikTok and YouTube, pick caption style, read the campaign rules you want to join. Pre-release footage gets removed, so don't post any.",
      ],
      [
        "Nov 19 (launch)",
        "Stream or watch",
        "Play or follow the streams you're allowed to clip. Write down timestamps of silent plays while they happen.",
      ],
      [
        "Nov 20",
        "First batch",
        "Import yesterday's VODs. Keep the 3–5 strongest spoken moments; finish them properly instead of posting twenty rough ones.",
      ],
      [
        "Nov 21–23",
        "Steady output",
        "One VOD in, a few clips out, every day. Avoid the story: spoiler clips get reported and removed.",
      ],
      [
        "Nov 24–25",
        "Read the numbers",
        "Compare which kinds of moment kept people watching: chases, reactions, map discoveries, takes. Clip more of those.",
      ],
    ],
  },
  momentMap: {
    eyebrow: "Moment map · example stream",
    title: "One four-hour GTA 6 stream, three kinds of moment",
    description:
      "Select a marker. SupoClip reads the transcript, so spoken moments land on its shortlist; silent plays need your eyes, and spoilers stay out.",
    legendAria: "Legend",
    momentsAria: "Moments in the stream",
    kinds: {
      spoken: { label: "Spoken moment: the AI shortlist can find it", short: "AI finds it" },
      silent: { label: "Silent action: check it yourself", short: "Review by hand" },
      skip: { label: "Story spoiler or cutscene: leave it out", short: "Skip" },
    },
    moments: [
      { minute: 6, kind: "spoken", title: "First reaction to the opening view", note: "The streamer says what they see and why it surprises them. Speech carries it, so a transcript can find it." },
      { minute: 31, kind: "skip", title: "Story cutscene", note: "Isolated cutscenes are among the things Rockstar's video policy says it removes. Keep story beats inside commentary, or leave them out." },
      { minute: 58, kind: "silent", title: "Near-miss on the highway", note: "A great play with no words over it. A speech-led shortlist can miss it; note the timestamp while you watch." },
      { minute: 84, kind: "spoken", title: "Chat asks about the map size", note: "A question plus a clear answer. Keep the question on screen or in the first sentence." },
      { minute: 112, kind: "spoken", title: "Five-star chase, narrated", note: "Action with running commentary. Keep the setup, the chase and the reaction in one cut." },
      { minute: 139, kind: "silent", title: "Physics glitch", note: "Funny to watch, nobody explains it. Find it by scrubbing, then frame the action, not the face." },
      { minute: 171, kind: "spoken", title: "Hot take on the soundtrack", note: "An opinion with a reason. Mute or swap licensed radio music before posting." },
      { minute: 203, kind: "skip", title: "Story ending reveal", note: "Posting the ending or a big reveal is exactly what the policy targets, and it gets reported." },
      { minute: 226, kind: "spoken", title: "Wrap-up: the best moment of the day", note: "The streamer summarises their own highlight. Useful for a hook title and as a pointer to the real moment." },
    ],
    showTableSummary: "Show every moment as a table",
    tableHeaders: ["Time", "Moment", "Review"],
  },
};
