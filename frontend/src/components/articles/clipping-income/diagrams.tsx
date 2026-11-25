import {
  BadgeCheck,
  Captions,
  CircleAlert,
  CircleCheck,
  CircleX,
  Crop,
  DollarSign,
  Film,
  ListChecks,
  Scissors,
  Send,
  ShieldAlert,
  TrendingUp,
} from "lucide-react";

const MONETIZATION_PATHS = [
  {
    model: "Whop / Content Rewards (CPM)",
    pay: "$0.20–$5.00 / 1K views (median ~$1.00–$1.50)",
    barStart: 12,
    barWidth: 46,
    barrier: "No follower minimum",
    catch: "10% creator fee on CPM/pool campaigns, per-clip caps, finite budgets, review rejections",
    bestFor: "Beginners building reps, testing hooks, and proving view counts",
  },
  {
    model: "Direct creator retainers",
    pay: "$300–$1,000/mo freelance · $1,000–$3,000+/mo dedicated",
    barStart: 42,
    barWidth: 58,
    barrier: "Portfolio + outbound pitching",
    catch: "Fixed quotas, client revisions, and commercial tool licensing",
    bestFor: "Clippers who want predictable monthly income instead of algorithm variance",
  },
  {
    model: "TikTok Creator Rewards",
    pay: "$0.40–$1.00+ / 1K qualified views",
    barStart: 18,
    barWidth: 28,
    barrier: "10K followers · 100K views / 30d · 60s+ videos",
    catch: "Strict unoriginal-content enforcement; 5 flags in 30 days triggers disqualification",
    bestFor: "Original commentary, narrated edits, or clipping your own long-form shows",
  },
  {
    model: "YouTube Shorts ad share",
    pay: "$0.01–$0.07 / 1K views (up to ~$0.10–$0.33 Tier-1)",
    barStart: 4,
    barWidth: 18,
    barrier: "YPP: 1K subs + 10M Shorts views / 90d",
    catch: "45% creator pool after music split; Reused Content blocks raw reposts even with permission",
    bestFor: "Growing a channel funnel toward long-form ads and sponsorships",
  },
  {
    model: "Owned-page affiliates & funnels",
    pay: "10%–40% commissions or sponsor fees",
    barStart: 25,
    barWidth: 65,
    barrier: "Consistent niche audience + buyer intent",
    catch: "Takes months to build; low-trust pages do not convert",
    bestFor: "Long-term leverage when you own the distribution",
  },
] as const;

