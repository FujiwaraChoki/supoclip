import { NextRequest } from "next/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { GET } from "./route";
import { fetchBackend } from "@/server/backend-api";

vi.mock("@/server/backend-api", () => ({
  fetchBackend: vi.fn(async () => Response.json({ templates: [] })),
  createProxyResponse: (response: Response) => response,
}));

const get = (query = "") => GET(new NextRequest(`http://localhost/api/caption-templates${query}`));

describe("/api/caption-templates", () => {
  beforeEach(() => vi.mocked(fetchBackend).mockClear());

  it("asks for the legacy list by default", async () => {
    await get();
    expect(fetchBackend).toHaveBeenCalledWith("/caption-templates", { cache: "no-store" });
  });

  it("forwards include=fx, and nothing else", async () => {
    await get("?include=fx");
    expect(fetchBackend).toHaveBeenLastCalledWith("/caption-templates?include=fx", { cache: "no-store" });
    await get("?include=FX,%20other,fx");
    expect(fetchBackend).toHaveBeenLastCalledWith("/caption-templates?include=fx", { cache: "no-store" });
    await get("?include=../../admin");
    expect(fetchBackend).toHaveBeenLastCalledWith("/caption-templates", { cache: "no-store" });
  });
});
