import { NextRequest, NextResponse } from "next/server";
import { SESv2Client, SendEmailCommand } from "@aws-sdk/client-sesv2";

const EMAIL_REGEX = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export async function POST(request: NextRequest) {
  try {
    const { email } = await request.json();

    if (!email || typeof email !== "string") {
      return NextResponse.json({ error: "Email is required" }, { status: 400 });
    }

    const normalizedEmail = email.trim().toLowerCase();
    if (!EMAIL_REGEX.test(normalizedEmail)) {
      return NextResponse.json({ error: "Invalid email format" }, { status: 400 });
    }

    const region = process.env.AWS_REGION;
    const accessKeyId = process.env.AWS_ACCESS_KEY_ID;
    const secretAccessKey = process.env.AWS_SECRET_ACCESS_KEY;
    if (!region || !accessKeyId || !secretAccessKey) {
      return NextResponse.json({ error: "Email service is not configured" }, { status: 503 });
    }

    const ses = new SESv2Client({
      region,
      credentials: { accessKeyId, secretAccessKey },
    });

    try {
      await ses.send(
        new SendEmailCommand({
          FromEmailAddress: process.env.SES_FROM_EMAIL ?? "SupoClip <noreply@shiori.ai>",
          Destination: { ToAddresses: [normalizedEmail] },
          Content: {
            Simple: {
              Subject: { Data: "Welcome to the SupoClip waitlist", Charset: "UTF-8" },
              Body: {
                Html: {
                  Data: `
                    <p>Thanks for joining the SupoClip waitlist.</p>
                    <p>We will email you when early access is available.</p>
                  `,
                  Charset: "UTF-8",
                },
              },
            },
          },
        })
      );
    } catch (error) {
      console.error("SES error:", error);
      return NextResponse.json(
        { error: "Failed to send confirmation email" },
        { status: 500 }
      );
    }

    return NextResponse.json({ message: "Successfully added to waitlist" });
  } catch (error) {
    console.error("Waitlist signup error:", error);
    return NextResponse.json({ error: "Internal server error" }, { status: 500 });
  }
}