/** Side-by-side comparison of the five ways clippers earn money. */
export function MonetizationComparisonVisual() {
  return (
    <figure className="not-prose space-y-4" aria-labelledby="monetization-comparison-title">
      <div className="space-y-1">
        <p className="text-xs font-semibold uppercase tracking-widest text-muted-foreground">Monetization map</p>
        <h3 id="monetization-comparison-title" className="text-xl font-bold tracking-tight text-foreground">How the five clipping income paths compare</h3>
      </div>

      <div className="overflow-x-auto rounded-2xl border bg-background">
        <table className="w-full min-w-[660px] text-left text-sm leading-6">
          <caption className="border-b bg-muted/30 px-4 py-3 text-left text-xs font-medium text-muted-foreground">
            Typical rates, entry barriers, and tradeoffs across clipping business models (checked November 2026).
          </caption>
          <thead className="bg-muted/50 text-xs uppercase tracking-wider text-muted-foreground">
            <tr>
              <th scope="col" className="p-4">Model</th>
              <th scope="col" className="p-4">Typical pay</th>
              <th scope="col" className="p-4">Entry bar</th>
              <th scope="col" className="p-4">Main catch</th>
            </tr>
          </thead>
          <tbody>
            {MONETIZATION_PATHS.map((row) => (
              <tr key={row.model} className="border-t">
                <th scope="row" className="p-4 align-top font-semibold text-foreground">
                  <div>{row.model}</div>
                  <div className="mt-2 h-1.5 w-28 overflow-hidden rounded-full bg-muted" aria-hidden="true">
                    <div
                      className="h-full rounded-full bg-foreground/80"
                      style={{ marginLeft: `${row.barStart}%`, width: `${Math.min(row.barWidth, 100 - row.barStart)}%` }}
                    />
                  </div>
                </th>
                <td className="p-4 align-top font-medium text-foreground">{row.pay}</td>
                <td className="p-4 align-top text-muted-foreground">{row.barrier}</td>
                <td className="p-4 align-top text-muted-foreground">{row.catch}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </figure>
  );
}

/** Visual flow of a Whop / Content Rewards CPM campaign from brand pool to wallet withdrawal. */
export function WhopPayoutFlowDiagram() {
  return (
    <figure className="not-prose space-y-4 rounded-2xl border bg-muted/20 p-5 sm:p-7" aria-labelledby="whop-flow-title">
      <div className="space-y-1">
        <p className="text-xs font-semibold uppercase tracking-widest text-muted-foreground">Whop &amp; Content Rewards mechanics</p>
        <h3 id="whop-flow-title" className="text-xl font-bold tracking-tight text-foreground">How a view turns into money in your Whop wallet</h3>
      </div>

      <svg
        viewBox="0 0 860 250"
        role="img"
        aria-labelledby="whop-flow-svg-title whop-flow-svg-desc"
        className="h-auto w-full"
      >
        <title id="whop-flow-svg-title">Whop Content Rewards payout pipeline</title>
        <desc id="whop-flow-svg-desc">
          Five stages of a Whop Content Rewards CPM campaign: 1) Brand funds a fixed budget pool and brief, 2) Clipper posts vertical clips to TikTok, Reels, Shorts, or X, 3) Clip clears the minimum view threshold and enters brand and anti-bot review, 4) Approved views earn across a 7-day earning window plus 3-day hold up to 30 days or the per-clip cap, 5) Content Rewards deducts a 10% creator fee on CPM campaigns and settles the remaining 90% to your Whop balance.
        </desc>

        {/* Stage 1 */}
        <g transform="translate(8 24)">
          <rect width="150" height="168" rx="14" className="fill-background stroke-border" strokeWidth="1.5" />
          <rect x="14" y="14" width="28" height="24" rx="6" className="fill-foreground" />
          <text x="28" y="30" textAnchor="middle" className="fill-background" fontSize="12" fontWeight="700">01</text>
          <text x="14" y="60" className="fill-foreground" fontSize="14" fontWeight="700">Campaign pool</text>
          <text x="14" y="82" className="fill-muted-foreground" fontSize="11.5">Brand funds a fixed</text>
          <text x="14" y="98" className="fill-muted-foreground" fontSize="11.5">budget, CPM rate,</text>
          <text x="14" y="114" className="fill-muted-foreground" fontSize="11.5">cap &amp; brief rules.</text>
          <rect x="14" y="132" width="122" height="22" rx="6" className="fill-muted" />
          <text x="75" y="147" textAnchor="middle" className="fill-foreground" fontSize="10.5" fontWeight="600">First-come budget</text>
        </g>

        {/* Arrow 1->2 */}
        <path d="M164 108 H180 M175 102 L182 108 L175 114" className="stroke-muted-foreground" strokeWidth="2" fill="none" strokeLinecap="round" strokeLinejoin="round" />

        {/* Stage 2 */}
        <g transform="translate(186 24)">
          <rect width="150" height="168" rx="14" className="fill-background stroke-border" strokeWidth="1.5" />
          <rect x="14" y="14" width="28" height="24" rx="6" className="fill-foreground" />
          <text x="28" y="30" textAnchor="middle" className="fill-background" fontSize="12" fontWeight="700">02</text>
          <text x="14" y="60" className="fill-foreground" fontSize="14" fontWeight="700">Edit &amp; submit</text>
          <text x="14" y="82" className="fill-muted-foreground" fontSize="11.5">Post 9:16 clip on</text>
          <text x="14" y="98" className="fill-muted-foreground" fontSize="11.5">TikTok, Reels, Shorts</text>
          <text x="14" y="114" className="fill-muted-foreground" fontSize="11.5">or X &amp; paste URL.</text>
          <rect x="14" y="132" width="122" height="22" rx="6" className="fill-muted" />
          <text x="75" y="147" textAnchor="middle" className="fill-foreground" fontSize="10.5" fontWeight="600">0 followers needed</text>
        </g>

        {/* Arrow 2->3 */}
        <path d="M342 108 H358 M353 102 L360 108 L353 114" className="stroke-muted-foreground" strokeWidth="2" fill="none" strokeLinecap="round" strokeLinejoin="round" />

        {/* Stage 3 */}
        <g transform="translate(364 24)">
          <rect width="150" height="168" rx="14" className="fill-background stroke-border" strokeWidth="1.5" />
          <rect x="14" y="14" width="28" height="24" rx="6" className="fill-foreground" />
          <text x="28" y="30" textAnchor="middle" className="fill-background" fontSize="12" fontWeight="700">03</text>
          <text x="14" y="60" className="fill-foreground" fontSize="14" fontWeight="700">Review &amp; check</text>
          <text x="14" y="82" className="fill-muted-foreground" fontSize="11.5">Must clear min views,</text>
          <text x="14" y="98" className="fill-muted-foreground" fontSize="11.5">brief compliance &amp;</text>
          <text x="14" y="114" className="fill-muted-foreground" fontSize="11.5">anti-bot scoring.</text>
          <rect x="14" y="132" width="122" height="22" rx="6" className="fill-amber-500/15" />
          <text x="75" y="147" textAnchor="middle" className="fill-amber-700 dark:fill-amber-400" fontSize="10.5" fontWeight="600">Most rejections here</text>
        </g>

        {/* Arrow 3->4 */}
        <path d="M520 108 H536 M531 102 L538 108 L531 114" className="stroke-muted-foreground" strokeWidth="2" fill="none" strokeLinecap="round" strokeLinejoin="round" />

        {/* Stage 4 */}
        <g transform="translate(542 24)">
          <rect width="150" height="168" rx="14" className="fill-background stroke-border" strokeWidth="1.5" />
          <rect x="14" y="14" width="28" height="24" rx="6" className="fill-foreground" />
          <text x="28" y="30" textAnchor="middle" className="fill-background" fontSize="12" fontWeight="700">04</text>
          <text x="14" y="60" className="fill-foreground" fontSize="14" fontWeight="700">Earning rounds</text>
          <text x="14" y="82" className="fill-muted-foreground" fontSize="11.5">7-day earning cycle</text>
          <text x="14" y="98" className="fill-muted-foreground" fontSize="11.5">+ 3-day fraud hold</text>
          <text x="14" y="114" className="fill-muted-foreground" fontSize="11.5">(up to 30 days max).</text>
          <rect x="14" y="132" width="122" height="22" rx="6" className="fill-muted" />
          <text x="75" y="147" textAnchor="middle" className="fill-foreground" fontSize="10.5" fontWeight="600">Stops at clip cap</text>
        </g>

        {/* Arrow 4->5 */}
        <path d="M698 108 H714 M709 102 L716 108 L709 114" className="stroke-muted-foreground" strokeWidth="2" fill="none" strokeLinecap="round" strokeLinejoin="round" />

        {/* Stage 5 */}
        <g transform="translate(720 24)">
          <rect width="132" height="168" rx="14" className="fill-background stroke-foreground/40" strokeWidth="1.5" />
          <rect x="14" y="14" width="28" height="24" rx="6" className="fill-emerald-600 dark:fill-emerald-500" />
          <text x="28" y="30" textAnchor="middle" fill="#ffffff" fontSize="12" fontWeight="700">05</text>
          <text x="14" y="60" className="fill-foreground" fontSize="14" fontWeight="700">Whop wallet</text>
          <text x="14" y="82" className="fill-muted-foreground" fontSize="11.5">90% net after 10%</text>
          <text x="14" y="98" className="fill-muted-foreground" fontSize="11.5">creator fee lands</text>
          <text x="14" y="114" className="fill-muted-foreground" fontSize="11.5">in your balance.</text>
          <rect x="14" y="132" width="104" height="22" rx="6" className="fill-emerald-500/15" />
          <text x="66" y="147" textAnchor="middle" className="fill-emerald-700 dark:fill-emerald-400" fontSize="10.5" fontWeight="600">Bank / Card</text>
        </g>

        {/* Bottom note strip */}
        <text x="8" y="226" className="fill-muted-foreground" fontSize="12">
          Note: Per-post and retainer campaigns with budgets of $5,000+ waive the 10% creator fee. Standard CPM and pool campaigns keep it.
        </text>
      </svg>

      <figcaption className="text-sm leading-6 text-muted-foreground">
        Content Rewards started inside Whop and now also runs at contentrewards.com while settling payouts through Whop balances. Always check each campaign&apos;s live brief—minimums, caps, and holds vary by operator.
      </figcaption>
    </figure>
  );
}

