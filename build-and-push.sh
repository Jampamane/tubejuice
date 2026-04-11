#!/usr/bin/env bash
# build-and-push.sh
#
# Builds the TubeJuice image with Kaniko and pushes it to the
# GitHub Container Registry (ghcr.io).
#
# Prerequisites:
#   - kubectl configured against a cluster that has Kaniko available, OR
#     the kaniko executor binary available locally via `docker run`
#   - A GitHub Personal Access Token (classic) with `write:packages` scope
#     stored in the environment as GITHUB_TOKEN
#   - Your GitHub username in GITHUB_USER (or passed as the first argument)
#
# Usage:
#   GITHUB_TOKEN=ghp_xxx GITHUB_USER=yourname ./build-and-push.sh [tag]
#
# Examples:
#   ./build-and-push.sh                  # pushes :latest
#   ./build-and-push.sh v1.2.3           # pushes :v1.2.3 and :latest
set -euo pipefail

# ── Config ────────────────────────────────────────────────────────────────────
GITHUB_USER="${GITHUB_USER:-${1:-}}"
IMAGE_NAME="tubejuice"
TAG="${2:-latest}"
REGISTRY="ghcr.io"
FULL_IMAGE="${REGISTRY}/${GITHUB_USER}/${IMAGE_NAME}"

# ── Validate ──────────────────────────────────────────────────────────────────
if [[ -z "$GITHUB_USER" ]]; then
  echo "❌  GITHUB_USER is not set."
  echo "    Run:  GITHUB_USER=yourname ./build-and-push.sh"
  exit 1
fi

if [[ -z "${GITHUB_TOKEN:-}" ]]; then
  echo "❌  GITHUB_TOKEN is not set."
  echo "    Create a token at: https://github.com/settings/tokens"
  echo "    Required scope: write:packages"
  exit 1
fi

echo "🏗️  Building and pushing:"
echo "    Image : ${FULL_IMAGE}:${TAG}"
echo "    Also  : ${FULL_IMAGE}:latest"
echo ""

# ── Write a temporary Docker config with ghcr.io credentials ─────────────────
# Kaniko reads this from /kaniko/.docker/config.json
DOCKER_CONFIG_DIR="$(mktemp -d)"
AUTH_TOKEN="$(echo -n "${GITHUB_USER}:${GITHUB_TOKEN}" | base64 -w0)"

cat > "${DOCKER_CONFIG_DIR}/config.json" <<EOF
{
  "auths": {
    "ghcr.io": {
      "auth": "${AUTH_TOKEN}"
    }
  }
}
EOF

# Ensure the temp creds are cleaned up on exit
trap 'rm -rf "${DOCKER_CONFIG_DIR}"' EXIT

# ── Determine build context (directory containing Dockerfile) ─────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Assumes the Dockerfile is in the same directory as this script.
# Adjust CONTEXT_DIR if your layout differs.
CONTEXT_DIR="${SCRIPT_DIR}"

# ── Build with Kaniko via Docker ──────────────────────────────────────────────
# This runs the Kaniko executor container locally using Docker as the runtime.
# If you're running inside a Kubernetes cluster, replace this block with a
# Kubernetes Job (see the commented section further below).

echo "🚀  Running Kaniko executor..."

docker run --rm \
  -v "${CONTEXT_DIR}:/workspace" \
  -v "${DOCKER_CONFIG_DIR}:/kaniko/.docker:ro" \
  gcr.io/kaniko-project/executor:latest \
    --context="dir:///workspace" \
    --dockerfile="/workspace/Dockerfile" \
    --destination="${FULL_IMAGE}:${TAG}" \
    --destination="${FULL_IMAGE}:latest" \
    --cache=true \
    --cache-ttl=24h \
    --compressed-caching=false \
    --snapshot-mode=redo \
    --log-format=text

echo ""
echo "✅  Done! Image pushed to:"
echo "    ${FULL_IMAGE}:${TAG}"
echo "    ${FULL_IMAGE}:latest"
echo ""
echo "Pull it with:"
echo "    docker pull ${FULL_IMAGE}:${TAG}"


# ── Alternative: Kubernetes Job ───────────────────────────────────────────────
# Uncomment and adapt this block if you want to run Kaniko inside a k8s cluster
# instead of locally via Docker.
#
# # First create the docker-config secret:
# kubectl create secret generic ghcr-creds \
#   --from-file=config.json="${DOCKER_CONFIG_DIR}/config.json" \
#   --dry-run=client -o yaml | kubectl apply -f -
#
# kubectl apply -f - <<YAML
# apiVersion: batch/v1
# kind: Job
# metadata:
#   name: tubejuice-build-${TAG//./-}
# spec:
#   ttlSecondsAfterFinished: 300
#   template:
#     spec:
#       restartPolicy: Never
#       containers:
#         - name: kaniko
#           image: gcr.io/kaniko-project/executor:latest
#           args:
#             - "--context=git://github.com/${GITHUB_USER}/${IMAGE_NAME}"
#             - "--dockerfile=Dockerfile"
#             - "--destination=${FULL_IMAGE}:${TAG}"
#             - "--destination=${FULL_IMAGE}:latest"
#             - "--cache=true"
#             - "--cache-ttl=24h"
#           volumeMounts:
#             - name: docker-config
#               mountPath: /kaniko/.docker
#       volumes:
#         - name: docker-config
#           secret:
#             secretName: ghcr-creds
#             items:
#               - key: config.json
#                 path: config.json
# YAML
#
# kubectl wait --for=condition=complete job/tubejuice-build-${TAG//./-} --timeout=600s
# kubectl logs job/tubejuice-build-${TAG//./-}