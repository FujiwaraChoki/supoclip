import { NextResponse } from "next/server";
import { BillingAccountHoldError } from "@/lib/stripe-accounts";
import { findCheckoutCreatorOffer } from "@/server/affiliates";
import { getPrismaClient } from "@/server/prisma";

/** Public: whether `?code=maya` is an approved creator's (or a house) code. Codes are shared publicly by design. */
export async function GET(request: Request) {
  try {
    const code = new URL(request.url).searchParams.get("code") ?? "";
    const offer = await findCheckoutCreatorOffer(getPrismaClient(), code);
    if (!offer) {
      return NextResponse.json({ error: "Unknown creator code" }, { status: 404 });
    }
    return NextResponse.json({ code: offer.code });
  } catch (error) {
    // The checkout account can't take creator codes yet (e.g. its coupon isn't mapped); checkout holds too.
    if (error instanceof BillingAccountHoldError) {
      return NextResponse.json({ error: error.message }, { status: 503 });
    }
    console.error("Failed to look up creator code:", error);
    return NextResponse.json({ error: "Internal server error" }, { status: 500 });
  }
}
