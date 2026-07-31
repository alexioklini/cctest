---
name: project_weburl_miner_path_bug
description: Fixed v9.215.1 — project-sync web-url/changed-file miner crashed because mine(files=) got str not Path
metadata: 
  node_type: memory
  type: project
  originSessionId: 4e01ac63-6b3d-486f-a0ab-b3f88a30cdcb
---

Fixed 2026-06-27 (v9.215.1, committed f924dec8, pushed). The project-sync web-url + changed-file mining crashed with `AttributeError: 'str' object has no attribute 'read_text'` → 0 drawers filed for affected projects (firmenbewertung, macrumors web-urls, etc.). User spotted it in server.error.log.

**Root cause:** `_mine_batched` (server_daemons.py) normalises scan_project's PosixPaths → STR via `os.fspath()` so the drawer source_file pre-filter (`_mined.get(f)`, str-keyed) matches — that was the v9.189.4 fix ([[project_sync_changed_file_optimization]]). But the str list was then passed straight to `mp_miner.mine(files=...)`, and mempalace's `mine()` iterates the list calling **Path methods** (`.read_text()` at miner.py:1308, `.suffix` at 1510) on each entry → crashes on str. CONFLICT: pre-filter needs str, mine() needs Path.

**Fix:** keep `files` as str for the pre-filter; convert back to `[Path(f) for f in files]` / `[Path(f) for f in batch]` ONLY at the two `mine(files=)` call boundaries (server_daemons.py ~1765, ~1786). Added missing `from pathlib import Path` import — **py_compile does NOT catch a module-level NameError**, so verified via runtime `import server_daemons; server_daemons.Path`. Root cause + fix verified against the real firmenbewertung/web-urls folder (str.read_text absent, Path.read_text works).

**LESSON:** mempalace's `mine(files=)` contract requires pathlib.Path entries, NOT str — any caller that str-normalises for other reasons must re-wrap at the mine boundary. Independent of the 9.214/9.215 code-intelligence work. Internal mining fix → CHANGELOG_OK=1 override on push (no curated entry; nothing user-visible).
