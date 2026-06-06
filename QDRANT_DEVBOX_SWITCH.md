# Dev-box switch to Qdrant (native macOS binary, no Docker) — TOMORROW

**Status:** PLAN — for execution tomorrow. Dev box is currently healthy on **sqlite_exact**
(palace `~/.mempalace/brain-sqlite`, 16,884 drawers, no HNSW → corruption-free).
**Prereq met:** the int8 quantization venv patch is already applied + backed up
(`~/.mempalace/span_patch_backup/qdrant.py.3.4.0-{clean,patched}` + `.diff`). See
[[project_qdrant_migration_plan]] for the full prod plan; THIS doc is the focused dev-box
cutover using the **native binary** (not Docker).

This is the dev-box dry run of the production migration. We keep quantization OFF for the
first cutover (correctness first), confirm retrieval works, then optionally flip int8 on.

---

## 0. Why native binary (not Docker)

Qdrant ships a self-contained `darwin/aarch64` (Apple Silicon) binary — no daemon manager, no
container runtime. For a single-machine dev box it's the lightest option: one process, one
`storage/` dir, one `config.yaml`. We run it under launchd so it survives like the other
supervised services (sidecar / SearXNG / crawl4ai pattern).

This machine: **arm64**, **Homebrew present**, qdrant **not yet installed**.

---

## 1. Install the binary

**Option A — Homebrew (simplest, auto-PATH, easy upgrades):**
```bash
brew install qdrant
qdrant --version    # confirm it resolves
```
Homebrew puts the binary on PATH and can run it as a brew service. Pin/record the version.

**Option B — direct download (no brew dependency, explicit version pin):**
```bash
# Pick a release from github.com/qdrant/qdrant/releases (aarch64-apple-darwin tarball)
cd /tmp
curl -L -o qdrant.tar.gz \
  https://github.com/qdrant/qdrant/releases/download/<vX.Y.Z>/qdrant-aarch64-apple-darwin.tar.gz
tar xzf qdrant.tar.gz
mkdir -p ~/.qdrant/bin && mv qdrant ~/.qdrant/bin/
~/.qdrant/bin/qdrant --version
```
⚠️ macOS Gatekeeper may quarantine a downloaded binary: `xattr -d com.apple.quarantine ~/.qdrant/bin/qdrant` if it refuses to run. (Homebrew binaries are already notarized → no quarantine; prefer Option A unless you need a specific version.)

**Decision for tomorrow:** start with **Homebrew (A)** unless we need a version Homebrew doesn't have.

---

## 2. Configure storage + run as a launchd service

Qdrant needs a storage dir + a minimal config. Keep it OUT of the mempalace palace dirs (Qdrant
manages its own store; MemPalace just talks to it over REST).

```bash
mkdir -p ~/.qdrant/storage ~/.qdrant/snapshots
cat > ~/.qdrant/config.yaml <<'YAML'
storage:
  storage_path: /Users/alexander/.qdrant/storage
  snapshots_path: /Users/alexander/.qdrant/snapshots
  # on_disk vectors are set per-collection by the venv patch (when int8 enabled),
  # not globally — leave storage defaults here.
service:
  host: 127.0.0.1        # localhost-only (same posture as sidecar/SearXNG)
  http_port: 6333
  grpc_port: 6334
log_level: INFO
YAML
```

**launchd plist** `~/Library/LaunchAgents/com.brain-agent.qdrant.plist` (mirrors the existing
service plists — KeepAlive, logs to a file). Sketch:
```xml
<dict>
  <key>Label</key><string>com.brain-agent.qdrant</string>
  <key>ProgramArguments</key>
  <array>
    <string>/opt/homebrew/bin/qdrant</string>          <!-- or ~/.qdrant/bin/qdrant -->
    <string>--config-path</string>
    <string>/Users/alexander/.qdrant/config.yaml</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>/Users/alexander/.qdrant/qdrant.log</string>
  <key>StandardErrorPath</key><string>/Users/alexander/.qdrant/qdrant.error.log</string>
</dict>
```
```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.brain-agent.qdrant.plist
# smoke test the service (independent of Brain):
curl -s http://127.0.0.1:6333/healthz        # expect ok
curl -s http://127.0.0.1:6333/collections    # expect empty list
```
(For the very first run we can also just `qdrant --config-path …` in a foreground terminal to
watch it boot before committing to launchd.)

