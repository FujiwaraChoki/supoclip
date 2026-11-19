#!/bin/bash

# SupoClip - Quick Start Script
# This script helps you start SupoClip with a single command

set -e  # Exit on error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo "============================================"
echo "  SupoClip - AI Video Clipping Tool"
echo "============================================"
echo ""

# Check if .env file exists
if [ ! -f .env ]; then
    echo -e "${RED}Error: .env file not found!${NC}"
    echo ""
    echo "Please create a .env file with your API keys:"
    echo "  1. Copy the template: cp .env.example .env"
    echo "  2. Or use the provided .env file"
    echo "  3. Edit .env and add your API keys:"
    echo "     - ASSEMBLY_AI_API_KEY (required)"
    echo "     - OPENAI_API_KEY, GOOGLE_API_KEY, ANTHROPIC_API_KEY, or OPENROUTER_API_KEY"
    echo "     - OR set LLM=ollama:<model> (optional: OLLAMA_BASE_URL, OLLAMA_API_KEY)"
    echo ""
    exit 1
fi

# Check if required API keys are set
source .env

# Match Compose's host port, including its legacy deployment fallback.
FRONTEND_PORT_EFFECTIVE="${FRONTEND_PORT:-3001}"
FRONTEND_LOCAL_URL="http://localhost:${FRONTEND_PORT_EFFECTIVE}"

# When TAILSCALE=true, bind the published ports to the Tailscale address (or all
# interfaces if the CLI is unavailable) and advertise that reachable URL instead
# of localhost. Otherwise keep the loopback-only bind.
FRONTEND_BIND="127.0.0.1"
BACKEND_BIND="127.0.0.1"
FRONTEND_URL="$FRONTEND_LOCAL_URL"
BACKEND_URL="http://localhost:8000"
if [ "${TAILSCALE:-false}" = "true" ]; then
    TAILSCALE_IP="$(tailscale ip -4 2>/dev/null | head -n1)"
    if [ -n "$TAILSCALE_IP" ]; then
        FRONTEND_BIND="$TAILSCALE_IP"
        BACKEND_BIND="$TAILSCALE_IP"
        FRONTEND_URL="http://${TAILSCALE_IP}:${FRONTEND_PORT_EFFECTIVE}"
        BACKEND_URL="http://${TAILSCALE_IP}:8000"
    else
        echo -e "${YELLOW}Warning: TAILSCALE=true but 'tailscale ip' returned no address; binding to all interfaces${NC}"
        echo ""
        FRONTEND_BIND="0.0.0.0"
        BACKEND_BIND="0.0.0.0"
        FRONTEND_URL="http://localhost:${FRONTEND_PORT_EFFECTIVE}"
        BACKEND_URL="http://localhost:8000"
    fi
fi
export FRONTEND_BIND BACKEND_BIND

# Add an origin to the auth allow-list and CORS without duplicating it.
add_origin() {
    local origin="$1"
    [ -z "$origin" ] && return 0
    BETTER_AUTH_TRUSTED_ORIGINS="${BETTER_AUTH_TRUSTED_ORIGINS:+${BETTER_AUTH_TRUSTED_ORIGINS},}${origin}"
    CORS_ORIGINS="${CORS_ORIGINS:-http://localhost:${FRONTEND_PORT_EFFECTIVE},http://sp.localhost:${FRONTEND_PORT_EFFECTIVE},http://supoclip.localhost:${FRONTEND_PORT_EFFECTIVE}}"
    case ",${CORS_ORIGINS}," in
        *",${origin},"*) ;;
        *) CORS_ORIGINS="${CORS_ORIGINS},${origin}" ;;
    esac
}

# Let auth and CORS accept the Tailscale origin without breaking localhost.
if [ "$FRONTEND_BIND" != "127.0.0.1" ] && [ "$FRONTEND_URL" != "$FRONTEND_LOCAL_URL" ]; then
    add_origin "$FRONTEND_URL"
    export BETTER_AUTH_TRUSTED_ORIGINS CORS_ORIGINS
fi

# Cloudflare Tunnel: accept the public https origin and start the opt-in service.
CLOUDFLARE_URL=""
CLOUDFLARE_MCP_URL=""
if [ -n "${CLOUDFLARE_PUBLIC_HOSTNAME:-}" ]; then
    CLOUDFLARE_URL="https://${CLOUDFLARE_PUBLIC_HOSTNAME}"
    add_origin "$CLOUDFLARE_URL"
    export BETTER_AUTH_TRUSTED_ORIGINS CORS_ORIGINS
    if [ -n "${CLOUDFLARE_MCP_HOSTNAME:-}" ]; then
        CLOUDFLARE_MCP_URL="https://${CLOUDFLARE_MCP_HOSTNAME}/mcp"
        add_origin "https://${CLOUDFLARE_MCP_HOSTNAME}"
        export BETTER_AUTH_TRUSTED_ORIGINS CORS_ORIGINS
    fi
    CF_CONFIG_DIR="${CLOUDFLARE_CONFIG_DIR:-$HOME/.cloudflared}"
    CF_CREDS="${CF_CONFIG_DIR}/${CLOUDFLARE_TUNNEL_ID:-}.json"
    if [ -z "${CLOUDFLARE_TUNNEL_ID:-}" ] || [ ! -f "$CF_CREDS" ]; then
        echo -e "${YELLOW}Warning: CLOUDFLARE_PUBLIC_HOSTNAME is set but CLOUDFLARE_TUNNEL_ID is empty or its credentials file is missing in ${CF_CONFIG_DIR}; the tunnel will not start${NC}"
        echo ""
    else
        # Render the ingress config next to the credentials so the mounted
        # directory holds both; credentials-file path matches the container mount.
        {
            echo "tunnel: ${CLOUDFLARE_TUNNEL_ID}"
            echo "credentials-file: /etc/cloudflared/${CLOUDFLARE_TUNNEL_ID}.json"
            echo "ingress:"
            echo "  - hostname: ${CLOUDFLARE_PUBLIC_HOSTNAME}"
            echo "    service: http://frontend:3107"
            if [ -n "${CLOUDFLARE_MCP_HOSTNAME:-}" ]; then
                echo "  - hostname: ${CLOUDFLARE_MCP_HOSTNAME}"
                echo "    service: http://mcp:9100"
            fi
            echo "  - service: http_status:404"
        } > "${CF_CONFIG_DIR}/config.yml"
        export CLOUDFLARE_CONFIG_DIR="$CF_CONFIG_DIR"
        COMPOSE_PROFILES="${COMPOSE_PROFILES:+${COMPOSE_PROFILES},}cloudflare"
        export COMPOSE_PROFILES
    fi
