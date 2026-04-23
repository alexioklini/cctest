#!/usr/bin/env bash
# Build BrainAgent.app bundle + DMG (macOS arm64, airgapped).
#
# Outputs:
#   packaging/mac/dist/BrainAgent.app
#   packaging/mac/dist/BrainAgent-<version>-arm64.dmg
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
BUILD="$HERE/build"
DIST="$HERE/dist"
APP="$DIST/BrainAgent.app"
PY_TARBALL="$REPO/packaging/downloads/python-mac-arm64.tar.gz"
REQS="$REPO/packaging/common/requirements.txt"
VERSION="$(grep '^VERSION' "$REPO/claude_cli.py" | head -1 | cut -d'"' -f2)"
DMG_NAME="BrainAgent-${VERSION}-arm64.dmg"

echo "==> Brain Agent v${VERSION} — macOS arm64 bundle"

# 1. Extract embedded Python + install deps (cache via BUILD dir)
if [[ ! -d "$BUILD/python/bin" ]]; then
  echo "  -> Extracting Python 3.14.4..."
  mkdir -p "$BUILD"
  tar -xzf "$PY_TARBALL" -C "$BUILD"
fi
if [[ ! -d "$BUILD/python/lib/python3.14/site-packages/mempalace" ]]; then
  echo "  -> Installing Python deps..."
  "$BUILD/python/bin/python3" -m pip install --upgrade pip > /dev/null
  "$BUILD/python/bin/python3" -m pip install -r "$REQS"
fi

# 2. Assemble .app bundle
echo "  -> Assembling .app bundle..."
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources/app"

# Info.plist
cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleExecutable</key><string>BrainAgent</string>
  <key>CFBundleIdentifier</key><string>com.brain-agent.server</string>
  <key>CFBundleName</key><string>BrainAgent</string>
  <key>CFBundleDisplayName</key><string>Brain Agent</string>
  <key>CFBundleShortVersionString</key><string>${VERSION}</string>
  <key>CFBundleVersion</key><string>${VERSION}</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>LSMinimumSystemVersion</key><string>11.0</string>
  <key>LSApplicationCategoryType</key><string>public.app-category.developer-tools</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>LSUIElement</key><false/>
</dict>
</plist>
PLIST

# Launcher — copies tree to ~/Library/Application Support on first run, runs server
cat > "$APP/Contents/MacOS/BrainAgent" <<'LAUNCHER'
#!/usr/bin/env bash
set -euo pipefail
APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
RESOURCES="$APP_DIR/Resources"
DATA_DIR="${BRAIN_DATA_DIR:-$HOME/Library/Application Support/BrainAgent}"
LOG="$DATA_DIR/server.log"

mkdir -p "$DATA_DIR"

# Seed on first run or when bundled source is newer
if [[ ! -f "$DATA_DIR/.brain-bundle-version" ]] || \
   [[ "$(cat "$DATA_DIR/.brain-bundle-version" 2>/dev/null)" != "$(cat "$RESOURCES/app/.version" 2>/dev/null)" ]]; then
  echo "Seeding $DATA_DIR from bundle..." >&2
  rsync -a --exclude='agents/main/*.db' --exclude='agents/main/*.db-*' \
        --exclude='config.json' \
        "$RESOURCES/app/" "$DATA_DIR/"
  # Seed config.json only if missing
  [[ -f "$DATA_DIR/config.json" ]] || cp "$RESOURCES/app/config.json" "$DATA_DIR/config.json"
  cp "$RESOURCES/app/.version" "$DATA_DIR/.brain-bundle-version"
fi

export PYTHONDONTWRITEBYTECODE=1
cd "$DATA_DIR"
exec "$RESOURCES/python/bin/python3" "$DATA_DIR/server.py" "$@"
LAUNCHER
chmod +x "$APP/Contents/MacOS/BrainAgent"

# 3. Copy Python runtime + site-packages
echo "  -> Copying Python runtime..."
cp -R "$BUILD/python" "$APP/Contents/Resources/python"

# 4. Copy Brain source tree — allowlist, not denylist, to avoid shipping
#    incidental files from the dev repo.
echo "  -> Copying Brain source tree..."
APP_OUT="$APP/Contents/Resources/app"
# Python modules
for f in server.py claude_cli.py auth.py client.py adapters.py \
         notifications.py execution.py mcp_bridge.py telegram.py \
         node.py tui.py brain.py tools.md tools_config.json; do
  [[ -f "$REPO/$f" ]] && cp "$REPO/$f" "$APP_OUT/$f"
done
# Static trees
for d in web mcp-servers; do
  [[ -d "$REPO/$d" ]] && rsync -a \
    --exclude="__pycache__" --exclude="*.pyc" --exclude="node_modules" \
    "$REPO/$d/" "$APP_OUT/$d/"
done
# mcp-servers/sqlite ships node_modules (required to run at runtime)
if [[ -d "$REPO/mcp-servers/sqlite/node_modules" ]]; then
  rsync -a "$REPO/mcp-servers/sqlite/node_modules/" \
           "$APP_OUT/mcp-servers/sqlite/node_modules/"
fi

# Stamp version for upgrade detection
echo "${VERSION}" > "$APP/Contents/Resources/app/.version"

# Minimal default config — server binds 127.0.0.1:8420, no providers
cat > "$APP/Contents/Resources/app/config.json" <<JSON
{
  "providers": {},
  "default_provider": "",
  "server": {"host": "127.0.0.1", "port": 8420},
  "telegram": {"enabled": false},
  "execution_mode": "server"
}
JSON

# 5. Ensure main agent dir exists with stub
mkdir -p "$APP/Contents/Resources/app/agents/main"
if [[ ! -f "$APP/Contents/Resources/app/agents/main/agent.json" ]]; then
  echo '{"display_name": "Main", "description": "Main orchestrator", "model": ""}' \
    > "$APP/Contents/Resources/app/agents/main/agent.json"
fi
touch "$APP/Contents/Resources/app/agents/main/soul.md"

# 6. Report bundle size
APP_SIZE=$(du -sh "$APP" | cut -f1)
echo "  -> Bundle ready: $APP ($APP_SIZE)"

# 7. Build DMG (hdiutil — no external tools)
echo "  -> Building DMG..."
rm -f "$DIST/$DMG_NAME"
DMG_SRC="$DIST/dmg-src"
rm -rf "$DMG_SRC"
mkdir -p "$DMG_SRC"
cp -R "$APP" "$DMG_SRC/"
ln -s /Applications "$DMG_SRC/Applications"
hdiutil create -volname "Brain Agent ${VERSION}" \
               -srcfolder "$DMG_SRC" \
               -ov -format UDZO \
               "$DIST/$DMG_NAME" > /dev/null
rm -rf "$DMG_SRC"

DMG_SIZE=$(du -sh "$DIST/$DMG_NAME" | cut -f1)
echo "==> Done: $DIST/$DMG_NAME ($DMG_SIZE)"
