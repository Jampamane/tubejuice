#!/usr/bin/env bash
# build-and-push.sh
#
# Builds the TubeJuice image for ARM64 (Raspberry Pi 4) using
# docker buildx with QEMU emulation, then pushes to ghcr.io.
#
# Prerequisites:
#   - Docker with buildx support (Docker Desktop or Docker Engine 20.10+)
#   - A GitHub Personal Access Token with write:packages scope in GITHUB_TOKEN
#   - Your GitHub username in GITHUB_USER (or passed as the first argument)
#
# Usage:
#   GITHUB_TOKEN=ghp_xxx GITHUB_USER=yourname ./build-and-push.sh [tag]
set -euo pipefail

# ── Config ────────────────────────────────────────────────────────────────────
GITHUB_USER="${GITHUB_USER:-${1:-}}"
TAG="${2:-latest}"
IMAGE_NAME="tubejuice"
REGISTRY="ghcr.io"
FULL_IMAGE="${REGISTRY}/${GITHUB_USER}/${IMAGE_NAME}"
BUILDER_NAME="tubejuice-arm-builder"

# ── Validate ──────────────────────────────────────────────────────────────────
if [[ -z "$GITHUB_USER" ]]; then
  echo "❌  GITHUB_USER is not set."
  echo "    Run: GITHUB_USER=yourname ./build-and-push.sh"
  exit 1
fi

if [[ -z "${GITHUB_TOKEN:-}" ]]; then
  echo "❌  GITHUB_TOKEN is not set."
  echo "    Create one at: https://github.com/settings/tokens"
  echo "    Required scope: write:packages"
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "🏗️  Building for linux/arm64 and pushing:"
echo "    ${FULL_IMAGE}:${TAG}"
if [[ "$TAG" != "latest" ]]; then
  echo "    ${FULL_IMAGE}:latest"
fi
echo ""

# ── Log in to ghcr.io ─────────────────────────────────────────────────────────
echo "🔑  Logging in to ghcr.io..."
echo "${GITHUB_TOKEN}" | docker login ghcr.io -u "${GITHUB_USER}" --password-stdin

# ── Install QEMU emulators (needed to build ARM64 on x86) ────────────────────
echo "⚙️   Installing QEMU ARM64 emulator..."
docker run --privileged --rm tonistiigi/binfmt --install arm64

# ── Create (or reuse) a buildx builder that supports ARM64 ───────────────────
if ! docker buildx inspect "${BUILDER_NAME}" &>/dev/null; then
  echo "🔧  Creating buildx builder '${BUILDER_NAME}'..."
  docker buildx create --name "${BUILDER_NAME}" --driver docker-container --use
else
  echo "🔧  Reusing existing builder '${BUILDER_NAME}'..."
  docker buildx use "${BUILDER_NAME}"
fi

docker buildx inspect --bootstrap

# ── Build and push ────────────────────────────────────────────────────────────
echo ""
echo "🚀  Building and pushing (this will take a while on first run)..."

# Build destination tags
DEST_TAGS="--tag ${FULL_IMAGE}:${TAG}"
if [[ "$TAG" != "latest" ]]; then
  DEST_TAGS="${DEST_TAGS} --tag ${FULL_IMAGE}:latest"
fi

docker buildx build \
  --platform linux/arm64 \
  --file "${SCRIPT_DIR}/Dockerfile" \
  ${DEST_TAGS} \
  --push \
  --provenance=false \
  "${SCRIPT_DIR}"

echo ""
echo "✅  Done!"
echo "    ${FULL_IMAGE}:${TAG}"
[[ "$TAG" != "latest" ]] && echo "    ${FULL_IMAGE}:latest"
echo ""
echo "On your Raspberry Pi, pull and run with:"
echo "    docker pull ${FULL_IMAGE}:${TAG}"
echo "    docker compose up -d"