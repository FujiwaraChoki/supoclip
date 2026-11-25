import Image from "next/image";
import Link from "next/link";

import { ArticleShell, type ArticleFaq } from "@/components/articles/article-shell";
import {
  CampaignBriefChecklist,
  ClippingPipelineDiagram,
  MonetizationComparisonVisual,
  PlatformRulesMatrix,
  RetainerProgressionPath,
  WhopPayoutFlowDiagram,
} from "@/components/articles/clipping-income/diagrams";
import { ClippingIncomeCalculator } from "@/components/articles/clipping-income/income-calculator";
import type { BlogPost } from "@/lib/blog-posts";

export const CLIPPING_INCOME_SOURCES = {
  whopContentRewards: "https://whop.com/blog/content-rewards/",
  contentRewards: "https://contentrewards.com/",
  youtubeShortsMonetization: "https://support.google.com/youtube/answer/12504220",
  youtubeChannelPolicies: "https://support.google.com/youtube/answer/1311392",
  tiktokCreatorRewards: "https://support.tiktok.com/en/business-and-creator/creator-rewards-program/creator-rewards-program",
  supoclipLicense: "https://github.com/FujiwaraChoki/supoclip/blob/main/LICENSE",
} as const;

const FAQS: ArticleFaq[] = [
  {
    question: "Can you actually make money with Whop clipping?",
    answer:
      "Yes, some clippers make consistent side income on Whop / Content Rewards, and a smaller group turns it into a full-time freelance or agency business. Expect piece-rate economics, not passive income: most open CPM campaigns pay around $1.00–$1.50 per 1,000 approved views minus a 10% platform fee, individual clips often hit payout caps, and beginners commonly earn $50–$400 a month while learning hooks and distribution.",
  },
  {
    question: "What is the difference between Whop and Content Rewards?",
    answer:
      "Content Rewards started as an app inside Whop and expanded onto contentrewards.com while still settling payouts through Whop balances. When people talk about 'Whop clipping', they are usually referring to Content Rewards campaigns where brands, streamers, and podcasters fund a fixed budget and pay clippers per 1,000 verified views, per post, or on a retainer.",
  },
  {
    question: "Do I need followers to start clipping on Whop?",
    answer:
      "Most open CPM clipping campaigns on Whop / Content Rewards do not require an existing follower count—you are paid on verified views per clip. You do need to be 18+ for payouts, link your social accounts (Instagram typically requires a Creator or Business account), and warm up fresh accounts like a normal user so TikTok or Reels does not throttle new uploads at zero views.",
  },
  {
    question: "Why don't raw podcast or streamer clips qualify for YouTube Shorts or TikTok Creator Rewards?",
    answer:
      "Both platforms enforce strict originality rules. YouTube's Reused Content policy disqualifies channels that re-upload or lightly caption someone else's material—even if the original creator gave you permission. TikTok's Creator Rewards Program only pays on videos over 60 seconds and disqualifies accounts that rack up five unoriginal-content flags in a rolling 30-day window. Use Whop campaigns or direct client retainers when clipping third-party footage.",
  },
  {
    question: "How much does a freelance clipper or clipping agency charge on retainer?",
    answer:
      "Market rates generally run $300–$1,000/month for entry-level freelance packages (around 10–20 clips a month), $1,000–$3,000/month for dedicated retainers delivering 20–90 finished clips with moment selection and captions, and $1,500–$6,500+/month for full-service podcast post-production and multi-platform publishing.",
  },
  {
    question: "Can I self-host SupoClip for free if I am clipping for Whop campaigns?",
    answer:
      "Yes, if you are an individual creator posting to your own channels under an Authorized Clipping Campaign—SupoClip's Noncommercial Source License explicitly covers that under Permitted Monetized Use. If you clip for paying clients, manage someone else's channels, or run a clipping agency, use hosted SupoClip (Pro, Creator, or Team) or request written commercial permission.",
  },
];