const BRIEF_CHECKS = [
  {
    title: "Remaining budget vs. speed",
    detail: "A $3 CPM campaign that is 92% spent can empty while your clip sits in review. Prioritize campaigns with fresh pools or recurring refills.",
  },
  {
    title: "Minimum view threshold",
    detail: "If a campaign sets a $6 minimum payout at $3 CPM, every clip needs at least 2,000 views before it even enters review.",
  },
  {
    title: "Per-clip payout cap",
    detail: "Many campaigns cap single clips at $100–$250 so one viral upload cannot drain the budget. Know the ceiling before you spend hours polishing one edit.",
  },
  {
    title: "Audience geography",
    detail: "Higher-paying US/UK/CA campaigns often require a minimum share of Tier-1 viewers. Never fake location with a VPN—platforms flag and throttle it.",
  },
  {
    title: "Mandatory brief details",
    detail: "Required hashtags, tagged handles, clip length floor, banned watermarks, and no Instagram Collab posts. One missed rule voids the whole clip.",
  },
  {
    title: "Source competition",
    detail: "If 400 clippers already cut the same viral podcast segment, platform algorithms fingerprint duplicates and hold new uploads at 200 views.",
  },
] as const;

export function CampaignBriefChecklist() {
  return (
    <figure className="not-prose space-y-4 rounded-2xl border bg-background p-5 sm:p-6" aria-labelledby="brief-checklist-title">
      <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-widest text-muted-foreground">
        <BadgeCheck className="size-4" aria-hidden="true" />
        Pre-flight check
      </div>
      <h3 id="brief-checklist-title" className="text-lg font-bold tracking-tight text-foreground">
        Six things to check in a Whop campaign brief before you cut a frame
      </h3>
      <div role="list" className="grid gap-3 sm:grid-cols-2">
        {BRIEF_CHECKS.map((item, idx) => (
          <div role="listitem" key={item.title} className="rounded-xl border bg-muted/20 p-4">
            <div className="flex items-center gap-2 text-sm font-semibold text-foreground">
              <span className="inline-flex size-5 shrink-0 items-center justify-center rounded-md bg-foreground font-mono text-[11px] font-bold text-background">
                {idx + 1}
              </span>
              {item.title}
            </div>
            <p className="mt-2 text-xs leading-5 text-muted-foreground">{item.detail}</p>
          </div>
        ))}
      </div>
    </figure>
  );
}

