#!/usr/bin/env bash
# Brain Agent — build airgapped installers for both macOS arm64 and Windows x64.
# Run on macOS. Requires: curl, uv, hdiutil, zip, rsync.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"

echo "=============================="
echo "Brain Agent — airgapped bundles"
echo "=============================="

# Sanity: tools
for t in curl tar rsync hdiutil zip uv; do
  if ! command -v "$t" >/dev/null 2>&1; then
    echo "ERROR: missing tool: $t" >&2
    echo "  brew install $t" >&2
    exit 1
  fi
done

# 1. Download Pythons if missing
DOWN="$HERE/downloads"
mkdir -p "$DOWN"
MAC_PY_URL="https://github.com/astral-sh/python-build-standalone/releases/download/20260414/cpython-3.14.4%2B20260414-aarch64-apple-darwin-install_only_stripped.tar.gz"
WIN_PY_URL="https://github.com/astral-sh/python-build-standalone/releases/download/20260414/cpython-3.14.4%2B20260414-x86_64-pc-windows-msvc-install_only_stripped.tar.gz"

if [[ ! -f "$DOWN/python-mac-arm64.tar.gz" ]]; then
  echo "==> Downloading macOS arm64 Python..."
  curl -# -L -o "$DOWN/python-mac-arm64.tar.gz" "$MAC_PY_URL"
fi
if [[ ! -f "$DOWN/python-win-x64.tar.gz" ]]; then
  echo "==> Downloading Windows x64 Python..."
  curl -# -L -o "$DOWN/python-win-x64.tar.gz" "$WIN_PY_URL"
fi

# 2. Run per-platform builds
"$HERE/mac/build_app.sh"
"$HERE/win/build_win.sh"

echo
echo "==> All bundles built successfully."
echo
ls -lh "$HERE/mac/dist/"*.dmg 2>/dev/null | awk '{print $NF, $5}'
ls -lh "$HERE/win/dist/"*.zip 2>/dev/null | awk '{print $NF, $5}'
