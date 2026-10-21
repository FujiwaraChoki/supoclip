# syntax=docker/dockerfile:1.7

# Use Node.js with pnpm as the package manager
FROM node:22-bookworm-slim AS base

ENV PNPM_HOME="/pnpm"
ENV PATH="$PNPM_HOME:$PATH"

RUN npm install -g pnpm@10.27.0

# Dependencies stage
FROM base AS deps
WORKDIR /app

# Install OpenSSL for Prisma postinstall
RUN apt-get update && apt-get install -y openssl && rm -rf /var/lib/apt/lists/*

# Copy dependency manifests only so app/schema changes don't invalidate deps
COPY package.json pnpm-lock.yaml ./
RUN --mount=type=cache,id=pnpm,target=/pnpm/store \
    pnpm install --frozen-lockfile --ignore-scripts --store-dir /pnpm/store

# Development stage
FROM base AS development
WORKDIR /app

# Install OpenSSL (required for Prisma) and curl for healthchecks
RUN apt-get update && apt-get install -y openssl curl && rm -rf /var/lib/apt/lists/*

# Copy dependencies from deps stage
COPY --from=deps /app/node_modules ./node_modules
COPY package.json pnpm-lock.yaml ./

# Copy application code (will be overridden by volume mounts in dev)
COPY . .

# Generate Prisma client
RUN pnpm exec prisma generate

EXPOSE 3107

ENV PORT=3107
ENV HOSTNAME="0.0.0.0"
ENV NEXT_TELEMETRY_DISABLED=1

# Start development server with hot-reload
# Run prisma generate first to handle volume mount scenarios where node_modules is overwritten
CMD ["sh", "-c", "pnpm exec prisma generate && pnpm run dev"]

# Builder stage (for production)
FROM base AS builder
WORKDIR /app

# Install OpenSSL (required for Prisma)
# ca-certificates: the PostHog CLI (source map upload) needs system roots for TLS.
RUN apt-get update && apt-get install -y openssl ca-certificates && rm -rf /var/lib/apt/lists/*

COPY --from=deps /app/node_modules ./node_modules
COPY . .

# Generate Prisma client before building
RUN pnpm exec prisma generate

# Build the Next.js app
ENV NEXT_TELEMETRY_DISABLED=1
# Skip linting during build (generated Prisma code has lint warnings)
ENV SKIP_LINT=1

# Build-time env vars (NEXT_PUBLIC_* must be present at build time)
ARG NEXT_PUBLIC_API_URL=http://localhost:8000
ARG NEXT_PUBLIC_APP_URL=http://localhost:3107
ARG NEXT_PUBLIC_SELF_HOST=true
ARG NEXT_PUBLIC_PRO_PRICE_MONTHLY=9.99
ARG NEXT_PUBLIC_SCALE_PRICE_MONTHLY=24.99
ARG NEXT_PUBLIC_PRO_PRICE_ANNUAL=99.90
ARG NEXT_PUBLIC_SCALE_PRICE_ANNUAL=249.90
ARG NEXT_PUBLIC_PRO_PLAN_MINUTE_LIMIT=600
ARG NEXT_PUBLIC_SCALE_PLAN_MINUTE_LIMIT=1500
ARG NEXT_PUBLIC_SOCIAL_PUBLISHING=
ARG NEXT_PUBLIC_DATAFAST_WEBSITE_ID=
ARG NEXT_PUBLIC_DATAFAST_DOMAIN=
ARG NEXT_PUBLIC_DATAFAST_ALLOW_LOCALHOST=false
ARG NEXT_PUBLIC_POSTHOG_KEY=
ARG NEXT_PUBLIC_POSTHOG_HOST=https://eu.i.posthog.com
# Source map upload to PostHog; skipped unless both are set. Kept as ARGs (not
# ENV) so they stay out of the runtime image.
ARG POSTHOG_PERSONAL_API_KEY=
ARG POSTHOG_PROJECT_ID=
ARG POSTHOG_RELEASE_VERSION=
ENV NEXT_PUBLIC_API_URL=${NEXT_PUBLIC_API_URL}
ENV NEXT_PUBLIC_APP_URL=${NEXT_PUBLIC_APP_URL}
ENV NEXT_PUBLIC_SELF_HOST=${NEXT_PUBLIC_SELF_HOST}
ENV NEXT_PUBLIC_PRO_PRICE_MONTHLY=${NEXT_PUBLIC_PRO_PRICE_MONTHLY}
ENV NEXT_PUBLIC_SCALE_PRICE_MONTHLY=${NEXT_PUBLIC_SCALE_PRICE_MONTHLY}
ENV NEXT_PUBLIC_PRO_PRICE_ANNUAL=${NEXT_PUBLIC_PRO_PRICE_ANNUAL}
ENV NEXT_PUBLIC_SCALE_PRICE_ANNUAL=${NEXT_PUBLIC_SCALE_PRICE_ANNUAL}
ENV NEXT_PUBLIC_PRO_PLAN_MINUTE_LIMIT=${NEXT_PUBLIC_PRO_PLAN_MINUTE_LIMIT}
ENV NEXT_PUBLIC_SCALE_PLAN_MINUTE_LIMIT=${NEXT_PUBLIC_SCALE_PLAN_MINUTE_LIMIT}
ENV NEXT_PUBLIC_SOCIAL_PUBLISHING=${NEXT_PUBLIC_SOCIAL_PUBLISHING}
ENV NEXT_PUBLIC_DATAFAST_WEBSITE_ID=${NEXT_PUBLIC_DATAFAST_WEBSITE_ID}
ENV NEXT_PUBLIC_DATAFAST_DOMAIN=${NEXT_PUBLIC_DATAFAST_DOMAIN}
ENV NEXT_PUBLIC_DATAFAST_ALLOW_LOCALHOST=${NEXT_PUBLIC_DATAFAST_ALLOW_LOCALHOST}
ENV NEXT_PUBLIC_POSTHOG_KEY=${NEXT_PUBLIC_POSTHOG_KEY}
ENV NEXT_PUBLIC_POSTHOG_HOST=${NEXT_PUBLIC_POSTHOG_HOST}

RUN pnpm run build

# Runner stage (for production)
# Next's standalone output is a Node server, so production only needs Node.
FROM node:22-bookworm-slim AS runner
WORKDIR /app

ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1

# Install OpenSSL (required for Prisma) and curl for healthchecks
RUN apt-get update && apt-get install -y openssl curl && rm -rf /var/lib/apt/lists/*

# Create a non-root user
RUN groupadd --system --gid 1001 nodejs
RUN useradd --system --uid 1001 --gid nodejs nextjs

# Copy the built application
COPY --from=builder /app/public ./public
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static

# Copy Prisma generated client and schema
COPY --from=builder --chown=nextjs:nodejs /app/src/generated/prisma ./src/generated/prisma
COPY --from=builder /app/prisma ./prisma

# The production Compose service bind-mounts /app/src for local-development
# compatibility. Keep the native Prisma engine outside that mount and point
# Prisma at the immutable copy so authentication cannot lose database access.
COPY --from=builder --chown=nextjs:nodejs \
    /app/src/generated/prisma/libquery_engine-debian-openssl-3.0.x.so.node \
    /app/prisma-engine/libquery_engine-debian-openssl-3.0.x.so.node
ENV PRISMA_QUERY_ENGINE_LIBRARY=/app/prisma-engine/libquery_engine-debian-openssl-3.0.x.so.node

USER nextjs

EXPOSE 3107

ENV PORT=3107
ENV HOSTNAME="0.0.0.0"

# Start the application
CMD ["node", "server.js"]
