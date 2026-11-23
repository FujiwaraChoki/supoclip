import { BASE_ERROR_CODES, betterAuth } from "better-auth";
import { APIError, createEmailVerificationToken } from "better-auth/api";
import { prismaAdapter } from "better-auth/adapters/prisma";
import prisma from "@/lib/prisma";
import { nextCookies } from "better-auth/next-js";
import { getAuthTrustedOrigins } from "@/lib/auth-origins";
import { prepareAccountDeletion } from "@/server/account-deletion";
import { fetchBackend } from "@/server/backend-api";

const disableSignUp = ["1", "true", "yes"].includes(
  (process.env.DISABLE_SIGN_UP ?? "").toLowerCase()
);

/**
 * Accounts created at or after this instant must verify their email before they
 * get a session; older accounts keep signing in as before. Unset = never required.
 */
export function parseVerificationCutoff(value: string | undefined): Date | null {
  const raw = value?.trim();
  if (!raw) return null;
  const cutoff = new Date(raw);
  if (Number.isNaN(cutoff.getTime())) {
    throw new Error(`EMAIL_VERIFICATION_REQUIRED_SINCE is not a date: ${raw}`);
  }
  return cutoff;
}

const verificationRequiredSince = parseVerificationCutoff(
  process.env.EMAIL_VERIFICATION_REQUIRED_SINCE
);
const verificationExpiresIn = 3600;

async function sendPasswordResetEmail(userId: string, url: string) {
  const response = await fetchBackend("/account/password-reset-email", {
    method: "POST",
    userId,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url }),
    cache: "no-store",
  });

  if (!response.ok) {
    const detail = await response.text();
    throw new Error(
      `Backend password reset email failed with ${response.status}: ${detail || "unknown error"}`
    );
  }
}

const trustedOrigins = getAuthTrustedOrigins({
  NEXT_PUBLIC_APP_URL: process.env.NEXT_PUBLIC_APP_URL,
  BETTER_AUTH_URL: process.env.BETTER_AUTH_URL,
  BETTER_AUTH_TRUSTED_ORIGINS: process.env.BETTER_AUTH_TRUSTED_ORIGINS,
  NEXT_PUBLIC_SELF_HOST: process.env.NEXT_PUBLIC_SELF_HOST,
});

export const auth = betterAuth({
  database: prismaAdapter(prisma, {
    provider: "postgresql",
  }),
  user: {
    deleteUser: {
      enabled: true,
      beforeDelete: async (user) => {
        await prepareAccountDeletion(user.id);
      },
    },
    additionalFields: {
      is_admin: {
        type: "boolean",
        input: false,
      },
    },
  },
  trustedOrigins,
  // Over HTTP only; server code can still call auth.api.verifyPassword. A public
  // verify-password would let a stolen session guess the password at the generic
  // rate limit, around the limits of the email change flow, and change-email
  // would skip that flow's codes if it were ever switched on.
  disabledPaths: ["/verify-password", "/change-email"],
  emailVerification: {
    // Only when verification is required; otherwise the link is sent on request
    // (API keys, MCP consent).
    sendOnSignUp: verificationRequiredSince !== null,
    sendOnSignIn: false,
    autoSignInAfterVerification: true,
    expiresIn: verificationExpiresIn,
    sendVerificationEmail: async ({ user, url }) => {
      const response = await fetchBackend("/account/verification-email", {
        method: "POST",
        userId: user.id,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url }),
        cache: "no-store",
      });
      if (!response.ok) {
        // Do not log the verification URL or token.
        throw new Error(`Verification email delivery failed (${response.status})`);
      }
    },
  },
  emailAndPassword: {
    enabled: true,
    disableSignUp,
    // A new account gets its session from the verification link, not from sign-up.
    // This also makes sign-up answer the same for a taken address.
    autoSignIn: verificationRequiredSince === null,
    revokeSessionsOnPasswordReset: true,
    sendResetPassword: async ({ user, url }) => {
      // Not awaited so response timing doesn't reveal whether an account exists.
      void sendPasswordResetEmail(user.id, url).catch((error) => {
        console.error("Failed to send password reset email", error);
      });
    },
  },
  databaseHooks: {
    session: {
      create: {
        // Every way of getting a session passes here, after the password check, so
        // an unverified new account can't sign in by any route. Better Auth's own
        // requireEmailVerification would also lock out every older unverified account.
        before: async (session, ctx) => {
          if (!verificationRequiredSince || !ctx) return;
          const user = await ctx.context.internalAdapter.findUserById(session.userId);
          if (!user || user.emailVerified) return;
          if (new Date(user.createdAt).getTime() < verificationRequiredSince.getTime()) return;
          if (ctx.path === "/sign-in/email") {
            // Signing in resends the link, so "check your inbox" is always true.
            const token = await createEmailVerificationToken(
              ctx.context.secret, user.email, undefined, verificationExpiresIn, { userId: user.id }
            );
            const url = `${ctx.context.baseURL}/verify-email?token=${token}&callbackURL=${encodeURIComponent("/")}`;
            await ctx.context.options.emailVerification?.sendVerificationEmail?.({ user, url, token }, ctx.request)
              .catch((error) => console.error("Failed to resend verification email", error));
          }
          throw APIError.from("FORBIDDEN", BASE_ERROR_CODES.EMAIL_NOT_VERIFIED);
        },
      },
    },
  },
  plugins: [
    nextCookies(), // Enable Next.js cookie handling
  ],
});

export type Session = typeof auth.$Infer.Session;
