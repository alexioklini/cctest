---
name: feedback_never_sigkill_brain
description: "NEVER SIGKILL the brain-agent server (no `kickstart -k`, no `kill -9`) — corrupts MemPalace HNSW; use graceful SIGTERM. Enforced by a PreToolUse deny hook."
metadata: 
  node_type: memory
  type: feedback
  originSessionId: d59e8b4b-32e8-4797-9d95-d5bf69512161
---

NEVER send SIGKILL to the brain-agent server / MemPalace process. Forbidden: `launchctl kickstart -k …`, `kill -9`, `kill -KILL`, `pkill -9`. The ONLY approved restart is graceful SIGTERM:

```
launchctl kickstart gui/$(id -u)/com.brain-agent.server   # NO -k
```

**Why:** SIGKILL bypasses brain-agent's v9.60.3 graceful-shutdown handler, so chromadb never flushes its HNSW index to disk. On next boot the startup scan sees sqlite >> HNSW, fails integrity, and quarantines + rebuilds the vector segment — the recurring MemPalace corruption the user has spent multiple versions fixing. (On 2026-06-03 I used `kickstart -k` despite the user asking for a graceful restart; it caused one quarantine — `003c1084….drift-20260603-104004`. No data lost — all ~13,380 drawer embeddings survived in durable sqlite — but it was exactly the failure mode they were guarding against.) The low `sync_threshold=1000` venv patch ([[project_mempalace_venv_patches]]) is only a backstop, not a license to SIGKILL.

**How to apply:** Default to the graceful `kickstart` (no `-k`). After restart, confirm `/v1/status` version == `brain.VERSION` ([[feedback_compile_check_brain_py]]) and grep `~/.brain-agent/server.error.log` for `quarantin|corrupt|hnsw|drift` to confirm a clean boot. A SIGKILL to brain-agent now requires EXPLICIT user approval each time.

**Deterministic enforcement:** `.claude/settings.json` (committed) wires a `PreToolUse(Bash)` deny hook → `.claude/hooks/block-sigkill-brain.sh`, which blocks `kickstart -k` (any `-k` form) and `kill/pkill -9/-KILL/-s KILL` that reference brain-agent/mempalace/server.py, printing the graceful alternative. The hook — not memory — is the hard guarantee; this note is the rationale + the why. Related: [[project_chroma_bulk_delete_corruption]], [[project_mempalace_review_findings]].
