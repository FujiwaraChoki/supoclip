import Image from "next/image";
import Link from "next/link";

import { ArticleShell, type ArticleFaq } from "@/components/articles/article-shell";
import { ClipAnatomy, LaunchWeekTable, ReactionLayoutFigure, RulesBoard, WorkflowDiagram } from "@/components/articles/gta6/diagrams";
import { EarningsCalculator } from "@/components/articles/gta6/earnings-calculator";
import { MomentMap } from "@/components/articles/gta6/moment-map";
import type { BlogPost } from "@/lib/blog-posts";

export const GTA6_SOURCES = {
  rockstarPolicy: "https://support.rockstargames.com/articles/7bNaeoMFTV0iUDGhStTXvz",
  gtaSix: "https://www.rockstargames.com/VI",
  whopContentRewards: "https://whop.com/blog/content-rewards/",
  youtubeMonetization: "https://support.google.com/youtube/answer/1311392",
} as const;

const FAQS: ArticleFaq[] = [
  { question: "Can you really make money clipping GTA 6?", answer: "Some clippers do, mostly through campaigns that pay per 1,000 approved views, often around $1. Most clips earn very little and a few carry the month, campaign budgets run out, and many campaigns cap what a single clip can earn. Treat it as paid practice at editing, not a guaranteed income." },
  { question: "Is it legal to post GTA 6 clips?", answer: "Rockstar's video policy allows fan videos of its games for non-commercial use and lets Take-Two remove footage at any time. It specifically targets the ending, big story reveals, isolated cutscenes and pre-release footage. Monetizing is not explicitly granted, so read the policy and your campaign's rules before you count on the money." },
  { question: "Can SupoClip clip a GTA 6 stream while it's live?", answer: "No. SupoClip works on recorded video: a Twitch or Kick VOD or clip, a YouTube video, or a file you upload. Clip the VOD once the stream ends." },
  { question: "Will the AI find every good GTA 6 moment?", answer: "No. SupoClip picks moments from what is said, so narrated chases, reactions and opinions land on its shortlist. A silent near-miss or physics glitch may not. Note those timestamps while you watch and cut them by hand." },
  { question: "How many GTA 6 clips do I get from one stream?", answer: "SupoClip's shortlist grows with the length of the video, roughly one candidate per five minutes up to a cap. Keep only the ones that work on their own; a few finished clips beat twenty rough ones." },
  { question: "What about the in-game radio music?", answer: "GTA radio stations play licensed songs. They can get a clip muted or claimed on TikTok and YouTube, separate from Rockstar's own policy. Cut around songs or lower the music where you can." },
];

