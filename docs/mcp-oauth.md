# MCP OAuth for connectors

claude.ai, ChatGPT and other apps with custom connectors can't send an
`Authorization` header someone pastes in, so they sign in with OAuth 2.1. The
hosted MCP server (`mcp.supoclip.com`) is the authorization server; users
approve on www.supoclip.com; the access token is an ordinary SupoClip API key.
Plain API key Bearer auth keeps working unchanged.

OAuth is on when `MCP_OAUTH_SECRET` is set on both the backend and the MCP
server (and `SUPOCLIP_MCP_REQUIRE_BEARER_AUTH=true`). Without it the server
behaves as before: API keys only, OAuth discovery answers a JSON 404.

## Flow

1. The connector calls `POST /mcp` without a token and gets `401` with
   `WWW-Authenticate: Bearer ..., resource_metadata="https://mcp.supoclip.com/.well-known/oauth-protected-resource/mcp"`
   (`/sse` and `/` name their own metadata URL).
2. It reads that document (RFC 9728), then
   `/.well-known/oauth-authorization-server` (RFC 8414; also served with a path
   suffix).
3. `POST /register` (RFC 7591, open). Redirect URIs must be `https`, or `http`
   on `localhost`/`127.0.0.1`/`[::1]`, with no fragment. The registered scope is
   always `supoclip`. A client that registers only the `authorization_code`
   grant is accepted.
4. `GET /authorize` with PKCE S256 (`plain` is refused by the SDK). The redirect
   URI must exactly match a registered one. The MCP server stores the request and
   redirects to `https://www.supoclip.com/oauth/consent?request=<id>`.
5. The consent page (signed-out users sign in and come back) shows the app's
   name, where it will send you back to, and what it can do. Allow or Deny goes
   through the web app's `/api/oauth/requests/<id>/decision` proxy to the backend
   with the signed session headers, never an API key.
   - Deny: back to the app with `error=access_denied` and the `state`.
   - Allow (email verified, fewer than 25 active keys): a single-use code bound to
     the client id, redirect URI and code challenge; back to the app with
     `code` and `state`.
6. `POST /token` (`authorization_code` + `code_verifier`): the SDK checks the
   client, redirect URI, expiry and PKCE; the backend consumes the code and
   creates an API key named "<app> connector", sends the usual "new API key"
   email, and returns it once as the access token (`token_type: Bearer`,
   `scope: supoclip`, no `expires_in`, no refresh token).
7. The connector calls `/mcp` with `Authorization: Bearer sk_...`, checked like
   any key. Revoking the key at /settings/api-keys disconnects it: tool calls
   fail at once, and the MCP server stops accepting the key within 60 seconds
   (its verification cache).

## State

Nothing is kept in the MCP process or on its disk. It calls the backend's
`/internal/mcp-oauth/*` routes with `x-supoclip-mcp-secret: $MCP_OAUTH_SECRET`:

| What | Where | Lifetime |
|---|---|---|
| Registered clients | PostgreSQL `mcp_oauth_clients` (`backend/src/repositories/mcp_oauth_repository.py`) | Permanent |
| Pending authorize requests | Redis `mcp_oauth:request:<id>` | 10 minutes, consumed by Allow/Deny |
| Authorization codes | Redis `mcp_oauth:code:<sha256(code)>` | 10 minutes, consumed by the exchange (`GETDEL`) |
| Rate-limit counters | Redis `mcp_oauth:rate:*` | One window |
| Access tokens | `api_keys` (SHA-256 only), like every key | Until revoked |

Rate limits (per caller IP from `X-Real-IP`, else the last `X-Forwarded-For`
hop, plus a global cap): `/register` 20 per hour (1000 overall), `/token` 60
per 10 minutes (3000 overall). Answered with `429` and `Retry-After`.

Client secrets issued by dynamic registration are stored as given: the SDK
compares them in plain form. They are not account credentials; PKCE and the
exact redirect URI protect the code.

## Code

- `mcp/src/supoclip_mcp/oauth.py`: provider for the SDK's `create_auth_routes`,
  backend client, rate limiting, registration compatibility, discovery routes.
- `mcp/src/supoclip_mcp/http_app.py`, `auth.py`: route wiring and the 401 challenge.
- `backend/src/api/routes/mcp_oauth.py`, `backend/src/services/mcp_oauth_service.py`.
- `frontend/src/app/oauth/consent/page.tsx`, `frontend/src/app/api/oauth/requests/`.

## Operating it

- Set the same `MCP_OAUTH_SECRET` (`openssl rand -hex 32`) for the `backend` and
  `mcp` services, and `SUPOCLIP_APP_URL` for the MCP server if the web app is not
  `https://www.supoclip.com`.
- `/internal/mcp-oauth/*` is reachable wherever the backend is; it refuses
  callers without the secret. A reverse proxy may block `/internal/` on the
  public API host since only the MCP container needs it.
- Tests: `mcp/tests/test_oauth.py`, `backend/tests/unit/test_mcp_oauth.py`,
  `backend/tests/integration/test_mcp_oauth_flow.py`, and
  `mcp/tests/test_oauth_e2e.py` against running services (see its docstring).
