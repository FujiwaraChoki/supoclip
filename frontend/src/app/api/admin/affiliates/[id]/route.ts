import { NextResponse } from "next/server";
import {
  creatorCouponId,
  deactivatePromotionCode,
  grantAffiliatePro,
  revokeAffiliatePro,
  sendAffiliateEmail,
} from "@/server/affiliates";
import { getPrismaClient } from "@/server/prisma";
import { getServerSession } from "@/server/session";
import { getStripeClient } from "@/lib/stripe";

async function requireAdmin() {
  const session = await getServerSession();
  if (!session?.user?.id) {
    return { error: NextResponse.json({ error: "Unauthorized" }, { status: 401 }) };
  }

  const isAdmin = Boolean((session.user as { is_admin?: boolean }).is_admin);
  if (!isAdmin) {
    return { error: NextResponse.json({ error: "Forbidden" }, { status: 403 }) };
  }

  return { session };
}

const ALREADY_REVIEWED = NextResponse.json({ error: "This application was already reviewed" }, { status: 409 });

/**
 * Review a creator application: `{ action: "approve" | "decline" | "revoke" | "app_store_code_added", reason? }`.
 * `app_store_code_added` records that the creator's custom code was added to the App Store "Creator offer".
 */
export async function POST(request: Request, { params }: { params: Promise<{ id: string }> }) {
  try {
    const adminCheck = await requireAdmin();
    if (adminCheck.error) {
      return adminCheck.error;
    }
    const adminId = adminCheck.session.user.id;

    const { id } = await params;
    const body = (await request.json().catch(() => ({}))) as { action?: unknown; reason?: unknown };
    const reason = typeof body.reason === "string" ? body.reason.trim().slice(0, 1000) || null : null;

    const prisma = getPrismaClient();
    const affiliate = await prisma.affiliate.findUnique({
      where: { id },
      select: { id: true, user_id: true, slug: true, status: true, stripe_promotion_code_id: true },
    });
    if (!affiliate) {
      return NextResponse.json({ error: "Application not found" }, { status: 404 });
    }
    const reviewed = { reviewed_at: new Date(), reviewed_by: adminId };

    if (body.action === "approve") {
      if (affiliate.status !== "pending" || !affiliate.slug) return ALREADY_REVIEWED;

      const stripe = getStripeClient();
      let promotionCode;
      try {
        promotionCode = await stripe.promotionCodes.create({
          promotion: { type: "coupon", coupon: creatorCouponId() },
          code: affiliate.slug.toUpperCase(),
          metadata: { affiliate_id: affiliate.id, user_id: affiliate.user_id },
        });
      } catch (error) {
        // e.g. the code already exists in Stripe, or the creator coupon is missing.
        const message = error instanceof Error ? error.message : "unknown error";
        return NextResponse.json({ error: `Stripe rejected the promotion code: ${message}` }, { status: 502 });
      }

      const { count } = await prisma.affiliate.updateMany({
        where: { id, status: "pending" },
        data: { status: "approved", stripe_promotion_code_id: promotionCode.id, ...reviewed },
      });
      if (count === 0) {
        // Another admin reviewed it meanwhile; don't leave a live code behind.
        await stripe.promotionCodes.update(promotionCode.id, { active: false });
        return ALREADY_REVIEWED;
      }

      await grantAffiliatePro(prisma, affiliate.user_id);
      sendAffiliateEmail(affiliate.user_id, { event: "approved", slug: affiliate.slug });
      return NextResponse.json({ status: "approved" });
    }

    if (body.action === "decline") {
      // Clearing the slug frees it for other applicants.
      const { count } = await prisma.affiliate.updateMany({
        where: { id, status: "pending" },
        data: { status: "declined", slug: null, decline_reason: reason, ...reviewed },
      });
      if (count === 0) return ALREADY_REVIEWED;

      sendAffiliateEmail(affiliate.user_id, {
        event: "declined",
        slug: affiliate.slug ?? "",
        decline_reason: reason,
      });
      return NextResponse.json({ status: "declined" });
    }

    if (body.action === "revoke") {
      const { count } = await prisma.affiliate.updateMany({
        where: { id, status: "approved" },
        data: { status: "revoked", decline_reason: reason, ...reviewed },
      });
      if (count === 0) {
        return NextResponse.json({ error: "Only approved creators can be revoked" }, { status: 409 });
      }

      // Remove Pro before touching Stripe so a Stripe failure can't leave a revoked creator on free Pro.
      // Checkout only honours approved codes, so the Stripe deactivation is best effort.
      await revokeAffiliatePro(prisma, affiliate.user_id);
      if (affiliate.stripe_promotion_code_id) {
        await deactivatePromotionCode(affiliate.stripe_promotion_code_id);
      }
      return NextResponse.json({ status: "revoked" });
    }

    if (body.action === "app_store_code_added") {
      const { count } = await prisma.affiliate.updateMany({
        where: { id, status: "approved" },
        data: { app_store_code_added_at: new Date() },
      });
      if (count === 0) {
        return NextResponse.json({ error: "Only approved creators have an App Store code" }, { status: 409 });
      }
      return NextResponse.json({ status: "approved" });
    }

    return NextResponse.json({ error: "action must be approve, decline, revoke or app_store_code_added" }, { status: 400 });
  } catch (error) {
    console.error("Failed to review affiliate application:", error);
    return NextResponse.json({ error: "Internal server error" }, { status: 500 });
  }
}
