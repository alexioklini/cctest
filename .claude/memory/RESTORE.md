# Claude Code memory — snapshot for cross-machine development

This directory is a **committed snapshot** of Claude Code's persistent project
memory. The live copy lives OUTSIDE the repo, in Claude Code's per-machine data
directory, and is what Claude actually reads/writes during sessions:

- macOS/Linux: `~/.claude/projects/<encoded-project-path>/memory/`
- Windows: `%USERPROFILE%\.claude\projects\<encoded-project-path>\memory\`

`<encoded-project-path>` is the absolute path of this repo checkout with path
separators (and the drive colon on Windows) replaced by `-`. Easiest way to find
it: open Claude Code once inside the repo, then look for the newest directory
under `~/.claude/projects/` (or `%USERPROFILE%\.claude\projects\`).

## Install on a new machine

1. Clone the repo and open Claude Code in it once (creates the project dir).
2. Copy the contents of this folder (everything except this RESTORE.md) into
   the `memory/` subdirectory of that project dir, creating it if needed.
3. Restart the Claude Code session — `MEMORY.md` is the index it loads.

## Keeping it in sync

Git does NOT auto-sync this. Before switching machines, re-copy the live
memory dir into `.claude/memory/` and commit; after pulling on the other
machine, copy it out again (step 2 above). Snapshot taken: 2026-07-31.