**Install the Python client into the mempalace venv** (the backend uses raw REST for
create/query, but `_client()` / health paths may import it):
```bash
~/.mempalace/venv/bin/pip install qdrant-client
~/.mempalace/venv/bin/python -c "import qdrant_client; print(qdrant_client.__version__)"
```

---

## 3. Point Brain at Qdrant (fresh palace dir, like the sqlite_exact switch)

Same pattern that worked for sqlite_exact — **fresh palace dir** to avoid `BackendMismatchError`
(the dir must not already contain another backend's artifacts), and **env at the launchd seam**
(the `~/.mempalace/config.json` `backend` key does NOT apply to the brain palace because its
`palace_path` is `/palace`, not our brain dir — confirmed during the sqlite_exact switch).

```bash
mkdir -p ~/.mempalace/brain-qdrant     # fresh, empty
```

Edit `~/Library/LaunchAgents/com.brain-agent.server.plist` → `EnvironmentVariables`:
```
MEMPALACE_BACKEND          = qdrant          # was sqlite_exact
MEMPALACE_QDRANT_URL       = http://127.0.0.1:6333
MEMPALACE_EMBEDDING_MODEL  = embeddinggemma  # unchanged
MEMPALACE_EMBEDDING_DEVICE = mlx             # unchanged — NEVER auto/coreml (NaN trap)
# Quantization stays OFF for the first cutover:
# (do NOT set MEMPALACE_QDRANT_QUANTIZATION yet)
```
Use `plutil -replace EnvironmentVariables.MEMPALACE_BACKEND -string qdrant …` etc.

Edit Brain `config.json` → `mempalace.palace_path` → `/Users/alexander/.mempalace/brain-qdrant`
(keep `_chroma_rollback_path`; add `_sqlite_rollback_path: /Users/alexander/.mempalace/brain-sqlite`).
Back up first: `cp config.json config.json.bak-preqdrant`.

---

## 4. Pre-flight dry-run (before restarting the server)

Prove the backend opens + round-trips on the fresh dir, exactly as we did for sqlite_exact:
```bash
MEMPALACE_PALACE_PATH=~/.mempalace/brain-qdrant MEMPALACE_BACKEND=qdrant \
MEMPALACE_QDRANT_URL=http://127.0.0.1:6333 \
MEMPALACE_EMBEDDING_MODEL=embeddinggemma MEMPALACE_EMBEDDING_DEVICE=mlx \
~/.mempalace/venv/bin/python - <<'PY'
from mempalace.palace import get_collection
col = get_collection("/Users/alexander/.mempalace/brain-qdrant", create=True)
print("type:", type(col).__name__)                      # EmbeddingCollection
col.add(documents=["The capital of France is Paris."], ids=["t1"],
        metadatas=[{"wing":"test","room":"r"}])
r = col.query(query_texts=["capital of France?"], n_results=1)
print("hit:", r["ids"][0], "dist:", r["distances"][0])  # expect t1, low dist
col.delete(ids=["t1"]); print("count:", col.count())    # expect 0
PY
```
PASS = a Qdrant collection is created, query_texts auto-embeds (the wrapper), hit returned, no
`BackendMismatchError`, no NaN. Confirms the same path Brain uses works on Qdrant.

---

## 5. Clear cursors + restart → daemons re-mine into Qdrant

```bash
# back up + clear chat-sync cursors so chats re-mine into the fresh Qdrant palace
sqlite3 agents/main/chats.db ".backup '/tmp/chats_preqdrant.db'"   # full backup
sqlite3 agents/main/chats.db "DELETE FROM chat_mempalace_sync;"
python3 -m py_compile brain.py        # per the compile-check rule
launchctl kickstart -k gui/$(id -u)/com.brain-agent.server   # NO -k? -- see note
```
⚠️ **Restart correctly:** the server plist is KeepAlive. Use
`launchctl kill SIGTERM gui/$(id -u)/com.brain-agent.server` then it auto-restarts, OR
`bootout` + `bootstrap`. **NEVER SIGKILL / `kickstart -k`** ([[feedback_never_sigkill_brain]]) —
though note that with sqlite_exact/Qdrant there's no HNSW to corrupt, the rule still stands for
the chat/context SQLite DBs. Safest: `launchctl kickstart gui/$(id -u)/com.brain-agent.server`
(no `-k`) after a graceful stop, exactly as we did today.

Then watch the re-mine fill Qdrant (same as today — count climbs, ~853 chat-sync cursors
written back):
```bash
curl -s http://127.0.0.1:6333/collections/mempalace_drawers | python3 -m json.tool | grep points_count
```

---

## 6. Verify + acceptance

- `GET /v1/status` version == 9.90.1.
- Live semantic query returns relevant hits (re-run the project audio overview — it should
  complete; that was the original symptom).
- `curl /collections/mempalace_drawers` points_count ≈ 16,884 once mining settles.
- No `BackendMismatchError` / NaN / tracebacks in `~/.brain-agent/server.error.log`.
- Qdrant log clean (`~/.qdrant/qdrant.error.log`).

---

## 7. (Optional, after §6 passes) flip int8 quantization ON

Only after correctness is confirmed unquantized — so a recall change is attributable to
quantization, not the backend swap:
```
# launchd plist EnvironmentVariables:
MEMPALACE_QDRANT_QUANTIZATION = int8
```
Then **drop + re-mine** the collection (quantization is set at collection-create time by the
patch — an existing collection won't retroactively quantize). Re-run a query; confirm recall
still good. At dev-box scale (~17k vectors) RAM isn't a concern — this step is really a
*rehearsal* of the prod int8 path, not a dev-box necessity. Skip if you just want it working.

---

## 8. Rollback (any step)

- **To sqlite_exact (today's working state):** plist `MEMPALACE_BACKEND=sqlite_exact`,
  `config.json` palace_path → `~/.mempalace/brain-sqlite`, restart. (sqlite_exact palace is
  untouched — it's a different dir.)
- **To chroma:** restore `config.json.bak-presqlite` + plist backups, palace_path → `/brain`.
- All three palaces coexist on disk (`brain` = chroma, `brain-sqlite`, `brain-qdrant`) — switch
  is just env + path + restart. Nothing destroyed until we choose to clean up.

---

## 9. Notes / gotchas carried from today

- **Embedding env MUST be set + identical** for mine + query, `mlx` (or `cpu`), NEVER
  `auto`/`coreml` (100% NaN on this Mac).
- **Fresh palace dir per backend** — `get_collection` raises `BackendMismatchError` if a dir
  holds another backend's artifacts (chroma.sqlite3 / sqlite_exact.sqlite3 / qdrant_backend.json).
- **The `backend` key in `~/.mempalace/config.json` does NOT apply to the brain palace** (path
  mismatch: config says `/palace`, we use `/brain-*`). Drive backend via `MEMPALACE_BACKEND` env
  only.
- The int8 venv patch is **inert unless `MEMPALACE_QDRANT_QUANTIZATION` is truthy** — safe to
  leave installed during the unquantized cutover.
- **Qdrant native binary is single-machine.** This dev-box plan ≠ prod HA; prod sizing/quant
  decisions live in [[project_qdrant_migration_plan]] + get settled by the scale eval.
