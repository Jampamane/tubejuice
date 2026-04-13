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

# Create the jeremy user and group, with a home directory
RUN groupadd --gid 1000 jeremy \
 && useradd --uid 1000 --gid 1000 --create-home --shell /bin/bash jeremy

WORKDIR /app

# Copy project files
COPY pyproject.toml .
COPY tubejuice/ ./tubejuice/
COPY beets_config.yaml.template .

# Install dependencies and project with uv into the system Python
RUN uv tool install .

# Create data dirs and give jeremy ownership before switching user
RUN mkdir -p /app/music /app/downloads \
 && chown -R jeremy:jeremy /app

# Switch to jeremy for all subsequent commands and at runtime
USER jeremy

# Persistent data volumes
VOLUME ["/app/music", "/app/downloads"]

ENV PYTHONUNBUFFERED=1

EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8765/')" || exit 1

CMD ["tubejuice"]