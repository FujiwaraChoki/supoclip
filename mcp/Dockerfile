FROM python:3.12-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    UV_SYSTEM_PYTHON=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && curl -LsSf https://astral.sh/uv/install.sh | sh \
    && apt-get purge -y --auto-remove curl \
    && apt-get clean \
    && find /var/lib/apt/lists -type f -delete

ENV PATH="/root/.local/bin:${PATH}"

COPY pyproject.toml uv.lock* ./
COPY src/ ./src/
COPY README.md ./

RUN uv pip install .

EXPOSE 9100

CMD ["supoclip-mcp"]
