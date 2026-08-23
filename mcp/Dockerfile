# syntax=docker/dockerfile:1.7
FROM python:3.12-slim-bookworm

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    UV_SYSTEM_PYTHON=1 \
    UV_LINK_MODE=copy

# Copy uv binary directly from official image
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /root/.local/bin/
ENV PATH="/root/.local/bin:${PATH}"

COPY pyproject.toml uv.lock* ./
COPY src/ ./src/
COPY README.md ./

# Install package with UV cache mount
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install .

EXPOSE 9100

CMD ["supoclip-mcp"]

