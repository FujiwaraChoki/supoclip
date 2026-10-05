import Link from "next/link";
import { ArrowRight } from "lucide-react";

import { getRelatedMarketingLinks } from "@/lib/marketing-links";

export function RelatedGuides({ href, heading = "Continue exploring" }: { href: string; heading?: string }) {
  const links = getRelatedMarketingLinks(href);
  if (links.length === 0) return null;

  return (
    <section aria-labelledby="related-guides-heading" className="not-prose">
      <h2 id="related-guides-heading" className="text-2xl font-bold tracking-tight">{heading}</h2>
      <div className="mt-6 grid gap-4 md:grid-cols-3">
        {links.map((link) => (
          <Link
            key={link.href}
            href={link.href}
            className="group rounded-lg border p-5 !no-underline transition-colors hover:border-foreground/30"
          >
            <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">{link.eyebrow}</p>
            <h3 className="mt-2 font-semibold text-foreground">{link.title}</h3>
            <p className="mt-2 text-sm leading-6 !text-muted-foreground">{link.description}</p>
            <span className="mt-4 inline-flex items-center gap-1.5 text-sm font-semibold text-foreground">
              Read <ArrowRight className="h-3.5 w-3.5 transition-transform group-hover:translate-x-0.5" />
            </span>
          </Link>
        ))}
      </div>
    </section>
  );
}
