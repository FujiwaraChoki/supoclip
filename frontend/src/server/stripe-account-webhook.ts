import { randomUUID } from "node:crypto";
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

/** Where a verified delivery stopped: tells a lease conflict from a failed reconciliation. */
export type AccountWebhookFailureStage =
  | "claim"
  | "busy"
  | "reconcile"
  | "finish"
  | "release";

const SAFE_CODE = /^[A-Za-z0-9_.:-]{1,64}$/;

/**
 * Error shape without its message or stack: those can carry customer emails,
 * ids or payload fragments. Only the class name and Stripe/Prisma codes remain.
 */
export function describeWebhookError(error: unknown) {
  if (!(error instanceof Error)) return { name: typeof error };
  const fields = error as Error & {
    type?: unknown;
    code?: unknown;
    statusCode?: unknown;
  };
  const safe = (value: unknown) =>
    typeof value === "string" && SAFE_CODE.test(value) ? value : undefined;
  return {
    name: safe(error.name) ?? "Error",
    ...(safe(fields.type) ? { type: fields.type } : {}),
    ...(safe(fields.code) ? { code: fields.code } : {}),
    ...(typeof fields.statusCode === "number"
      ? { statusCode: fields.statusCode }
      : {}),
  };
}

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
    // Shown in Stripe's delivery attempt and in our logs, so one finds the other.
    const reference = randomUUID();
    const context = {
      reference,
      accountId,
      eventId: event.id,
      eventType: event.type,
      livemode: event.livemode,
    };
    const fail = (stage: AccountWebhookFailureStage, error?: unknown) =>
      console.error("Stripe account webhook failed", {
        ...context,
        stage,
        ...(error === undefined ? {} : { error: describeWebhookError(error) }),
      });

    let claim: WebhookClaim;
    try {
      claim = await dependencies.claim(
        accountId,
        event.livemode,
        event.id,
        event.type,
      );
    } catch (error) {
      fail("claim", error);
      throw error;
    }
    if (claim.kind === "duplicate")
      return NextResponse.json({ received: true });
    if (claim.kind === "busy") {
      // Another delivery holds the lease; Stripe's retry finds it finished or expired.
      console.warn("Stripe account webhook busy", { ...context, stage: "busy" });
      return NextResponse.json(
        { error: "Event is already processing", reason: "busy", reference },
        { status: 503 },
      );
    }
    let stage: "reconcile" | "finish" = "reconcile";
    try {
      const client = await dependencies.client(accountId);
      await runWithStripeAccount({ accountId, client }, async () => {
        if (!(await handleMigrationEvent(accountId, event, dependencies)))
          await dependencies.dispatch(event);
      });
      stage = "finish";
      await dependencies.finish(claim, true);
      return NextResponse.json({ received: true });
    } catch (error) {
      fail(stage, error);
      try {
        await dependencies.finish(claim, false);
      } catch (releaseError) {
        fail("release", releaseError);
        throw releaseError;
      }
      return NextResponse.json(
        {
          error: "Stripe reconciliation failed; retry required",
          reason: stage,
          reference,
        },
        { status: 503 },
      );
    }
  };
}
