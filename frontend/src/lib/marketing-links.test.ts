import { blogPosts } from "./blog-posts";
import { getRelatedMarketingLinks, marketingLinks } from "./marketing-links";
import { seoPages } from "./seo-pages";

describe("marketing link registry", () => {
  const hrefs = new Set(marketingLinks.map((link) => link.href));

  it("registers every indexable SEO page, blog post, and the demo", () => {
    const expected = [
      "/demo",
      ...seoPages.map((page) => `/${page.slug}`),
      ...blogPosts.map((post) => `/blog/${post.slug}`),
    ];
    for (const href of expected) expect(hrefs, href).toContain(href);
    expect(hrefs.size).toBe(marketingLinks.length);
  });

  it("gives every page three valid related links that never point to itself", () => {
    for (const link of marketingLinks) {
      expect(link.related, link.href).toHaveLength(3);
      expect(link.related, link.href).not.toContain(link.href);
      expect(getRelatedMarketingLinks(link.href), link.href).toHaveLength(3);
    }
  });

  it("links every page from at least one other page", () => {
    const linkedTo = new Set(marketingLinks.flatMap((link) => link.related));
    for (const link of marketingLinks) expect(linkedTo, link.href).toContain(link.href);
  });

  it("only uses internal links inside SEO page sections that resolve", () => {
    for (const page of seoPages) {
      for (const section of page.sections) {
        for (const link of section.links ?? []) expect(hrefs, `${page.slug} -> ${link.href}`).toContain(link.href);
      }
    }
  });
});
