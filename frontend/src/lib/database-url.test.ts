import { describe, expect, it } from "vitest";
import { databaseUrlWithPoolLimits } from "./database-url";

describe("database connection budget", () => {
  it("bounds the pool while preserving credentials and unrelated URL options", () => {
    const url = new URL(databaseUrlWithPoolLimits("postgresql://app:p%40ss@db/clips?schema=public&sslmode=require")!);
    expect(url.searchParams.get("connection_limit")).toBe("5");
    expect(url.searchParams.get("pool_timeout")).toBe("10");
    expect(url.password).toBe("p%40ss");
    expect(url.searchParams.get("sslmode")).toBe("require");
    expect(url.searchParams.get("schema")).toBe("public");
  });

  it("honors explicit bounded settings", () => {
    const url = new URL(databaseUrlWithPoolLimits("postgresql://db/clips?connection_limit=3&pool_timeout=20")!);
    expect(url.searchParams.get("connection_limit")).toBe("3");
    expect(url.searchParams.get("pool_timeout")).toBe("20");
    expect(databaseUrlWithPoolLimits(undefined)).toBeUndefined();
  });

  it.each(["connection_limit=0", "pool_timeout=0", "connection_limit=-1", "connection_limit=nope"])("rejects unlimited or invalid %s", (setting) => {
    expect(() => databaseUrlWithPoolLimits(`postgresql://db/clips?${setting}`)).toThrow(/positive integer/);
  });
});
