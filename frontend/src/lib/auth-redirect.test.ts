import { authPagePath, postAuthPath, signInPath } from "./auth-redirect";

describe("postAuthPath", () => {
  it("follows a same-site path", () => {
    expect(postAuthPath("?next=/settings/creator-program")).toBe("/settings/creator-program");
  });

  it.each([
    "",
    "?next=",
    "?next=https://evil.example",
    "?next=//evil.example",
    "?next=/\\evil.example",
    "?next=settings",
    // A browser drops the tab or newline and lands on //evil.example.
    "?next=/%09/evil.example",
    "?next=/%0A/evil.example",
    "?next=/%0D/evil.example",
    "?next=/a%5C..%5C%5Cevil.example",
    "?next=/%00",
    "?next=///evil.example",
    "?next=/..//evil.example",
  ])("falls back to home for %j", (search) => {
    expect(postAuthPath(search)).toBe("/");
  });

  it("keeps the query and hash of a same-site path", () => {
    expect(postAuthPath(`?next=${encodeURIComponent("/invite/abc?x=1#top")}`)).toBe("/invite/abc?x=1#top");
  });

  it("normalizes dot segments instead of passing them through", () => {
    expect(postAuthPath("?next=/a/../settings")).toBe("/settings");
  });
});

describe("authPagePath", () => {
  it("carries the return path and invited email to the other auth page", () => {
    const href = authPagePath("/sign-in", { next: "/invite/abc", email: "ada@example.com" });
    expect(href).toBe("/sign-in?next=%2Finvite%2Fabc&email=ada%40example.com");
    expect(postAuthPath(href.slice(href.indexOf("?")))).toBe("/invite/abc");
  });

  it("links to the bare page when there is nothing to carry", () => {
    expect(authPagePath("/sign-up", {})).toBe("/sign-up");
    expect(authPagePath("/sign-up", { next: "/" })).toBe("/sign-up");
  });

  it("drops an off-site return path", () => {
    expect(authPagePath("/sign-up", { next: "//evil.example", email: " " })).toBe("/sign-up");
    expect(authPagePath("/sign-up", { next: ["/\t/evil.example"] })).toBe("/sign-up");
  });
});

describe("signInPath", () => {
  it("round-trips a path with its query", () => {
    const path = "/tasks/abc/edit?clip=c1";
    const url = signInPath(path);
    expect(url.startsWith("/sign-in?next=")).toBe(true);
    expect(postAuthPath(url.slice(url.indexOf("?")))).toBe(path);
  });
});
