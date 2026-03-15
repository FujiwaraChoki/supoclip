import { proxyAdminApiRequest } from "@/lib/admin-api-proxy";

export async function GET(request: Request) {
  return proxyAdminApiRequest(request, ["youtube-cookie-accounts"]);
}

export async function POST(request: Request) {
  return proxyAdminApiRequest(request, ["youtube-cookie-accounts"]);
}
