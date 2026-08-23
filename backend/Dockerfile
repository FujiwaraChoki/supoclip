# syntax=docker/dockerfile:1.7
# Use NVIDIA CUDA 12 base image for GPU support
FROM nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04

# Avoid tzdata interactive prompt and configure UV for container environments
ENV DEBIAN_FRONTEND=noninteractive \
    UV_LINK_MODE=copy

# Copy uv and uvx binaries directly from the official image
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /root/.local/bin/

# Install system dependencies with BuildKit APT cache mounts
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
    software-properties-common \
    && add-apt-repository ppa:deadsnakes/ppa -y \
    && apt-get update && apt-get install -y --no-install-recommends \
    python3.11 \
    python3.11-venv \
    python3.11-dev \
    ffmpeg \
    curl \
    unzip \
    git \
    libsndfile1 \
    libgl1 \
    libglib2.0-0 \
    libgles2 \
    fonts-noto-color-emoji \
    fontconfig \
    && fc-cache -f

# Install deno (required by yt-dlp for YouTube JS challenge solving)
RUN curl -fsSL https://deno.land/install.sh | sh
ENV DENO_DIR=/root/.deno
ENV PATH="/root/.deno/bin:/root/.local/bin:${PATH}"

# Set working directory
WORKDIR /app

# Pre-create necessary directories and fetch static face detector model
RUN mkdir -p /app/uploads /app/clips /app/logs /app/models /tmp && \
    curl -fsSL -o /app/models/blaze_face_short_range.tflite https://storage.googleapis.com/mediapipe-models/face_detector/blaze_face_short_range/float16/1/blaze_face_short_range.tflite

# Copy pyproject.toml and uv.lock for dependency installation
COPY pyproject.toml uv.lock* ./

# Create virtual environment and install dependencies using cached uv wheels
RUN --mount=type=cache,target=/root/.cache/uv \
    uv venv --python 3.11 .venv && uv sync --no-install-project --no-dev

# Copy application assets and source code (keeps heavy dependency layer cached on code changes)
COPY fonts/ ./fonts/
COPY transitions/ ./transitions/
COPY src/ ./src/

# Expose the port FastAPI will run on
EXPOSE 8000

# Activate virtual environment and start the application
CMD [".venv/bin/uvicorn", "src.main_refactored:app", "--host", "0.0.0.0", "--port", "8000"]

