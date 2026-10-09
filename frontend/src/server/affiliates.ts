import type Stripe from "stripe";
import { fetchBackend } from "@/server/backend-api";
import type { getPrismaClient } from "@/server/prisma";

type PrismaClient = ReturnType<typeof getPrismaClient>;

/**
 * Creator (affiliate) program.
 *
 * A signed-in user applies with a slug (`maya`). An admin approves it, which
 * creates the Stripe promotion code `MAYA` on the shared creator coupon (20%
 * off forever) and gives the creator Pro for free. Their audience reaches
 * checkout through `?ref=maya` or by typing the code, and gets a free first
 * month (a trial) on top of the coupon.
 */

export const AFFILIATE_PLATFORMS = ["tiktok", "youtube", "instagram", "x", "other"] as const;
export const AUDIENCE_SIZES = ["under-1k", "1k-10k", "10k-100k", "100k-plus"] as const;
export const REAPPLY_AFTER_DAYS = 30;
export const CREATOR_TRIAL_DAYS = 30;
/** Entitlement owner for Pro granted by the program (see the Stripe/Apple webhooks' provider filters). */
export const AFFILIATE_PROVIDER = "affiliate";

const RESERVED_SLUGS = new Set([
  "admin", "affiliate", "affiliates", "api", "billing", "creator", "creators", "free", "help",
  "official", "pro", "scale", "settings", "staff", "support", "supoclip", "team", "test",
]);
const SLUG_PATTERN = /^[a-z0-9](?:[a-z0-9-]{1,18})[a-z0-9]$/;

export function creatorCouponId() {
  return process.env.STRIPE_CREATOR_COUPON_ID || "creator-20-forever";
}

export type SlugCheck = { ok: true; slug: string } | { ok: false; error: string };

export function validateSlug(raw: unknown): SlugCheck {
  const slug = typeof raw === "string" ? raw.trim().toLowerCase() : "";
  if (slug.length < 3 || slug.length > 20) {
    return { ok: false, error: "Use 3 to 20 characters." };
  }
  if (!SLUG_PATTERN.test(slug) || slug.includes("--")) {
    return { ok: false, error: "Use letters, numbers and single hyphens, starting and ending with a letter or number." };
  }
  if (RESERVED_SLUGS.has(slug) || slug.startsWith("supoclip")) {
    return { ok: false, error: "That code is reserved." };
  }
  return { ok: true, slug };
}

/** A slug is free unless another user's pending, approved or revoked application holds it. */
export async function isSlugAvailable(prisma: PrismaClient, slug: string, userId: string) {
  const holder = await prisma.affiliate.findUnique({ where: { slug }, select: { user_id: true } });
  return !holder || holder.user_id === userId;
}

export type AffiliateApplication = {
  slug: string;
  platform: (typeof AFFILIATE_PLATFORMS)[number];
  profile_url: string;
  audience_size: (typeof AUDIENCE_SIZES)[number];
  video_url: string | null;
  promotion_plan: string | null;
};

function parseUrl(raw: unknown, label: string, required: boolean): { value: string | null } | { error: string } {
  const value = typeof raw === "string" ? raw.trim() : "";
  if (!value) return required ? { error: `${label} is required.` } : { value: null };
  try {
    const url = new URL(value);
    if ((url.protocol !== "https:" && url.protocol !== "http:") || value.length > 500) throw new Error();
  } catch {
    return { error: `${label} must be a full link starting with https://` };
  }
  return { value };
}