export function Gta6ClippingArticle({ post }: { post: BlogPost }) {
  return (
    <ArticleShell
      post={post}
      breadcrumb="How to clip GTA 6"
      toc={[["why-now", "Why GTA 6"], ["what-it-pays", "What it pays"], ["rules", "The rules"], ["workflow", "The workflow"], ["launch-week", "Launch week"], ["mistakes", "Mistakes"], ["faq", "FAQ"]]}
      faqs={FAQS}
      cta={{ title: "Clip your first GTA 6 stream", body: "Paste a finished Twitch, Kick or YouTube VOD. Review the shortlist, fix the framing and captions, and post the clips you'd actually watch.", label: "Clip a GTA 6 VOD" }}
    >
      <figure className="not-prose space-y-3">
        <Image src="/blog/how-to-clip-gta-6.webp" alt={post.image?.alt ?? ""} width={1672} height={941} priority sizes="(max-width: 768px) 100vw, 768px" className="h-auto w-full rounded-xl border" />
        <figcaption className="text-sm text-muted-foreground">Original illustration. SupoClip is not affiliated with Rockstar Games or Take-Two Interactive.</figcaption>
      </figure>

      <p>
        Your feed is full of them: a streamer&apos;s face on top, a GTA chase underneath, captions bouncing word by
        word, and someone in the comments asking how much the clipper got paid. Clipping GTA 6 is the obvious play of
        the season, and it does pay some people. It pays the ones who treat it like editing, not like a lottery ticket.
      </p>
      <p>
        This guide is the full workflow: where clips come from, what campaigns actually pay, which rules get videos taken
        down, and how to go from a four-hour VOD to a handful of vertical clips with SupoClip. The visual tools along the
        way are interactive; change their numbers to your own.
      </p>

      <section aria-labelledby="why-now" className="space-y-5">
        <h2 id="why-now">Why everyone is clipping GTA 6 right now</h2>
        <p>
          <a href={GTA6_SOURCES.gtaSix}>Grand Theft Auto VI</a> launches on <strong>November 19, 2026</strong>, on
          PlayStation 5 and Xbox Series X|S, with a PC version planned later. It is set in the state of Leonida, around
          Vice City, and follows Lucia and Jason. Launch week will likely be one of the most-streamed weeks of the year, and most
          people will see it as short clips rather than four-hour VODs.
        </p>
        <p>
          That is the opportunity in one sentence: <strong>streams are long, feeds want 30 to 60 seconds</strong>. Someone has to
          find the moment, cut it so a stranger understands it, frame it for a phone and caption it. Streamers, brands and agencies
          pay clippers to do that, and creators who play the game themselves do it to grow their own channels.
        </p>
      </section>

      <section aria-labelledby="what-it-pays" className="space-y-5">
        <h2 id="what-it-pays">What clipping GTA 6 actually pays</h2>
        <p>There are two ways GTA 6 clips turn into money, and they work very differently.</p>
        <ol>
          <li>
            <strong>Clipping campaigns.</strong> A streamer or brand funds a budget on a marketplace such as{" "}
            <a href={GTA6_SOURCES.whopContentRewards}>Whop Content Rewards</a>, sets a rate per 1,000 views and rules for
            the footage. You post clips on your own accounts, submit the links, and get paid for views that pass review.
            Whop puts the average around $1 per 1,000 views; individual GTA campaigns set their own rate, often a cap per
            clip, and stop paying when the budget is gone.
          </li>
          <li>
            <strong>Your own channel.</strong> If you play and commentate yourself, clips drive people to your streams and
            can earn through platform programs. YouTube&apos;s{" "}
            <a href={GTA6_SOURCES.youtubeMonetization}>monetization policies</a> exclude reused content without meaningful
            commentary or editing, so a raw re-upload of somebody else&apos;s gameplay is the wrong plan.
          </li>
        </ol>
        <p>
          The math for campaigns is simple and humbling. At $1 per 1,000 views, 100,000 approved views is $100. Try your own
          numbers below, including what the SupoClip plan you would need costs:
        </p>
        <EarningsCalculator />
      </section>

      <section aria-labelledby="rules" className="space-y-5">
        <h2 id="rules">The rules that get GTA 6 clips taken down</h2>
        <p>
          Rockstar publishes a <a href={GTA6_SOURCES.rockstarPolicy}>policy on posting its copyrighted material</a>. It
          allows fan videos for <em>non-commercial</em> use, which it defines as not making money through the footage, and
          keeps the right to have anything removed. It specifically calls out videos of the ending or big story reveals,
          edited compilations of cutscenes, and pre-release footage. Cutscenes shown as part of a longer, narrated
          playthrough are fine.
        </p>
        <RulesBoard />
        <p>
          On top of that, each campaign has its own rules: which streams you may use, required tags or links, minimum clip
          length, and banned tricks like bought views. Break them and the views don&apos;t count. Read them before you cut,
          not after.
        </p>
      </section>

      <section aria-labelledby="workflow" className="space-y-5">
        <h2 id="workflow">The workflow: from a GTA 6 VOD to finished clips</h2>
        <p>
          Here is the whole process at a glance. The split matters: SupoClip does the repetitive work (transcribing,
          shortlisting, cropping, timing captions) so your attention goes to the decisions only you can make.
        </p>
        <WorkflowDiagram />

        <h3>1. Pick a VOD you&apos;re allowed to clip</h3>
        <p>
          Use your own stream, or the VODs a campaign names. Wait until the broadcast ends: SupoClip works on recordings, not
          live streams. Paste a Twitch or Kick VOD or clip link, a YouTube link, or upload the file. Hosted plans are metered
          in minutes of source video, so a four-hour VOD uses 240 minutes, whether you keep three clips or ten.
        </p>

        <h3>2. Let SupoClip shortlist, then look for what it can&apos;t hear</h3>
        <p>
          SupoClip transcribes the stream and proposes moments, each with hook, engagement, value and shareability scores.
          Use the scores to decide what to watch first, not as a view forecast. Because the shortlist comes from speech, it
          is strong on narrated chases, reactions, opinions and answers to chat, and blind to a silent near-miss.
        </p>
        <MomentMap />

        <h3>3. Frame the streamer and the gameplay</h3>
        <p>
          Most GTA streams put the webcam in a corner. Cropped naively to 9:16, you get either a giant face or a sliver of the
          game. SupoClip detects a steady corner webcam and stacks it above the gameplay:
        </p>
        <ReactionLayoutFigure />
        <p>
          Watch the whole clip at phone size anyway. Check that the minimap or the car you&apos;re chasing survives the crop, and
          that captions don&apos;t cover the action. If the moment is all gameplay, a wider frame beats a big face.
        </p>

        <h3>4. Cut for a stranger</h3>
        <p>
          The viewer has not seen the last three hours. Start on the situation, keep only the context they need, and stop right
          after the payoff. Open the clip in the studio to trim or split it, or type an instruction in the AI edit field, like
          &ldquo;cut the dead air before the chase&rdquo;, and review the change before you keep it.
        </p>
        <ClipAnatomy />

        <h3>5. Fix the captions</h3>
        <p>
          Captions are word-synced, but transcription has never heard of half of GTA 6. Correct character and place names
          (Lucia, Jason, Leonida, Vice City), usernames and shouted lines. Each clip also gets a short hook title at the top for
          its first seconds; make sure it describes what actually happens, because a promise the clip doesn&apos;t keep loses
          viewers and campaign approvals.
        </p>
        <figure className="not-prose space-y-3">
          <Image src="/blog/supoclip-editor.webp" alt="SupoClip caption editor with style controls, a word-level transcript and a safe-area preview" width={1800} height={1109} sizes="(max-width: 768px) 100vw, 768px" className="h-auto w-full rounded-xl border" />
          <figcaption className="text-sm leading-6 text-muted-foreground">The SupoClip editor, shown with a sample video: transcript, caption style and preview side by side.</figcaption>
        </figure>

        <h3>6. Export, publish and submit</h3>
        <p>
          Export in 9:16 for TikTok, Shorts or Reels, or publish to connected TikTok and YouTube accounts straight from SupoClip.
          On Creator you can schedule posts ahead, which helps you keep a steady pace through launch week. For campaigns, submit
          each link as soon as it is live, and keep a simple log: source VOD, timestamp, kind of moment, views.
        </p>
        <p><Link href="/blog/schedule-week-of-youtube-shorts">How to schedule a week of Shorts from one recording</Link></p>
      </section>

      <section aria-labelledby="launch-week" className="space-y-5">
        <h2 id="launch-week">A launch-week plan</h2>
        <p>
          The first days after launch are the most crowded and the most watched. Speed matters, but a rough clip posted fast
          still loses to a clean one posted an hour later. A realistic rhythm:
        </p>
        <LaunchWeekTable />
      </section>

      <section aria-labelledby="mistakes" className="space-y-5">
        <h2 id="mistakes">Mistakes that cost GTA 6 clippers views</h2>
        <ul>
          <li><strong>Spoilers for reach.</strong> They spike, then get reported and removed, and can sink the account with them.</li>
          <li><strong>Clips that need the stream.</strong> &ldquo;He finally did it&rdquo; means nothing to a stranger. Say what &ldquo;it&rdquo; is.</li>
          <li><strong>Ending before the payoff.</strong> If you ask people to wait for something, show it.</li>
          <li><strong>Unreadable framing.</strong> Tiny HUD text and a crop that loses the car. Preview on a phone.</li>
          <li><strong>Licensed music.</strong> A radio hit under your clip can mute it or hand the revenue to someone else.</li>
          <li><strong>Volume over quality.</strong> Twenty near-identical clips from one chase compete with each other. Post the best one.</li>
        </ul>
        <p>
          More on spotting standalone moments in <Link href="/blog/find-best-moments-in-video">how to find the best moments in a long video</Link>,
          and on stream footage in general in <Link href="/blog/twitch-vod-to-shorts">turning Twitch VODs into TikTok clips and Shorts</Link>.
        </p>
      </section>

      <section aria-labelledby="sources" className="space-y-3 text-sm">
        <h2 id="sources">Sources</h2>
        <ul>
          <li><a href={GTA6_SOURCES.gtaSix}>Rockstar Games: Grand Theft Auto VI</a></li>
          <li><a href={GTA6_SOURCES.rockstarPolicy}>Rockstar Games: Policy on posting copyrighted Rockstar Games material</a></li>
          <li><a href={GTA6_SOURCES.whopContentRewards}>Whop: Content Rewards</a></li>
          <li><a href={GTA6_SOURCES.youtubeMonetization}>YouTube Help: Channel monetization policies</a></li>
        </ul>
        <p>Checked November 17, 2026. Campaign rates and platform rules change; check the current versions before you rely on them.</p>
      </section>
    </ArticleShell>
  );
}
