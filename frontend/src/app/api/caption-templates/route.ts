import { createProxyResponse, fetchBackend } from "@/server/backend-api";

export async function GET() {
  return createProxyResponse(await fetchBackend("/caption-templates", { cache: "no-store" }));
}
