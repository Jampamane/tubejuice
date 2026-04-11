# ── Stage 1: build a venv with uv ────────────────────────────────────────────
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

WORKDIR /app

# Install deps into an isolated venv so the final stage stays clean
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv

# Copy only the files uv needs to resolve deps first (better layer caching)
COPY pyproject.toml .
# Generate a lockfile if one doesn't exist yet
RUN uv lock --check 2>/dev/null || uv lock

# Install all dependencies
RUN uv sync --frozen --no-dev --no-install-project

# Now copy the rest of the source and install the project itself
COPY . .
RUN uv sync --frozen --no-dev


# ── Stage 2: lean runtime image ───────────────────────────────────────────────
FROM python:3.12-slim-bookworm AS runtime

# ffmpeg is required by yt-dlp for audio conversion
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy the venv built in stage 1
COPY --from=builder /app/.venv /app/.venv
# Copy application source
COPY --from=builder /app /app

# Persistent data lives in a volume so it survives container restarts
VOLUME ["/app/music", "/app/downloads"]

# Put the venv on PATH
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

EXPOSE 8765

# Healthcheck — hits the root HTML page
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8765/')" || exit 1

CMD ["tubejuice"]