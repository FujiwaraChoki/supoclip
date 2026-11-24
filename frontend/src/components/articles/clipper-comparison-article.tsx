import Link from "next/link";

import { ArticleShell, type ArticleFaq } from "@/components/articles/article-shell";
import { ComparisonTable } from "@/components/articles/comparison-table";
import { CLIPPER_SOURCES as S } from "@/components/articles/clipper-sources";
import type { BlogPost } from "@/lib/blog-posts";
import { getPublicBillingPlans } from "@/lib/billing-plans";
import { OPUSCLIP_COMPARISON_HREF, SELF_HOSTING_GUIDE_HREF } from "@/lib/marketing-links";

export const CLIPPER_COMPARISON_CHECKED = "November 24, 2026";

const FAQS: ArticleFaq[] = [
  {
    question: "Which is cheapest: SupoClip, OpusClip, Klap or Vizard?",
    answer:
      "On published entry prices, hosted SupoClip Pro is the cheapest at $9.99 a month for 10 hours of source video, in 720p. OpusClip Starter is $15 a month for 150 minutes. Klap and Vizard both start at $29 a month, Klap for 100 clips and Vizard for 600 credits. Self-hosted SupoClip costs only your own hardware and AI usage.",
  },
  {
    question: "Which of these has a free plan?",
    answer:
      "OpusClip and Vizard both have free plans with 60 credits a month and a watermark. Klap has no free plan; you can try one video but not download the clips. Hosted SupoClip has no free plan, but you can self-host it for free as an individual creator.",
  },
  {
    question: "Is Klap better than OpusClip?",
    answer:
      "They charge differently. Klap counts generated clips, so long recordings don't use up your plan, while OpusClip counts minutes of source video. If you clip long streams or podcasts and keep a few clips from each, Klap's model can work out cheaper; if you clip short videos into many clips, OpusClip's can.",
  },
  {
    question: "Are any of these open source?",
    answer:
      "Only SupoClip publishes its source code. It's under the SupoClip Noncommercial Source License: free for noncommercial use and individual creators, with written permission needed for companies, agencies and client work. OpusClip, Klap and Vizard are proprietary hosted services.",
  },
  {
    question: "Do paid plans add a watermark?",
    answer:
      "No. None of the four watermarks exports on a paid plan. SupoClip never adds one, hosted or self-hosted. OpusClip and Vizard watermark their free plans.",
  },
];

