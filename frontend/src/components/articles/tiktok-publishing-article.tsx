import Link from "next/link";

import { ArticleShell } from "@/components/articles/article-shell";
import { SocialProviderIcon } from "@/components/social-provider-icon";
import type { BlogPost } from "@/lib/blog-posts";

export function TikTokPublishingArticle({ post }: { post: BlogPost }) {
  return (
    <ArticleShell
      post={post}
      breadcrumb="TikTok publishing"
      toc={[["whats-live", "What’s live"], ["first-post", "Your first post"], ["both-platforms", "TikTok + YouTube"], ["scheduling", "Scheduling"], ["faq", "FAQ"]]}
      faqs={[
        { question: "I already connected YouTube. Do I need to change anything?", answer: "Keep your existing YouTube connection. Add TikTok in Social settings, then choose either account or both in the web publisher." },
        { question: "Do I need Creator to publish to TikTok?", answer: "Creator is required for scheduling posts for later. Publishing a finished clip now is available without Creator on hosted SupoClip." },
        { question: "What about Instagram publishing?", answer: "Direct Instagram publishing isn’t available yet. You can still download a finished clip and upload it to Instagram yourself." },
      ]}
      cta={{ title: "Connect TikTok and publish your next clip", body: "Already have a finished clip? Connect your account, open the clip, and choose Publish.", label: "Connect TikTok", href: "/settings/social" }}
    >
      <p>
        TikTok publishing is live on hosted SupoClip. If you’ve been downloading finished clips just
        to upload them to TikTok, you can now keep that step inside SupoClip too.
      </p>
      <p>
        YouTube publishing is already available, and the web publisher now lets you select more than
        one connected account. Pick TikTok, YouTube, or both for the same clip.
      </p>

      <section aria-labelledby="whats-live" className="space-y-5">
        <h2 id="whats-live">From finished clip to published post</h2>
        <div className="not-prose flex flex-wrap items-center gap-4 rounded-xl border bg-muted/30 p-5">
          <span className="inline-flex items-center gap-2 font-semibold"><SocialProviderIcon provider="tiktok" className="size-6" />TikTok</span>
          <span aria-hidden="true" className="text-muted-foreground">+</span>
          <span className="inline-flex items-center gap-2 font-semibold"><SocialProviderIcon provider="youtube" className="size-6" />YouTube</span>
        </div>
        <p>
          Keep using SupoClip to find moments, edit captions, and review your clips. When a clip is
          ready, send it to a connected account from its Publish menu. You choose the caption and
          the privacy settings available for each account before submitting.
        </p>
      </section>

      <section aria-labelledby="first-post" className="space-y-5">
        <h2 id="first-post">Publish your first TikTok clip</h2>
        <ol>
          <li>Open <Link href="/settings/social">Social settings</Link>, choose TikTok, and complete the connection. Check that you’ve connected the account you want to post to.</li>
          <li>Open a finished clip on the web and choose <strong>Publish</strong>.</li>
          <li>Select your TikTok account. Review the caption, hashtags, and privacy setting. TikTok uses the caption rather than the clip’s title.</li>
          <li>Submit the post, then check its status in Social settings.</li>
        </ol>
        <p>
          Before posting, play the whole clip once. Check the opening, the cut at the end, and any
          names or numbers in the captions. Publishing is quicker now; that last review still matters.
        </p>
      </section>

      <section aria-labelledby="both-platforms" className="space-y-5">
        <h2 id="both-platforms">Post to TikTok and YouTube in one go</h2>
        <p>
          The account picker on the web is now a selector. Connect both accounts, tick TikTok and
          YouTube in the Publish window, and review the privacy setting for each destination.
          You don’t need to open a second publishing window for the same clip.
        </p>
        <p>
          The selected accounts share the post’s title, caption, and hashtags. If you want different
          wording for each platform, publish to one account at a time. After submitting to both,
          check each account’s status before trying again if something needs attention.
        </p>
      </section>

      <section aria-labelledby="scheduling" className="space-y-5">
        <h2 id="scheduling">Plan ahead with Creator</h2>
        <p>
          On <strong>Creator</strong>, you can turn on <strong>Schedule for later</strong> and choose
          a date and time instead of publishing now. When you select both accounts on the web,
          that schedule applies to both destinations.
        </p>
        <p>
          Review scheduled posts in <Link href="/settings/social">Social settings</Link>. A queued
          or scheduled status means the post hasn’t finished publishing yet.
        </p>
      </section>

      <p>
        This launch is for the hosted app. Self-hosted installations need their own social-provider
        configuration. If you’re using hosted SupoClip, head to Social settings to connect TikTok.
      </p>
    </ArticleShell>
  );
}
