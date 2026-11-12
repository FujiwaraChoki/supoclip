import { handleAccountBillingWebhook } from "@/server/stripe-billing-webhook";

/** The path is matched against server configuration before accepting a signature. */
export async function POST(
  request: Request,
  context: { params: Promise<{ accountId: string }> },
): Promise<Response> {
  const { accountId } = await context.params;
  return handleAccountBillingWebhook(request, accountId);
}
