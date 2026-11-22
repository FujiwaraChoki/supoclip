import { intlLocale, type Locale } from "@/i18n/config";
import type { ArticleClipGallery } from "@/lib/article-clips";
import { getArticleClip } from "@/lib/article-clips";
import { getClipExamplesChrome } from "@/lib/localized-guides";

/** Full, locally hosted SupoClip renders with source and adaptation credits. */
export function ArticleClipExamples({ gallery, locale = "en" }: { gallery: ArticleClipGallery; locale?: Locale }) {
  const chrome = getClipExamplesChrome(locale);
  const numberFormat = new Intl.NumberFormat(intlLocale(locale));

  return (
    <section aria-labelledby="worked-clip-examples" className="not-prose space-y-6 rounded-2xl border bg-muted/20 p-5 sm:p-7">
      <div className="space-y-3">
        <p className="text-xs font-semibold uppercase tracking-widest text-muted-foreground">{chrome.eyebrow}</p>
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
                aria-label={chrome.videoAriaLabel(clip.title, clip.source.title)}
                className="mx-auto aspect-[9/16] w-full max-w-[270px] rounded-xl bg-black ring-1 ring-border" />
              <figcaption className="space-y-3 text-sm leading-6">
                <h3 className="!pt-0 font-semibold text-foreground">{clip.title}</h3>
                <p className="font-mono text-xs text-muted-foreground">{chrome.fullClipMeta(Math.round(clip.durationSeconds), clip.sourceRange)}</p>
                <p className="text-muted-foreground">{note}</p>
                <p className="text-xs leading-6 text-muted-foreground">
                  {chrome.sourcePrefix} <a href={clip.source.url} className="underline underline-offset-4">{clip.source.title}</a> · {clip.source.credit} · <a href={clip.source.licenseUrl} className="underline underline-offset-4">{clip.source.license}</a>
                </p>
                <a href={`${clip.source.url}&t=${Math.floor(clip.startSeconds)}s`} className="inline-flex font-medium underline underline-offset-4">{chrome.watchOriginal}</a>
                <details className="rounded-lg border p-3 text-muted-foreground">
                  <summary className="cursor-pointer font-medium text-foreground">{chrome.detailsSummary}</summary>
                  <div className="mt-3 space-y-2 text-xs leading-6">
                    <p><a href={clip.source.url} className="underline underline-offset-4">{clip.source.title}</a> · {clip.source.credit} · {clip.source.publishedAt}</p>
                    <p>{chrome.viewsNote(numberFormat.format(clip.source.viewsAtSelection), clip.source.checkedAt)}</p>
                    <p><a href={clip.source.licenseUrl} className="underline underline-offset-4">{clip.source.license}</a> · <a href={clip.source.evidenceUrl} className="underline underline-offset-4">{chrome.licenseRecord}</a></p>
                    <p>{clip.edits} {chrome.noEndorsement}</p>
                    <a href={clip.attributionFile} className="inline-block underline underline-offset-4">{chrome.downloadAttribution}</a>
                  </div>
                </details>
              </figcaption>
            </figure>
          );
        })}
      </div>
      <p className="text-xs leading-6 text-muted-foreground">{chrome.footerNote}</p>
    </section>
  );
}
