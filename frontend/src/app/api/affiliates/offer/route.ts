import { NextResponse } from "next/server";
import { findCreatorOffer } from "@/server/affiliates";
import { getPrismaClient } from "@/server/prisma";

/** Public: whether `?code=maya` is an approved creator's code. Codes are shared publicly by design. */
export async function GET(request: Request) {
  try {
    const code = new URL(request.url).searchParams.get("code") ?? "";
    const offer = await findCreatorOffer(getPrismaClient(), code);
    if (!offer) {
      return NextResponse.json({ error: "Unknown creator code" }, { status: 404 });
    }
    return NextResponse.json({ code: offer.code });
  } catch (error) {
    console.error("Failed to look up creator code:", error);
    return NextResponse.json({ error: "Internal server error" }, { status: 500 });
  }
}
