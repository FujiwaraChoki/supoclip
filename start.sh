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
    echo "     - OPENAI_API_KEY or GOOGLE_API_KEY or ANTHROPIC_API_KEY"
    echo "     - OR set LLM=ollama:<model> (optional: OLLAMA_BASE_URL, OLLAMA_API_KEY)"
    echo ""
    exit 1
fi

# Check if required API keys are set
source .env

if [ -z "$ASSEMBLY_AI_API_KEY" ]; then
    echo -e "${YELLOW}Warning: ASSEMBLY_AI_API_KEY is not set in .env${NC}"
    echo "Video transcription will not work without this key."
    echo ""
fi

if [ -z "$OPENAI_API_KEY" ] && [ -z "$GOOGLE_API_KEY" ] && [ -z "$ANTHROPIC_API_KEY" ]; then
    if [[ "${LLM:-}" == ollama:* ]]; then
        :
    else
    echo -e "${YELLOW}Warning: No AI provider API key is set in .env${NC}"
    echo "You need at least one of: OPENAI_API_KEY, GOOGLE_API_KEY, ANTHROPIC_API_KEY, or LLM=ollama:<model>"
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
echo "  - Frontend:  http://localhost:3000"
echo "  - Backend:   http://localhost:8000"
echo "  - API Docs:  http://localhost:8000/docs"
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
    echo "  1. Open http://localhost:3000 in your browser"
    echo "  2. View logs: $DOCKER_COMPOSE logs -f"
    echo "  3. Stop services: $DOCKER_COMPOSE down"
else
    echo -e "${YELLOW}Services are starting... Check logs if you encounter issues:${NC}"
    echo "  $DOCKER_COMPOSE logs -f"
fi

echo ""
echo "============================================"
