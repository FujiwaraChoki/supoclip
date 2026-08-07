import { NextRequest, NextResponse } from "next/server";
import { getPrismaClient } from "@/server/prisma";
import { getServerSession } from "@/server/session";

// GET /api/preferences - Get user preferences
export async function GET() {
  try {
    const session = await getServerSession();
    const userId = session?.user?.id || "local-user";

    const prisma = getPrismaClient();
    const user = await prisma.user.findUnique({
      where: { id: userId },
      select: {
        default_font_family: true,
        default_font_size: true,
        default_font_color: true,
        notify_on_completion: true,
      },
    });

    if (!user) {
      return NextResponse.json(
        { error: "User not found" },
        { status: 404 }
      );
    }

    return NextResponse.json({
      fontFamily: user.default_font_family || "TikTokSans-Regular",
      fontSize: user.default_font_size || 24,
      fontColor: user.default_font_color || "#FFFFFF",
      notifyOnCompletion: user.notify_on_completion ?? true,
    });
  } catch (error) {
    console.error("Error fetching preferences:", error);
    return NextResponse.json(
      { error: "Internal server error" },
      { status: 500 }
    );
  }
}

// PATCH /api/preferences - Update user preferences
export async function PATCH(request: NextRequest) {
  try {
    const session = await getServerSession();
    const userId = session?.user?.id || "local-user";

    const body = await request.json();
    const { fontFamily, fontSize, fontColor, notifyOnCompletion } = body;

    // Validate inputs
    if (fontFamily && typeof fontFamily !== "string") {
      return NextResponse.json(
        { error: "Invalid fontFamily" },
        { status: 400 }
      );
    }

    if (fontSize && (typeof fontSize !== "number" || fontSize < 12 || fontSize > 48)) {
      return NextResponse.json(
        { error: "Invalid fontSize (must be between 12 and 48)" },
        { status: 400 }
      );
    }

    if (fontColor && !/^#[0-9A-Fa-f]{6}$/.test(fontColor)) {
      return NextResponse.json(
        { error: "Invalid fontColor (must be hex format like #FFFFFF)" },
        { status: 400 }
      );
    }

    if (
      notifyOnCompletion !== undefined &&
      typeof notifyOnCompletion !== "boolean"
    ) {
      return NextResponse.json(
        { error: "Invalid notifyOnCompletion" },
        { status: 400 }
      );
    }

    const prisma = getPrismaClient();
    const updatedUser = await prisma.user.update({
      where: { id: userId },
      data: {
        ...(fontFamily !== undefined && { default_font_family: fontFamily }),
        ...(fontSize !== undefined && { default_font_size: fontSize }),
        ...(fontColor !== undefined && { default_font_color: fontColor }),
        ...(notifyOnCompletion !== undefined && {
          notify_on_completion: notifyOnCompletion,
        }),
      },
      select: {
        default_font_family: true,
        default_font_size: true,
        default_font_color: true,
        notify_on_completion: true,
      },
    });

    return NextResponse.json({
      fontFamily: updatedUser.default_font_family,
      fontSize: updatedUser.default_font_size,
      fontColor: updatedUser.default_font_color,
      notifyOnCompletion: updatedUser.notify_on_completion,
    });
  } catch (error) {
    console.error("Error updating preferences:", error);
    return NextResponse.json(
      { error: "Internal server error" },
      { status: 500 }
    );
  }
}
