import { postAuthPath } from "./auth-redirect";

describe("postAuthPath", () => {
  it("follows a same-site path", () => {
    expect(postAuthPath("?next=/settings/creator-program")).toBe("/settings/creator-program");
  });

  it.each(["", "?next=", "?next=https://evil.example", "?next=//evil.example", "?next=/\\evil.example", "?next=settings"])(
    "falls back to home for %j",
    (search) => {
      expect(postAuthPath(search)).toBe("/");
    },
  );
});
