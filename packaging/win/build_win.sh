#!/usr/bin/env bash
# Build BrainAgent Windows x64 portable zip.
# Runs on macOS — cross-fetches Windows wheels via uv.
#
# Outputs:
#   packaging/win/dist/BrainAgent-<version>-win-x64/    (portable tree)
#   packaging/win/dist/BrainAgent-<version>-win-x64.zip (portable zip)
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
BUILD="$HERE/build"
DIST="$HERE/dist"
PY_TARBALL="$REPO/packaging/downloads/python-win-x64.tar.gz"
REQS="$REPO/packaging/common/requirements.txt"
VERSION="$(grep '^VERSION' "$REPO/brain.py" | head -1 | cut -d'"' -f2)"
OUT_NAME="BrainAgent-${VERSION}-win-x64"
OUT_DIR="$DIST/$OUT_NAME"

echo "==> Brain Agent v${VERSION} — Windows x64 bundle (cross-built on macOS)"

# 1. Extract Windows Python
if [[ ! -f "$BUILD/python/python.exe" ]]; then
  echo "  -> Extracting Windows Python 3.14.4..."
  rm -rf "$BUILD/python"
  mkdir -p "$BUILD"
  tar -xzf "$PY_TARBALL" -C "$BUILD"
fi

# 2. Install Windows wheels via uv (handles env markers correctly across platforms)
SITE="$BUILD/python/Lib/site-packages"
if [[ ! -d "$SITE/mempalace" ]]; then
  echo "  -> Downloading + installing Windows wheels..."
  if ! command -v uv >/dev/null 2>&1; then
    echo "ERROR: uv not installed. brew install uv" >&2
    exit 1
  fi
  uv pip install \
    --python-platform x86_64-pc-windows-msvc \
    --python-version 3.14 \
    --target "$SITE" \
    -r "$REQS"
fi

# 3. Assemble output tree
echo "  -> Assembling portable tree..."
rm -rf "$OUT_DIR"
mkdir -p "$OUT_DIR/app"

# Python runtime
cp -R "$BUILD/python" "$OUT_DIR/python"

# Brain source — allowlist
for f in server.py brain.py auth.py client.py adapters.py \
         notifications.py execution.py mcp_bridge.py telegram.py \
         node.py tui.py launcher.py tools.md tools_config.json; do
  [[ -f "$REPO/$f" ]] && cp "$REPO/$f" "$OUT_DIR/app/$f"
done
for d in web mcp-servers; do
  [[ -d "$REPO/$d" ]] && rsync -a \
    --exclude="__pycache__" --exclude="*.pyc" --exclude="node_modules" \
    "$REPO/$d/" "$OUT_DIR/app/$d/"
done
if [[ -d "$REPO/mcp-servers/sqlite/node_modules" ]]; then
  rsync -a "$REPO/mcp-servers/sqlite/node_modules/" \
           "$OUT_DIR/app/mcp-servers/sqlite/node_modules/"
fi

# Default config
cat > "$OUT_DIR/app/config.json" <<'JSON'
{
  "providers": {},
  "default_provider": "",
  "server": {"host": "127.0.0.1", "port": 8420},
  "telegram": {"enabled": false},
  "execution_mode": "server"
}
JSON

mkdir -p "$OUT_DIR/app/agents/main"
cat > "$OUT_DIR/app/agents/main/agent.json" <<'JSON'
{"display_name": "Main", "description": "Main orchestrator", "model": ""}
JSON
touch "$OUT_DIR/app/agents/main/soul.md"
echo "${VERSION}" > "$OUT_DIR/app/.version"

# 4. Windows launcher (.bat) — seeds %LOCALAPPDATA%\BrainAgent on first run
cat > "$OUT_DIR/BrainAgent.bat" <<'BAT'
@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
if "%BRAIN_DATA_DIR%"=="" set "BRAIN_DATA_DIR=%LOCALAPPDATA%\BrainAgent"

if not exist "%BRAIN_DATA_DIR%" mkdir "%BRAIN_DATA_DIR%"

rem Seed on version change
set "VFILE=%BRAIN_DATA_DIR%\.brain-bundle-version"
set "CUR_VER="
if exist "%VFILE%" set /p CUR_VER=<"%VFILE%"
set /p NEW_VER=<"%SCRIPT_DIR%app\.version"
if not "%CUR_VER%"=="%NEW_VER%" (
  echo Seeding %BRAIN_DATA_DIR% from bundle...
  xcopy /E /I /Y /Q "%SCRIPT_DIR%app\*" "%BRAIN_DATA_DIR%\" >nul
  copy /Y "%SCRIPT_DIR%app\.version" "%VFILE%" >nul
)

set "PYTHONDONTWRITEBYTECODE=1"
cd /d "%BRAIN_DATA_DIR%"
"%SCRIPT_DIR%python\python.exe" "%BRAIN_DATA_DIR%\server.py" %*
endlocal
BAT

# Unix line endings won't work in .bat — convert to CRLF
if command -v unix2dos >/dev/null 2>&1; then
  unix2dos "$OUT_DIR/BrainAgent.bat" 2>/dev/null
else
  # Inline CRLF conversion
  awk 'BEGIN{RS="\n"; ORS="\r\n"} {print}' "$OUT_DIR/BrainAgent.bat" > "$OUT_DIR/BrainAgent.bat.crlf"
  mv "$OUT_DIR/BrainAgent.bat.crlf" "$OUT_DIR/BrainAgent.bat"
fi

# 5. README.txt for Windows users
cat > "$OUT_DIR/README.txt" <<README
Brain Agent v${VERSION} — Windows x64 Portable

Quick start
-----------
1. Extract this folder to any location (e.g. C:\BrainAgent\).
2. Double-click BrainAgent.bat to start the server.
3. Open http://127.0.0.1:8420 in a browser.

Data location
-------------
On first run, configuration is copied to:
    %LOCALAPPDATA%\BrainAgent\
Override with the BRAIN_DATA_DIR environment variable if needed.

Airgapped install
-----------------
This bundle ships a complete Python 3.14 runtime + every dependency.
No network access is required after extraction.
README

# 6. Build the zip
echo "  -> Packaging zip..."
rm -f "$DIST/${OUT_NAME}.zip"
(cd "$DIST" && zip -qr "${OUT_NAME}.zip" "$OUT_NAME")

ZIP_SIZE=$(du -sh "$DIST/${OUT_NAME}.zip" | cut -f1)
DIR_SIZE=$(du -sh "$OUT_DIR" | cut -f1)
echo "==> Done:"
echo "    $OUT_DIR  ($DIR_SIZE uncompressed)"
echo "    $DIST/${OUT_NAME}.zip  ($ZIP_SIZE)"
