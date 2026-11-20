import { NextResponse } from "next/server";
import { headers } from "next/headers";
import { APIError } from "better-auth/api";

import { auth } from "@/lib/auth";
import { getAuthTrustedOrigins } from "@/lib/auth-origins";
import { fetchBackend } from "@/server/backend-api";
import {
  cancelEmailChange,
  EmailChangeError,
  finishEmailChange,
  generateEmailChangeCode,
  getEmailChangeState,
  resendEmailChangeCode,
  startEmailChange,
  submitNewEmail,
  verifyCurrentEmailCode,
  verifyNewEmailCode,
  type EmailChangeActor,
  type EmailChangeDeps,
} from "@/server/email-change";
import { liveBillingEmailDeps, updateBillingEmail } from "@/server/email-change-billing";
import { getPrismaClient } from "@/server/prisma";
import { prismaSqlExecutor } from "@/server/user-acquisition";

// Session cookies only: API keys and the MCP server can't change the sign-in address.
async function actorFor(requestHeaders: Headers): Promise<EmailChangeActor | null> {
  const session = await auth.api.getSession({ headers: requestHeaders });
  if (!session?.user?.id) return null;
  return {
    userId: session.user.id,
    sessionId: session.session.id,
    sessionCreatedAt: new Date(session.session.createdAt),
    ip: clientIp(requestHeaders),
  };
}

/** Same rule as the MCP OAuth limits: X-Real-IP from the proxy, else the last X-Forwarded-For hop. */
function clientIp(requestHeaders: Headers): string | null {
  const real = requestHeaders.get("x-real-ip")?.trim();
  if (real) return real;
  const forwarded = requestHeaders.get("x-forwarded-for")?.split(",").map((part) => part.trim()).filter(Boolean);
  return forwarded?.at(-1) ?? null;
}

function crossOrigin(request: Request) {
  const origin = request.headers.get("origin");
  if (!origin || origin === new URL(request.url).origin) return false;
  const trusted = getAuthTrustedOrigins({
    NEXT_PUBLIC_APP_URL: process.env.NEXT_PUBLIC_APP_URL,
    BETTER_AUTH_URL: process.env.BETTER_AUTH_URL,
    BETTER_AUTH_TRUSTED_ORIGINS: process.env.BETTER_AUTH_TRUSTED_ORIGINS,
    NEXT_PUBLIC_SELF_HOST: process.env.NEXT_PUBLIC_SELF_HOST,
  });
  return !trusted.includes(origin);
}

function liveDeps(requestHeaders: Headers): EmailChangeDeps {
  const secret = process.env.BETTER_AUTH_SECRET;
  if (!secret) throw new Error("BETTER_AUTH_SECRET is not configured");
  const prisma = getPrismaClient();
  return {
    sql: prismaSqlExecutor(prisma),
    secret,
    now: () => new Date(),
    generateCode: generateEmailChangeCode,
    async sendEmail(userId, message) {
      const response = await fetchBackend("/account/email-change-email", {
        method: "POST",
        userId,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(message),
        cache: "no-store",
      });
      // Never log the body: it carries the code.
      if (!response.ok) throw new Error(`Email change email failed (${response.status})`);
    },
    updateBillingEmail: (userId, _from, to) => updateBillingEmail(liveBillingEmailDeps(prisma), userId, to),
    async verifyPassword(password) {
      try {
        await auth.api.verifyPassword({ body: { password }, headers: requestHeaders });
        return true;
      } catch (error) {
        if (error instanceof APIError && error.statusCode === 400) return false;
        throw error;
      }
    },
  };
}

function errorResponse(error: unknown) {
  if (error instanceof EmailChangeError) {
    return NextResponse.json(
      { error: error.code, ...error.details },
      {
        status: error.status,
        headers: error.details.retryAfterSeconds ? { "Retry-After": String(error.details.retryAfterSeconds) } : undefined,
      },
    );
  }
  console.error("Email change failed", error);
  return NextResponse.json({ error: "server_error" }, { status: 500 });
}

const noStore = { headers: { "Cache-Control": "no-store" } };

export async function GET() {
  const requestHeaders = await headers();
  const actor = await actorFor(requestHeaders);
  if (!actor) return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  try {
    return NextResponse.json(await getEmailChangeState(liveDeps(requestHeaders), actor), noStore);
  } catch (error) {
    return errorResponse(error);
  }
}

export async function POST(request: Request) {
  if (crossOrigin(request)) return NextResponse.json({ error: "forbidden" }, { status: 403 });
  // JSON only, so a cross-site form post can't drive it with the cookie.
  if (!request.headers.get("content-type")?.toLowerCase().startsWith("application/json")) {
    return NextResponse.json({ error: "invalid_request" }, { status: 415 });
  }
  const requestHeaders = await headers();
  const actor = await actorFor(requestHeaders);
  if (!actor) return NextResponse.json({ error: "unauthorized" }, { status: 401 });

  const body = (await request.json().catch(() => null)) as Record<string, unknown> | null;
  if (!body || typeof body !== "object") return NextResponse.json({ error: "invalid_request" }, { status: 400 });

  try {
    const deps = liveDeps(requestHeaders);
    switch (body.action) {
      case "start":
        return NextResponse.json(
          await startEmailChange(deps, actor, { password: typeof body.password === "string" ? body.password : undefined }),
          noStore,
        );
      case "resend":
        return NextResponse.json(await resendEmailChangeCode(deps, actor), noStore);
      case "verify_current":
        return NextResponse.json(await verifyCurrentEmailCode(deps, actor, body.code), noStore);
      case "new_email":
        return NextResponse.json(await submitNewEmail(deps, actor, body.email), noStore);
      case "verify_new":
        return NextResponse.json({ done: await verifyNewEmailCode(deps, actor, body.code) }, noStore);
      case "finish":
        return NextResponse.json({ done: await finishEmailChange(deps, actor) }, noStore);
      default:
        return NextResponse.json({ error: "invalid_request" }, { status: 400 });
    }
  } catch (error) {
    return errorResponse(error);
  }
}

export async function DELETE(request: Request) {
  if (crossOrigin(request)) return NextResponse.json({ error: "forbidden" }, { status: 403 });
  const requestHeaders = await headers();
  const actor = await actorFor(requestHeaders);
  if (!actor) return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  try {
    await cancelEmailChange(liveDeps(requestHeaders), actor);
    return NextResponse.json({ ok: true });
  } catch (error) {
    return errorResponse(error);
  }
}
