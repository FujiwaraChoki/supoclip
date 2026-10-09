import Image from "next/image";
import Link from "next/link";

import { getMarketingLinks } from "@/lib/marketing-links";
import { APP_STORE_URL, GITHUB_URL } from "@/lib/site";

const linkClassName = "hover:text-foreground transition-colors";

/** Crawlable footer shared by the landing page, SEO pages, and blog. */
export function SiteFooter() {
  const columns = [
    { heading: "Guides", links: getMarketingLinks("guide").map(({ href, label }) => ({ href, label })) },
    {
      heading: "Compare",
      links: [...getMarketingLinks("comparison").map(({ href, label }) => ({ href, label })), { href: "/blog", label: "All articles" }],
    },
    {
      heading: "Product",
      links: [
        ...getMarketingLinks("product").map(({ href, label }) => ({ href, label })),
        { href: "/sign-up", label: "Start clipping" },
        { href: "/#pricing", label: "Pricing" },
        { href: "/affiliate", label: "Creator program" },
        { href: "/privacy", label: "Privacy" },
        { href: "/terms", label: "Terms" },
      ],
    },
  ];

  return (
    <footer className="border-t px-6 py-12">
      <div className="mx-auto grid max-w-6xl gap-10 sm:grid-cols-2 lg:grid-cols-[1.2fr_1fr_1fr_1fr]">
        <div>
          <Link href="/" className="flex items-center gap-2">
            <Image src="/logo.png" alt="SupoClip" width={24} height={24} className="rounded-md" />
            <span className="text-sm font-semibold" style={{ fontFamily: "var(--font-syne), system-ui" }}>
              SupoClip
            </span>
          </Link>
          <p className="mt-3 max-w-xs text-xs leading-6 text-muted-foreground">
            Open-source AI video clipper. Use the hosted app or run it on your own infrastructure.
          </p>
          <div className="mt-4 flex gap-4 text-xs text-muted-foreground">
            <a href={GITHUB_URL} target="_blank" rel="noopener noreferrer" className={linkClassName}>GitHub</a>
            <a href={APP_STORE_URL} target="_blank" rel="noopener noreferrer" className={linkClassName}>iOS App</a>
            <span>&copy; {new Date().getFullYear()}</span>
          </div>
        </div>
        {columns.map((column) => (
          <nav key={column.heading} aria-label={`${column.heading} links`}>
            <p className="text-xs font-semibold uppercase tracking-[0.16em]">{column.heading}</p>
            <ul className="mt-4 space-y-2.5 text-xs text-muted-foreground">
              {column.links.map((link) => (
                <li key={link.href}>
                  <Link href={link.href} className={linkClassName}>{link.label}</Link>
                </li>
              ))}
            </ul>
          </nav>
        ))}
      </div>
    </footer>
  );
}
