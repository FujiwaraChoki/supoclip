import { proxyAdminFeedback } from "@/server/admin-feedback-proxy";

export const dynamic = "force-dynamic";

type Context = { params: Promise<{ path?: string[] }> };

const handle = (request: Request, { params }: Context) => proxyAdminFeedback(request, "/admin/feedback-webhooks", params);

export { handle as GET, handle as POST, handle as PATCH, handle as DELETE };
