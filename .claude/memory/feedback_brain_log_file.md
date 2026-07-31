---
name: Brain server logs go to server.error.log not server.log
description: macOS launchd FD-redirect quirk — both stdout and stderr land in ~/.brain-agent/server.error.log. server.log captures only the startup banner. Always tail .error.log for live debugging.
type: feedback
originSessionId: 960d069c-8b9b-4582-a9f7-c2c3d1d52ba1
---
When debugging the Brain server's daemons (`[project-sync.kg]`, `[project-sync.conv]`, `[warmup-keeper]`, `[mempalace-miner]`, etc.), **tail `~/.brain-agent/server.error.log`, NOT `~/.brain-agent/server.log`**.

**Why:** the plist `com.brain-agent.server.plist` declares `StandardOutPath=...server.log` and `StandardErrorPath=...server.error.log`. On macOS launchd, both fd1 and fd2 actually map to `server.error.log` (verified live via `lsof -p <pid>`); `server.log` only receives the startup banner and stays dormant from then on.

**How to apply:** Always `grep ... ~/.brain-agent/server.error.log` for daemon output, never `server.log`. If a Monitor task with `tail -F` filter gets timeouts despite the daemon clearly running, the file path is the most likely cause. Don't trust the older memory notes / commits / CLAUDE.md fragments that say `[mempalace-miner] prints reach server.log immediately` — that line predates the FD-quirk discovery; today's reality is `server.error.log`.

**Why this happened:** never definitively diagnosed — possibly an old launchctl bootstrap that swapped FDs and persisted; possibly a macOS-version-specific quirk. Not worth fixing the plist (risk of breaking the live server); fixed by documentation only in v8.20.1 (CLAUDE.md "Deployment" section).

**Cursor table is authoritative anyway:** `chats.db.kg_extraction_log` records every KG cycle with seen/processed/skipped/triples/errors/elapsed. If you can't see a daemon log line, query the cursor — that's the ground truth.
