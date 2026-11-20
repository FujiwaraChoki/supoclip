import Stripe from "stripe";
import { NextResponse } from "next/server";
import {
  handleMigrationEvent,
  type MigrationEngineDependencies,
} from "@/lib/stripe-migration-engine";
import { runWithStripeAccount } from "@/lib/stripe-accounts";
import type { WebhookClaim } from "@/server/stripe-webhook-inbox";

export type AccountWebhookDependencies = MigrationEngineDependencies & {
  signatureClient(accountId: string): Stripe;
  secret(accountId: string): string;
  assertMode(accountId: string, event: Stripe.Event): void;
  claim(
    accountId: string,
    live: boolean,
    eventId: string,
    type: string,
  ): Promise<WebhookClaim>;
  finish(
    claim: Extract<WebhookClaim, { kind: "claimed" }>,
    success: boolean,
  ): Promise<void>;
  dispatch(event: Stripe.Event): Promise<void>;
};

/** Same handler is used by Next routes and the signed webhook/database tests. */
export function createAccountWebhookHandler(
  dependencies: AccountWebhookDependencies,
) {
  return async (request: Request, accountId: string): Promise<Response> => {
    let event: Stripe.Event;
    try {
      const signature = request.headers.get("stripe-signature");
      if (!signature)
        return NextResponse.json(
          { error: "Missing Stripe signature" },
          { status: 400 },
        );
      const client = dependencies.signatureClient(accountId);
      event = await client.webhooks.constructEventAsync(
        await request.text(),
        signature,
        dependencies.secret(accountId),
      );
      dependencies.assertMode(accountId, event);
    } catch {
      return NextResponse.json(
        { error: "Invalid Stripe account, mode or signature" },
        { status: 400 },
      );
    }
    const claim = await dependencies.claim(
      accountId,
      event.livemode,
      event.id,
      event.type,
    );
    if (claim.kind === "duplicate")
      return NextResponse.json({ received: true });
    if (claim.kind === "busy")
      return NextResponse.json(
        { error: "Event is already processing" },
        { status: 503 },
      );
    try {
      const client = await dependencies.client(accountId);
      await runWithStripeAccount({ accountId, client }, async () => {
        if (!(await handleMigrationEvent(accountId, event, dependencies)))
          await dependencies.dispatch(event);
      });
      await dependencies.finish(claim, true);
      return NextResponse.json({ received: true });
    } catch {
      await dependencies.finish(claim, false);
      return NextResponse.json(
        { error: "Stripe reconciliation failed; retry required" },
        { status: 503 },
      );
    }
  };
}
