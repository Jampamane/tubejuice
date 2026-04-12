# Target: Raspberry Pi 4 (ARM64 / aarch64)
FROM --platform=linux/arm64 python:3.12-slim-bookworm

# ffmpeg for yt-dlp audio conversion, curl for uv installer
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:$PATH"

WORKDIR /app

# Copy project files
COPY pyproject.toml .
COPY tubejuice/ ./tubejuice/
COPY beets_config.yaml.template .

# Install dependencies and project with uv into the system Python
RUN uv tool install .

# Persistent data volumes
VOLUME ["/app/music", "/app/downloads"]

ENV PYTHONUNBUFFERED=1

EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8765/')" || exit 1

CMD ["tubejuice"]