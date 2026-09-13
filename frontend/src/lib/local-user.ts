// Local-first mode: no login. Every request resolves to this single implicit
// user (must match backend/src/auth_headers.py's LOCAL_USER_ID exactly).
export const LOCAL_USER_ID = "local";

// Set REQUIRE_AUTH=true (server-side env, matches the backend's own flag) to
// restore real Better Auth session checks — e.g. for a hosted, multi-user
// deployment. Defaults to local-first (no auth) otherwise.
export const REQUIRE_AUTH = process.env.REQUIRE_AUTH === "true";