fi

if [ -n "${LLM:-}" ]; then
    case "$LLM" in
        google:*|google-gla:*|openai:*|openrouter:*|anthropic:*|ollama:*)
            ;;
        *)
            echo -e "${YELLOW}Warning: Unsupported LLM value '$LLM'${NC}"
            echo "Use google-gla:*, openai:*, openrouter:*, anthropic:*, or ollama:*"
            echo ""
            ;;
    esac
fi

if [ -z "$ASSEMBLY_AI_API_KEY" ]; then
    echo -e "${YELLOW}Warning: ASSEMBLY_AI_API_KEY is not set in .env${NC}"
    echo "Video transcription will not work without this key."
    echo ""
fi

if [ "${LLM:-}" = "ollama:" ]; then
    echo -e "${YELLOW}Warning: LLM=ollama: is missing a model name${NC}"
    echo "Use a value like LLM=ollama:gpt-oss:20b"
    echo ""
elif [[ "${LLM:-}" == ollama:* ]] && [ -z "${OLLAMA_BASE_URL:-}" ]; then
    echo "Ollama base URL is not set; SupoClip will use its local/Docker default."
    echo ""
fi

if [ -z "${OPENAI_API_KEY:-}" ] && \
   [ -z "${GOOGLE_API_KEY:-}" ] && \
   [ -z "${ANTHROPIC_API_KEY:-}" ] && \
   [ -z "${OPENROUTER_API_KEY:-}" ]; then
    if [[ "${LLM:-}" == ollama:* ]]; then
        :
    else
    echo -e "${YELLOW}Warning: No AI provider API key is set in .env${NC}"
    echo "You need at least one of: OPENAI_API_KEY, GOOGLE_API_KEY, ANTHROPIC_API_KEY, OPENROUTER_API_KEY, or LLM=ollama:<model>"
    echo ""
    fi
fi

# Check if Docker is running
if ! docker info > /dev/null 2>&1; then
    echo -e "${RED}Error: Docker is not running!${NC}"
    echo "Please start Docker Desktop and try again."
    echo ""
    exit 1
fi

# Check if docker-compose is available
if ! command -v docker-compose &> /dev/null && ! docker compose version &> /dev/null; then
    echo -e "${RED}Error: docker-compose is not installed!${NC}"
    echo "Please install Docker Compose and try again."
    echo ""
    exit 1
fi

# Determine which docker compose command to use
if docker compose version &> /dev/null; then
    DOCKER_COMPOSE="docker compose"
else
    DOCKER_COMPOSE="docker-compose"
fi

echo -e "${GREEN}Starting SupoClip...${NC}"
echo ""

# Build and start containers
echo "Building and starting Docker containers..."
echo "(This may take a few minutes on the first run)"
echo ""

$DOCKER_COMPOSE up -d --build

echo ""
echo -e "${GREEN}SupoClip is starting up!${NC}"
echo ""
echo "Services will be available at:"
echo "  - Frontend:  $FRONTEND_URL"
echo "  - Backend:   $BACKEND_URL"
echo "  - API Docs:  $BACKEND_URL/docs"
if [ -n "$CLOUDFLARE_URL" ]; then
    echo "  - Public:    $CLOUDFLARE_URL (Cloudflare Tunnel)"
    if [ -n "$CLOUDFLARE_MCP_URL" ]; then
        echo "  - MCP:       $CLOUDFLARE_MCP_URL (Cloudflare Tunnel)"
    fi
fi
echo ""
echo "To view logs, run:"
echo "  $DOCKER_COMPOSE logs -f"
echo ""
echo "To stop all services, run:"
echo "  $DOCKER_COMPOSE down"
echo ""
echo "Waiting for services to be healthy..."

# Wait for services to be healthy
sleep 5

# Check if services are running
if $DOCKER_COMPOSE ps | grep -q "Up"; then
    echo -e "${GREEN}Services are starting successfully!${NC}"
    echo ""
    echo "You can now:"
    echo "  1. Open $FRONTEND_URL in your browser"
    echo "  2. View logs: $DOCKER_COMPOSE logs -f"
    echo "  3. Stop services: $DOCKER_COMPOSE down"
else
    echo -e "${YELLOW}Services are starting... Check logs if you encounter issues:${NC}"
    echo "  $DOCKER_COMPOSE logs -f"
fi

echo ""
echo "============================================"
