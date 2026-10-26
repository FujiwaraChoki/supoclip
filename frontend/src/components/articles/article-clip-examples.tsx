import type { ArticleClipGallery } from "@/lib/article-clips";
import { getArticleClip } from "@/lib/article-clips";

/** Full, locally hosted SupoClip renders with source and adaptation credits. */
export function ArticleClipExamples({ gallery }: { gallery: ArticleClipGallery }) {
  return (
    <section aria-labelledby="worked-clip-examples" className="not-prose space-y-6 rounded-2xl border bg-muted/20 p-5 sm:p-7">
      <div className="space-y-3">
        <p className="text-xs font-semibold uppercase tracking-widest text-muted-foreground">Made with SupoClip</p>
        <h2 id="worked-clip-examples" className="!pt-0 text-2xl font-bold tracking-tight">{gallery.heading}</h2>
        <p className="text-sm leading-7 text-muted-foreground">{gallery.intro}</p>
      </div>
      <div className="grid gap-8 sm:grid-cols-2">
        {gallery.examples.map(({ clipId, note }) => {
          const clip = getArticleClip(clipId);
          if (!clip) return null;
          return (
            <figure key={clip.id} id={`example-${clip.id}`} className="scroll-mt-8 space-y-4">
              <video src={clip.src} poster={clip.poster} controls playsInline preload="none" width={540} height={960}
                aria-label={`${clip.title} — SupoClip example from ${clip.source.title}`}
                className="mx-auto aspect-[9/16] w-full max-w-[270px] rounded-xl bg-black ring-1 ring-border" />
              <figcaption className="space-y-3 text-sm leading-6">
                <h3 className="!pt-0 font-semibold text-foreground">{clip.title}</h3>
                <p className="font-mono text-xs text-muted-foreground">{Math.round(clip.durationSeconds)}s full clip · source {clip.sourceRange}</p>
                <p className="text-muted-foreground">{note}</p>
                <p className="text-xs leading-6 text-muted-foreground">
                  Source: <a href={clip.source.url} className="underline underline-offset-4">{clip.source.title}</a> · {clip.source.credit} · <a href={clip.source.licenseUrl} className="underline underline-offset-4">{clip.source.license}</a>
                </p>
                <a href={`${clip.source.url}&t=${Math.floor(clip.startSeconds)}s`} className="inline-flex font-medium underline underline-offset-4">Watch the original moment</a>
                <details className="rounded-lg border p-3 text-muted-foreground">
                  <summary className="cursor-pointer font-medium text-foreground">Source, license, and edits</summary>
                  <div className="mt-3 space-y-2 text-xs leading-6">
                    <p><a href={clip.source.url} className="underline underline-offset-4">{clip.source.title}</a> · {clip.source.credit} · {clip.source.publishedAt}</p>
                    <p>{new Intl.NumberFormat("en").format(clip.source.viewsAtSelection)} YouTube views when selected on {clip.source.checkedAt}.</p>
                    <p><a href={clip.source.licenseUrl} className="underline underline-offset-4">{clip.source.license}</a> · <a href={clip.source.evidenceUrl} className="underline underline-offset-4">Source and license record</a></p>
                    <p>{clip.edits} These editorial examples do not imply endorsement by the speakers or source publisher.</p>
                    <a href={clip.attributionFile} className="inline-block underline underline-offset-4">Download attribution and processing notes</a>
                  </div>
                </details>
              </figcaption>
            </figure>
          );
        })}
      </div>
      <p className="text-xs leading-6 text-muted-foreground">These are complete example clips, encoded at 540×960 for this page. Source timestamps refer to the original recording; removed pauses can make the final clip shorter than that span.</p>
    </section>
  );
}