const RULE_CARDS = [
  {
    platform: "TikTok Creator Rewards",
    tone: "warn" as const,
    Icon: CircleAlert,
    requirements: [
      "10,000+ followers and 100,000 authentic views in the last 30 days",
      "Only videos over 60 seconds qualify for RPM payouts",
      "At least 1,000 qualified For You views (5s+ watch time)",
    ],
    killers: [
      "Unedited podcast/stream reposts with only auto-captions or filters",
      "Third-party watermarks, static slideshows, Duets, and Stitches",
      "5 unoriginal flags in a rolling 30-day window = full program removal",
    ],
  },
  {
    platform: "YouTube Shorts (YPP)",
    tone: "no" as const,
    Icon: CircleX,
    requirements: [
      "1,000 subscribers + 10M public Shorts views in 90 days (or 4,000 watch hours)",
      "45% share of the Shorts Creator Pool allocated after music licensing",
      "Shorts must pass YouTube's channel-wide originality review",
    ],
    killers: [
      "Reused Content policy blocks aggregator and raw clipping channels",
      "Creator permission solves copyright—it does NOT waive Reused Content",
      "Low-effort compilations often lose both monetization and Shorts feed reach",
    ],
  },
  {
    platform: "Whop / Content Rewards",
    tone: "ok" as const,
    Icon: CircleCheck,
    requirements: [
      "No follower count required on most open campaigns (18+ for payouts)",
      "Linked social accounts (Instagram usually needs Creator/Business mode)",
      "Submit URL promptly and clear the campaign's minimum view floor",
    ],
    killers: [
      "Bot views, engagement pods, or VPN geo-spoofing trigger fraud holds",
      "Brand holds final approval—strict adherence to tags, length, and tone",
      "If the pool empties while a clip is pending review, views stop paying",
    ],
  },
] as const;

