import Link from "next/link";

import { ArticleShell, type ArticleFaq } from "@/components/articles/article-shell";
import { ComparisonTable } from "@/components/articles/comparison-table";
import { CLIPPER_SOURCES as S } from "@/components/articles/clipper-sources";
import type { BlogPost } from "@/lib/blog-posts";
import { getPublicBillingPlans } from "@/lib/billing-plans";
import { SELF_HOSTING_GUIDE_HREF } from "@/lib/marketing-links";

export const FREE_ALTERNATIVES_CHECKED = "November 24, 2026";

const FAQS: ArticleFaq[] = [
  {
    question: "What is the best free alternative to OpusClip?",
    answer:
      "It depends on what \"free\" has to mean for you. For the most hosted minutes without paying, Reap and Quso give an hour or more a month, with a watermark and 720p exports. For no watermark, no minute cap and full control, self-host an open-source clipper such as SupoClip or OpenShorts on your own computer.",
  },
  {
    question: "Is there a free OpusClip alternative with no watermark?",
    answer:
      "Among hosted tools, most free plans we checked add a watermark, keep exports at 720p, or both. YouTube Studio's built-in Shorts tool doesn't add one, but it only works on your own uploads and doesn't reframe. Self-hosted open-source clippers never add a watermark, because you run the renderer yourself.",
  },
  {
    question: "Does SupoClip have a free plan?",
    answer:
      "Hosted SupoClip doesn't. Processing needs a Pro or Creator plan. The source code is free to self-host for noncommercial use and for individual creators, including monetized channels; you pay for your own computer and any AI services you choose.",
  },
  {
    question: "Can I use a free clipper for client work?",
    answer:
      "Check each license and plan. MIT-licensed projects like OpenShorts and FunClip allow commercial use. SupoClip's source license doesn't cover companies, agencies or client work without written permission. Hosted free plans usually allow commercial use but carry the vendor's watermark.",
  },
  {
    question: "Do free AI clippers make worse clips than OpusClip?",
    answer:
      "We didn't run a controlled quality test, so we don't rank output quality. Free tiers usually limit minutes, resolution and retention rather than the AI itself. Run the same video through two or three tools and compare.",
  },
];

