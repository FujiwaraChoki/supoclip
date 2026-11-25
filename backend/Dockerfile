# Use Python 3.11 slim image
FROM python:3.11.17-slim-trixie

# Install system dependencies including ffmpeg and unzip (for deno).
# Noto provides fallback glyphs for multilingual captions (including Hindi)
# and colour emoji. libass/fontconfig selects them when a chosen font lacks glyphs.
RUN apt-get update && apt-get install -y \
    ffmpeg \
    tesseract-ocr \
    tesseract-ocr-eng \
    curl \
    unzip \
    fonts-noto-core \
    fonts-noto-cjk \
    fonts-noto-color-emoji \
    fontconfig \
    && fc-cache -f \
    && rm -rf /var/lib/apt/lists/*

# Install deno, pinned: yt-dlp uses it for YouTube JS challenges, and the dynamic-edits renderer (src/fx_runtime)
# runs fx-core on @napi-rs/canvas under it. The npm cache is baked below so renders work offline.
ARG DENO_VERSION=2.9.8
RUN curl -fsSL https://deno.land/install.sh | sh -s v${DENO_VERSION}
ENV DENO_DIR=/root/.deno
ENV PATH="/root/.deno/bin:${PATH}"

# Install uv package manager
RUN pip install uv

# Set working directory
WORKDIR /app

# Copy pyproject.toml and uv.lock for dependency installation
COPY pyproject.toml uv.lock* ./

# New self-hosted setups can select CPU wheels without changing legacy builds.
ARG BACKEND_CPU_ONLY=false
RUN uv venv .venv && \
    if [ "$BACKEND_CPU_ONLY" = "true" ]; then \
      uv sync --frozen --extra cpu; \
    else \
      uv sync --frozen --extra cuda; \
    fi

# yt-dlp and its PO-token plugin are installed from uv.lock above.

# The small speech model that listens to the audio around each cut and corrects word times the transcript has
# wrong there (src/media/edge_listen.py). CLIP_EDGE_LISTEN=auto uses it only when the file is already on disk,
# so a render never waits on a download. About 460 MB; build with --build-arg EDGE_LISTEN_MODEL= to leave it out.
ARG EDGE_LISTEN_MODEL=small
RUN if [ -n "$EDGE_LISTEN_MODEL" ]; then \
      .venv/bin/python -c "import os, whisper; whisper._download(whisper._MODELS['$EDGE_LISTEN_MODEL'], os.path.join(os.path.expanduser('~'), '.cache', 'whisper'), False)"; \
    fi

# Bake the fx runtime's pinned npm/jsr cache into DENO_DIR before the source (layer cached on the lockfile).
# At runtime main.ts runs with --cached-only --frozen and never touches the network.
COPY src/fx_runtime/deno.json src/fx_runtime/deno.lock ./src/fx_runtime/
RUN cd src/fx_runtime && deno install --frozen

# Copy source code
COPY src/ ./src/
COPY LICENSE ./

# Copy fonts and transitions directories
COPY fonts/ ./fonts/
COPY transitions/ ./transitions/

# Self-hoster drop-in sounds and music beds for dynamic edits (FX_LIBRARY_DIR), like fonts/
COPY fx_library/ ./fx_library/

# Create necessary directories for video processing
RUN mkdir -p /app/uploads /app/clips /app/logs /tmp

# Expose the port FastAPI will run on
EXPOSE 8000

# Activate virtual environment and start the application
CMD [".venv/bin/uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