const TONE_STYLE = {
  ok: "text-emerald-700 dark:text-emerald-400",
  warn: "text-amber-700 dark:text-amber-400",
  no: "text-red-700 dark:text-red-400",
} as const;

export function PlatformRulesMatrix() {
  return (
    <figure className="not-prose space-y-3" aria-labelledby="platform-rules-title">
      <figcaption id="platform-rules-title" className="flex items-center gap-2 text-xs font-semibold uppercase tracking-widest text-muted-foreground">
        <ShieldAlert className="size-4" aria-hidden="true" />
        Where clippers get demonetized or rejected · summary of published rules
      </figcaption>
      <div className="grid gap-4 md:grid-cols-3">
        {RULE_CARDS.map(({ platform, tone, Icon, requirements, killers }) => (
          <section key={platform} className="flex flex-col rounded-xl border bg-background p-4" aria-label={platform}>
            <div className={`flex items-center gap-2 font-semibold ${TONE_STYLE[tone]}`}>
              <Icon className="size-4 shrink-0" aria-hidden="true" />
              {platform}
            </div>
            <div className="mt-3 space-y-3 text-xs leading-5">
              <div>
                <div className="font-semibold uppercase tracking-wider text-foreground">Baseline rules</div>
                <ul className="mt-1.5 ml-4 list-disc space-y-1.5 text-muted-foreground">
                  {requirements.map((item) => <li key={item}>{item}</li>)}
                </ul>
              </div>
              <div className="border-t pt-3">
                <div className="font-semibold uppercase tracking-wider text-foreground">What kills payouts</div>
                <ul className="mt-1.5 ml-4 list-disc space-y-1.5 text-muted-foreground">
                  {killers.map((item) => <li key={item}>{item}</li>)}
                </ul>
              </div>
            </div>
          </section>
        ))}
      </div>
    </figure>
  );
}

const PIPELINE_STEPS = [
  {
    Icon: Film,
    title: "Source",
    you: "Pick fresh podcasts, interviews, or VODs with authorized rights",
    app: "Imports YouTube, Twitch, and Kick links or uploaded recordings",
  },
  {
    Icon: ListChecks,
    title: "Shortlist",
    you: "Skip moments 300 others clipped; pick standalone claims and stories",
    app: "Transcribes speech and scores hooks, engagement, value, shareability",
  },
  {
    Icon: Crop,
    title: "Frame & cut",
    you: "Open on the tension in 2 seconds; cut right after the payoff",
    app: "Face-aware 9:16 reframing, stacked webcam layout, studio trim & split",
  },
  {
    Icon: Captions,
    title: "Captions & hook",
    you: "Fix proper names, match brief rules, keep subtitles clear of UI buttons",
    app: "Word-synced animated captions, hook titles, dynamic edits, zero watermark",
  },
  {
    Icon: Send,
    title: "Publish & log",
    you: "Submit links to Whop or clients; track retention and hook winners",
    app: "Publishes or schedules to TikTok and YouTube; clean 9:16 export for Reels",
  },
] as const;

