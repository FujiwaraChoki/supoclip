import { headers } from "next/headers";

import { auth } from "@/lib/auth";
import { LOCAL_USER_ID, REQUIRE_AUTH } from "@/lib/local-user";

const LOCAL_SESSION = {
  user: {
    id: LOCAL_USER_ID,
    email: "local@supoclip.local",
    name: "Local User",
    is_admin: true,
  },
};

/**
 * Local-first default: no login, so this always resolves to a single
 * implicit local session instead of checking a real Better Auth session.
 * Set REQUIRE_AUTH=true to restore real session checks (e.g. a hosted,
 * multi-user deployment).
 */
export async function getServerSession() {
  if (!REQUIRE_AUTH) {
    return LOCAL_SESSION;
  }
  return auth.api.getSession({ headers: await headers() });
}
