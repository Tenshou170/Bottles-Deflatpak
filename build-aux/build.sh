#!/usr/bin/env bash
# build.sh — native Meson build helper for Bottles-Deflatpak
# Mirrors what build-packages.sh does but keeps the build tree for
# incremental rebuilds and development use.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
BUILD_DIR="${PROJECT_DIR}/build"
PREFIX="${PREFIX:-/usr}"

echo "==> Configuring Bottles-Deflatpak"
echo "    Project : ${PROJECT_DIR}"
echo "    Build   : ${BUILD_DIR}"
echo "    Prefix  : ${PREFIX}"

meson setup "${BUILD_DIR}" "${PROJECT_DIR}" \
    --prefix="${PREFIX}" \
    --wipe 2>/dev/null || meson setup "${BUILD_DIR}" "${PROJECT_DIR}" --prefix="${PREFIX}"

echo "==> Building"
ninja -C "${BUILD_DIR}"

echo ""
echo "==> Build complete."
echo "    To install:  sudo ninja -C '${BUILD_DIR}' install"
echo "    To run directly from the build tree, set:"
echo "      export XDG_DATA_DIRS='${BUILD_DIR}/data:\$XDG_DATA_DIRS'"
echo "      python3 '${PROJECT_DIR}/bottles/__init__.py'"
