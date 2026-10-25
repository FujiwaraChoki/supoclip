// @vitest-environment node
import { afterEach, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";
import { middleware } from "../middleware";

afterEach(() => vi.unstubAllEnvs());

it("keeps product-update opt-outs reachable in landing-only mode", () => {
  vi.stubEnv("NEXT_PUBLIC_LANDING_ONLY_MODE", "true");
  for (const method of ["GET", "POST"]) {
    const response = middleware(new NextRequest(
      "https://app.example.com/email/product-updates/unsubscribe?token=v1.signed", { method },
    ));
    expect(response.headers.get("x-middleware-next")).toBe("1");
  }
  const blocked = middleware(new NextRequest("https://app.example.com/dashboard"));
  expect(blocked.status).toBe(307);
});
