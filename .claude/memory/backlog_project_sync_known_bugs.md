---
name: project-sync daemon known bugs (closet log silence + non-idempotent startup wipe)
description: 2026-04-28 — two pre-existing project-sync bugs found during closet-regen rollout; both deferred to post-presentation, neither blocking
type: project
originSessionId: 7486d080-f9df-4a1c-8a53-d9a3c60c884c
---
Two bugs found in the project-sync daemon during the 2026-04-28 closet-regen rollout. Both pre-existing, neither blocking the presentation. Captured here so they don't get rediscovered.

**1. `_run_closet_regen_for` swallows wrapper log line** (server.py:13361)

```python
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    out = kg_extract.run_closet_regen_incremental(...)  # prints "[project-sync.closet] wing=... ok"
```

The intent was to suppress upstream `mempalace.closet_llm.regenerate_closets`'s chatty per-file `Regenerating closets for N source files...` output. The wrapper's own one-line summary (`run_closet_regen_incremental` in `kg_extract.py:1448`) ALSO uses `print()` and gets captured into `buf`, then thrown away.

**Why:** when this was written, the assumption was that wrapper logs would somehow escape `redirect_stdout`. They don't.

**How to apply:** the fix is straightforward — change `run_closet_regen_incremental` to return the summary string in its result dict instead of printing, and have the daemon emit the line itself outside the redirect block. ~10 LOC. Don't print/log the wrapper internals from the daemon side — read `out["summary"]` and call `print()` after `with contextlib.redirect_stdout(buf):` exits.

To verify regen ran without the log fix: `sqlite3 chats.db "SELECT palace_wing, COUNT(*) FROM closet_regen_progress GROUP BY palace_wing"` — cursor row counts == source files seen, updated_at == last cycle.

**2. Startup wipe — REMOVED 2026-04-28** (server.py around line 13430)

Originally a "one-shot" cleanup added in 8.18.2 to drop drawers tagged with the legacy `project__<name>--<agent_id>` wing scheme after the rename to ID-only `project__<id>`. The cleanup was never idempotency-gated, so every Brain restart deleted ~1739 project drawers and triggered a ~20-minute re-mine. Multiple recent restarts (2026-04-28 evening) hit `[project-sync] startup wipe failed: KeyError: '/Users/alexander/.mempalace/brain'` which accidentally protected the data — not a feature, just luck.

Briefly tried marker-file gate (`<palace_path>/.startup_wipe_done` + `BRAIN_FORCE_PROJECT_WIPE=1` override). Verified working but then **removed entirely** — the migration has been complete on every live install for weeks; carrying boot-time migration code with hidden side effects (also cleared `sync_status` JSON in every `project.json`) is the wrong shape for occasional one-time work. Replaced with a NOTE comment pointing future migrations at an explicit admin endpoint or `brain.py` subcommand.

**How to apply if a future wing scheme migration is needed**: build it as `POST /v1/mempalace/migrate` (admin-only, audit-logged) or `brain.py migrate-wings` — explicit, observable, audit-logged. NOT as silent boot-time behavior. Don't re-add startup wipes; the implicit-behavior pattern is what caused the rewipe loop in the first place.
