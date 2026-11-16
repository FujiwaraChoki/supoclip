-- OAuth clients registered with the hosted MCP server (RFC 7591 dynamic
-- registration, e.g. claude.ai and ChatGPT connectors). The access token a
-- client receives is an ordinary API key, so no tokens are stored here.
CREATE TABLE IF NOT EXISTS mcp_oauth_clients (
    client_id VARCHAR(64) PRIMARY KEY,
    -- Issued at registration and compared on /token. Not an account credential:
    -- PKCE and the exact redirect URI protect the code exchange.
    client_secret VARCHAR(128),
    client_name VARCHAR(200),
    -- The registration response as returned to the client (without the secret).
    metadata JSONB NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_used_at TIMESTAMP WITH TIME ZONE
);

CREATE INDEX IF NOT EXISTS idx_mcp_oauth_clients_created_at ON mcp_oauth_clients (created_at)
