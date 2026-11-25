import { safeReturnPath, withQuery } from "./return-path";

describe("safeReturnPath", () => {
  it("keeps paths on this site", () => {
    expect(safeReturnPath("/tasks/abc")).toBe("/tasks/abc");
    expect(safeReturnPath("/tasks/abc?onboarding=1")).toBe("/tasks/abc?onboarding=1");
  });

  it("refuses anything that could leave the site", () => {
    for (const bad of ["https://evil.example/", "//evil.example", "/\\evil.example", "/.//evil.example", "/a/..//evil.example", "tasks/abc", "/a\nb", "", 42, null]) {
      expect(safeReturnPath(bad)).toBeNull();
    }
    expect(safeReturnPath(`/${"a".repeat(400)}`)).toBeNull();
  });

  it("adds a query value", () => {
    expect(withQuery("/tasks/abc", "billing", "success")).toBe("/tasks/abc?billing=success");
    expect(withQuery("/tasks/abc?x=1", "billing", "success")).toBe("/tasks/abc?x=1&billing=success");
  });
});
