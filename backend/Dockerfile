# Use Python 3.11 slim image
FROM python:3.11-slim

# Install system dependencies including ffmpeg and unzip (for deno)
RUN apt-get update && apt-get install -y \
    ffmpeg \
    curl \
    unzip \
    && rm -rf /var/lib/apt/lists/*

# Install deno (required by yt-dlp for YouTube JS challenge solving)
RUN curl -fsSL https://deno.land/install.sh | sh
ENV DENO_DIR=/root/.deno
ENV PATH="/root/.deno/bin:${PATH}"

# Install uv package manager
RUN pip install uv

# Set working directory
WORKDIR /app

# Copy pyproject.toml and uv.lock for dependency installation
COPY pyproject.toml uv.lock* ./

# Create virtual environment and install dependencies using uv sync
RUN uv venv .venv && uv sync

# Force update yt-dlp to the absolute latest version to handle YouTube API changes
# Also install from git to get the very latest commits
RUN uv pip install --upgrade --force-reinstall yt-dlp
RUN uv pip install --upgrade --force-reinstall "yt-dlp[default]"

# Copy source code
COPY src/ ./src/

# Copy fonts and transitions directories
COPY fonts/ ./fonts/
COPY transitions/ ./transitions/

# Download the OpenCV DNN face-detector model (SSD/ResNet). video_utils.py uses
# it for accurate, face-aware vertical cropping; without it the pipeline drops
# to the weak Haar cascade and crops are poorly placed. The .pb lives on a
# content-stable opencv_3rdparty branch; the .pbtxt is pinned to the opencv tag
# matching our opencv-python. See Integrity-Labs/supoclip#15.
# Pinned SHA-256 digests verify the downloads weren't tampered with or changed
# upstream; a mismatch fails the build rather than baking a bad model.
RUN mkdir -p /app/models \
    && curl -fsSL -o /app/models/opencv_face_detector.pbtxt \
       https://raw.githubusercontent.com/opencv/opencv/4.13.0/samples/dnn/face_detector/opencv_face_detector.pbtxt \
    && curl -fsSL -o /app/models/opencv_face_detector_uint8.pb \
       https://github.com/opencv/opencv_3rdparty/raw/dnn_samples_face_detector_20180220_uint8/opencv_face_detector_uint8.pb \
    && echo "ca0eb6b3a0e0b124282d093a3d8a9d453967f37af3c1a933978ac55605a8f2cb  /app/models/opencv_face_detector.pbtxt" | sha256sum -c - \
    && echo "5c71d752ef2cbf2f457ac82fdd580fcb2522fd04c5efdaed18eb6d9e2843fbed  /app/models/opencv_face_detector_uint8.pb" | sha256sum -c -

# Create necessary directories for video processing
RUN mkdir -p /app/uploads /app/clips /app/logs /tmp

# Expose the port FastAPI will run on
EXPOSE 8000

# Activate virtual environment and start the application
CMD [".venv/bin/uvicorn", "src.main_refactored:app", "--host", "0.0.0.0", "--port", "8000"]
