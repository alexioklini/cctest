#!/bin/bash
# claude-code-maintain.sh — Nightly maintenance: stop Claude Code, update, restart
# Called by launchd at 04:00 daily
# Logs to ~/.brain-agent/claude-code-maintain.log

set -euo pipefail

LOG="$HOME/.brain-agent/claude-code-maintain.log"
PLIST_LABEL="com.claude-code.remote"
NVM_DIR="$HOME/.nvm"

mkdir -p "$(dirname "$LOG")"
exec >> "$LOG" 2>&1

echo ""
echo "=== $(date '+%Y-%m-%d %H:%M:%S') — Maintenance starting ==="

# Load nvm (claude is installed via npm/nvm)
if [ -s "$NVM_DIR/nvm.sh" ]; then
    source "$NVM_DIR/nvm.sh"
fi

# Ensure claude is on PATH
export PATH="$HOME/.nvm/versions/node/$(node --version 2>/dev/null || echo v22.20.0)/bin:$PATH"

CLAUDE_BIN=$(which claude 2>/dev/null || echo "")
if [ -z "$CLAUDE_BIN" ]; then
    echo "ERROR: claude binary not found"
    exit 1
fi

echo "Claude binary: $CLAUDE_BIN"
echo "Current version: $(claude --version 2>&1 || echo unknown)"

# Step 1: Stop the launchd service
echo "Stopping $PLIST_LABEL..."
launchctl bootout "gui/$(id -u)/$PLIST_LABEL" 2>/dev/null || true
sleep 2

# Kill any remaining claude processes for this project
pkill -f "claude.*remote-control.*Brain Agent" 2>/dev/null || true
sleep 1

# Step 2: Update Claude Code
echo "Updating Claude Code..."
npm update -g @anthropic-ai/claude-code 2>&1 || {
    echo "WARNING: npm update failed, trying npm install..."
    npm install -g @anthropic-ai/claude-code@latest 2>&1 || echo "ERROR: Update failed"
}

echo "Updated version: $(claude --version 2>&1 || echo unknown)"

# Step 3: Restart the launchd service
echo "Restarting $PLIST_LABEL..."
launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/$PLIST_LABEL.plist" 2>/dev/null || \
    launchctl load "$HOME/Library/LaunchAgents/$PLIST_LABEL.plist" 2>/dev/null || true

sleep 3

# Verify it's running
if launchctl print "gui/$(id -u)/$PLIST_LABEL" &>/dev/null; then
    echo "Service restarted successfully"
else
    echo "WARNING: Service may not have started — check launchctl"
fi

echo "=== $(date '+%Y-%m-%d %H:%M:%S') — Maintenance complete ==="