export function parseApplication(body: unknown): { ok: true; application: AffiliateApplication } | { ok: false; error: string } {
  const input = (body && typeof body === "object" ? body : {}) as Record<string, unknown>;

  const slug = validateSlug(input.slug);
  if (!slug.ok) return slug;

  const platform = AFFILIATE_PLATFORMS.find((value) => value === input.platform);
  if (!platform) return { ok: false, error: "Pick the platform you post on." };

  const audience = AUDIENCE_SIZES.find((value) => value === input.audience_size);
  if (!audience) return { ok: false, error: "Pick your audience size." };

  const profile = parseUrl(input.profile_url, "Profile link", true);
  if ("error" in profile) return { ok: false, error: profile.error };
  const video = parseUrl(input.video_url, "Video link", false);
  if ("error" in video) return { ok: false, error: video.error };

  const plan = typeof input.promotion_plan === "string" ? input.promotion_plan.trim() : "";
  if (plan.length > 1000) return { ok: false, error: "Keep your plan under 1000 characters." };

  if (input.accept_terms !== true) {
    return { ok: false, error: "Please accept the program terms." };
  }

  return {
    ok: true,
    application: {
      slug: slug.slug,
      platform,
      profile_url: profile.value as string,
      audience_size: audience,
      video_url: video.value,
      promotion_plan: plan || null,
    },
  };
}

type ExistingApplication = { status: string; reviewed_at: Date | null } | null;

/** When the user may (re)apply, or null if they can't right now. */
export function applyAvailableAt(existing: ExistingApplication, now = new Date()): Date | null {
  if (!existing) return now;
  if (existing.status !== "declined") return null;
  const reviewedAt = existing.reviewed_at ?? now;
  return new Date(Math.max(now.getTime(), reviewedAt.getTime() + REAPPLY_AFTER_DAYS * 24 * 60 * 60 * 1000));
}

export type CreatorOffer = {
  affiliateUserId: string;
  code: string;
  promotionCodeId: string;
};

/** The approved affiliate behind a ref/code, or null. */
export async function findCreatorOffer(prisma: PrismaClient, rawCode: string): Promise<CreatorOffer | null> {
  const slug = validateSlug(rawCode);
  if (!slug.ok) return null;
  const affiliate = await prisma.affiliate.findUnique({
    where: { slug: slug.slug },
    select: { user_id: true, status: true, stripe_promotion_code_id: true },
  });
  if (!affiliate || affiliate.status !== "approved" || !affiliate.stripe_promotion_code_id) return null;
  return {
    affiliateUserId: affiliate.user_id,
    code: slug.slug.toUpperCase(),
    promotionCodeId: affiliate.stripe_promotion_code_id,
  };
}

/** The free month is for first-time subscribers only, not for cancel-and-resubscribe. */
export async function hasHadStripeSubscription(stripe: Stripe, customerId: string) {
  const { data } = await stripe.subscriptions.list({ customer: customerId, status: "all", limit: 1 });
  return data.length > 0;
}

/** Gives Pro to a creator who isn't already paying; never touches a paid Stripe or App Store plan. */
export async function grantAffiliatePro(prisma: PrismaClient, userId: string) {
  await prisma.user.updateMany({
    where: {
      id: userId,
      OR: [
        { subscription_provider: AFFILIATE_PROVIDER },
        { subscription_status: { notIn: ["active", "trialing"] } },
      ],
    },
    data: { plan: "pro", subscription_status: "active", subscription_provider: AFFILIATE_PROVIDER },
  });
}

export async function revokeAffiliatePro(prisma: PrismaClient, userId: string) {
  await prisma.user.updateMany({
    where: { id: userId, subscription_provider: AFFILIATE_PROVIDER },
    data: { plan: "free", subscription_status: "inactive", subscription_provider: null },
  });
}

type AffiliateEmail =
  | { event: "applied"; slug: string; platform: string; profile_url: string; audience_size: string; video_url: string | null; promotion_plan: string | null }
  | { event: "approved"; slug: string }
  | { event: "declined"; slug: string; decline_reason: string | null };

/** Fire-and-forget: a failed email must never fail the application or review. */
export function sendAffiliateEmail(userId: string, email: AffiliateEmail) {
  void fetchBackend("/account/affiliate-email", {
    method: "POST",
    userId,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(email),
    cache: "no-store",
  })
    .then(async (response) => {
      if (!response.ok) {
        console.error(`Affiliate ${email.event} email failed with ${response.status}: ${await response.text()}`);
      }
    })
    .catch((error) => console.error(`Affiliate ${email.event} email failed`, error));
}