export function FreeOpusClipAlternativesArticle({ post }: { post: BlogPost }) {
  const [pro] = getPublicBillingPlans();
  return (
    <ArticleShell
      post={post}
      breadcrumb="Free OpusClip alternatives"
      toc={[
        ["short-answer", "Short answer"],
        ["what-free-means", "What free means"],
        ["hosted", "Hosted free tiers"],
        ["open-source", "Open source"],
        ["by-need", "Pick by need"],
        ["left-out", "Left out"],
        ["test", "Test them"],
        ["faq", "FAQ"],
      ]}
      faqs={FAQS}
      cta={{
        title: "Run a clipper with no plan limits",
        body: "Self-host SupoClip with Docker for free as an individual creator, or let us run it for you on a hosted plan.",
        label: "Read the self-hosting guide",
        href: SELF_HOSTING_GUIDE_HREF,
      }}
    >
      <p>
        OpusClip made AI clipping mainstream: drop in a long video, get vertical, captioned shorts back. Its free
        plan is a fine way to try that, but it runs out quickly and adds a watermark. If you&apos;re searching for a
        free OpusClip alternative, you probably want one of two things: more free minutes somewhere else, or a tool
        that costs nothing because you run it yourself.
      </p>
      <p>
        We checked the pricing and help pages of every tool below on {FREE_ALTERNATIVES_CHECKED}, and read the
        GitHub repositories of the open-source ones. We build SupoClip, so it&apos;s on this list, and we say plainly
        where it isn&apos;t free. We didn&apos;t run a controlled quality test, so nothing here ranks whose clips are
        &ldquo;best&rdquo;; this is about what each free option actually gives you.
      </p>

      <section aria-labelledby="short-answer" className="space-y-5">
        <h2 id="short-answer">The short answer</h2>
        <ul>
          <li>
            <strong>Most free hosted minutes:</strong> <a href={S.reap.pricing}>Reap</a> (1 hour of AI clipping a
            month) and <a href={S.quso.pricing}>Quso</a> (75 credits, about 75 minutes). Both watermark free exports
            and cap them at 720p.
          </li>
          <li>
            <strong>Easiest if you only post on YouTube:</strong> the{" "}
            <a href={S.youtube.clips}>clips and Shorts tool in YouTube Studio</a>. Free and built in, but it only
            works on your own videos and doesn&apos;t reframe to 9:16.
          </li>
          <li>
            <strong>No watermark, no minute cap:</strong> a self-hosted open-source clipper.{" "}
            <Link href="/open-source-video-clipper">SupoClip</Link> gives you a full browser app with an editor;{" "}
            <a href={S.openshorts.repo}>OpenShorts</a> is MIT-licensed and fully free for commercial use. Both need
            your own computer and some setup.
          </li>
          <li>
            <strong>Gaming streams:</strong> <a href={S.eklipse.pricing}>Eklipse</a> and{" "}
            <a href={S.spikes.pricing}>Spikes Studio</a> are built around Twitch and Kick, with watermarked 720p free
            tiers.
          </li>
        </ul>
      </section>

      <section aria-labelledby="what-free-means" className="space-y-5">
        <h2 id="what-free-means">What &ldquo;free&rdquo; usually means for an AI clipper</h2>
        <p>
          Processing video costs money: transcription, a language model to pick moments, and GPU or CPU time to
          render. So hosted free plans ration it. Almost every free tier we checked limits the same four things:
        </p>
        <ul>
          <li><strong>Minutes or credits per month.</strong> Usually 30 to 75 minutes of source video, enough for one podcast episode.</li>
          <li><strong>A watermark</strong> on exported clips, removed on the first paid plan.</li>
          <li><strong>720p exports.</strong> Fine on a phone, noticeably soft next to 1080p.</li>
          <li><strong>Retention.</strong> Projects often disappear after 3 to 7 days, so download what you keep.</li>
        </ul>
        <p>
          Open-source tools flip that: the software is free and has no limits, but you supply the hardware, install
          it, and keep it running. Some also call paid AI APIs, so &ldquo;free&rdquo; can still mean a few cents per
          video unless you run local models.
        </p>
      </section>

      <section aria-labelledby="hosted" className="space-y-5">
        <h2 id="hosted">Hosted tools with a free plan</h2>
        <p>
          Free-tier limits as each vendor publishes them. &ldquo;Not stated&rdquo; means the vendor&apos;s page
          didn&apos;t say, not that the limit is absent.
        </p>
        <ComparisonTable
          caption={`Free plans of hosted AI clippers, checked ${FREE_ALTERNATIVES_CHECKED}`}
          headers={["Tool", "Free allowance", "Watermark", "Export", "Cheapest paid plan"]}
          minWidth={760}
          rows={[
            [<a key="opus" href={S.opusclip.pricing}>OpusClip</a>, "60 credits (60 min) / month; no editing; export within 3 days", "Yes", "Up to 1080p", "$15/mo"],
            [<a key="klap" href={S.klap.pricing}>Klap</a>, "No free plan: one trial video you can preview but not download", "—", "—", "$29/mo"],
            [<a key="vizard" href={S.vizard.pricing}>Vizard</a>, "60 credits / month; uploads up to 60 min; 3-day storage", "Yes", "720p, clips up to 10 min", "$29/mo"],
            [<a key="reap" href={S.reap.pricing}>Reap</a>, "1 hr AI clipping + 1 hr captions / month; 7-day projects", "Yes", "720p", "$9.99/mo"],
            [<a key="quso" href={S.quso.pricing}>Quso</a>, "75 credits (≈75 min) / month; 7-day retention", "Yes", "720p", "$29/mo"],
            [<a key="choppity" href={S.choppity.pricing}>Choppity</a>, "45 min AI analysis; projects kept 3 days", "Not stated", "Below 1080p", "$24/mo"],
            [<a key="2short" href={S.twoShort.pricing}>2short.ai</a>, "30 min / month, YouTube links only", "Not stated", "Not stated", "$9.90/mo"],
            [<a key="spikes" href={S.spikes.pricing}>Spikes Studio</a>, "30 min / month; 7-day storage", "Yes", "720p", "$14.09/mo yearly"],
            [<a key="kapwing" href={S.kapwing.pricing}>Kapwing</a>, "10 credits; exports up to 4 min", "Yes", "720p", "$16/mo"],
            [<a key="descript" href={S.descript.pricing}>Descript</a>, "60 media min / month; clips \"limited\"", "Yes", "720p", "$16/mo"],
            [<a key="riverside" href={S.riverside.pricing}>Riverside</a>, "Magic Clips included, no number given", "Yes", "720p", "$24/mo yearly"],
            [<a key="capcut" href={S.capcut.longToShorts}>CapCut</a>, "Free tool; credit limits not published", "No (per tool page)", "Not stated", "$19.99/mo"],
            [<a key="youtube" href={S.youtube.clips}>YouTube Studio</a>, "Free, your own uploads only", "No", "Source quality", "Free"],
            [<a key="eklipse" href={S.eklipse.limits}>Eklipse</a>, "Stream highlights (gaming)", "Yes", "720p", "$24.99/mo"],
          ]}
        />

        <h3>OpusClip, Klap and Vizard</h3>
        <p>
          OpusClip&apos;s own <a href={S.opusclip.pricing}>free plan</a> gives 60 credits a month, and a credit is one
          minute of source video. Free clips render up to 1080p with a watermark, can&apos;t be edited, and stop being
          exportable after three days. <a href={S.vizard.pricing}>Vizard</a> also gives 60 credits a month, with
          uploads up to an hour, 720p watermarked exports and three-day storage. <a href={S.klap.pricing}>Klap</a>{" "}
          has no free plan: you can run one video and preview the clips, but downloading them needs a subscription.
        </p>
        <p>
          We compare those three with SupoClip on features, pricing and workflow in{" "}
          <Link href="/blog/supoclip-vs-opusclip-klap-vizard">SupoClip vs OpusClip vs Klap vs Vizard</Link>.
        </p>

        <h3>Reap: the most generous free minutes</h3>
        <p>
          Reap&apos;s free plan includes an hour of AI clipping and an hour of AI captions every month, and it takes
          YouTube, Twitch, X and Vimeo links as well as uploads up to 5 GB. Free clips carry a watermark, export at
          720p, and projects expire after seven days. The first paid plan removes the watermark and adds 1080p.
        </p>

        <h3>Quso (formerly vidyo.ai)</h3>
        <p>
          Quso gives 75 credits a month, roughly 75 minutes of video, with 720p watermarked exports and a seven-day
          retention window. Its free plan also includes TikTok publishing. Paid plans start well above the others
          here, at $29 a month.
        </p>

        <h3>2short.ai: built for YouTube links</h3>
        <p>
          2short.ai works from a pasted YouTube link, and the video needs captions (YouTube&apos;s automatic ones are
          usually enough). The free Starter plan covers 30 minutes of analysis a month, with face tracking and
          animated subtitles. Its pricing page also says 15 minutes in one place, so expect the lower number.
        </p>

        <h3>Choppity, Kapwing, Descript and Riverside</h3>
        <p>
          These are editors first, with AI clipping as one feature. Choppity&apos;s free plan has 45 minutes of AI
          analysis but keeps projects for only three days. Kapwing&apos;s <a href={S.kapwing.clipMaker}>Clip Maker</a>{" "}
          picks and reframes clips, but the free plan&apos;s 10 credits and 4-minute exports make it a trial.
          Descript gives 60 media minutes a month and marks clip creation as limited. Riverside includes{" "}
          <a href={S.riverside.magicClips}>Magic Clips</a> on free, which suits you best if you already record there.
        </p>

        <h3>CapCut and YouTube Studio: free, with catches</h3>
        <p>
          CapCut&apos;s <a href={S.capcut.longToShorts}>long video to shorts tool</a> picks highlights from speech,
          reframes to 9:16 and adds captions, and its page describes it as free and without watermarks. CapCut
          doesn&apos;t publish the credit limits, and it takes uploads and cloud drives rather than YouTube links.
        </p>
        <p>
          YouTube Studio can now make Shorts from your own long videos: pick a range by transcript or timestamps, or
          take one of its AI suggestions. Captions come from the transcript. It doesn&apos;t reframe; the vertical
          Short shows a blurred crop of your 16:9 frame, and AI suggestions are only available in some countries and
          languages.
        </p>

        <h3>Gaming: Eklipse and Spikes Studio</h3>
        <p>
          Both connect to Twitch and Kick and look for highlights in streams. Eklipse&apos;s{" "}
          <a href={S.eklipse.limits}>free plan</a> exports in 720p with an Eklipse watermark on every clip. Spikes
          Studio gives 30 minutes a month at 720p with a watermark, but its web editor only handles videos up to five
          minutes on the free plan. For a whole-VOD workflow see{" "}
          <Link href="/blog/twitch-vod-to-shorts">turning Twitch VODs into Shorts</Link>.
        </p>
      </section>

      <section aria-labelledby="open-source" className="space-y-5">
        <h2 id="open-source">Free and open source: no watermark, no minute cap</h2>
        <p>
          These run on your own machine, so the only limits are your hardware and your patience with setup. Stars and
          last activity are from GitHub on {FREE_ALTERNATIVES_CHECKED}.
        </p>
        <ComparisonTable
          caption="Open-source and source-available AI clippers"
          headers={["Project", "What you get", "License", "Needs"]}
          minWidth={720}
          rows={[
            [<a key="supoclip" href={S.supoclip.repo}>SupoClip</a>, "Browser app: clip picks with scores, 9:16 face tracking, word-synced captions, full editor, publishing to YouTube and TikTok", "Noncommercial source license; individual creators may monetize", "Docker; AssemblyAI or local Whisper; any major LLM or local Ollama"],
            [<a key="openshorts" href={S.openshorts.repo}>OpenShorts</a>, "Web dashboard, CLI, API and MCP: 3 to 15 clips per video, word-level captions, face-tracked 9:16 and split-screen", "MIT", "Docker; Gemini key or a local LLM; GPU optional"],
            [<a key="funclip" href={S.funclip.repo}>FunClip</a>, "Local web UI: transcribe, clip by text or speaker, optional LLM picks, burned subtitles. No 9:16 reframing", "MIT", "Python and PyTorch; Chinese by default, English with a flag"],
            [<a key="aiyt" href={S.aiShorts.repo}>AI-Youtube-Shorts-Generator</a>, "Command line: Whisper, LLM ranking, vertical crop with face tracking", "MIT", "ffmpeg plus an OpenAI or Gemini key (or a paid API in default mode)"],
            [<a key="viral" href={S.viralCutter.repo}>ViralCutter</a>, "Gradio UI, CLI or Colab: LLM picks, WhisperX word captions, auto-crop and split-screen", "GPL-3.0", "An NVIDIA GPU is strongly recommended"],
            [<a key="clipsai" href={S.clipsai.repo}>Clips AI</a>, "Python library for transcript-based clips and speaker-following resize; no captions", "MIT", "WhisperX, a Hugging Face token. Last updated January 2024"],
          ]}
        />

        <h3>SupoClip: a complete clipping app you can host yourself</h3>
        <p>
          SupoClip is our project, and the closest thing on this list to OpusClip&apos;s full workflow. Paste a
          YouTube, Twitch or Kick link or upload a file; it transcribes the video, proposes moments with hook,
          engagement, value and shareability scores, reframes them to 9:16 around faces, and adds word-synced
          captions. You then trim, fix captions and framing in an editor, and export or publish. Rendered clips have
          no watermark.
        </p>
        <p>
          Self-hosting is free for noncommercial use and for individual creators posting to their own channels,
          monetized ones included. Companies, agencies and client work need written permission, so if that&apos;s
          you, an MIT project like OpenShorts is the cleaner free option. You can run it fully locally with Whisper
          and Ollama, or plug in hosted AI. The <Link href={SELF_HOSTING_GUIDE_HREF}>Docker guide</Link> covers setup,
          and <Link href="/blog/best-free-opusclip-alternative">what self-hosting really costs</Link> covers the
          bill. If you&apos;d rather not run it, hosted SupoClip starts at ${pro.priceMonthly} a month; there is no
          free hosted plan.
        </p>

        <h3>OpenShorts: the most-starred open-source option</h3>
        <p>
          OpenShorts has the most GitHub stars of the projects here and an MIT license, so it&apos;s free for any use,
          including client work. It ships a web dashboard, a CLI, an API and an MCP server, picks 3 to 15 clips per
          video with Gemini or a local model, and does face-tracked vertical crops and split-screen layouts. It runs on
          a CPU, slowly: its README estimates five to eight minutes for an eight-minute video. Its maintainers also
          sell a hosted version with a small watermarked free tier.
        </p>

        <h3>FunClip, AI-Youtube-Shorts-Generator, ViralCutter and Clips AI</h3>
        <p>
          FunClip, from Alibaba&apos;s ModelScope team, is strongest at precise text-based cutting and Chinese speech;
          it doesn&apos;t produce vertical crops, so plan to reframe elsewhere. AI-Youtube-Shorts-Generator is a
          command-line script that suits developers. ViralCutter covers the full pipeline but really wants an NVIDIA
          GPU. Clips AI is a library rather than an app and hasn&apos;t been updated since early 2024. We compare
          the library and app approaches in{" "}
          <Link href="/blog/open-source-video-clippers">open-source video clippers</Link>.
        </p>
      </section>

      <section aria-labelledby="by-need" className="space-y-5">
        <h2 id="by-need">Which free alternative fits you</h2>
        <ComparisonTable
          caption="Free OpusClip alternatives by situation"
          headers={["If you…", "Try", "Why"]}
          minWidth={600}
          rows={[
            ["Want to try AI clipping on one video", "Reap or Quso", "The most free hosted minutes, no setup"],
            ["Only post on YouTube", "YouTube Studio", "Free, no watermark, already where your videos are"],
            ["Clip every week and hate watermarks", "SupoClip or OpenShorts, self-hosted", "No minute cap and no watermark"],
            ["Do client or agency work for free", "OpenShorts or FunClip", "MIT licenses allow commercial use"],
            ["Clip gaming streams", "Eklipse, Spikes Studio, or SupoClip", "Twitch and Kick VOD input"],
            ["Want to build your own pipeline", "Clips AI or FunClip", "Library and CLI building blocks"],
          ]}
        />
      </section>

      <section aria-labelledby="left-out" className="space-y-5">
        <h2 id="left-out">Tools we left out, and why</h2>
        <ul>
          <li><strong>Munch</strong> (now Munch Studio) has no free plan any more, only a <a href={S.munch.pricing}>7-day trial</a>.</li>
          <li><strong>Submagic</strong>&apos;s <a href={S.submagic.pricing}>pricing page</a> shows no free plan, and its Magic Clips feature starts on paid plans.</li>
          <li><strong>Clipchamp</strong> is a capable free editor, but <a href={S.clipchamp.compare}>nothing in it</a> cuts a long video into clips for you.</li>
          <li><strong>VEED</strong> publishes conflicting information about its free watermark, so we couldn&apos;t list its limits with confidence.</li>
        </ul>
      </section>

      <section aria-labelledby="test" className="space-y-5">
        <h2 id="test">How to test free clippers in an afternoon</h2>
        <ol>
          <li>Pick one recording you know well, 20 to 40 minutes long, and note three moments you&apos;d clip yourself.</li>
          <li>Run it through two hosted free tiers and, if you&apos;re comfortable with Docker, one self-hosted tool.</li>
          <li>Check whether each tool found your three moments, and whether its clips start and end on complete thoughts.</li>
          <li>Watch the vertical crop when speakers change and look for caption errors on names.</li>
          <li>Count the minutes you spent fixing each clip. That, not the free allowance, is the real cost.</li>
        </ol>
        <p>
          <Link href="/blog/find-best-moments-in-video">How to find the best moments in a long video</Link> has a
          checklist for judging what the AI picked, and the <Link href="/demo">SupoClip demo</Link> shows real output
          next to its source.
        </p>
      </section>

      <section aria-labelledby="sources" className="space-y-3 text-sm">
        <h2 id="sources">Sources</h2>
        <ul>
          <li>Pricing and plan pages: <a href={S.opusclip.pricing}>OpusClip</a>, <a href={S.klap.pricing}>Klap</a>, <a href={S.vizard.pricing}>Vizard</a>, <a href={S.reap.pricing}>Reap</a>, <a href={S.quso.pricing}>Quso</a>, <a href={S.choppity.pricing}>Choppity</a>, <a href={S.twoShort.pricing}>2short.ai</a>, <a href={S.spikes.pricing}>Spikes Studio</a>, <a href={S.kapwing.pricing}>Kapwing</a>, <a href={S.descript.pricing}>Descript</a>, <a href={S.riverside.pricing}>Riverside</a>, <a href={S.eklipse.pricing}>Eklipse</a>, <a href={S.munch.pricing}>Munch Studio</a>, <a href={S.submagic.pricing}>Submagic</a></li>
          <li>Feature pages: <a href={S.capcut.longToShorts}>CapCut long video to shorts</a>, <a href={S.youtube.clips}>YouTube Help: clips and Shorts</a>, <a href={S.eklipse.limits}>Eklipse free vs premium</a>, <a href={S.kapwing.clipMaker}>Kapwing Clip Maker</a>, <a href={S.riverside.magicClips}>Riverside Magic Clips</a>, <a href={S.clipchamp.compare}>Clipchamp feature comparison</a></li>
          <li>Repositories: <a href={S.supoclip.repo}>SupoClip</a>, <a href={S.openshorts.repo}>OpenShorts</a>, <a href={S.funclip.repo}>FunClip</a>, <a href={S.aiShorts.repo}>AI-Youtube-Shorts-Generator</a>, <a href={S.viralCutter.repo}>ViralCutter</a>, <a href={S.clipsai.repo}>Clips AI</a></li>
        </ul>
        <p>
          Checked {FREE_ALTERNATIVES_CHECKED}. Free plans change often; if a number here is out of date, the
          vendor&apos;s page wins. We build SupoClip.
        </p>
      </section>
    </ArticleShell>
  );
}
