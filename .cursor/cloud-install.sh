#!/usr/bin/env bash
# Idempotent Cloud Agent install: system packages, Python 3.11, Node/pnpm, backend, frontend, MCP.
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive
export COREPACK_ENABLE_DOWNLOAD_PROMPT=0
if [ -d /workspace/backend ]; then
  ROOT=/workspace
else
  ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fi

sudo apt-get update
sudo apt-get install -y --no-install-recommends \
  postgresql \
  postgresql-contrib \
  redis-server \
  ffmpeg \
  libgl1 \
  libglib2.0-0 \
  libgomp1 \
  fonts-noto-core \
  fonts-noto-cjk \
  fonts-noto-color-emoji \
  fontconfig \
  ca-certificates \
  curl \
  unzip \
  build-essential \
  pkg-config \
  libffi-dev \
  openssl \
  git

install_node() {
  if [ -x /usr/local/bin/node ]; then
    return
  fi
  local src=""
  if [ -x /home/ubuntu/.nvm/versions/node/v22.23.3/bin/node ]; then
    src=/home/ubuntu/.nvm/versions/node/v22.23.3/bin
  elif command -v node >/dev/null 2>&1; then
    src="$(dirname "$(readlink -f "$(command -v node)")")"
  else
    local ver=22.23.3
    curl -fsSL "https://nodejs.org/dist/v${ver}/node-v${ver}-linux-x64.tar.xz" -o /tmp/node-v${ver}.tar.xz
    sudo tar -xJf /tmp/node-v${ver}.tar.xz -C /usr/local --strip-components=1
    return
  fi
  sudo ln -sfn "${src}/node" /usr/local/bin/node
  sudo ln -sfn "${src}/npm" /usr/local/bin/npm
  sudo ln -sfn "${src}/npx" /usr/local/bin/npx
  sudo ln -sfn "${src}/corepack" /usr/local/bin/corepack
}

install_node
sudo corepack enable
sudo COREPACK_ENABLE_DOWNLOAD_PROMPT=0 corepack prepare pnpm@10.27.0 --activate
if [ ! -x /usr/local/bin/pnpm ]; then
  sudo ln -sfn "$(command -v pnpm)" /usr/local/bin/pnpm
fi

if [ ! -x /usr/local/bin/uv ]; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
  sudo ln -sfn "${HOME}/.local/bin/uv" /usr/local/bin/uv
  sudo ln -sfn "${HOME}/.local/bin/uvx" /usr/local/bin/uvx
fi

if [ ! -x /usr/local/bin/deno ]; then
  curl -fsSL https://deno.land/install.sh | sh
  sudo ln -sfn "${HOME}/.deno/bin/deno" /usr/local/bin/deno
fi

bash "${ROOT}/.cursor/cloud-services.sh" ensure

cd "${ROOT}/backend"
uv python install 3.11
uv sync --frozen --all-groups --extra cpu

cd "${ROOT}/frontend"
DATABASE_URL="postgresql://supoclip:supoclip_password@127.0.0.1:5432/supoclip" \
  pnpm install --frozen-lockfile

cd "${ROOT}/mcp"
if [ -f uv.lock ]; then
  uv sync --frozen
else
  uv sync
fi

sudo fc-cache -f >/dev/null
echo "cloud-install: ok"
