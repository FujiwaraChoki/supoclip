// @vitest-environment node
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { findNewerSyntax } from "../../scripts/check-client-syntax.mjs";
import nextConfig from "../../next.config";

describe("findNewerSyntax", () => {
  it("flags syntax that Chrome 90-93 and older Safari cannot parse", () => {
    const hits = findNewerSyntax(
      "class A{static{this.x=1}}function f(){using r=open();return/[\\p{L}--a]/v.test(r)}",
    );
    expect(hits.map((hit: { feature: string }) => hit.feature)).toEqual([
      "class static block",
      "using declaration",
      "RegExp v flag",
    ]);
  });

  it("accepts syntax the bundle already relies on", () => {
    const source =
      "class E extends Error{status;#id=1;static n=0;static get d(){return this.n}has(o){return #id in o}}" +
      "let s=globalThis.Symbol;s.dispose??=s('d');const m=a?.b??/(?<y>\\d{4})/dgu.exec('2026');";
    expect(findNewerSyntax(source)).toEqual([]);
  });
});

describe("next.config transpilePackages", () => {
  it("compiles intl-messageformat while it ships class static blocks", () => {
    // Resolve it the way the client bundle does: next-intl -> use-intl -> intl-messageformat.
    const fromNextIntl = createRequire(createRequire(import.meta.url).resolve("next-intl"));
    const entry = createRequire(fromNextIntl.resolve("use-intl")).resolve("intl-messageformat");
    if (findNewerSyntax(readFileSync(entry, "utf8")).length === 0) return;
    expect(nextConfig.transpilePackages).toContain("intl-messageformat");
  });
});