export function ClippingPipelineDiagram() {
  return (
    <figure className="not-prose space-y-4" aria-labelledby="clipping-pipeline-title">
      <figcaption id="clipping-pipeline-title" className="text-xs font-semibold uppercase tracking-widest text-muted-foreground">
        Repeatable clipping workflow · editorial taste stays with you, repetitive prep goes to SupoClip
      </figcaption>
      <div role="list" className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
        {PIPELINE_STEPS.map(({ Icon, title, you, app }, index) => (
          <div role="listitem" key={title} className="rounded-xl border bg-background p-4">
            <div className="flex items-center gap-2.5">
              <span className="flex size-7 shrink-0 items-center justify-center rounded-lg bg-foreground font-mono text-xs font-bold text-background">
                {index + 1}
              </span>
              <Icon className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
              <span className="font-semibold text-foreground">{title}</span>
            </div>
            <dl className="mt-3 space-y-2.5 text-xs leading-5">
              <div>
                <dt className="font-semibold uppercase tracking-wider text-muted-foreground">You</dt>
                <dd className="mt-0.5 text-foreground">{you}</dd>
              </div>
              <div>
                <dt className="font-semibold uppercase tracking-wider text-muted-foreground">SupoClip</dt>
                <dd className="mt-0.5 text-muted-foreground">{app}</dd>
              </div>
            </dl>
          </div>
        ))}
      </div>
    </figure>
  );
}

const STAGES = [
  {
    stage: "Month 1",
    Icon: Scissors,
    title: "Paid reps on Whop",
    income: "$50–$400 / mo",
    bullets: [
      "Join 2–3 well-funded campaigns in niches you actually watch",
      "Post 10–15 finished clips a week; track hook drop-off and approvals",
      "Save your top 5 clips and view screenshots as portfolio proof",
    ],
  },
  {
    stage: "Month 2–3",
    Icon: TrendingUp,
    title: "First direct clients",
    income: "$800–$1,800 / mo",
    bullets: [
      "Find podcasters and B2B founders posting 1h+ YouTube videos with weak Shorts",
      "Send 2 unsolicited sample clips from their latest episode—not a cold pitch wall",
      "Offer 20–30 clips/month + captions + scheduling for a flat starter retainer",
    ],
  },
  {
    stage: "Month 4+",
    Icon: DollarSign,
    title: "Retainers + performance upside",
    income: "$2,000–$5,000+ / mo",
    bullets: [
      "Lock 2–3 recurring clients on $1,000–$2,000/mo retainers",
      "Add view-milestone bonuses or affiliate splits on products their clips sell",
      "Use a shared workspace (like SupoClip Team) if you hire a second editor",
    ],
  },
] as const;

export function RetainerProgressionPath() {
  return (
    <figure className="not-prose space-y-4 rounded-2xl border bg-muted/20 p-5 sm:p-7" aria-labelledby="progression-path-title">
      <div className="space-y-1">
        <p className="text-xs font-semibold uppercase tracking-widest text-muted-foreground">Career ladder</p>
        <h3 id="progression-path-title" className="text-xl font-bold tracking-tight text-foreground">
          How full-time clippers graduate out of open CPM bounties
        </h3>
      </div>

      <div className="grid gap-4 md:grid-cols-3">
        {STAGES.map(({ stage, Icon, title, income, bullets }) => (
          <section key={stage} className="rounded-xl border bg-background p-4" aria-label={title}>
            <div className="flex items-center justify-between gap-2">
              <span className="inline-flex items-center gap-1.5 font-mono text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                <Icon className="size-3.5" aria-hidden="true" />
                {stage}
              </span>
              <span className="rounded-md bg-muted px-2 py-0.5 font-mono text-xs font-semibold text-foreground">
                {income}
              </span>
            </div>
            <h4 className="mt-2 font-bold text-foreground">{title}</h4>
            <ul className="mt-3 ml-4 list-disc space-y-1.5 text-xs leading-5 text-muted-foreground">
              {bullets.map((bullet) => <li key={bullet}>{bullet}</li>)}
            </ul>
          </section>
        ))}
      </div>

      <figcaption className="text-xs leading-5 text-muted-foreground">
        Illustrative progression based on published clipping-agency retainers and community reports—not a guarantee. Most beginners earn modest pocket money in month one while learning pacing and hooks.
      </figcaption>
    </figure>
  );
}
