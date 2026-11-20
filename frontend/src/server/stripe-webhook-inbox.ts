import { randomUUID } from "node:crypto";
import type { PrismaClient } from "@/generated/prisma";

export type WebhookClaim =
  | { kind: "claimed"; key: string; token: string }
  | { kind: "duplicate" }
  | { kind: "busy" };

/** Claim and finalization are fenced by a lease token, including failed-attempt retries. */
export function createPrismaWebhookInbox(db: PrismaClient) {
  return {
    async claim(
      accountId: string,
      livemode: boolean,
      eventId: string,
      type: string,
    ): Promise<WebhookClaim> {
      const key = `${accountId}:${livemode ? "live" : "test"}:${eventId}`;
      const token = randomUUID();
      return db.$transaction(async (tx) => {
        if (accountId === process.env.STRIPE_SOURCE_ACCOUNT_ID && livemode) {
          const legacy = await tx.stripeWebhookEvent.findUnique({
            where: { id: eventId },
          });
          if (legacy?.status === "processed") return { kind: "duplicate" };
        }
        await tx.$executeRaw`INSERT INTO stripe_webhook_events (id, type, status) VALUES (${key}, ${type}, 'failed') ON CONFLICT (id) DO NOTHING`;
        await tx.$queryRaw`SELECT id FROM stripe_webhook_events WHERE id = ${key} FOR UPDATE`;
        const row = await tx.stripeWebhookEvent.findUniqueOrThrow({
          where: { id: key },
        });
        if (row.status === "processed") return { kind: "duplicate" };
        if (
          row.status === "processing" &&
          row.lease_expires_at &&
          row.lease_expires_at.getTime() > Date.now()
        )
          return { kind: "busy" };
        await tx.stripeWebhookEvent.update({
          where: { id: key },
          data: {
            status: "processing",
            claim_token: token,
            lease_expires_at: new Date(Date.now() + 300000),
          },
        });
        return { kind: "claimed", key, token };
      });
    },
    async finish(
      claim: Extract<WebhookClaim, { kind: "claimed" }>,
      success: boolean,
    ): Promise<void> {
      const updated = await db.stripeWebhookEvent.updateMany({
        where: {
          id: claim.key,
          claim_token: claim.token,
          status: "processing",
        },
        data: {
          status: success ? "processed" : "failed",
          lease_expires_at: null,
          claim_token: null,
        },
      });
      if (updated.count !== 1)
        throw new Error("Stripe event lease changed during processing");
    },
  };
}