export function MakeMoneyClippingArticle({ post }: { post: BlogPost }) {
  return (
    <ArticleShell
      post={post}
      breadcrumb="How to make money clipping"
      toc={[
        ["four-ways", "4 ways it pays"],
        ["whop-content-rewards", "Whop & Content Rewards"],
        ["the-math", "The real math"],
        ["platform-rules", "Rules & rejections"],
        ["workflow", "The workflow"],
        ["scaling-up", "Scaling to retainers"],
        ["mistakes", "7 mistakes"],
        ["faq", "FAQ"],
      ]}
      faqs={FAQS}
      cta={{
        title: "Build a repeatable clipping pipeline",
        body: "Paste a YouTube, Twitch, or Kick link—or upload a podcast recording. Let SupoClip transcribe, shortlist, reframe, and caption without watermarks so you can focus on hooks and distribution.",
        label: "Start clipping with SupoClip",
      }}
    >
      <figure className="not-prose space-y-3">
        <Image
          src="/blog/how-to-make-money-clipping.webp"
          alt={post.image?.alt ?? ""}
          width={1672}
          height={940}
          priority
          sizes="(max-width: 768px) 100vw, 768px"
          className="h-auto w-full rounded-xl border"
        />
        <figcaption className="text-sm text-muted-foreground">
          Long recordings become distribution when someone finds the hook, frames it for a phone, and gets it into the feed.
        </figcaption>
      </figure>

      <p>
        Every week, a new short-form video claims you can make $500 a day sitting on your phone cutting podcast clips on
        Whop. The screenshots are loud, the math is usually missing, and the comments are split between people who have
        actually cashed payouts and people whose last twenty uploads flatlined at 180 views.
      </p>
      <p>
        Here is the honest version. <strong>Making money with clipping is real</strong>—brands, streamers, and podcasters
        spend millions every year turning long-form recordings into TikToks, Instagram Reels, and YouTube Shorts. What the
        hype leaves out is that clipping is piece-rate editing and distribution work, not passive income. The clippers who
        actually get paid treat it like a craft, understand how Whop Content Rewards and platform policies work, and
        eventually graduate from volatile per-view bounties into predictable client retainers.
      </p>

      <section aria-labelledby="four-ways" className="space-y-5">
        <h2 id="four-ways">The four real ways clippers make money</h2>
        <p>
          &ldquo;Clipping&rdquo; gets lumped into one bucket online, which is why beginners mix up incompatible advice.
          In practice, short-form clippers earn through four distinct models (plus owned affiliate funnels if you build a
          niche audience of your own). Each has completely different ceilings, entry bars, and failure modes:
        </p>
        <MonetizationComparisonVisual />
        <p>
          Notice the big split right away: <strong>Whop / Content Rewards</strong> lets you start with zero followers
          because a brand is paying you directly for distribution, whereas <strong>TikTok Creator Rewards</strong> and{" "}
          <strong>YouTube Shorts ad sharing</strong> require large audience thresholds and enforce strict originality
          rules that routinely disqualify third-party reposts. Meanwhile, <strong>direct client retainers</strong> pay
          the most reliably, because a podcast host pays you for delivered clips every month regardless of whether the
          algorithm gives a video 800 views or 800,000.
        </p>
      </section>

      <section aria-labelledby="whop-content-rewards" className="space-y-5">
        <h2 id="whop-content-rewards">How Whop and Content Rewards actually work</h2>
        <p>
          If you searched how to make money clipping recently, you ran straight into &ldquo;the Whop stuff.&rdquo;
          Specifically, you saw{" "}
          <a href={CLIPPING_INCOME_SOURCES.whopContentRewards}>Whop Content Rewards</a>, which started as an app inside
          Whop and expanded into its own dedicated marketplace at{" "}
          <a href={CLIPPING_INCOME_SOURCES.contentRewards}>contentrewards.com</a> while still using Whop wallets and
          payment rails for creator payouts. Across its marketplace, Content Rewards reports more than $14 million paid
          out across hundreds of thousands of registered clippers and creators.
        </p>
        <p>
          The premise is straightforward. Instead of a streamer or software company paying one influencer $10,000 upfront
          and hoping a sponsorship lands, they deposit a fixed budget (often $1,000 to $30,000+) into a campaign, upload
          raw podcasts, livestreams, or product footage, and set a rate per 1,000 verified views (CPM). Independent
          clippers cut vertical shorts, post them on their own TikTok, Instagram Reels, YouTube Shorts, or X accounts,
          and submit the URLs back to the dashboard.
        </p>

        <WhopPayoutFlowDiagram />

        <h3>The four campaign formats you will see</h3>
        <ul>
          <li>
            <strong>CPM (Cost Per 1,000 Views):</strong> The most common clipping format. Headline rates run roughly
            $0.20 to $5.00 per 1,000 approved views, with most active clipping campaigns clustering around{" "}
            <strong>$1.00 to $1.50 CPM</strong>. Face-to-camera UGC campaigns usually pay higher rates than pure
            repurposed podcast or stream clipping.
          </li>
          <li>
            <strong>Pool campaigns:</strong> A brand puts up a fixed prize pool split among approved clippers according
            to their share of total campaign views once the campaign closes and holds clear.
          </li>
          <li>
            <strong>Per-post bounties:</strong> Fixed payment for each approved clip that meets the brief, regardless
            of whether it goes viral.
          </li>
          <li>
            <strong>Retainer campaigns:</strong> Recurring monthly deals listed inside the marketplace for proven
            clippers who can hit a steady weekly quota.
          </li>
        </ul>

        <h3>Fees, holds, and payout windows you need to budget for</h3>
        <p>
          Three mechanical details catch almost every new clipper off guard:
        </p>
        <ol>
          <li>
            <strong>The 10% creator fee:</strong> On standard CPM and pool campaigns, Content Rewards deducts a 10%
            creator fee before your net earnings hit your Whop balance. (Per-post and retainer campaigns with budgets of
            $5,000 or more waive that creator fee.) Withdrawals from your Whop balance to a bank account or instant card
            can also carry standard transfer fees depending on the rail you pick.
          </li>
          <li>
            <strong>The 10-day earning and hold cycle:</strong> CPM clips typically earn in rounds—a{" "}
            <strong>7-day earning window followed by a 3-day fraud and verification hold</strong>—and can continue
            accumulating paid views for up to 30 days after submission until they hit the clip cap or the budget empties.
          </li>
          <li>
            <strong>Finite, first-come budgets:</strong> Once a campaign&apos;s funded pool hits zero, tracking stops.
            If your clip is still sitting in the brand&apos;s review queue when the budget runs dry, those views will not
            pay out unless the brand tops up the campaign.
          </li>
        </ol>

        <CampaignBriefChecklist />

        <p>
          One more reality check from active clipping communities: <strong>you do not control approvals</strong>.
          Campaign operators review submissions and run automated anti-bot scoring. Missing a required hashtag, leaving
          an editing-app watermark in the corner, posting an Instagram Collab when the brief forbids it, or getting
          heavily non-Tier-1 traffic on a US-targeted campaign are all routine reasons for rejection. Read the brief
          line by line before you open an editor, and spread your effort across two or three well-funded campaigns
          instead of betting your whole week on one operator.
        </p>
      </section>

      <section aria-labelledby="the-math" className="space-y-5">
        <h2 id="the-math">What clipping actually pays (run your own numbers)</h2>
        <p>
          Industry analytics on live Content Rewards campaigns show why screenshots on social media are misleading: the
          median approved clip earns single-digit dollars, and most beginners earn roughly{" "}
          <strong>$50 to $400 a month</strong> while learning how to pick moments and hold retention. A small fraction of
          breakout clips carries the month—until they bump into the per-clip cap.
        </p>
        <p>
          Switch between <strong>Whop / Content Rewards</strong> and <strong>Client Retainers</strong> below to compare
          what the math looks like after the 10% Content Rewards fee, per-clip caps, and your clipping tool cost:
        </p>

        <ClippingIncomeCalculator />

        <p>
          Play with the cap slider for a minute and the structural lesson jumps off the screen. On open CPM campaigns,
          your downside is uncompensated editing hours and your upside is clipped by a $150–$250 cap per video. On a
          direct retainer, you trade lottery-ticket upside for predictable hourly pay—often $25 to $60+ per delivered
          clip before you even count performance bonuses.
        </p>
      </section>

      <section aria-labelledby="platform-rules" className="space-y-5">
        <h2 id="platform-rules">Platform rules that kill payouts (and why accounts stall at 200 views)</h2>
        <p>
          New clippers often assume that if a podcaster or Whop campaign gives them permission to clip a show, they can
          double-dip by collecting Whop CPM payouts <em>and</em> joining YouTube Partner Program or TikTok Creator
          Rewards on the same page. Usually, that plan blows up within a month.
        </p>
        <p>
          The reason is simple: <strong>copyright permission and platform originality are two separate tests</strong>.
          A Whop campaign authorizes you to use the footage so the creator will not DMCA-strike you. It does not make
          a lightly captioned repost &ldquo;original content&rdquo; in TikTok&apos;s or YouTube&apos;s eyes.
        </p>

        <PlatformRulesMatrix />

        <h3>Why brand-new clipping accounts get stuck at 0–200 views</h3>
        <ul>
          <li>
            <strong>Duplicate-clip fingerprinting:</strong> If a major podcast drops at noon and 300 clippers chop the
            exact same 40-second rant with the same crop and caption preset, TikTok and Instagram recognize the visual
            and audio hash and suppress late copies.
          </li>
          <li>
            <strong>Cold-account spam:</strong> Creating a fresh TikTok or Instagram account and dumping eight videos on
            day one looks indistinguishable from a bot farm. Spend a few days scrolling, watching niche content, and
            ramping up from one or two clean clips a day.
          </li>
          <li>
            <strong>The VPN geo-spoofing trap:</strong> Clippers outside the US sometimes try to force Tier-1
            distribution with consumer VPNs. Platforms detect datacenter IPs quickly and routinely zero-view the account.
          </li>
        </ul>
      </section>

      <section aria-labelledby="workflow" className="space-y-5">
        <h2 id="workflow">A repeatable clipping workflow (and where SupoClip fits)</h2>
        <p>
          Clipping only pays when your turnaround time is fast enough to make the hourly math work, without turning your
          exports into generic AI slop that gets skipped in two seconds. Split the job down the middle: let software
          handle transcription, first-pass candidate discovery, face-aware 9:16 cropping, and word-synced captions; keep
          moment selection, hook pacing, and final quality control human.
        </p>

        <ClippingPipelineDiagram />

        <h3>1. Pick sources that are not already picked clean</h3>
        <p>
          In open Whop campaigns, speed and angle beat raw volume. Skip the single headline clip everyone already posted
          from the first five minutes of a podcast. Scan the middle and back half of two-hour episodes, Q&amp;A segments,
          and full stream VODs where sharp stories and hot takes sit untouched. If you are clipping gaming streams, our{" "}
          <Link href="/blog/how-to-clip-gta-6">GTA 6 clipping guide</Link> and{" "}
          <Link href="/blog/twitch-vod-to-shorts">Twitch VOD to Shorts walkthrough</Link> cover how to pair AI speech
          shortlists with manual timestamps for silent action.
        </p>

        <h3>2. Cut for a stranger in the first two seconds</h3>
        <p>
          Nobody scrolling TikTok or Reels knows who the guest is or what was said ten minutes ago. Cut straight into
          the tension, claim, or punchline—never leave &ldquo;yeah, so I was telling my friend…&rdquo; at the start.
          Trim stutters and dead air, keep only the context a stranger needs, and end immediately after the payoff so
          viewers loop instead of swiping early. See our checklist on{" "}
          <Link href="/blog/find-best-moments-in-video">finding the best moments in a long video</Link>.
        </p>

        <h3>3. Fix framing, captions, and safe zones—and never ship a watermark</h3>
        <p>
          Most clipping campaigns instantly reject clips with third-party app watermarks, and automated captions still
          misspell guest names, brands, and slang. In SupoClip, every export is watermark-free on both hosted and
          self-hosted installs, face-aware reframing tracks speakers (or stacks a corner webcam above gameplay), and the
          built-in editor lets you fix misheard words, tweak caption styling, and check UI safe zones before rendering.
        </p>

        <figure className="not-prose space-y-3">
          <Image
            src="/blog/supoclip-editor.webp"
            alt="SupoClip caption editor with style controls, word-level transcript, and vertical safe-area preview"
            width={1800}
            height={1109}
            sizes="(max-width: 768px) 100vw, 768px"
            className="h-auto w-full rounded-xl border"
          />
          <figcaption className="text-sm leading-6 text-muted-foreground">
            Inside the SupoClip editor (shown with sample footage): fix misheard names in the word-level transcript,
            check vertical framing, and keep captions clear of TikTok, Reels, and Shorts overlays.
          </figcaption>
        </figure>

        <h3>4. Cross-post cleanly and track what actually converts</h3>
        <p>
          One strong clip is an asset you can distribute across TikTok, YouTube Shorts, and Instagram Reels (as long as
          the campaign allows multi-platform submissions and you upload clean, non-watermarked files to each platform).
          On hosted SupoClip, you can{" "}
          <Link href="/blog/tiktok-publishing-is-live">publish directly to TikTok and YouTube together</Link> and
          schedule batches ahead on Creator, then download clean 9:16 exports for Reels. Keep a simple spreadsheet:
          campaign, source video, hook style, views at 48 hours, approval status, and net payout.
        </p>

        <h3>A transparent note on SupoClip&apos;s license for clippers</h3>
        <p>
          Before you choose between hosted SupoClip and self-hosting, check which bucket you are in under the{" "}
          <a href={CLIPPING_INCOME_SOURCES.supoclipLicense}>SupoClip Noncommercial Source License</a>:
        </p>
        <ul>
          <li>
            <strong>Solo clippers on Whop / Content Rewards:</strong> Participating as an individual creator in an
            Authorized Clipping Campaign and posting to your own channels is explicitly permitted on a free{" "}
            <Link href="/blog/self-host-supoclip-docker">self-hosted SupoClip install</Link> (you only pay for your own
            compute and any cloud transcription/LLM keys, or run local Whisper + Ollama). If you prefer zero setup,
            hosted Pro ($9.99/mo for 10 hours) and Creator ($24.99/mo for 25 hours + scheduling) handle everything.
          </li>
          <li>
            <strong>Freelance client editors and clipping agencies:</strong> Cutting clips for paying clients, managing
            a client&apos;s channels, or running a multi-editor crew is commercial use. Use a hosted SupoClip plan (Pro,
            Creator, or Team) or email <code>support@supoclip.com</code> for written commercial self-hosting permission.
          </li>
        </ul>
      </section>

      <section aria-labelledby="scaling-up" className="space-y-5">
        <h2 id="scaling-up">Moving from Whop bounties to $1,500+/month retainers</h2>
        <p>
          The smartest clippers treat Whop / Content Rewards as a paid training ground rather than the final destination.
          Open campaigns pay you to learn pacing, hooks, captions, and platform analytics while building a verifiable
          track record. Once you can consistently turn a one-hour recording into clips that hold viewers past the
          three-second mark, you hold a skill that podcasters, B2B founders, coaches, and streamers gladly pay monthly
          retainers to outsource.
        </p>

        <RetainerProgressionPath />

        <h3>How to land your first retainer without spammy cold DMs</h3>
        <ol>
          <li>
            <strong>Find creators with a distribution gap:</strong> Look for YouTube channels and video podcasts
            publishing weekly 45–120 minute episodes whose Shorts, TikTok, or Reels accounts are either empty or full of
            flat, unedited landscape crops.
          </li>
          <li>
            <strong>Lead with finished proof:</strong> Cut two genuinely strong 35–50 second clips from their newest
            episode, fix every caption word, and send private links alongside a two-sentence message—plus screenshots of
            your best Whop campaign view counts.
          </li>
          <li>
            <strong>Sell a clear monthly deliverable:</strong> For example, &ldquo;24 vertical clips a month from your
            weekly episodes, custom-branded captions, hook titles, and scheduled posting to YouTube Shorts and TikTok
            for $1,000/month.&rdquo; Once you prove consistency, add a bonus tier for clips crossing 100K views or a
            commission split on whatever the channel sells.
          </li>
        </ol>
        <p>
          Want to see what a clean podcast-to-Shorts handoff looks like end to end? Read{" "}
          <Link href="/blog/podcast-to-shorts">how to turn a video podcast into Shorts and Reels</Link> and{" "}
          <Link href="/blog/word-by-word-video-captions">how to style readable word-by-word captions</Link>.
        </p>
      </section>

      <section aria-labelledby="mistakes" className="space-y-5">
        <h2 id="mistakes">Seven mistakes that keep clippers broke</h2>
        <ul>
          <li>
            <strong>Chasing high CPMs on nearly empty budgets.</strong> A $4 CPM campaign with $80 left will drain
            before your clip clears review. Check remaining pool size and payout velocity first.
          </li>
          <li>
            <strong>Clipping the exact same moment as everyone else.</strong> If it was in the YouTube trailer or the
            first three minutes of a trending podcast, hundreds of clippers already uploaded it. Dig deeper into the
            recording.
          </li>
          <li>
            <strong>Shipping raw AI exports without watching them.</strong> AI gets you 80% of the way there in minutes;
            the last 20%—tightening the opening second, fixing misheard names, cutting dead air—is the difference
            between 250 views and 50,000.
          </li>
          <li>
            <strong>Ignoring one line in the campaign brief.</strong> Missing a mandatory hashtag, exporting with a
            watermark, or posting below the minimum length throws away views you already earned.
          </li>
          <li>
            <strong>Betting on TikTok or YouTube ad share for third-party reposts.</strong> Platform originality
            systems will eventually flag unoriginal aggregator pages. Keep native ad-share expectations for original or
            heavily transformative work.
          </li>
          <li>
            <strong>Trying to trick algorithms with fresh spam accounts or VPNs.</strong> Warm up accounts naturally,
            post consistently, and compete on watch time instead of hacks that trigger zero-view throttling.
          </li>
          <li>
            <strong>Stopping at clip #12.</strong> Short-form feeds take reps to calibrate. Track which hook styles and
            niches earn above-average retention across 30–50 uploads before deciding whether a niche works.
          </li>
        </ul>
      </section>

      <section aria-labelledby="sources" className="space-y-3 text-sm">
        <h2 id="sources">Sources</h2>
        <ul>
          <li>
            <a href={CLIPPING_INCOME_SOURCES.whopContentRewards}>Whop: Content Rewards overview</a>
          </li>
          <li>
            <a href={CLIPPING_INCOME_SOURCES.contentRewards}>Content Rewards marketplace (fees, payout cycles, and campaign formats)</a>
          </li>
          <li>
            <a href={CLIPPING_INCOME_SOURCES.youtubeShortsMonetization}>YouTube Help: YouTube Shorts monetization policies</a>
          </li>
          <li>
            <a href={CLIPPING_INCOME_SOURCES.youtubeChannelPolicies}>YouTube Help: Channel monetization policies (Reused Content)</a>
          </li>
          <li>
            <a href={CLIPPING_INCOME_SOURCES.tiktokCreatorRewards}>TikTok Support: Creator Rewards Program eligibility and originality rules</a>
          </li>
          <li>
            <a href={CLIPPING_INCOME_SOURCES.supoclipLicense}>SupoClip Noncommercial Source License (Authorized Clipping Campaigns)</a>
          </li>
        </ul>
        <p>
          Checked November 25, 2026. Campaign CPMs, platform fees, and monetization policies change frequently—confirm
          the current terms in each campaign brief and platform help center before relying on them.
        </p>
      </section>
    </ArticleShell>
  );
}
