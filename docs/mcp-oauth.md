# MCP OAuth (draft, not built)

Status: design draft. Today the hosted MCP server (`mcp.supoclip.com`) only
accepts a SupoClip API key as a Bearer token, and answers OAuth discovery with
a JSON 404 that says so. claude.ai and ChatGPT connectors can't paste a header,
so they need the OAuth flow below.

## What the connectors do

1. Call the MCP URL, get `401` with `WWW-Authenticate: Bearer resource_metadata=...`.
2. Read `/.well-known/oauth-protected-resource`, then the authorization server's
   `/.well-known/oauth-authorization-server`.
3. Register themselves with dynamic client registration (`POST /register`).
4. Send the user to `/authorize` (authorization code + PKCE S256), receive a
   code on their callback, exchange it at `/token`.
5. Call the MCP URL with `Authorization: Bearer <access token>`.

## Proposed design

- **The MCP server is the authorization server.** The installed SDK
  (`mcp` 1.30) ships `OAuthAuthorizationServerProvider` and
  `create_auth_routes()`, which implement `/register`, `/authorize`, `/token`,
  `/revoke` and both metadata documents, including PKCE checks. We implement the
  provider; `http_app.py` mounts its routes next to `/mcp` and `/sse`, and the
  401 gains `resource_metadata`.
- **Consent on www.supoclip.com.** `provider.authorize()` stores the pending
  request (client, redirect URI, PKCE challenge, state, scopes) under a random
  id and redirects to `https://www.supoclip.com/oauth/consent?request=<id>`.
  The Next.js page uses the Better Auth session (sign-in first if needed), shows
  the client name and redirect host, and on Allow calls a backend endpoint.
- **The token is an API key.** On Allow, the backend creates a normal API key
  named after the client ("Claude (claude.ai)"), stores it encrypted or hashed
  against a one-time authorization code, and returns the redirect URL with
  `code` and `state`. `/token` swaps the code for that key. The key appears on
  `/settings/api-keys` and revoking it there disconnects the connector. The MCP
  server keeps validating tokens exactly as today, so both auth paths share one
  check.
- **Storage.** No local disk (a restart or second instance would lose it).
  Registered clients, pending requests and codes live in Redis with TTLs
  (requests and codes ~10 minutes, single use) or in PostgreSQL behind new
  internal backend endpoints authenticated with `BACKEND_AUTH_SECRET`. Redis is
  simpler; clients would need a long TTL or Postgres so a connector that
  registered months ago still works.

## Open questions

1. **Token lifetime.** Hand out the API key with no expiry and no refresh token
   (simplest, revocable in settings), or short-lived access tokens with refresh
   tokens (more moving parts, a new token table)?
2. **Scopes.** Full account access like an API key, or a narrower scope (for
   example no deletes)? API keys have no scopes today, so anything narrower is
   backend work.
3. **Consent UI.** A new `/oauth/consent` page in the web app: what does it
   show, and how does it look? Does the iOS app ever need it?
4. **Key limits and email.** Each connection creates a key: count it toward the
   25-key limit, and send the "new API key" email (the current create path
   does)? Reuse an existing key for the same client and user instead?
5. **Who may register.** Open dynamic registration (any client, any HTTPS
   redirect URI) as the spec expects, or an allowlist of redirect hosts
   (claude.ai, chatgpt.com, ...)? Rate limits on `/register`.
6. **Storage choice.** Redis in the MCP container (new dependency and network
   access) or backend endpoints over PostgreSQL?

Rough size once these are decided: provider and routes in `mcp/` (about a day),
backend endpoints for consent and code exchange, the consent page with
translations, and tests for the full flow.
