import type { NextRequest } from "next/server";

import { createProxyResponse, fetchBackend } from "@/server/backend-api";

/** Allowed `include` values: `fx` adds the animated (dynamic edits) templates, asked for only when they're enabled. */
const INCLUDES = new Set(["fx"]);

export async function GET(request: NextRequest) {
  const include = (request.nextUrl.searchParams.get("include") ?? "")
    .split(",")
    .map((part) => part.trim().toLowerCase())
    .filter((part) => INCLUDES.has(part));
  const query = include.length ? `?include=${[...new Set(include)].join(",")}` : "";
  return createProxyResponse(await fetchBackend(`/caption-templates${query}`, { cache: "no-store" }));
}
