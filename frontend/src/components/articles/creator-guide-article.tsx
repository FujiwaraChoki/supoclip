import Image from "next/image";
import Link from "next/link";

import { ArticleShell } from "@/components/articles/article-shell";
import { ArticleClipExamples } from "@/components/articles/article-clip-examples";
import { articleClipGalleries, getArticleVideoObjects } from "@/lib/article-clips";
import { getSiteUrl } from "@/lib/site";
import type { CreatorGuide } from "@/lib/creator-guides";

export function CreatorGuideArticle({ guide }: { guide: CreatorGuide }) {
  const gallery = articleClipGalleries[guide.post.slug];
  return (
    <ArticleShell post={guide.post} breadcrumb={guide.post.title}
      toc={[...guide.sections.map((section): [string, string] => [section.id, section.heading]), ["faq", "FAQ"]]}
      faqs={guide.faqs} cta={guide.cta} videos={getArticleVideoObjects(guide.post.slug, getSiteUrl())}>
      <p>{guide.intro}</p>
      {guide.sections.map((section) => (
        <section key={section.id} aria-labelledby={section.id} className="space-y-5">
          <h2 id={section.id}>{section.heading}</h2>
          {section.paragraphs.map((paragraph) => <p key={paragraph}>{paragraph}</p>)}
          {section.steps ? <ol>{section.steps.map((step) => <li key={step}>{step}</li>)}</ol> : null}
          {section.bullets ? <ul>{section.bullets.map((bullet) => <li key={bullet}>{bullet}</li>)}</ul> : null}
          {section.table ? (
            <div className="overflow-x-auto rounded-xl border">
              <table className="w-full min-w-[540px] text-left text-sm leading-6">
                <caption className="border-b px-4 py-3 text-left font-medium">{section.table.caption}</caption>
                <thead className="bg-muted/50"><tr>{section.table.headers.map((header) => <th key={header} scope="col" className="p-4">{header}</th>)}</tr></thead>
                <tbody>{section.table.rows.map(([label, ...cells]) => <tr key={label} className="border-t"><th scope="row" className="p-4 align-top">{label}</th>{cells.map((cell) => <td key={cell} className="p-4 align-top text-muted-foreground">{cell}</td>)}</tr>)}</tbody>
              </table>
            </div>
          ) : null}
          {gallery?.afterSection === section.id ? <ArticleClipExamples gallery={gallery} /> : null}
          {section.example === "editor" ? (
            <figure className="not-prose space-y-3">
              <a href="/blog/supoclip-editor.webp" aria-label="Open the full-size SupoClip editor screenshot">
                <Image src="/blog/supoclip-editor.webp" alt="SupoClip caption editor showing style controls, a word-level transcript, and safe-area preview" width={1800} height={1109} sizes="(max-width: 768px) 100vw, 768px" className="h-auto w-full rounded-xl border" />
              </a>
              <figcaption className="text-sm leading-6 text-muted-foreground">Editor screenshot using a sample video. The caption controls and transcript sit beside the preview; open the image to inspect them at full size.</figcaption>
            </figure>
          ) : null}
          {section.links ? <p className="flex flex-wrap gap-x-5 gap-y-2 text-sm">{section.links.map((link) => <Link key={link.href} href={link.href}>{link.label}</Link>)}</p> : null}
        </section>
      ))}
    </ArticleShell>
  );
}
