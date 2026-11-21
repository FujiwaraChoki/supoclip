import { NextResponse } from "next/server";
import { getServerSession } from "@/server/session";
import { acceptTeamInvitation, TeamError } from "@/server/teams";
import { WORKSPACE_COOKIE } from "@/server/workspace";

const COOKIE_MAX_AGE_SECONDS = 60 * 60 * 24 * 365;

export async function POST(
  _request: Request,
  context: { params: Promise<{ token: string }> },
) {
  const session = await getServerSession();
  if (!session?.user?.id || !session.user.email) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const { token } = await context.params;
  try {
    const joined = await acceptTeamInvitation({
      rawToken: token,
      userId: session.user.id,
      userEmail: session.user.email,
    });
    const response = NextResponse.json({ joined });
    response.cookies.set(WORKSPACE_COOKIE, joined.teamId, {
      httpOnly: true,
      sameSite: "lax",
      secure: process.env.NODE_ENV === "production",
      path: "/",
      maxAge: COOKIE_MAX_AGE_SECONDS,
    });
    return response;
  } catch (error) {
    if (error instanceof TeamError) {
      return NextResponse.json(
        { error: error.message, code: error.code, ...(error.details ?? {}) },
        { status: error.status },
      );
    }
    console.error("Failed to accept team invitation", error);
    return NextResponse.json(
      { error: "Failed to accept invitation" },
      { status: 500 },
    );
  }
}
