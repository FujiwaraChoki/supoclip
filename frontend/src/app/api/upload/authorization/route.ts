import { headers } from "next/headers";
import { NextResponse } from "next/server";

import { auth } from "@/lib/auth";
import { buildBackendAuthHeaders, hasSignedBackendAuth } from "@/lib/backend-auth";

export async function POST() {
  const session = await auth.api.getSession({ headers: await headers() });
  if (!session?.user?.id) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  if (!hasSignedBackendAuth()) {
    return NextResponse.json(
      {
        directUpload: false,
        reason: "signed_backend_auth_required",
      },
      {
        headers: {
          "Cache-Control": "no-store",
        },
      },
    );
  }

  const apiUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
  const normalizedApiUrl = apiUrl.replace(/\/$/, "");

  return NextResponse.json(
    {
      directUpload: true,
      uploadUrl: `${normalizedApiUrl}/upload`,
      headers: buildBackendAuthHeaders(session.user.id),
    },
    {
      headers: {
        "Cache-Control": "no-store",
      },
    },
  );
}