export function ClipperComparisonArticle({ post }: { post: BlogPost }) {
  const [pro, scale] = getPublicBillingPlans();
  const proHours = pro.minuteLimit / 60;
  return (
    <ArticleShell
      post={post}
      breadcrumb="SupoClip vs OpusClip vs Klap vs Vizard"
      toc={[
        ["short-answer", "Short answer"],
        ["at-a-glance", "At a glance"],
        ["pricing", "Pricing"],
        ["features", "Features"],
        ["each-tool", "Tool by tool"],
        ["choose", "How to choose"],
        ["sources", "Sources"],
        ["faq", "FAQ"],
      ]}
      faqs={FAQS}
      cta={{
        title: "Try SupoClip on your next long video",
        body: "Paste a YouTube, Twitch or Kick link or upload a file, review the clips it picks, and export without a watermark.",
        label: "Start clipping",
      }}
    >
      <p>
        OpusClip, Klap and Vizard are the three names that come up most when people look for an AI clipper: tools
        that take a long video and return vertical, captioned shorts. SupoClip does the same job, with one difference
        the others don&apos;t offer: you can use it hosted or run the source code yourself.
      </p>
      <p>
        This comparison uses what each company publishes on its own pricing, help and API pages, checked on{" "}
        {CLIPPER_COMPARISON_CHECKED}. We build SupoClip. We haven&apos;t run a controlled test of clip quality, so
        we don&apos;t claim anyone&apos;s clips are better; we compare price, limits and features, and link a source for
        each. For a deeper two-way look, see <Link href={OPUSCLIP_COMPARISON_HREF}>SupoClip vs OpusClip</Link>.
      </p>

      <section aria-labelledby="short-answer" className="space-y-5">
        <h2 id="short-answer">The short answer</h2>
        <ul>
          <li>
            <strong>SupoClip</strong> if you want the lowest price per hour of video, no watermark, or the option to
            self-host. It&apos;s the only one of the four with public source code.
          </li>
          <li>
            <strong>OpusClip</strong> if you want the most established product, a free plan to start on, and
            publishing to the widest range of social platforms.
          </li>
          <li>
            <strong>Klap</strong> if you clip long streams or podcasts and keep a few clips from each: it charges per
            clip, not per minute of video.
          </li>
          <li>
            <strong>Vizard</strong> if you want very long uploads (up to 10 hours), the broadest list of import sources,
            caption translation, or a native iPhone app for clipping.
          </li>
        </ul>
      </section>

      <section aria-labelledby="at-a-glance" className="space-y-5">
        <h2 id="at-a-glance">At a glance</h2>
        <ComparisonTable
          caption={`SupoClip, OpusClip, Klap and Vizard compared, checked ${CLIPPER_COMPARISON_CHECKED}`}
          headers={["", "SupoClip", "OpusClip", "Klap", "Vizard"]}
          minWidth={820}
          rows={[
            ["How you run it", "Hosted web app, iOS app, or self-hosted with Docker", "Hosted; iOS and Android apps", "Hosted web app", "Hosted; iOS app"],
            ["Source code", <span key="src">Public (<a href={S.supoclip.license}>noncommercial source license</a>)</span>, "Proprietary", "Proprietary", "Proprietary"],
            ["Free plan", "No hosted free plan; self-hosting is free for individual creators", "60 credits / month, watermark", "None; one trial video, no download", "60 credits / month, watermark, 720p"],
            ["Entry paid plan", `$${pro.priceMonthly}/mo: ${proHours} hours of video, 720p`, "$15/mo: 150 minutes", "$29/mo: 100 clips", "$29/mo: 600 credits"],
            ["What you pay for", "Minutes of source video", "Minutes of source video", "Clips generated", "Credits, shared with its AI agent"],
            ["Longest source video", `${pro.youtubeMaxMinutes / 60} h on Pro, ${scale.youtubeMaxMinutes / 60} h on ${scale.name}`, "Not published", "3 hours", "10 hours on paid plans"],
            ["Best export", `1080p on ${scale.name}`, "1080p", "Up to 4K", "4K on paid plans"],
            ["Watermark on paid plans", "Never, on any plan", "No", "No", "No"],
          ]}
        />
      </section>

      <section aria-labelledby="pricing" className="space-y-5">
        <h2 id="pricing">Pricing: minutes, clips or credits</h2>
        <p>
          The four don&apos;t sell the same unit, which is why their price pages are hard to compare. SupoClip and
          OpusClip charge for minutes of the video you put in: a one-hour podcast uses 60 minutes however many clips
          you keep. Klap charges for <em>clips out</em>, so a four-hour stream costs the same as a ten-minute video if
          you keep the same number of clips. Vizard sells credits that its clipper and its AI editing agent share.
        </p>
        <ComparisonTable
          caption="Monthly plans as published (USD)"
          headers={["Plan", "Price", "Allowance", "Roughly per unit"]}
          minWidth={600}
          rows={[
            [`SupoClip ${pro.name}`, `$${pro.priceMonthly}/mo or $${pro.priceAnnual}/yr`, `${pro.minuteLimit} min of video, 720p`, "≈ $1 per hour of video"],
            [`SupoClip ${scale.name}`, `$${scale.priceMonthly}/mo or $${scale.priceAnnual}/yr`, `${scale.minuteLimit} min of video, 1080p, scheduling`, "≈ $1 per hour of video"],
            ["OpusClip Starter", "$15/mo (monthly only)", "150 credits = 150 min", "≈ $6 per hour of video"],
            ["OpusClip Pro", "$29/mo or $174/yr", "3,600 credits a year on annual", "≈ $2.90 per hour on annual"],
            ["Klap Basic", "$29/mo or $174/yr", "100 clips", "≈ $0.29 per clip"],
            ["Klap Pro", "$79/mo or $474/yr", "300 clips", "≈ $0.26 per clip"],
            ["Vizard Creator", "$29/mo or $174/yr", "600 credits", "≈ $0.05 per credit"],
            ["Vizard Business", "$39/mo or $234/yr", "600 credits, brand kit, seats", "≈ $0.07 per credit"],
          ]}
        />
        <p className="text-sm">
          &ldquo;Roughly per unit&rdquo; divides the monthly price by the allowance. Units differ, so compare within a
          row type. OpusClip&apos;s monthly Pro credit count isn&apos;t listed on its pricing page, so we used the
          annual figure. Vizard doesn&apos;t say how many minutes a credit buys.
        </p>
        <p>
          A worked example: you record four one-hour podcast episodes a month and keep five clips from each. That&apos;s
          240 minutes and 20 clips. It fits SupoClip {pro.name} (${pro.priceMonthly}), OpusClip Pro on annual billing,
          Klap Basic ($29) with room to spare, and Vizard Creator ($29) if a credit is close to a minute. On OpusClip
          Starter&apos;s 150 minutes it doesn&apos;t fit. If you need 1080p, SupoClip&apos;s option is {scale.name} at $
          {scale.priceMonthly}.
        </p>
        <p>
          Self-hosting SupoClip changes the math again: no plan, no minute cap, and you pay for the computer and any
          AI APIs you choose. The <Link href={SELF_HOSTING_GUIDE_HREF}>self-hosting guide</Link> shows a fully local
          setup with no per-video fees. It&apos;s free for noncommercial use and individual creators; companies,
          agencies and client work need written permission.
        </p>
      </section>

      <section aria-labelledby="features" className="space-y-5">
        <h2 id="features">Features side by side</h2>
        <ComparisonTable
          caption="Documented features. A dash means the vendor's pages don't mention it."
          headers={["", "SupoClip", "OpusClip", "Klap", "Vizard"]}
          minWidth={820}
          rows={[
            ["Imports", "YouTube, Twitch, Kick, file upload", "YouTube, Zoom, Vimeo, Google Drive, Dropbox, upload", "YouTube, upload (more via API)", "YouTube, Drive, Vimeo, Twitch, Loom, StreamYard and more"],
            ["Languages", "Auto-detected; 100+ languages and regional variants", "25+", "99+", "Caption translation into 100+"],
            ["Virality score", "Yes: hook, engagement, value and shareability", "Yes", "Yes", "—"],
            ["Vertical reframing", "Face tracking, stacked webcam and gameplay layouts", "Reframe with object tracking, screenshare and gameplay layouts", "Active-speaker reframe, split screen, gaming", "Auto reframe; face tracking on paid plans"],
            ["Editing", "Clip studio: timeline, transcript, captions, framing, B-roll, AI edit by prompt", "Text and timeline editing", "Editor with B-roll and hook titles", "Text and timeline editing, AI agent"],
            ["B-roll", "Stock B-roll from Pexels", "AI and stock B-roll on Pro", "Yes", "Auto B-roll on paid plans"],
            ["Direct publishing", "YouTube, TikTok; scheduling on Creator", "Up to 9 networks on Pro", "TikTok, YouTube, Instagram, LinkedIn, Facebook", "TikTok, YouTube, Instagram, X, Facebook, LinkedIn"],
            ["API", <span key="api">REST API keys and an <a href={S.supoclip.mcp}>MCP server</a></span>, <span key="oapi"><a href={S.opusclip.api}>Beta on Pro</a>, Max and Business</span>, <span key="kapi"><a href={S.klap.api}>Pay-per-use API</a> and MCP server</span>, <span key="vapi"><a href={S.vizard.apiPricing}>On paid plans</a></span>],
            ["Choose your AI model", "Yes when self-hosting: OpenAI, Google, Anthropic, OpenRouter or local Ollama", "—", "—", "—"],
          ]}
        />
      </section>

      <section aria-labelledby="each-tool" className="space-y-5">
        <h2 id="each-tool">Tool by tool</h2>

        <h3>SupoClip</h3>
        <p>
          SupoClip transcribes your video, proposes moments with four sub-scores (hook, engagement, value and
          shareability), reframes them to 9:16 around faces, and adds word-synced captions and a short hook title.
          Stream recordings with a corner webcam get the webcam stacked above the gameplay. Everything opens in a
          full clip studio where you can trim, edit captions and framing, add B-roll, or type an instruction like
          &ldquo;cut the dead air&rdquo; and review the change. Finished clips publish to YouTube and TikTok.
        </p>
        <p>
          The trade-offs: there&apos;s no free hosted plan, {pro.name} exports in 720p, and it imports fewer sources
          than Vizard or OpusClip. What sets it apart is that the code is public. You can run the whole thing on your
          own machine, pick your own transcription and language models, and automate it through the API or MCP
          server. See <Link href="/demo">real clips next to their source</Link>.
        </p>

        <h3>OpusClip</h3>
        <p>
          OpusClip is the best-known of the four, with a free plan, mobile apps on both platforms and the longest list
          of publishing destinations. Its <a href={S.opusclip.credits}>credits</a> are one per minute of source video,
          rounded down. The <a href={S.opusclip.pricing}>free plan</a> is limited: 60 credits a month, a watermark, no
          editing, and clips you must export within three days. Starter at $15 removes the watermark but has only 150
          minutes; most features people compare against (scheduling, AI B-roll, Premiere export, API) are on Pro.
        </p>

        <h3>Klap</h3>
        <p>
          <a href={S.klap.home}>Klap</a> stands out for its pricing model: plans count clips, and the{" "}
          <a href={S.klap.pricing}>pricing page</a> says source length doesn&apos;t count against them, with sources up to
          three hours. That suits streamers and long-form podcasters. It lists 99+ languages, gaming and split-screen
          layouts, and exports up to 4K. There&apos;s no free plan, and its entry price is the highest of the four. Its
          developer <a href={S.klap.api}>API</a> is sold separately, pay as you go.
        </p>

        <h3>Vizard</h3>
        <p>
          <a href={S.vizard.home}>Vizard</a> takes the longest uploads (10 hours on paid plans), imports from the widest
          range of sources, and translates captions. Its AI agent, which edits from instructions, now sits at the
          center of the product and shares the same credits as clipping. The{" "}
          <a href={S.vizard.freePlan}>free plan</a> exports in 720p with a watermark, keeps files for three days, and
          limits clips to 10 minutes. It also has an <a href={S.vizard.ios}>iPhone app</a>.
        </p>
      </section>

      <section aria-labelledby="choose" className="space-y-5">
        <h2 id="choose">How to choose</h2>
        <ComparisonTable
          caption="Which clipper fits which situation"
          headers={["If you…", "Look at", "Because"]}
          minWidth={600}
          rows={[
            [`Clip up to ${proHours} hours a month on a budget`, "SupoClip", `$${pro.priceMonthly}/mo is the lowest published entry price`],
            ["Want to start free with no setup", "OpusClip or Vizard", "60 credits a month to try"],
            ["Clip 3-hour streams and keep a few clips", "Klap or SupoClip", "Per-clip pricing, or a low price per hour"],
            ["Need 4K exports", "Klap or Vizard", "Both list 4K on paid plans"],
            ["Want to self-host or keep video on your servers", "SupoClip", "The only one with public source"],
            ["Post to many networks at once", "OpusClip", "The longest list of publishing targets on Pro"],
          ]}
        />
        <p>
          Whichever you shortlist, run the same video through each. Mark three moments you&apos;d clip yourself, then
          check which tool finds them, whether clips start and end on complete thoughts, and how long you spend fixing
          captions and framing. <Link href="/blog/free-opusclip-alternatives">Our list of free OpusClip alternatives</Link>{" "}
          covers more tools if none of these four fit.
        </p>
      </section>

      <section aria-labelledby="sources" className="space-y-3 text-sm">
        <h2 id="sources">Sources</h2>
        <ul>
          <li>OpusClip: <a href={S.opusclip.pricing}>pricing</a>, <a href={S.opusclip.credits}>how credits are consumed</a>, <a href={S.opusclip.languages}>supported languages</a>, <a href={S.opusclip.api}>API overview</a>, <a href={S.opusclip.tool}>long video to short video</a>, <a href={S.opusclip.github}>GitHub organization</a></li>
          <li>Klap: <a href={S.klap.home}>home page</a>, <a href={S.klap.pricing}>pricing</a>, <a href={S.klap.api}>API</a></li>
          <li>Vizard: <a href={S.vizard.home}>home page</a>, <a href={S.vizard.pricing}>pricing</a>, <a href={S.vizard.freePlan}>free plan</a>, <a href={S.vizard.apiPricing}>API pricing</a>, <a href={S.vizard.apiInputs}>API import sources</a>, <a href={S.vizard.ios}>App Store</a></li>
          <li>SupoClip: <a href={S.supoclip.repo}>source code</a>, <a href={S.supoclip.license}>license</a>, <a href={S.supoclip.config}>configuration</a>, <a href={S.supoclip.mcp}>MCP server</a>, <Link href="/#pricing">plans</Link></li>
        </ul>
        <p>
          Checked {CLIPPER_COMPARISON_CHECKED}. Prices and plans change; the vendor&apos;s page wins over this one.
          Vizard&apos;s prices load in the browser and may vary by credit tier. We build SupoClip.
        </p>
      </section>
    </ArticleShell>
  );
}
