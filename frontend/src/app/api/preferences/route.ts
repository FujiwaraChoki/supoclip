import { NextRequest, NextResponse } from "next/server";
import { getPrismaClient } from "@/server/prisma";
import { isClipLength } from "@/lib/clip-duration";
import { parseCreateSettings } from "@/lib/create-settings";
import { getServerSession } from "@/server/session";

// GET /api/preferences - Get user preferences
export async function GET() {
  try {
    const session = await getServerSession();

    if (!session?.user?.id) {
      return NextResponse.json(
        { error: "Unauthorized" },
        { status: 401 }
      );
    }

    const prisma = getPrismaClient();
    const user = await prisma.user.findUnique({
      where: { id: session.user.id },
      select: {
        default_font_family: true,
        default_font_size: true,
        default_font_color: true,
        notify_on_completion: true,
        default_clip_length: true,
        create_settings: true,
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
      clipLength: user.default_clip_length || "auto",
      createSettings: parseCreateSettings(user.create_settings),
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

    if (!session?.user?.id) {
      return NextResponse.json(
        { error: "Unauthorized" },
        { status: 401 }
      );
    }

    const body = await request.json();
    if (!body || typeof body !== "object" || Array.isArray(body)) {
      return NextResponse.json({ error: "Expected a preferences object" }, { status: 400 });
    }
    const { fontFamily, fontSize, fontColor, notifyOnCompletion, clipLength, createSettings } = body;

    if (clipLength !== undefined && !isClipLength(clipLength)) {
      return NextResponse.json({ error: "Invalid clipLength" }, { status: 400 });
    }

    // Validate inputs
    if (fontFamily !== undefined && (typeof fontFamily !== "string" || !fontFamily.trim())) {
      return NextResponse.json(
        { error: "Invalid fontFamily" },
        { status: 400 }
      );
    }

    if (fontSize !== undefined && (typeof fontSize !== "number" || !Number.isInteger(fontSize) || fontSize < 12 || fontSize > 48)) {
      return NextResponse.json(
        { error: "Invalid fontSize (must be between 12 and 48)" },
        { status: 400 }
      );
    }

    if (fontColor !== undefined && (typeof fontColor !== "string" || !/^#[0-9A-Fa-f]{6}$/.test(fontColor))) {
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

    if (createSettings !== undefined && (typeof createSettings !== "object" || createSettings === null || Array.isArray(createSettings))) {
      return NextResponse.json({ error: "Invalid createSettings" }, { status: 400 });
    }

    const prisma = getPrismaClient();
    const updatedUser = await prisma.user.update({
      where: { id: session.user.id },
      data: {
        ...(clipLength !== undefined && { default_clip_length: clipLength }),
        ...(createSettings !== undefined && { create_settings: parseCreateSettings(createSettings) }),
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
        default_clip_length: true,
        create_settings: true,
      },
    });

    return NextResponse.json({
      fontFamily: updatedUser.default_font_family,
      fontSize: updatedUser.default_font_size,
      fontColor: updatedUser.default_font_color,
      notifyOnCompletion: updatedUser.notify_on_completion,
      clipLength: updatedUser.default_clip_length || "auto",
      createSettings: parseCreateSettings(updatedUser.create_settings),
    });
  } catch (error) {
    console.error("Error updating preferences:", error);
    return NextResponse.json(
      { error: "Internal server error" },
      { status: 500 }
    );
  }
}
