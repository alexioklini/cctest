"""Background daemon loops, extracted from server.py main().

Each of the seven long-running background daemons (file-change watcher,
MemPalace miner, MemPalace chat-sync, MemPalace project-sync, user-profile
maintainer, warmup keeper) lived as a closure nested inside server.main().
They are lifted here verbatim — same poll intervals, gating, error handling.

Cycle safety: this module imports `brain` (one-way) and the peer DB/auth
helpers from server_lib, but NEVER `server`. The handful of server.py-internal
singletons the daemons touch (server_config, warm_pool, the _warmup_wakeup /
_project_sync_* events+locks, _purge_drawers_by_room_and_source,
_profile_run_synchronous) are reached through the `srv` parameter — the server
module object, passed by main() at thread-spawn time (runtime, so no import
cycle). The start sites stay in server.main(); only the loop bodies moved here.

Invariant #5 (CLAUDE.md): the chat-sync classifier set-site still scopes
`current_user_id` for every classify call — now via
`with engine.request_context(current_user_id=...)` (Tier-G), which tears the
scope down automatically (replacing the old set-then-finally-restore pattern).
"""

import contextlib  # noqa: F401  (used by miner cycle via redirect_stdout)
import datetime  # noqa: F401  (project-sync run timestamps)
import hashlib  # noqa: F401  (chat-sync summary/closet hashing)
import io  # noqa: F401  (miner stdout capture)
import json  # noqa: F401  (chat-sync / project-sync payload handling)
import os
import re  # noqa: F401  (git-source wing → clone-dir sanitisation)
import sqlite3
import subprocess  # noqa: F401  (git clone/pull for source mining)
import time

import brain as engine
from server_lib import auth as _auth_mod  # noqa: F401  (miner/profile user lookups)
from server_lib.db import (  # noqa: F401
    ChatDB,
    _db_conn,
    _project_wing,
    _resolve_session_wing,
)


# Lifted from main()-local: only _file_change_watcher touches it. Module-level
# here (per-process singleton, exactly as the original main()-local dict was).
_file_mtimes: dict[str, float] = {}

# Lifted from main()-local constant (was _MEMPALACE_YAML_MARKER in main()).
_MEMPALACE_YAML_MARKER = "# managed by brain-agent server.py — do not edit\n"


def _file_change_watcher(srv):
    """Poll agent dirs for .md file changes and trigger post-write pipeline.
    Catches files created/modified by the SDK subprocess which bypasses _after_file_write."""
    import glob as _glob
    # Initial scan
    for agent_id in engine.list_agents():
        agent_dir = os.path.join(engine.AGENTS_DIR, agent_id)
        for path in _glob.glob(os.path.join(agent_dir, "*.md")):
            try:
                _file_mtimes[path] = os.path.getmtime(path)
            except OSError:
                pass
    while True:
        time.sleep(10)  # Check every 10 seconds
        try:
            for agent_id in engine.list_agents():
                agent_dir = os.path.join(engine.AGENTS_DIR, agent_id)
                for path in _glob.glob(os.path.join(agent_dir, "*.md")):
                    try:
                        mtime = os.path.getmtime(path)
                    except OSError:
                        continue
                    prev = _file_mtimes.get(path)
                    if prev is None or mtime > prev:
                        _file_mtimes[path] = mtime
                        if prev is not None:
                            # File was modified — trigger post-write pipeline
                            try:
                                engine._after_file_write(path, action="modified", agent_id=agent_id)
                            except Exception:
                                pass
        except Exception:
            pass


def _mempalace_yaml_for_artifacts(wing: str) -> str:
    # Default-room "general" satisfies miner.detect_room fallback. Rooms
    # field must be a list per miner spec, even if minimal.
    return (
        _MEMPALACE_YAML_MARKER
        + "wing: " + wing + "\n"
        + "rooms:\n"
        + "  - name: artifacts\n"
        + "    description: Files produced during chats and tasks\n"
        + "    keywords: [report, output, document]\n"
        + "  - name: general\n"
        + "    description: Fallback room\n"
        + "    keywords: [general]\n"
    )


def _ensure_mempalace_yaml(project_dir: str, wing: str) -> bool:
    """Write a mempalace.yaml if missing, if the brain-managed marker is
    gone, or if the wing line in the file disagrees with `wing` (the
    expected wing for the caller). Returns True if the file is present
    and matches `wing` (existing or freshly written)."""
    try:
        yaml_path = os.path.join(project_dir, "mempalace.yaml")
        existing_ok = False
        if os.path.isfile(yaml_path):
            try:
                with open(yaml_path, "r", encoding="utf-8", errors="replace") as f:
                    head = f.read(400)
                has_marker = (head.startswith(_MEMPALACE_YAML_MARKER)
                              or "wing:" in head)
                wing_matches = f"wing: {wing}" in head
                if has_marker and wing_matches:
                    existing_ok = True
            except Exception:
                existing_ok = False
        if existing_ok:
            return True
        os.makedirs(project_dir, exist_ok=True)
        with open(yaml_path, "w", encoding="utf-8") as f:
            f.write(_mempalace_yaml_for_artifacts(wing))
        return True
    except Exception as e:
        print(f"[mempalace-miner] failed to write yaml in {project_dir}: {e}", flush=True)
        return False


def _purge_orphan_chroma_queue(palace_path: str):
    """One-shot cleanup: remove embeddings_queue rows whose target segment
    has no max_seq_id bootstrap (= compactor never saw them, never will).
    Safe — these have been dead since 2026-04-19 and don't affect new writes."""
    try:
        db_path = os.path.join(palace_path, "chroma.sqlite3")
        if not os.path.isfile(db_path):
            return
        con = sqlite3.connect(db_path)
        cur = con.cursor()
        # Find segments without a max_seq_id row — those are the orphans
        orphan_segments = [r[0] for r in cur.execute(
            "SELECT s.id FROM segments s "
            "LEFT JOIN max_seq_id m ON m.segment_id = s.id "
            "WHERE m.seq_id IS NULL"
        ).fetchall()]
        if not orphan_segments:
            con.close()
            return
        # Each segment belongs to a collection; queue rows reference the
        # collection via topic suffix (last 36 chars = collection UUID).
        collection_uuids = [r[0] for r in cur.execute(
            "SELECT DISTINCT collection FROM segments WHERE id IN ({})".format(
                ",".join("?" * len(orphan_segments))
            ),
            orphan_segments,
        ).fetchall()]
        total = 0
        for cuid in collection_uuids:
            # Only purge rows older than 24h — leave today's writes alone
            cutoff = time.time() - 86400
            n = cur.execute(
                "DELETE FROM embeddings_queue "
                "WHERE topic LIKE ? AND strftime('%s', created_at) < ?",
                (f"%{cuid}", str(int(cutoff))),
            ).rowcount
            total += n
        con.commit()
        con.close()
        if total:
            print(f"[mempalace-miner] purged {total} stale queue row(s) "
                  f"(orphan segments: {len(orphan_segments)})", flush=True)
    except Exception as e:
        print(f"[mempalace-miner] queue cleanup failed: {type(e).__name__}: {e}", flush=True)


def _list_chat_artifact_folders():
    """Iterate per-agent artifact folders. Yields tuples
    (agent_id, folder_path, folder_name, kind) where kind is 'sched' or 'chat'."""
    try:
        for agent_id in os.listdir(engine.AGENTS_DIR):
            agent_root = os.path.join(engine.AGENTS_DIR, agent_id)
            if not os.path.isdir(agent_root):
                continue
            if agent_id.startswith(".") or agent_id == "main" and False:
                pass
            artifacts_root = os.path.join(agent_root, "artifacts")
            if not os.path.isdir(artifacts_root):
                continue
            for folder_name in os.listdir(artifacts_root):
                folder_path = os.path.join(artifacts_root, folder_name)
                if not os.path.isdir(folder_path):
                    continue
                # Folder names are <date>_<sid_prefix>. sched-task folders
                # use the sched-* prefix as the sid (brain.py L11319).
                parts = folder_name.split("_", 1)
                sid_part = parts[1] if len(parts) > 1 else parts[0]
                kind = "sched" if sid_part.startswith("sched-") else "chat"
                yield (agent_id, folder_path, folder_name, kind)
    except Exception as e:
        print(f"[mempalace-miner] discovery error: {type(e).__name__}: {e}", flush=True)


def _file_text_or_none(path: str, max_bytes: int = 2 * 1024 * 1024) -> str | None:
    """Read a file as text (utf-8, errors=replace). Returns None for
    binary blobs we can't reasonably treat as text."""
    try:
        size = os.path.getsize(path)
        if size <= 0 or size > max_bytes:
            return None
        with open(path, "rb") as f:
            blob = f.read()
        # Heuristic: skip if >5% null bytes
        if blob.count(b"\x00") > max(1, len(blob) // 20):
            return None
        return blob.decode("utf-8", errors="replace")
    except Exception:
        return None


def _sched_run_skipped_by_owner_pref(sid_prefix: str) -> bool:
    """For a sched folder named <date>_sched-<run_id>, resolve run → schedule
    → owner → preferences.memory_sched_default. Returns True iff the owner
    explicitly opted out (pref == 0). Anything else (no owner, pref unset,
    pref ≥ 1) keeps the default 'file artifacts' behavior."""
    if not sid_prefix.startswith("sched-"):
        return False
    try:
        run_id_part = sid_prefix[len("sched-"):]
        if "-" in run_id_part:  # sched-adhoc-<ts>
            return False
        run_id = int(run_id_part)
    except (ValueError, TypeError):
        return False
    try:
        sched_db = os.path.join(engine.AGENTS_DIR, "main", "scheduler.db")
        with sqlite3.connect(sched_db) as conn:
            row = conn.execute(
                "SELECT s.user_id FROM schedule_history h "
                "JOIN schedules s ON h.schedule_id = s.id "
                "WHERE h.id = ?",
                (run_id,),
            ).fetchone()
        if not row or not row[0]:
            return False
        uid = row[0]
    except Exception:
        return False
    try:
        user = _auth_mod.AuthDB.get_user(uid)
    except Exception:
        return False
    if not user:
        return False
    prefs = user.get("preferences") or {}
    v = prefs.get("memory_sched_default")
    return v == 0


def _mempalace_miner_loop(srv):
    mcfg = engine._load_mempalace_config()
    if not mcfg.get("enabled", True):
        print("[mempalace-miner] disabled (mempalace.enabled = false)", flush=True)
        return
    mine_cfg = mcfg.get("mine", {}) or {}
    if not mine_cfg.get("enabled", True):
        print("[mempalace-miner] disabled (mempalace.mine.enabled = false)", flush=True)
        return
    ok, err = engine._ensure_mempalace_importable()
    if not ok:
        print(f"[mempalace-miner] {err}", flush=True)
        return
    try:
        from mempalace import miner as mp_miner
        from mempalace.mcp_server import tool_add_drawer
    except Exception as e:
        print(f"[mempalace-miner] import failed: {e}", flush=True)
        return

    palace_path = mcfg.get("palace_path", "")
    interval = int(mine_cfg.get("interval_seconds", 1800))
    respect_git = bool(mine_cfg.get("respect_gitignore", True))

    # One-shot: purge orphaned queue rows from earlier runs
    _purge_orphan_chroma_queue(palace_path)
    # One-shot: drop drawers from the deprecated user_daily_summary room.
    # Replaced in v8.17.0 by the per-user profile file under
    # agents/main/user_profiles/. Idempotent — second call is a no-op once
    # the room is empty across all wings.
    try:
        from mempalace.mcp_server import tool_list_drawers as _tld
        # Walk every user wing (cheap — list_drawers paginates by room
        # within wing, so one query per user is bounded).
        try:
            _users = _auth_mod.AuthDB.list_users() if _auth_mod else []
        except Exception:
            _users = []
        _purged_total = 0
        for _u in _users:
            _uid = _u.get("id") or ""
            if not _uid:
                continue
            try:
                _purged_total += srv._purge_drawers_by_room_and_source(
                    wing=f"{_uid}--main",
                    room="user_daily_summary",
                )
            except Exception:
                pass
        if _purged_total:
            print(f"[startup-purge] dropped {_purged_total} legacy "
                  f"user_daily_summary drawer(s)", flush=True)
    except ImportError:
        pass
    except Exception as e:
        print(f"[startup-purge] failed: {type(e).__name__}: {e}", flush=True)

    # Small startup delay so we don't compete with initial provider probes.
    time.sleep(15)

    intermediate_exts = engine._ARTIFACT_INTERMEDIATE_EXTS

    while True:
        try:
            mcfg2 = engine._load_mempalace_config()
            if not mcfg2.get("enabled", True):
                return
            mine2 = mcfg2.get("mine") or {}
            # Build a session_id → save_to_memory map once per cycle
            memory_modes = ChatDB.session_memory_modes() or {}

            drawers_filed = 0
            folders_seen = 0
            folders_skipped_chat = 0
            folders_sched = 0
            sources_filed = 0

            # ── GitHub source mining (mine.git_sources) ─────────────────
            # Clone/pull the brain-agent source from GitHub and mine it into a
            # shared wing (e.g. brain_code) so Brainy can SEARCH the code
            # semantically — the missing piece behind "look it up in the source
            # when the docs don't cover it". Source MUST come from GitHub, not
            # a local path: in production there is no source tree on disk, and
            # a local checkout would also drift from the deployed build. We
            # shallow-clone into <palace>/.brain-source-clone/<wing> and `git
            # pull` on later cycles (cheap, always current within one cycle).
            # respect_gitignore keeps secrets (config.json etc.) out of the
            # index since they're gitignored in the repo.
            for src in (mine2.get("git_sources") or []):
                repo_url = (src or {}).get("repo_url") or ""
                src_wing = (src or {}).get("wing") or ""
                branch = (src or {}).get("branch") or "main"
                if not repo_url or not src_wing:
                    continue
                clone_root = os.path.join(palace_path, ".brain-source-clone")
                clone_dir = os.path.join(clone_root, re.sub(r"[^A-Za-z0-9_.-]", "_", src_wing))
                try:
                    os.makedirs(clone_root, exist_ok=True)
                    if os.path.isdir(os.path.join(clone_dir, ".git")):
                        subprocess.run(["git", "-C", clone_dir, "fetch", "--depth", "1", "origin", branch],
                                       check=True, capture_output=True, timeout=120)
                        subprocess.run(["git", "-C", clone_dir, "reset", "--hard", f"origin/{branch}"],
                                       check=True, capture_output=True, timeout=60)
                    else:
                        subprocess.run(["git", "clone", "--depth", "1", "--branch", branch,
                                        repo_url, clone_dir],
                                       check=True, capture_output=True, timeout=300)
                except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as e:
                    print(f"[mempalace-miner] git source {src_wing}: clone/pull failed: "
                          f"{type(e).__name__}: {e}", flush=True)
                    continue
                if not _ensure_mempalace_yaml(clone_dir, src_wing):
                    continue
                buf = io.StringIO()
                try:
                    with contextlib.redirect_stdout(buf):
                        mp_miner.mine(
                            project_dir=clone_dir,
                            palace_path=palace_path,
                            wing_override=src_wing,
                            agent="brain-miner-source",
                            respect_gitignore=True,
                        )
                    for line in buf.getvalue().splitlines():
                        s = line.strip()
                        if s.startswith("Drawers filed"):
                            try:
                                sources_filed += int(s.split(":")[-1].strip().split()[0])
                            except Exception:
                                pass
                            break
                except SystemExit:
                    pass
                except Exception as e:
                    print(f"[mempalace-miner] git source {src_wing}: mine failed: "
                          f"{type(e).__name__}: {e}", flush=True)

                # Rebuild the code structure graph from the SAME fresh clone,
                # so Brainy's code_graph_query (file_summary / callers_of / …)
                # reflects current source — not a stale local checkout. The
                # graph DB is a single shared store; we only rebuild it from
                # the configured code wing (brain_code), incremental so only
                # changed files re-parse. Best-effort: a graph failure must not
                # break the mining cycle.
                if src_wing == "brain_code":
                    try:
                        _cg = engine._get_code_graph()
                        _cg_stats = _cg.build(clone_dir, incremental=True)
                        if isinstance(_cg_stats, dict) and _cg_stats.get("error"):
                            print(f"[mempalace-miner] code-graph build: "
                                  f"{_cg_stats['error']}", flush=True)
                    except Exception as e:
                        print(f"[mempalace-miner] code-graph build failed: "
                              f"{type(e).__name__}: {e}", flush=True)

            for agent_id, folder_path, folder_name, kind in _list_chat_artifact_folders():
                folders_seen += 1
                # Reconstruct session_id from folder name. For chat folders
                # we only have the first 8 chars of the session_id; do a
                # prefix lookup. For sched-* folders the prefix already is
                # the full sched-<run_id> form.
                parts = folder_name.split("_", 1)
                sid_prefix = parts[1] if len(parts) > 1 else parts[0]

                if kind == "sched":
                    folders_sched += 1
                    # Per-user opt-out: if the schedule's owner has set
                    # `memory_sched_default = 0`, skip filing this run's
                    # artifacts. Default behavior (pref=null/1/2) keeps
                    # the legacy "always file" path.
                    if _sched_run_skipped_by_owner_pref(sid_prefix):
                        continue
                    # Sched: file only output-role files (extension-based)
                    for fname in os.listdir(folder_path):
                        fpath = os.path.join(folder_path, fname)
                        if not os.path.isfile(fpath):
                            continue
                        ext = fname.rsplit(".", 1)[-1].lower() if "." in fname else ""
                        if ext in intermediate_exts:
                            continue
                        text = _file_text_or_none(fpath)
                        if not text:
                            continue
                        wing = f"{agent_id}_artifacts"
                        try:
                            res = tool_add_drawer(
                                wing=wing,
                                room="artifacts",
                                content=text[:8000],
                                source_file=f"session/{sid_prefix}#artifact/{fname}",
                                added_by="brain-miner-sched",
                            )
                            if isinstance(res, dict) and res.get("success") \
                               and res.get("reason") != "already_exists":
                                drawers_filed += 1
                        except Exception as ex:
                            print(f"[mempalace-miner] sched add_drawer failed "
                                  f"{fname}: {ex}", flush=True)
                    continue

                # Chat: gate on the parent session's save_to_memory toggle.
                full_sid = ChatDB.session_id_for_prefix(sid_prefix)
                mem_mode = memory_modes.get(full_sid, 0) if full_sid else 0
                if mem_mode <= 0:
                    folders_skipped_chat += 1
                    continue

                # Memory ON: ensure yaml + run miner over the folder.
                wing = f"{agent_id}_artifacts"
                if not _ensure_mempalace_yaml(folder_path, wing):
                    continue
                buf = io.StringIO()
                try:
                    with contextlib.redirect_stdout(buf):
                        mp_miner.mine(
                            project_dir=folder_path,
                            palace_path=palace_path,
                            wing_override=wing,
                            agent="brain-miner-chat",
                            respect_gitignore=False,
                        )
                    out = buf.getvalue().strip()
                    for line in out.splitlines():
                        s = line.strip()
                        if s.startswith("Drawers filed"):
                            # "Drawers filed: N" — pull the integer
                            try:
                                drawers_filed += int(s.split(":")[-1].strip().split()[0])
                            except Exception:
                                pass
                            break
                except SystemExit:
                    # miner.load_config calls sys.exit on bad yaml — should not
                    # happen now that we always write one, but be defensive.
                    pass
                except Exception as e:
                    print(f"[mempalace-miner] chat folder {folder_name}: "
                          f"{type(e).__name__}: {e}", flush=True)

            print(f"[mempalace-miner] cycle: filed={drawers_filed} folders={folders_seen} "
                  f"(sched={folders_sched} chat-skip={folders_skipped_chat}) "
                  f"sources_filed={sources_filed}", flush=True)
        except Exception as e:
            print(f"[mempalace-miner] cycle error: {type(e).__name__}: {e}", flush=True)

        next_interval = int(((mcfg2.get("mine") or {}).get("interval_seconds", interval)))
        time.sleep(max(60, next_interval))


def _extract_references_from_tool_payload(tool_name, payload):
    """Turn a tool_result payload into a list of {title, url, snippet} dicts.

    Mirrors web/index.html's `extractReferencesFromToolResult` but server-side.
    Defensive against both raw dicts and JSON-string payloads.
    """
    if payload is None:
        return []
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (json.JSONDecodeError, ValueError):
            # Plain text — treat the whole thing as a single "snippet" record
            # only if it looks non-trivial. For web_fetch raw HTML we'd still
            # want to ingest, so take the first 4k chars.
            txt = payload.strip()
            if not txt:
                return []
            return [{"title": tool_name, "url": "", "snippet": txt[:4000]}]
    if not isinstance(payload, dict):
        return []

    refs = []
    # exa_search shape: {query, results: [{title, link, snippet}, ...]}
    if tool_name == "exa_search" or "results" in payload:
        for r in payload.get("results", []) or []:
            if not isinstance(r, dict):
                continue
            refs.append({
                "title": r.get("title", "") or r.get("name", ""),
                "url": r.get("link", "") or r.get("url", ""),
                "snippet": (r.get("snippet", "") or r.get("highlight", ""))[:3000],
            })
    # web_fetch shape: {url, status, length, content}
    elif tool_name == "web_fetch" or "content" in payload:
        content = payload.get("content", "") or payload.get("text", "")
        if content:
            refs.append({
                "title": payload.get("title", "") or tool_name,
                "url": payload.get("url", ""),
                "snippet": content[:4000],
            })
    # read_document shape: varies by parser; usually {pages|sheets|text}
    elif tool_name == "read_document":
        text = (
            payload.get("text")
            or payload.get("content")
            or json.dumps(payload, ensure_ascii=False)[:4000]
        )
        refs.append({
            "title": payload.get("filename", "") or payload.get("path", "") or "document",
            "url": payload.get("path", ""),
            "snippet": text[:4000],
        })
    return refs


def _mempalace_chat_sync_loop(srv):
    mcfg = engine._load_mempalace_config()
    if not mcfg.get("enabled", True):
        return
    sync_cfg = mcfg.get("chat_sync", {}) or {}
    if not sync_cfg.get("enabled", True):
        print("MemPalace chat-sync: disabled")
        return
    ok, err = engine._ensure_mempalace_importable()
    if not ok:
        print(f"MemPalace chat-sync: {err}")
        return
    # Ensure downstream mempalace.mcp_server uses the right palace path.
    palace_path = mcfg.get("palace_path", "")
    if palace_path:
        os.environ.setdefault("MEMPALACE_PALACE_PATH", palace_path)
    try:
        from mempalace.mcp_server import tool_add_drawer
        from mempalace.miner import detect_hall
        from mempalace.palace import (
            get_collection as _get_drawers_col,
            get_closets_collection,
            build_closet_lines,
            purge_file_closets,
            upsert_closet_lines,
        )
    except Exception as e:
        print(f"MemPalace chat-sync: import failed: {e}")
        return

    # Small delay so we don't fight the miner on cold start.
    time.sleep(20)

    while True:
        try:
            mcfg2 = engine._load_mempalace_config()
            if not mcfg2.get("enabled", True):
                return
            sync_cfg2 = mcfg2.get("chat_sync", {}) or {}
            if not sync_cfg2.get("enabled", True):
                time.sleep(60)
                continue

            include_roles = set(sync_cfg2.get("include_roles", ["user", "assistant"]))
            include_tool_results = set(sync_cfg2.get("include_tool_results", []) or [])
            max_chars = int(sync_cfg2.get("max_chars_per_message", 8000))
            include_summary = bool(sync_cfg2.get("include_session_summary", True))
            do_attach_meta = bool(sync_cfg2.get("attachment_metadata_drawer", True))
            do_closets = bool(sync_cfg2.get("build_closets", True))
            closet_head = int(sync_cfg2.get("closet_content_head_chars", 5000))
            default_room = sync_cfg2.get("room", "chat")

            # Classifier gate config
            clf_cfg = sync_cfg2.get("classifier", {}) or {}
            clf_enabled = bool(clf_cfg.get("enabled", False))
            clf_model = clf_cfg.get("model", "")
            clf_file_categories = set(clf_cfg.get("categories_to_file",
                ["fact", "preference", "decision", "reference"]))
            clf_min_turns = int(clf_cfg.get("min_turns", 0))

            closets_col = None
            if do_closets:
                try:
                    closets_col = get_closets_collection(palace_path, create=True)
                except Exception as ce:
                    print(f"[mempalace-chat-sync] closets collection unavailable: {ce}")
                    closets_col = None

            pending = ChatDB.mempalace_sessions_needing_sync() or []
            total_new = 0
            for session_row in pending:
                sid = session_row["session_id"]
                agent_id = session_row.get("agent_id") or "main"
                session_user_id = session_row.get("user_id") or ""
                after_id = int(session_row.get("last_message_id_filed") or 0)
                max_msg_id = int(session_row.get("max_message_id") or 0)
                # save_to_memory: 0=off, 1=on (save all), 2=auto (classifier/min_turns)
                mem_mode = int(session_row.get("save_to_memory") or 0)
                msg_count = int(session_row.get("message_count") or 0)

                # Off: skip entirely
                if mem_mode == 0:
                    ChatDB.mempalace_update_cursor(sid, max_msg_id)
                    continue

                # Auto/On: check min_turns (on=1 bypasses, auto=2 respects)
                if mem_mode == 2 and clf_min_turns > 0 and msg_count < clf_min_turns:
                    ChatDB.mempalace_update_cursor(sid, max_msg_id)
                    continue

                # Wing resolution (ID-only):
                #   project session → project_chat__<project_id>
                #     (NOT project__<id> — that's reserved for mined docs)
                #   team session    → team__<team_id>
                #   user session    → user__<user_id>
                #   anonymous       → "" (skipped)
                wing = _resolve_session_wing(session_row)
                if not wing:
                    # Anonymous — advance cursor so we don't reprocess
                    # forever, but don't file anything.
                    ChatDB.mempalace_update_cursor(sid, max_msg_id)
                    continue

                new_messages = ChatDB.mempalace_load_new_messages(sid, after_id) or []
                # Per (wing, room, source_file) → list[(drawer_id, text)] for closet rebuild.
                dirty_groups: dict[tuple, list] = {}

                def _file_drawer(w, r, content, source_file):
                    if not content:
                        return False
                    content = content[:max_chars]
                    engine.mempalace_activity.store_begin()
                    try:
                        try:
                            res = tool_add_drawer(
                                wing=w,
                                room=r,
                                content=content,
                                source_file=source_file,
                                added_by="brain-chat-sync",
                            )
                        except Exception as ex:
                            print(f"[mempalace-chat-sync] add_drawer failed: {ex}")
                            return False
                    finally:
                        engine.mempalace_activity.store_end()
                    if not isinstance(res, dict) or not res.get("success"):
                        return False
                    if res.get("reason") == "already_exists":
                        return False  # don't count dedup hits toward closet rebuild
                    # Stamp hall metadata (tool_add_drawer doesn't support it natively)
                    drawer_id = res.get("drawer_id", "")
                    if drawer_id:
                        try:
                            hall = detect_hall(content)
                            dcol = _get_drawers_col(palace_path, create=False)
                            if dcol and hall:
                                existing = dcol.get(ids=[drawer_id], include=["metadatas", "documents"])
                                if existing and existing["ids"]:
                                    meta = dict(existing["metadatas"][0])
                                    meta["hall"] = hall
                                    dcol.upsert(ids=[drawer_id], documents=existing["documents"], metadatas=[meta])
                        except Exception:
                            pass  # non-critical
                    group_key = (w, r, source_file)
                    dirty_groups.setdefault(group_key, []).append(
                        (drawer_id, content)
                    )
                    return True

                new_last_id = after_id

                # Build classifier skip-set: message IDs to skip based on LLM classification.
                # Skip classifier entirely if user toggled save_to_memory on this session.
                _clf_skip_ids: set[int] = set()
                if clf_enabled and clf_model and mem_mode == 2:
                    # Pin the chat owner's user_id on the daemon thread so
                    # client-mode ambient proxy can pick a tab of the right
                    # user. Empty for legacy sessions without owner — the
                    # picker returns None and the LLM call fails fast on
                    # an air-gapped server (same fail-fast contract as
                    # scheduled tasks; Stage 2 closes that hole).
                    with engine.request_context(current_user_id=session_user_id or ""):
                        i = 0
                        while i < len(new_messages):
                            m = new_messages[i]
                            m_role = (m.get("role") or "").strip()
                            m_id = int(m.get("id") or 0)
                            # Pair user+assistant for classification
                            if m_role == "user" and i + 1 < len(new_messages):
                                nxt = new_messages[i + 1]
                                nxt_role = (nxt.get("role") or "").strip()
                                nxt_id = int(nxt.get("id") or 0)
                                if nxt_role == "assistant":
                                    u_text = str(m.get("content") or "")[:2000]
                                    a_text = str(nxt.get("content") or "")[:2000]
                                    category = engine.classify_chat_for_memory(
                                        u_text, a_text, clf_model)
                                    if category and category not in clf_file_categories:
                                        _clf_skip_ids.add(m_id)
                                        _clf_skip_ids.add(nxt_id)
                                        print(f"[mempalace-classifier] skip ({category}): "
                                              f"{u_text[:60]}", flush=True)
                                    i += 2
                                    continue
                            i += 1

                # Track the current turn's anchor user-message id. Every drawer
                # filed from this turn (user, assistant, attachment, tool result)
                # inherits this id in its source_file so per-turn purge/memorise
                # can target one turn without touching neighbours.
                current_turn_id = 0
                # Seed with the last user id already in chats.db up to after_id so
                # orphan assistant messages (e.g. when the sync cursor advanced
                # mid-turn) still attach to their originating turn.
                try:
                    prior_last_user_id = ChatDB.mempalace_last_user_id_before(sid, after_id)
                except Exception:
                    prior_last_user_id = 0
                current_turn_id = int(prior_last_user_id or 0)

                for msg in new_messages:
                    mid = int(msg.get("id") or 0)
                    new_last_id = max(new_last_id, mid)
                    role = (msg.get("role") or "").strip()
                    content = msg.get("content")
                    meta = msg.get("metadata") or {}

                    # New user message opens a new turn.
                    if role == "user":
                        current_turn_id = mid

                    turn_suffix = f"#turn/{current_turn_id}" if current_turn_id else ""

                    # Normal chat turns.
                    if role in include_roles:
                        if mid in _clf_skip_ids:
                            continue
                        if isinstance(content, str):
                            text = content
                        else:
                            try:
                                text = json.dumps(content, ensure_ascii=False)
                            except Exception:
                                text = str(content)
                        body = f"[{role}] {text}".strip()
                        if body:
                            source_file = f"session/{sid}{turn_suffix}"
                            if _file_drawer(wing, default_room, body, source_file):
                                total_new += 1

                    # Attachment metadata.
                    if do_attach_meta and isinstance(meta, dict):
                        files = meta.get("files") or []
                        if isinstance(files, list):
                            for f in files:
                                if not isinstance(f, dict):
                                    continue
                                fname = f.get("name") or f.get("filename") or "unknown"
                                fmime = f.get("mime") or f.get("type") or "application/octet-stream"
                                fsize = f.get("size") or 0
                                body = (
                                    f"[attachment from {role or 'message'} "
                                    f"in session {sid}#{mid}]\n"
                                    f"filename: {fname}\n"
                                    f"mime: {fmime}\n"
                                    f"size: {fsize} bytes"
                                )
                                source_file = f"session/{sid}{turn_suffix}#attach/{mid}/{fname}"
                                if _file_drawer(wing, "chat_attachment", body, source_file):
                                    total_new += 1

                    # Tool-result references (allowlisted tools only).
                    if role == "tool" and include_tool_results and isinstance(content, (list, dict, str)):
                        # Brain stores tool results in several shapes; try to
                        # extract (tool_name, payload) pairs defensively.
                        tool_entries = []
                        if isinstance(content, list):
                            for item in content:
                                if isinstance(item, dict):
                                    tname = item.get("name") or item.get("tool_name") or ""
                                    payload = item.get("content") or item.get("result") or item
                                    tool_entries.append((tname, payload))
                        elif isinstance(content, dict):
                            tname = content.get("name") or content.get("tool_name") or ""
                            payload = content.get("content") or content.get("result") or content
                            tool_entries.append((tname, payload))
                        elif isinstance(meta, dict) and meta.get("tool_name"):
                            tool_entries.append((meta.get("tool_name"), content))

                        for tname, payload in tool_entries:
                            if tname not in include_tool_results:
                                continue
                            # Parse payload into a list of reference records.
                            refs = _extract_references_from_tool_payload(tname, payload)
                            for idx, ref in enumerate(refs):
                                ref_body = (
                                    f"[{tname} result from session {sid}#{mid}]\n"
                                    f"title: {ref.get('title','')}\n"
                                    f"url: {ref.get('url','')}\n\n"
                                    f"{ref.get('snippet','')}"
                                )
                                source_file = f"session/{sid}{turn_suffix}#tool/{tname}/{mid}/{idx}"
                                if _file_drawer(wing, "reference", ref_body, source_file):
                                    total_new += 1

                # Session summary — low-frequency text worth indexing separately.
                summary_hash = ""
                if include_summary:
                    summary = (session_row.get("summary") or "").strip()
                    if summary:
                        summary_hash = hashlib.sha256(summary.encode("utf-8")).hexdigest()[:16]
                        if summary_hash != (session_row.get("last_summary_hash") or ""):
                            body = f"[session summary for {sid}]\n{summary}"
                            source_file = f"session/{sid}#summary"
                            if _file_drawer(wing, "chat_summary", body, source_file):
                                total_new += 1

                # Rebuild closets per dirty group.
                if closets_col is not None and dirty_groups:
                    for (w, r, source_file), items in dirty_groups.items():
                        drawer_ids = [did for did, _ in items if did]
                        if not drawer_ids:
                            continue
                        concatenated = "\n\n".join(txt for _, txt in items)[:closet_head]
                        try:
                            purge_file_closets(closets_col, source_file)
                            lines = build_closet_lines(
                                source_file=source_file,
                                drawer_ids=drawer_ids,
                                content=concatenated,
                                wing=w,
                                room=r,
                            )
                            if lines:
                                closet_id_base = (
                                    f"{w}_{r}_"
                                    + hashlib.sha256(source_file.encode("utf-8")).hexdigest()[:12]
                                )
                                upsert_closet_lines(
                                    closets_col,
                                    closet_id_base,
                                    lines,
                                    {"source_file": source_file, "wing": w, "room": r},
                                )
                        except Exception as ce:
                            print(f"[mempalace-chat-sync] closet rebuild failed for {source_file}: {ce}")

                # Advance cursor even if nothing new was filed (all dedup'd) —
                # otherwise we keep re-scanning the same tail forever.
                ChatDB.mempalace_update_cursor(
                    sid,
                    max(new_last_id, max_msg_id),
                    last_summary_hash=summary_hash or session_row.get("last_summary_hash") or "",
                )

            if total_new:
                print(f"[mempalace-chat-sync] filed {total_new} new drawer(s) across {len(pending)} session(s)")
        except Exception as e:
            print(f"[mempalace-chat-sync] cycle error: {type(e).__name__}: {e}")

        next_interval = int((engine._load_mempalace_config().get("chat_sync") or {}).get("interval_seconds", 60))
        time.sleep(max(15, next_interval))


def _sync_project_web_urls(pdir, web_urls):
    """Fetch the project's configured web URLs into a `web-urls/` subfolder as
    hash-gated `.md` companion files, so the existing project-sync mine + KG
    pass treats each URL's content like any other project source file.

    Change detection mirrors the file path: re-fetch every cycle, but only
    rewrite the `.md` (→ re-mine + re-extract) when the fetched content's hash
    differs from what's on disk. Files for URLs no longer configured are
    deleted (the loop's stale-path purge then drops their drawers/triples).

    Returns the absolute path of the `web-urls/` folder (created on demand), or
    "" when there are no URLs configured.
    """
    import hashlib as _hl
    import datetime as _dt
    folder = os.path.join(pdir, "web-urls")
    urls = [(u.get("url") or "").strip() for u in (web_urls or []) if isinstance(u, dict)]
    urls = [u for u in urls if u]
    # Filename scheme: `<url-slug>_<YYYY-MM-DD-HHMM>.md`. The slug is the stable
    # per-URL identity (same URL → same slug every cycle); the timestamp marks
    # the LAST CONTENT CHANGE (= last mine), not the last fetch. So: unchanged
    # content keeps its file (name + timestamp) and is NOT re-mined; changed
    # content gets a fresh timestamped file and the old slug file(s) are
    # deleted (the loop's _is_stale_src then drops the old drawers because
    # their .md path no longer exists). A short URL-hash is appended to the
    # slug so two URLs that slugify the same never collide.
    def _url_slug(_u):
        from urllib.parse import urlparse as _up
        try:
            p = _up(_u)
            base = (p.netloc + p.path).strip("/")
        except Exception:
            base = _u
        s = re.sub(r"[^A-Za-z0-9]+", "-", base).strip("-").lower()
        s = s[:60] or "url"
        h = _hl.sha256(_u.encode("utf-8")).hexdigest()[:8]
        return f"{s}-{h}"

    # Recognise a web-url companion (current slug scheme OR legacy weburl-<hash>).
    def _is_weburl_md(fn):
        return fn.endswith(".md") and (fn.startswith("weburl-") or "_" in fn)

    if not urls:
        # No URLs: drop the folder's contents so stale-purge removes any
        # previously-mined URL drawers.
        if os.path.isdir(folder):
            for fn in os.listdir(folder):
                if _is_weburl_md(fn):
                    try:
                        os.remove(os.path.join(folder, fn))
                    except OSError:
                        pass
        return folder if os.path.isdir(folder) else ""
    os.makedirs(folder, exist_ok=True)
    # Slugs we keep this cycle; any other web-url .md is pruned at the end.
    kept_files = set()
    for entry in (web_urls or []):
        if not isinstance(entry, dict):
            continue
        url = (entry.get("url") or "").strip()
        if not url:
            continue
        title = (entry.get("title") or "").strip()
        slug = _url_slug(url)
        # Find an existing file for this slug (any timestamp).
        existing = sorted(
            fn for fn in os.listdir(folder)
            if fn.startswith(slug + "_") and fn.endswith(".md"))
        try:
            parsed = json.loads(engine.tool_web_fetch({"url": url, "force_fresh": True}))
        except (ValueError, TypeError):
            parsed = {}
        if parsed.get("error") or "content" not in parsed:
            # Transient fetch failure: keep the prior good copy untouched.
            print(f"[project-sync.weburl] fetch failed {url}: "
                  f"{parsed.get('error', 'unknown')}", flush=True)
            kept_files.update(existing)
            continue
        body = (f"<!-- brain-source: {url} -->\n"
                f"# {title or parsed.get('url', url)}\n\n"
                f"Source URL: {parsed.get('url', url)}\n\n{parsed['content']}\n")
        new_hash = _hl.sha256(body.encode('utf-8')).hexdigest()
        # Compare against the newest existing slug file (content hash).
        old_hash = ""
        if existing:
            try:
                with open(os.path.join(folder, existing[-1]), "r",
                          encoding="utf-8", errors="replace") as f:
                    old_hash = _hl.sha256(f.read().encode('utf-8')).hexdigest()
            except OSError:
                pass
        if new_hash == old_hash:
            # Unchanged content → keep the existing file as-is (no re-mine).
            kept_files.add(existing[-1])
            continue
        # Changed (or new) → write a fresh timestamped file, drop old ones.
        ts = _dt.datetime.now().strftime("%Y-%m-%d-%H%M")
        fname = f"{slug}_{ts}.md"
        fpath = os.path.join(folder, fname)
        try:
            with open(fpath, "w", encoding="utf-8") as f:
                f.write(body)
            kept_files.add(fname)
            # Delete previous versions of this slug so their drawers go stale.
            for old in existing:
                if old != fname:
                    try:
                        os.remove(os.path.join(folder, old))
                    except OSError:
                        pass
        except OSError as e:
            print(f"[project-sync.weburl] write failed {fpath}: {e}", flush=True)
            kept_files.update(existing)
    # Prune any web-url .md not kept this cycle (removed URLs + legacy files).
    for fn in os.listdir(folder):
        if _is_weburl_md(fn) and fn not in kept_files:
            try:
                os.remove(os.path.join(folder, fn))
            except OSError:
                pass
    return folder


def _project_sync_loop(srv):
    from engine import sync_log as _sync_log
    mcfg = engine._load_mempalace_config()
    if not mcfg.get("enabled", True):
        print("[project-sync] disabled (mempalace.enabled = false)", flush=True)
        return
    ok, err = engine._ensure_mempalace_importable()
    if not ok:
        print(f"[project-sync] {err}", flush=True)
        return
    try:
        from mempalace import miner as mp_miner
        from mempalace.mcp_server import tool_add_drawer  # noqa: F401  (used indirectly via miner)
        from mempalace.palace import get_collection as _get_drawers_col
    except Exception as e:
        print(f"[project-sync] import failed: {e}", flush=True)
        return

    # KG post-pass module — optional; tolerate import failure (the drawer
    # mining cycle still works). Loaded once per daemon start; if disabled
    # in config the helper short-circuits per call.
    try:
        from engine import kg_extract  # noqa: F401
    except Exception as e:
        kg_extract = None  # type: ignore[assignment]
        print(f"[project-sync] kg_extract import failed: "
              f"{type(e).__name__}: {e}", flush=True)

    # Document conversion module — converts .pdf/.docx into companion
    # .md files under <folder>/.brain-extracted/ before the miner runs.
    # The miner itself only reads .md/.txt/code extensions; without this
    # pass, PDFs dropped into an input folder are silently ignored.
    try:
        from engine import doc_convert  # noqa: F401
    except Exception as e:
        doc_convert = None  # type: ignore[assignment]
        print(f"[project-sync] doc_convert import failed: "
              f"{type(e).__name__}: {e}", flush=True)

    # Conversion preferences — markitdown vs legacy fitz/python-docx.
    # markitdown produces materially better markdown for LLM retrieval
    # (table structure, heading hierarchy, OCR fallback). Falls through
    # to legacy per-format extractors on any failure.
    def _conv_use_markitdown() -> bool:
        try:
            with open(engine.CONFIG_PATH) as f:
                top = json.load(f)
            conv = (top.get("conversion") or {})
            return bool(conv.get("use_markitdown", True))
        except Exception:
            return True

    palace_path = mcfg.get("palace_path", "")
    chats_db_path = os.path.join(engine.AGENTS_DIR, "main", "chats.db")

    def _run_kg_for(wing: str, source_prefix: str, item_set_fn,
                    item_kind: str, item_id: str):
        """Run the KG extraction post-pass scoped to (wing, source_prefix).
        Updates the per-item dict via `item_set_fn(kind, id, **fields)` so
        the UI sees triples_extracted + kg_state alongside drawers_filed.
        Refuses (safely) if kg_extract isn't loaded or KG is disabled.
        """
        if kg_extract is None:
            return
        kg_cfg = (engine._load_mempalace_config().get("kg") or {})
        if not kg_cfg.get("enabled", True):
            return
        scopes = kg_cfg.get("scopes") or ["projects"]
        if "projects" not in scopes:
            return
        # Resolve symlinks so the prefix matches what the miner stored in
        # drawer source_file (macOS /tmp → /private/tmp, /var → /private/var,
        # plus user-managed symlinks). Without this, every drawer-mining
        # cycle stores absolute resolved paths but our prefix filter uses
        # the un-resolved one and matches nothing.
        try:
            resolved_prefix = os.path.realpath(source_prefix) if source_prefix else source_prefix
        except OSError:
            resolved_prefix = source_prefix
        if resolved_prefix and not resolved_prefix.endswith(os.sep) \
                and source_prefix.endswith(os.sep):
            resolved_prefix += os.sep
        model = kg_cfg.get("extraction_model", "") or ""
        profile_name = kg_cfg.get("profile", "normative") or "normative"
        max_triples = int(kg_cfg.get("max_triples_per_drawer", 12))
        max_drawer_chars = int(kg_cfg.get("max_drawer_chars", 6000))
        min_conf = float(kg_cfg.get("min_confidence", 0.5))
        chunk_mode = (kg_cfg.get("chunking_mode") or "source_file").strip()
        if chunk_mode not in ("source_file", "per_drawer"):
            chunk_mode = "source_file"
        source_chunk_chars = int(kg_cfg.get("source_chunk_chars", 3500))
        try:
            import time as _time
            _kg_started_at = _time.time()
            _kg_chunks_done = [0]  # mutable cell for closure
            _kg_chunks_total = [0]

            def _kg_progress_cb(stage, **info):
                if stage == "extracting":
                    pass  # chunk started — total not yet known here
                elif stage in ("processed", "error"):
                    _kg_chunks_done[0] += 1
                    item_set_fn(item_kind, item_id,
                        kg_chunks_done=_kg_chunks_done[0],
                        kg_chunks_total=_kg_chunks_total[0],
                        kg_started_at=_kg_started_at,
                        kg_triples_live=info.get("running_total", 0))

            item_set_fn(item_kind, item_id,
                kg_state="extracting",
                kg_chunks_done=0,
                kg_chunks_total=0,
                kg_started_at=_kg_started_at,
                kg_triples_live=0)
            res = kg_extract.run_kg_post_pass(
                palace_path=palace_path, wing=wing,
                source_prefix=resolved_prefix,
                adapter_name="brain-project-kg",
                profile_name=profile_name, model=model,
                chats_db_path=chats_db_path,
                max_triples_per_drawer=max_triples,
                max_drawer_chars=max_drawer_chars,
                min_confidence=min_conf, skip_code=True,
                chunking_mode=chunk_mode,
                source_chunk_chars=source_chunk_chars,
                log_prefix="[project-sync.kg]",
                progress_cb=_kg_progress_cb,
            )
            _kg_chunks_total[0] = res.drawers_processed + res.drawers_skipped
            # Cumulative triple count for this source prefix, queried
            # straight from the KG. `res.triples_extracted` is the per-
            # cycle delta — fine to log, wrong for the UI's "M triples"
            # pill which should stay positive across cursor-skip cycles.
            # Cheap SQL: COUNT() over a prefix-scoped slice with the
            # adapter_name filter (3.3.3 schema).
            triples_cumulative = int(res.triples_extracted)
            try:
                cum_stats = kg_extract.kg_stats_for_wing(
                    palace_path=palace_path,
                    source_prefix=resolved_prefix,
                    adapter_name="brain-project-kg")
                triples_cumulative = int(cum_stats.get("triples", 0))
            except Exception:
                pass
            item_set_fn(item_kind, item_id,
                kg_state=("error" if res.errors and not res.triples_extracted
                          else "idle"),
                triples_extracted=triples_cumulative,
                triples_last_cycle=int(res.triples_extracted),
                kg_drawers_processed=int(res.drawers_processed),
                kg_parse_errors=int(res.errors),
                kg_last_error=res.error_msg or "",
                kg_elapsed_s=round(res.elapsed_s, 1))
        except Exception as e:
            item_set_fn(item_kind, item_id,
                kg_state="error",
                kg_last_error=f"{type(e).__name__}: {e}")
            print(f"[project-sync.kg] wing={wing} prefix={source_prefix} "
                  f"failed: {type(e).__name__}: {e}", flush=True)

    def _run_closet_regen_for(wing: str, source_prefix: str = ""):
        """Regenerate LLM-augmented closets for the wing using the same
        model the user selected for KG extraction. Closets boost the
        ranking of vector retrieval (mempalace_query) — this swaps
        MemPalace's regex-based closet generation for an LLM pass that
        captures implicit topics, foreign-language content, and
        contextual references the regex misses.

        Opt-in via mempalace.kg.regenerate_closets. The wrapper is
        **incremental** (kg_extract.run_closet_regen_incremental):
        walks the wing's source files, compares each file's (mtime,
        size) against the closet_regen_progress cursor in chats.db,
        and only triggers the upstream wing-wide rebuild when at
        least one source has changed since the last cycle. With 400
        unchanged PDFs the wrapper short-circuits in milliseconds;
        with one edited PDF it runs the full wing rebuild (upstream
        doesn't accept per-file filters yet) and refreshes every
        cursor row.
        """
        if kg_extract is None:
            return
        kg_cfg = (engine._load_mempalace_config().get("kg") or {})
        if not kg_cfg.get("regenerate_closets"):
            return
        model = kg_cfg.get("extraction_model", "") or ""
        if not model:
            return
        try:
            # Resolve the KG model's provider so we can hand closet_llm
            # the OpenAI-compatible endpoint + key directly. Reuses the
            # same plumbing the chat / KG paths use, so a single config
            # change in the GUI applies here too.
            prov = engine.resolve_provider_for_model(model)
            api_model = engine.get_api_model_id(model)
            endpoint = prov.get("base_url", "")
            api_key = prov.get("api_key", "")
            if not endpoint or not api_model:
                print(f"[project-sync.closet] {wing}: cannot resolve "
                      f"provider for {model!r} — skipping", flush=True)
                return
            # Suppress chatty progress output from upstream regen;
            # the incremental wrapper logs its own one-line summary.
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                out = kg_extract.run_closet_regen_incremental(
                    palace_path=palace_path, wing=wing,
                    source_prefix=source_prefix or "",
                    chats_db_path=chats_db_path,
                    endpoint=endpoint, api_key=api_key,
                    api_model=api_model,
                    log_prefix="[project-sync.closet]",
                )
            if isinstance(out, dict) and out.get("error"):
                print(f"[project-sync.closet] {wing}: error="
                      f"{out['error']}", flush=True)
            return out if isinstance(out, dict) else {}
        except Exception as e:
            print(f"[project-sync.closet] {wing}: failed: "
                  f"{type(e).__name__}: {e}", flush=True)
            return {"error": f"{type(e).__name__}: {e}"}

    def _count_wing_drawers_by_source(wing: str, source_prefix: str) -> int:
        """Authoritative count of drawers in `wing` whose source_file
        startswith(source_prefix). Used to populate per-item drawer
        counts after a mine — survives dedup-only re-runs unchanged.
        """
        try:
            col = _get_drawers_col(palace_path, create=False)
            if not col:
                return 0
            # Chroma supports operator filters; use $and + startswith via
            # `$contains` is unreliable on metadata. Pull all and filter
            # in-Python — wings are typically small.
            got = col.get(where={"wing": wing}, include=["metadatas"])
            metas = got.get("metadatas") or []
            hits = 0
            for m in metas:
                sf = (m or {}).get("source_file") or ""
                if sf.startswith(source_prefix):
                    hits += 1
            return hits
        except Exception:
            return 0

    def _count_wing_drawers_total(wing: str) -> int:
        try:
            col = _get_drawers_col(palace_path, create=False)
            if not col:
                return 0
            got = col.get(where={"wing": wing}, include=[])
            return len(got.get("ids") or [])
        except Exception:
            return 0

    def _count_wing_files_total(wing: str) -> int:
        """Distinct source_file count in `wing`. One file produces many
        drawers (chunks), so this is what the user actually means when
        they ask "how many files are indexed?" — drawer count is an
        internal storage detail."""
        try:
            col = _get_drawers_col(palace_path, create=False)
            if not col:
                return 0
            got = col.get(where={"wing": wing}, include=["metadatas"])
            seen: set = set()
            for m in (got.get("metadatas") or []):
                sf = (m or {}).get("source_file") or ""
                if sf:
                    seen.add(sf)
            return len(seen)
        except Exception:
            return 0

    # NOTE: A startup-wipe block lived here through 2026-04-28. It was
    # added in 8.18.2 to clean up drawers tagged with the legacy
    # `project__<name>--<agent_id>` wing scheme after the rename to the
    # ID-only `project__<id>` scheme, but had no idempotency gate and
    # silently re-wiped + re-mined every project on every restart for
    # weeks. Removed entirely. If a future migration needs a similar
    # one-time cleanup, build it as an explicit admin endpoint
    # (`POST /v1/mempalace/migrate`) or `brain.py` subcommand — not as
    # implicit boot-time behavior. The migration this block was for has
    # been complete on every live install for weeks; there is nothing
    # left to clean up.

    # Small startup delay so we don't compete with the other two daemons.
    time.sleep(25)

    while True:
        try:
            mcfg2 = engine._load_mempalace_config()
            if not mcfg2.get("enabled", True):
                return
            ps_cfg = (mcfg2.get("project_sync") or {})
            if not ps_cfg.get("enabled", True):
                time.sleep(60)
                continue
            # Default interval is 6 hours (21600s). Steady-state work is
            # incremental (doc_convert mtime/size, mp_miner content-hash
            # dedup, kg_extract cursor, closet_regen cursor) so re-running
            # every 30 min was wasted walks. Manual "Sync now" still works
            # on demand for instant re-mine after a file drop.
            interval = int(ps_cfg.get("interval_seconds", 21600))
            max_files_per_folder = int(ps_cfg.get("max_files_per_folder", 5000))

            # Drain manual "Sync now" requests first.
            with srv._project_sync_lock:
                requested = set(srv._project_sync_requests)
                req_triggers = dict(srv._project_sync_request_triggers)
                srv._project_sync_requests.clear()
                srv._project_sync_request_triggers.clear()

            # Enumerate all (agent, project) pairs by walking AGENTS_DIR.
            pairs = []
            try:
                for agent_id in sorted(os.listdir(engine.AGENTS_DIR)):
                    agent_dir = os.path.join(engine.AGENTS_DIR, agent_id)
                    if not os.path.isdir(agent_dir) or agent_id.startswith("."):
                        continue
                    proj_root = os.path.join(agent_dir, "projects")
                    if not os.path.isdir(proj_root):
                        continue
                    for proj_name in sorted(os.listdir(proj_root)):
                        pdir = os.path.join(proj_root, proj_name)
                        if not os.path.isdir(pdir) or proj_name.startswith("."):
                            continue
                        pairs.append((agent_id, proj_name))
            except Exception as e:
                print(f"[project-sync] enumerate failed: {e}", flush=True)
                pairs = []

            # Process requested-first, then everyone else.
            ordered = [p for p in pairs if p in requested] + [
                p for p in pairs if p not in requested
            ]

            cycle_filed = 0
            for agent_id, proj_name in ordered:
                # Manual "Sync now" overrides per-folder auto_sync gating —
                # the user is explicitly asking, so skipping their non-auto
                # folders would be confusing.
                is_manual = (agent_id, proj_name) in requested
                _trigger = req_triggers.get(
                    (agent_id, proj_name),
                    "manual" if is_manual else "scheduled")
                project = engine.ProjectManager.get_project(agent_id, proj_name)
                if not project:
                    continue
                if project.get("status") == "archived":
                    continue
                pdir = project.get("dir") or os.path.join(
                    engine.AGENTS_DIR, agent_id, "projects", proj_name)
                project_id = project.get("id") or ""
                if not project_id:
                    # Backfill safety net: get_project() should have set
                    # this on first read, but if persisting failed (RO
                    # filesystem etc.) skip this project for the cycle.
                    print(f"[project-sync] skip {agent_id}/{proj_name}: "
                          f"no project id", flush=True)
                    continue
                wing = _project_wing(project_id)

                # Project-level web URLs → fetch fresh into pdir/web-urls/ as
                # hash-gated .md files BEFORE mining, so they ride the same
                # convert→mine→KG pass as uploaded files. (web_fetch handles
                # JS-rendered pages via the crawl4ai fallback.) Removed URLs'
                # files are pruned here; their drawers get purged by the
                # stale-path sweep below (web-urls/ files that no longer exist).
                _weburl_folder = ""
                try:
                    _weburl_folder = _sync_project_web_urls(
                        pdir, project.get("web_urls") or [])
                except Exception as _e_wu:
                    print(f"[project-sync.weburl] {agent_id}/{proj_name}: "
                          f"{type(_e_wu).__name__}: {_e_wu}", flush=True)

                _run_id = _sync_log.start_run(
                    chats_db_path, project_id, triggered_by=_trigger)
                started_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
                srv._project_sync_set_live(agent_id, proj_name,
                    state="syncing", started_at=started_at,
                    files_filed=0, error="", run_id=_run_id)
                files_filed = 0
                folders_seen = 0
                last_error = ""
                # Per-item state map: keyed by ("attachment", source_hash) for
                # uploaded docs and ("folder", absolute_path) for input folders.
                # The web UI joins this onto its rendered lists so each row
                # carries its own indexed/syncing/error pill.
                # Seed from the prior persisted run so cumulative counters
                # (drawers_filed_total) survive — every cycle after the
                # first is dedup-mostly and would otherwise overwrite a
                # real "180 drawers indexed" snapshot with 0.
                prior_items = ((project.get("sync_status") or {}).get("items") or {})
                item_states: dict[str, dict] = {}
                for k, v in prior_items.items():
                    if isinstance(v, dict):
                        item_states[k] = dict(v)
                # Project-level cumulative count (across all cycles).
                prior_total_indexed = int(
                    (project.get("sync_status") or {}).get("total_indexed") or 0)

                # Stale-path purge: compare current input_folders against
                # what's in MemPalace. Any drawer whose source_file starts
                # with a path that is no longer an input folder gets purged.
                # This catches manual project.json edits, renames, and
                # moves that bypass the API handler's built-in purge.
                _stale_drawers_purged = 0
                _stale_closets_purged = 0
                try:
                    _current_folder_prefixes = set()
                    for _fe in (project.get("input_folders") or []):
                        _fp = (_fe.get("path") or "").strip()
                        if _fp:
                            _r = os.path.realpath(os.path.expanduser(_fp))
                            _current_folder_prefixes.add(
                                _r if _r.endswith(os.sep) else _r + os.sep)
                    # Also allow pdir itself (ingested attachments + the
                    # web-urls/ subfolder, both under pdir).
                    _pdir_real = os.path.realpath(pdir)
                    _current_folder_prefixes.add(
                        _pdir_real if _pdir_real.endswith(os.sep)
                        else _pdir_real + os.sep)

                    _mcfg_sp = engine._load_mempalace_config()
                    _palace_sp = _mcfg_sp.get("palace_path", "")
                    if _palace_sp and os.path.isdir(_palace_sp):
                        _ok_sp, _ = engine._ensure_mempalace_importable()
                        if _ok_sp:
                            from mempalace.palace import (
                                get_collection as _get_col_sp,
                                get_closets_collection as _get_ccol_sp,
                            )
                            _wing_sp = wing
                            # A drawer is stale if its source_file is outside
                            # every current input folder/pdir prefix, OR it's a
                            # web-urls/ companion whose .md no longer exists
                            # (the URL was removed from the project). The latter
                            # can't be caught by prefix alone since web-urls/ is
                            # under pdir (a kept prefix).
                            _weburl_dir_real = (os.path.realpath(_weburl_folder) + os.sep
                                                if _weburl_folder else None)
                            def _is_stale_src(_src):
                                _src = _src or ""
                                if not any(_src.startswith(_p) for _p in _current_folder_prefixes):
                                    return True
                                if (_weburl_dir_real and _src.startswith(_weburl_dir_real)
                                        and not os.path.exists(_src)):
                                    return True
                                return False
                            _col_sp = _get_col_sp(_palace_sp, create=False)
                            if _col_sp:
                                _res_sp = _col_sp.get(
                                    where={"wing": _wing_sp},
                                    include=["metadatas"])
                                _stale_ids = [
                                    _did for _did, _m in zip(
                                        _res_sp["ids"], _res_sp["metadatas"])
                                    if _is_stale_src(_m.get("source_file"))
                                ]
                                if _stale_ids:
                                    _col_sp.delete(ids=_stale_ids)
                                    _stale_drawers_purged = len(_stale_ids)
                                    print(f"[project-sync] stale-path purge "
                                          f"{agent_id}/{proj_name}: "
                                          f"deleted {_stale_drawers_purged} drawer(s)",
                                          flush=True)
                            _ccol_sp = _get_ccol_sp(_palace_sp, create=False)
                            if _ccol_sp:
                                _res_sp = _ccol_sp.get(
                                    where={"wing": _wing_sp},
                                    include=["metadatas"])
                                _stale_cids = [
                                    _cid for _cid, _m in zip(
                                        _res_sp["ids"], _res_sp["metadatas"])
                                    if _is_stale_src(_m.get("source_file"))
                                ]
                                if _stale_cids:
                                    _ccol_sp.delete(ids=_stale_cids)
                                    _stale_closets_purged = len(_stale_cids)
                                    print(f"[project-sync] stale-path purge "
                                          f"{agent_id}/{proj_name}: "
                                          f"deleted {_stale_closets_purged} closet(s)",
                                          flush=True)
                except Exception as _e_sp:
                    print(f"[project-sync] stale-path purge error "
                          f"{agent_id}/{proj_name}: "
                          f"{type(_e_sp).__name__}: {_e_sp}", flush=True)
                if _run_id and (_stale_drawers_purged or _stale_closets_purged):
                    _sync_log.step_update(
                        chats_db_path, _run_id, "stale_path_purge",
                        drawers_deleted=_stale_drawers_purged,
                        closets_deleted=_stale_closets_purged,
                        at=time.time())

                # Pre-scan to estimate cycle work for live progress / ETA.
                # Cheap: just os.walk the ingested + input folders and count
                # files (no hashing, no parsing). Counts every regular file;
                # the miner's gitignore + ext filter will narrow this down,
                # so the displayed P/T overshoots T slightly — that's fine,
                # the ETA is approximate by design and overshoot beats
                # undershoot (progress bar never appears stuck at 100%).
                cycle_total_files = 0
                cycle_processed_files = 0
                cycle_folder_file_counts: dict[str, int] = {}
                cycle_ingested_file_count = 0
                ingested_dir_pre = os.path.join(pdir, "ingested")
                if os.path.isdir(ingested_dir_pre):
                    try:
                        # Count distinct source uploads (hash count), not
                        # chunk count, so the progress P/T reflects "files
                        # the user uploaded" rather than internal chunks.
                        seen_hashes: set = set()
                        for fn in os.listdir(ingested_dir_pre):
                            if fn.startswith("ingest-") and fn.endswith(".md"):
                                parts_fn = fn.split("-", 2)
                                if len(parts_fn) >= 2:
                                    seen_hashes.add(parts_fn[1])
                        cycle_ingested_file_count = len(seen_hashes)
                        cycle_total_files += cycle_ingested_file_count
                    except OSError:
                        pass
                for entry_pre in (project.get("input_folders") or []):
                    fp_pre = entry_pre.get("path") or ""
                    if not fp_pre or not os.path.isdir(fp_pre):
                        continue
                    # Skip auto_sync=false folders unless the project is in
                    # the manual-trigger set. They still get a 0-count entry
                    # so the folder-loop later can render the "paused" state.
                    if not is_manual and entry_pre.get("auto_sync", True) is False:
                        cycle_folder_file_counts[fp_pre] = 0
                        continue
                    rec = bool(entry_pre.get("recursive", True))
                    cnt = 0
                    try:
                        if rec:
                            for _root, _dirs, _files in os.walk(fp_pre):
                                cnt += len(_files)
                        else:
                            cnt = sum(
                                1 for e in os.scandir(fp_pre) if e.is_file())
                    except OSError:
                        cnt = 0
                    cycle_folder_file_counts[fp_pre] = cnt
                    cycle_total_files += cnt
                srv._project_sync_set_live(agent_id, proj_name,
                    cycle_total_files=cycle_total_files,
                    cycle_processed_files=0)

                def _item_key(kind: str, ident: str) -> str:
                    return f"{kind}:{ident}"

                def _bump_processed(n: int):
                    nonlocal cycle_processed_files
                    cycle_processed_files += int(n or 0)
                    srv._project_sync_set_live(agent_id, proj_name,
                        cycle_processed_files=cycle_processed_files)

                def _set_item(kind: str, ident: str, **fields):
                    k = _item_key(kind, ident)
                    cur = item_states.setdefault(k, {"kind": kind, "id": ident})
                    cur.update(fields)
                    # Push live snapshot so the UI sees state changes during
                    # the cycle, not just after the project.json write.
                    live = dict(srv._project_sync_live_status(agent_id, proj_name))
                    items_live = dict(live.get("items") or {})
                    items_live[k] = dict(cur)
                    srv._project_sync_set_live(agent_id, proj_name,
                        state="syncing", items=items_live,
                        files_filed=files_filed,
                        cycle_total_files=cycle_total_files,
                        cycle_processed_files=cycle_processed_files)

                # 1. Manual attachments — `ingested/` mined into project wing.
                #    Each chunk file is named ingest-<src_hash>-<idx>.md;
                #    we group by src_hash so each upload appears as one item.
                ingested_dir = os.path.join(pdir, "ingested")
                if os.path.isdir(ingested_dir):
                    folders_seen += 1
                    # Discover all unique source hashes in the folder so we
                    # can mark each as "syncing" before mining begins.
                    hashes: set[str] = set()
                    try:
                        for fn in os.listdir(ingested_dir):
                            if fn.startswith("ingest-") and fn.endswith(".md"):
                                parts_fn = fn.split("-", 2)
                                if len(parts_fn) >= 2:
                                    hashes.add(parts_fn[1])
                    except OSError:
                        pass
                    for h in hashes:
                        _set_item("attachment", h,
                            state="syncing",
                            last_run_started=started_at)
                    if _ensure_mempalace_yaml(ingested_dir, wing):
                        # PDF/DOCX → .md pre-mine pass. The /ingested
                        # upload flow normally pre-chunks PDFs already,
                        # but covering this branch makes the daemon
                        # robust to direct file drops here too.
                        if doc_convert is not None:
                            if _run_id:
                                _sync_log.step_start(
                                    chats_db_path, _run_id, "doc_convert",
                                    folder=ingested_dir)
                            try:
                                _stale_cnt = doc_convert.sweep_stale(
                                    ingested_dir,
                                    log_prefix="[project-sync.conv]")
                                _conv_res = doc_convert.convert_folder(
                                    ingested_dir,
                                    log_prefix="[project-sync.conv]",
                                    use_markitdown=_conv_use_markitdown())
                                if _run_id:
                                    _sync_log.step_finish(
                                        chats_db_path, _run_id,
                                        "doc_convert",
                                        folder=ingested_dir,
                                        converted=_conv_res.converted,
                                        unchanged=_conv_res.skipped_unchanged,
                                        failed=_conv_res.failed,
                                        stale_removed=_stale_cnt,
                                        seen_total=_conv_res.seen_total,
                                        elapsed_s=round(_conv_res.elapsed_s, 2))
                            except Exception as e:
                                print(f"[project-sync.conv] "
                                      f"{ingested_dir}: "
                                      f"{type(e).__name__}: {e}",
                                      flush=True)
                                if _run_id:
                                    _sync_log.step_finish(
                                        chats_db_path, _run_id,
                                        "doc_convert",
                                        folder=ingested_dir,
                                        errors=[str(e)])
                        ingest_filed = 0
                        ingest_err = ""
                        if _run_id:
                            _sync_log.step_start(
                                chats_db_path, _run_id, "indexing",
                                folder=ingested_dir)
                        _index_t0 = time.time()
                        buf = io.StringIO()
                        try:
                            with contextlib.redirect_stdout(buf):
                                mp_miner.mine(
                                    project_dir=ingested_dir,
                                    palace_path=palace_path,
                                    wing_override=wing,
                                    agent="brain-project-sync",
                                    respect_gitignore=False,
                                )
                            for line in buf.getvalue().splitlines():
                                s = line.strip()
                                if s.startswith("Drawers filed"):
                                    try:
                                        ingest_filed = int(
                                            s.split(":")[-1].strip().split()[0])
                                    except Exception:
                                        pass
                                    break
                        except SystemExit:
                            pass
                        except Exception as e:
                            ingest_err = f"{type(e).__name__}: {e}"
                            last_error = ingest_err
                            print(f"[project-sync] {agent_id}/{proj_name} "
                                  f"ingested: {ingest_err}", flush=True)
                        if _run_id:
                            _sync_log.step_finish(
                                chats_db_path, _run_id, "indexing",
                                folder=ingested_dir,
                                drawers_created=ingest_filed,
                                elapsed_s=round(time.time() - _index_t0, 2),
                                errors=[ingest_err] if ingest_err else [])
                        files_filed += ingest_filed
                        finished_at_attach = datetime.datetime.now(
                            datetime.timezone.utc).isoformat()
                        # Authoritative count per source: pull all wing
                        # drawers whose source_file references the chunk
                        # files belonging to this hash. Miner mines from
                        # the chunk file, so source_file ends with
                        # ingest-<hash>-<idx>.md.
                        for h in hashes:
                            cnt = _count_wing_drawers_by_source(
                                wing, f"ingest-{h}-")
                            _set_item("attachment", h,
                                state=("error" if ingest_err else "indexed"),
                                last_run_finished=finished_at_attach,
                                drawers_filed=cnt,
                                error=ingest_err)
                        # Mark every uploaded source as processed in the
                        # cycle progress. Done as a batch since the miner
                        # call covers them all in one pass.
                        _bump_processed(len(hashes))
                        # KG extraction post-pass for each ingested
                        # attachment hash. Drawers carry source_file
                        # like .../ingested/ingest-<hash>-<idx>.md, so
                        # we can scope precisely per attachment.
                        for h in hashes:
                            if ingest_err:
                                continue
                            _run_kg_for(
                                wing=wing,
                                source_prefix=os.path.join(
                                    ingested_dir, f"ingest-{h}-"),
                                item_set_fn=_set_item,
                                item_kind="attachment", item_id=h)
                            if _run_id:
                                _it_a = item_states.get(
                                    _item_key("attachment", h)) or {}
                                _sync_log.step_update(
                                    chats_db_path, _run_id, "kg",
                                    folder=ingested_dir,
                                    attachment_hash=h,
                                    triples_this_cycle=_it_a.get("triples_last_cycle", 0),
                                    triples_total=_it_a.get("triples_extracted", 0),
                                    drawers_processed=_it_a.get("kg_drawers_processed", 0),
                                    parse_errors=_it_a.get("kg_parse_errors", 0),
                                    elapsed_s=_it_a.get("kg_elapsed_s", 0),
                                    error=_it_a.get("kg_last_error", ""))
                    else:
                        for h in hashes:
                            _set_item("attachment", h,
                                state="error",
                                error="failed to write mempalace.yaml")
                        _bump_processed(len(hashes))

                # 1b. Project web URLs — fetched into pdir/web-urls/ as .md
                #     above (_sync_project_web_urls). Already markdown, so no
                #     convert pass — just mine + KG, same as ingested. Each URL
                #     is a `<slug>_<timestamp>.md` file (legacy: weburl-<hash>.md);
                #     mine the folder in one pass, then run KG scoped to it.
                if _weburl_folder and os.path.isdir(_weburl_folder):
                    _wu_files = [fn for fn in os.listdir(_weburl_folder)
                                 if fn.endswith(".md")
                                 and (fn.startswith("weburl-") or "_" in fn)]
                    if _wu_files and _ensure_mempalace_yaml(_weburl_folder, wing):
                        folders_seen += 1
                        _wu_err = ""
                        if _run_id:
                            _sync_log.step_start(chats_db_path, _run_id,
                                                 "indexing", folder=_weburl_folder)
                        _wu_t0 = time.time()
                        _wu_filed = 0
                        _wu_buf = io.StringIO()
                        try:
                            with contextlib.redirect_stdout(_wu_buf):
                                mp_miner.mine(
                                    project_dir=_weburl_folder,
                                    palace_path=palace_path,
                                    wing_override=wing,
                                    agent="brain-project-sync",
                                    respect_gitignore=False,
                                )
                            for line in _wu_buf.getvalue().splitlines():
                                s = line.strip()
                                if s.startswith("Drawers filed"):
                                    try:
                                        _wu_filed = int(s.split(":")[-1].strip().split()[0])
                                    except Exception:
                                        pass
                                    break
                        except SystemExit:
                            pass
                        except Exception as e:
                            _wu_err = f"{type(e).__name__}: {e}"
                            last_error = _wu_err
                            print(f"[project-sync] {agent_id}/{proj_name} "
                                  f"web-urls: {_wu_err}", flush=True)
                        if _run_id:
                            _sync_log.step_finish(
                                chats_db_path, _run_id, "indexing",
                                folder=_weburl_folder,
                                drawers_created=_wu_filed,
                                elapsed_s=round(time.time() - _wu_t0, 2),
                                errors=[_wu_err] if _wu_err else [])
                        files_filed += _wu_filed
                        _bump_processed(len(_wu_files))
                        if not _wu_err:
                            _run_kg_for(
                                wing=wing,
                                source_prefix=os.path.realpath(_weburl_folder) + os.sep,
                                item_set_fn=_set_item,
                                item_kind="weburls", item_id="web-urls")

                # 2. User-specified input folders — each entry has its own
                #    mempalace.yaml, scanned recursively or top-level only.
                for entry in (project.get("input_folders") or []):
                    # Cancel check between folders.
                    if srv._project_sync_cancel_check(project_id):
                        if _run_id:
                            _sync_log.cancel_run(chats_db_path, _run_id)
                        last_error = "cancelled"
                        break
                    folders_seen += 1
                    fpath = entry.get("path", "")
                    if not fpath:
                        continue
                    # Honor the per-folder auto_sync gate on scheduled cycles.
                    # Manual "Sync now" overrides — the user is asking
                    # explicitly. Update the item row so the UI shows the
                    # paused state instead of a stale "syncing".
                    if not is_manual and entry.get("auto_sync", True) is False:
                        existing_drawers = (item_states.get(
                            _item_key("folder", fpath)) or {}).get("drawers_filed", 0)
                        _set_item("folder", fpath,
                            state="paused",
                            drawers_filed=existing_drawers,
                            error="")
                        continue
                    if not os.path.isdir(fpath):
                        _set_item("folder", fpath,
                            state="error",
                            error="folder not found",
                            last_run_started=started_at)
                        _bump_processed(cycle_folder_file_counts.get(fpath, 0))
                        continue
                    _set_item("folder", fpath,
                        state="syncing",
                        last_run_started=started_at,
                        error="")
                    # Tell live status which folder we're chewing on.
                    srv._project_sync_set_live(agent_id, proj_name,
                        state="syncing", current_folder=fpath,
                        files_filed=files_filed)
                    if not _ensure_mempalace_yaml(fpath, wing):
                        _set_item("folder", fpath,
                            state="error",
                            error="failed to write mempalace.yaml")
                        _bump_processed(cycle_folder_file_counts.get(fpath, 0))
                        continue
                    # Cap the number of top-level entries to avoid runaway
                    # walks on accidentally-pointed-at home dirs etc.
                    try:
                        top_count = sum(1 for _ in os.scandir(fpath))
                        if top_count > max_files_per_folder:
                            msg = (f"folder has {top_count} entries "
                                   f"(>{max_files_per_folder} cap) — skipped")
                            last_error = msg
                            _set_item("folder", fpath,
                                state="error", error=msg)
                            print(f"[project-sync] {agent_id}/{proj_name} "
                                  f"{fpath}: {msg}", flush=True)
                            _bump_processed(cycle_folder_file_counts.get(fpath, 0))
                            continue
                    except OSError as e:
                        _set_item("folder", fpath,
                            state="error", error=str(e))
                        _bump_processed(cycle_folder_file_counts.get(fpath, 0))
                        continue
                    folder_filed = 0
                    folder_err = ""
                    # PDF/DOCX → .md pre-mine pass. Without this the
                    # MemPalace miner silently skips binary documents
                    # (its READABLE_EXTENSIONS list is text-only).
                    # Idempotent — only re-converts when source mtime+size
                    # changes. Failures don't abort the cycle; per-file
                    # errors are logged and the rest of the folder still
                    # mines.
                    if doc_convert is not None:
                        if _run_id:
                            _sync_log.step_start(
                                chats_db_path, _run_id, "doc_convert",
                                folder=fpath)
                        try:
                            _stale_cnt_f = doc_convert.sweep_stale(
                                fpath, log_prefix="[project-sync.conv]")
                            _conv_res_f = doc_convert.convert_folder(
                                fpath, log_prefix="[project-sync.conv]",
                                use_markitdown=_conv_use_markitdown())
                            if _run_id:
                                _sync_log.step_finish(
                                    chats_db_path, _run_id, "doc_convert",
                                    folder=fpath,
                                    converted=_conv_res_f.converted,
                                    unchanged=_conv_res_f.skipped_unchanged,
                                    failed=_conv_res_f.failed,
                                    stale_removed=_stale_cnt_f,
                                    seen_total=_conv_res_f.seen_total,
                                    elapsed_s=round(_conv_res_f.elapsed_s, 2))
                        except Exception as e:
                            print(f"[project-sync.conv] {fpath}: "
                                  f"{type(e).__name__}: {e}", flush=True)
                            if _run_id:
                                _sync_log.step_finish(
                                    chats_db_path, _run_id, "doc_convert",
                                    folder=fpath, errors=[str(e)])
                    if _run_id:
                        _sync_log.step_start(
                            chats_db_path, _run_id, "indexing",
                            folder=fpath)
                    _index_t0_f = time.time()
                    buf = io.StringIO()
                    try:
                        with contextlib.redirect_stdout(buf):
                            mp_miner.mine(
                                project_dir=fpath,
                                palace_path=palace_path,
                                wing_override=wing,
                                agent="brain-project-sync",
                                respect_gitignore=True,
                            )
                        for line in buf.getvalue().splitlines():
                            s = line.strip()
                            if s.startswith("Drawers filed"):
                                try:
                                    folder_filed = int(
                                        s.split(":")[-1].strip().split()[0])
                                except Exception:
                                    pass
                                break
                    except SystemExit:
                        pass
                    except Exception as e:
                        folder_err = f"{type(e).__name__}: {e}"
                        last_error = folder_err
                        print(f"[project-sync] {agent_id}/{proj_name} "
                              f"{fpath}: {folder_err}", flush=True)
                    if _run_id:
                        _sync_log.step_finish(
                            chats_db_path, _run_id, "indexing",
                            folder=fpath,
                            drawers_created=folder_filed,
                            elapsed_s=round(time.time() - _index_t0_f, 2),
                            errors=[folder_err] if folder_err else [])
                    files_filed += folder_filed
                    # Authoritative cumulative drawer count: source_file
                    # in the wing always startswith the absolute folder
                    # path for files mined from this folder.
                    cum = _count_wing_drawers_by_source(wing, fpath)
                    _set_item("folder", fpath,
                        state=("error" if folder_err else "indexed"),
                        last_run_finished=datetime.datetime.now(
                            datetime.timezone.utc).isoformat(),
                        drawers_filed=cum,
                        error=folder_err)
                    # Bump cycle progress by the file count we pre-scanned
                    # for this folder. Slight overshoot is fine — see
                    # pre-scan comment.
                    _bump_processed(cycle_folder_file_counts.get(fpath, 0))
                    # KG extraction post-pass for this input folder.
                    # source_prefix is the absolute folder path; the
                    # extractor's per-drawer cursor makes re-runs cheap
                    # (already-processed drawers skipped in O(1)).
                    if not folder_err:
                        _run_kg_for(
                            wing=wing, source_prefix=fpath,
                            item_set_fn=_set_item,
                            item_kind="folder", item_id=fpath)
                        if _run_id:
                            _it = item_states.get(_item_key("folder", fpath)) or {}
                            _sync_log.step_update(
                                chats_db_path, _run_id, "kg",
                                folder=fpath,
                                triples_this_cycle=_it.get("triples_last_cycle", 0),
                                triples_total=_it.get("triples_extracted", 0),
                                drawers_processed=_it.get("kg_drawers_processed", 0),
                                parse_errors=_it.get("kg_parse_errors", 0),
                                elapsed_s=_it.get("kg_elapsed_s", 0),
                                error=_it.get("kg_last_error", ""))

                # Optional: regenerate closets via LLM for richer ranking.
                # Runs once per project cycle after all folders are mined
                # and KG-extracted. Opt-in via mempalace.kg.regenerate_closets;
                # reuses the KG model so a single GUI choice covers both.
                if last_error != "cancelled":
                    if _run_id:
                        _sync_log.step_start(
                            chats_db_path, _run_id, "closet_rerank")
                    _closet_out = _run_closet_regen_for(wing) or {}
                    if _run_id:
                        _sync_log.step_finish(
                            chats_db_path, _run_id, "closet_rerank",
                            sources_seen=_closet_out.get("sources_seen", 0),
                            sources_stale=_closet_out.get("sources_stale", 0),
                            regen_triggered=_closet_out.get("regen_triggered", False),
                            elapsed_s=round(_closet_out.get("elapsed_s", 0), 2),
                            errors=[_closet_out["error"]]
                                if _closet_out.get("error") else [])

                finished_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
                final_state = ("cancelled" if last_error == "cancelled"
                               else "error" if last_error and files_filed == 0
                               else "idle")
                # Authoritative total: query MemPalace for everything in
                # the project's wing. Survives dedup-only cycles unchanged.
                total_indexed = _count_wing_drawers_total(wing)
                # Distinct source-file count — what the user reads as
                # "how many files are indexed?" (a single PDF chunks into
                # many drawers, so total_indexed alone is misleading).
                total_files = _count_wing_files_total(wing)
                # Authoritative total triples for this project (sum across
                # all input folders in the wing). Cheap SQL — one COUNT
                # over a prefix-scoped slice of the KG.
                total_triples = 0
                if kg_extract is not None:
                    def _norm_p2(p):
                        try:
                            r = os.path.realpath(p)
                        except OSError:
                            r = p
                        if r and not r.endswith(os.sep):
                            r += os.sep
                        return r
                    try:
                        stats = kg_extract.kg_stats_for_wing(
                            palace_path=palace_path,
                            source_prefix=_norm_p2(pdir),
                            adapter_name="brain-project-kg")
                        total_triples = int(stats.get("triples", 0))
                        # Also accumulate triples reached via input-folder
                        # source files outside pdir.
                        pdir_real = _norm_p2(pdir)
                        for entry in (project.get("input_folders") or []):
                            fp = entry.get("path") or ""
                            fp_real = _norm_p2(fp) if fp else ""
                            if fp_real and not fp_real.startswith(pdir_real):
                                s2 = kg_extract.kg_stats_for_wing(
                                    palace_path=palace_path,
                                    source_prefix=fp_real,
                                    adapter_name="brain-project-kg")
                                total_triples += int(s2.get("triples", 0))
                    except Exception as e:
                        print(f"[project-sync] kg stats failed "
                              f"{agent_id}/{proj_name}: "
                              f"{type(e).__name__}: {e}", flush=True)
                sync_row = {
                    "state": final_state,
                    "last_run_started": started_at,
                    "last_run_finished": finished_at,
                    "last_triggered_by": _trigger,
                    "last_files_filed": files_filed,  # delta this cycle
                    "total_indexed": total_indexed,    # cumulative drawers
                    "total_files": total_files,        # cumulative files
                    "total_triples": total_triples,    # KG triples in wing
                    "last_folders_seen": folders_seen,
                    "last_error": last_error,
                    "items": item_states,
                }
                try:
                    engine.ProjectManager.update_project(agent_id, proj_name, {
                        "sync_status": sync_row,
                        "input_folders_last_scan": finished_at,
                    })
                except Exception as e:
                    print(f"[project-sync] persist failed {agent_id}/{proj_name}: "
                          f"{type(e).__name__}: {e}", flush=True)
                if _run_id and final_state != "cancelled":
                    _sync_log.finish_run(chats_db_path, _run_id, final_state, {
                        "total_files": total_files,
                        "total_indexed": total_indexed,
                        "total_triples": total_triples,
                        "files_filed_this_cycle": files_filed,
                        "folders_seen": folders_seen,
                        "final_state": final_state,
                        "elapsed_s": round(
                            time.time() - (
                                _sync_log.get_run(chats_db_path, _run_id) or {}
                            ).get("started_at", time.time()), 1),
                        "errors": [last_error] if last_error else [],
                    })
                srv._project_sync_clear_live(agent_id, proj_name)
                cycle_filed += files_filed

            print(f"[project-sync] cycle: filed={cycle_filed} "
                  f"projects={len(pairs)} requested={len(requested)}", flush=True)
        except Exception as e:
            print(f"[project-sync] cycle error: {type(e).__name__}: {e}", flush=True)

        # If a new request arrived mid-cycle it's already in the set.
        # Skip the sleep entirely so it runs immediately.
        with srv._project_sync_lock:
            has_pending = bool(srv._project_sync_requests)
        if has_pending:
            continue
        # Sweep the ad-hoc extraction cache once per cycle (companions
        # for chat attachments + arbitrary read_document paths). Project
        # companions under .brain-extracted/ are managed by sweep_stale
        # above and never touched here. 30-day atime LRU is plenty —
        # an active chat re-touches its companions on every read.
        if doc_convert is not None:
            try:
                doc_convert.evict_adhoc_cache(log_prefix="[project-sync.conv]")
            except Exception as e:
                print(f"[project-sync.conv] adhoc-evict: "
                      f"{type(e).__name__}: {e}", flush=True)
        # Wait for the next interval, but wake up on demand. Default
        # is 6 hours (21600s) — incremental layers (doc_convert,
        # mp_miner, kg_extract, closet_regen) all cursor-skip on
        # unchanged content, so frequent walks were wasted overhead.
        wait_for = max(60, int(((engine._load_mempalace_config().get(
            "project_sync") or {}).get("interval_seconds", 21600))))
        woken = srv._project_sync_wakeup.wait(timeout=wait_for)
        if woken:
            srv._project_sync_wakeup.clear()


_SEARXNG_HEALTH_INTERVAL_SEC = 4 * 3600  # every 4 hours, anchored to startup


def _searxng_engine_health_loop(srv):
    """Per-engine health probe of the bundled SearXNG instance every 4 hours,
    anchored to server startup, so the Server-settings panel always shows a
    recent state without the user having to click 'Test now'. The first probe
    runs ~30s after boot (give SearXNG time to come up); the 4h cadence is
    fixed on this loop's own timer, so a manual 'Test now' (which calls
    run_health_check directly via the endpoint) never shifts the next auto
    probe."""
    from server_lib import searxng_health
    time.sleep(30)
    while True:
        try:
            base = engine._searxng_base_url()
            if base:
                searxng_health.run_health_check(base)
        except Exception as e:
            print(f"[searxng-health] cycle error: {type(e).__name__}: {e}", flush=True)
        searxng_health.set_next_auto_at(time.time() + _SEARXNG_HEALTH_INTERVAL_SEC)
        time.sleep(_SEARXNG_HEALTH_INTERVAL_SEC)


def _user_profile_loop(srv):
    time.sleep(60)
    while True:
        try:
            _user_profile_cycle(srv)
        except Exception as e:
            print(f"[profile] cycle error: {type(e).__name__}: {e}", flush=True)
        time.sleep(1800)


def _user_profile_cycle(srv):
    users = _auth_mod.AuthDB.list_users_with_preferences()
    if not users:
        return
    now = time.time()
    local_hour = time.localtime(now).tm_hour
    for u in users:
        uid = u.get("id") or ""
        if not uid:
            continue
        prefs = u.get("preferences") or {}
        if not prefs.get("daily_summary_enabled"):
            continue
        target_hour = int(prefs.get("daily_summary_hour_local") or 6)
        if local_hour != target_hour:
            continue
        cur = _auth_mod.AuthDB.get_daily_summary_cursor(uid)
        if (now - float(cur.get("last_run_ts") or 0)) < 23 * 3600:
            continue
        try:
            srv._profile_run_synchronous(u, since_ts=cur.get("last_run_ts") or 0, now=now)
        except Exception as e:
            print(f"[profile] user={uid} failed: {type(e).__name__}: {e}", flush=True)
            _auth_mod.AuthDB.set_daily_summary_cursor(uid, now, f"error:{type(e).__name__}", "")


def _warmup_keeper_loop(srv):
    # Small startup delay so we don't race provider probes on boot
    time.sleep(5)
    while True:
        try:
            wcfg = srv.server_config.get("warmup", {}) or {}
            if not wcfg.get("enabled", True):
                time.sleep(30)
                continue
            interval = int(wcfg.get("interval_seconds", 30))
            allow_cloud_global = bool(wcfg.get("allow_cloud", False))
            max_concurrent = int(wcfg.get("max_concurrent", 1))

            # Snapshot models with warmup=true. Each model picks its own
            # warmup_mode ("full" default | "minimal"). Full primes the
            # KV prefix so first-token latency is ~5-6s; minimal only
            # loads weights. If multiple full-prime models together
            # exceed GPU memory, oMLX will evict as needed — that's a
            # user-managed tradeoff, we don't second-guess it here.
            #
            # Only re-prime models whose state is idle/cold/failed or
            # whose configured mode changed since last prime.
            #
            # Load-aware backoff: if the candidate's provider has live
            # user traffic (active non-warmup ticket, queued ticket, or
            # a recent release within the grace window), skip this
            # cycle. The keeper re-checks on the next interval (or on
            # an explicit wakeup), so warmup catches up the moment the
            # provider goes idle. This prevents the keeper from cutting
            # in line during eval runs / multi-turn user chats where a
            # 26B prime-fill would block the next real turn.
            pq = engine.get_provider_queue()
            load_grace = float(wcfg.get("load_grace_seconds", 15))
            now = time.time()
            candidates = []
            deferred = []
            for mid, _raw_cfg in list(engine._models_config.items()):
                cfg = engine.resolve_model_settings(mid)
                if not cfg.get("warmup"):
                    continue
                if not cfg.get("enabled", True):
                    continue
                desired_mode = (cfg.get("warmup_mode") or "full").lower()
                if desired_mode not in ("full", "minimal"):
                    desired_mode = "full"
                st = engine.get_warmup_state(mid)
                state_name = st.get("state", "idle")
                if state_name in ("warming", "skipped_cloud"):
                    continue
                if state_name == "warm":
                    prev_mode = st.get("mode", "full")
                    if prev_mode == desired_mode:
                        engine.set_warmup_state(mid, next_due_ts=0)
                        continue
                    # Mode flipped — fall through to re-prime.
                last = max(st.get("last_warmup_ts", 0), st.get("last_used_ts", 0))
                age = now - last if last else 10 ** 9
                prov_info = engine.resolve_provider_for_model(mid) or {}
                pname = prov_info.get("provider_name", "")
                if pq.provider_busy(pname, grace_seconds=load_grace):
                    deferred.append((mid, pname))
                    continue
                candidates.append((age, mid, cfg, desired_mode))

            if deferred:
                try: print(f"[warmup-keeper] deferred (provider busy): {deferred}")
                except Exception: pass

            # Oldest first; cap to max_concurrent per cycle
            candidates.sort(key=lambda t: t[0], reverse=True)
            for _, mid, cfg, desired_mode in candidates[:max_concurrent]:
                allow_cloud = bool(cfg.get("warmup_allow_cloud", allow_cloud_global))
                t0 = time.time()
                result = engine.run_model_warmup(
                    mid,
                    allow_cloud=allow_cloud,
                    agent_id="main",
                    timeout=int(wcfg.get("timeout_seconds", 30)),
                    mode=desired_mode,
                )
                dur = int((time.time() - t0) * 1000)
                st_name = result.get("state", "?")
                if result.get("ok"):
                    print(f"[warmup-keeper] {mid}: warm ({desired_mode}, {dur}ms)")
                elif st_name == "skipped_cloud":
                    pass
                else:
                    err = result.get("error", "?")
                    print(f"[warmup-keeper] {mid}: failed — {err}")

            # Second pass — top up the warm session pool toward target
            # depth. Only build for models that are fully warm (weights +
            # KV prefix primed). try_build is a no-op when the pool is
            # already full. Same load-aware backoff as the prime pass —
            # building a pool slot fires a full chat-shaped request and
            # would block live user traffic on the same provider.
            for mid, _raw_cfg in list(engine._models_config.items()):
                cfg = engine.resolve_model_settings(mid)
                if not cfg.get("warmup") or not cfg.get("enabled", True):
                    continue
                st = engine.get_warmup_state(mid)
                if st.get("state") != "warm":
                    continue
                prov_info = engine.resolve_provider_for_model(mid) or {}
                if pq.provider_busy(prov_info.get("provider_name", ""),
                                    grace_seconds=load_grace):
                    continue
                srv.warm_pool.try_build(mid)

            # Wait for either the interval or an explicit wake-up (model
            # config change, manual warmup trigger, etc.). wait() returns
            # True when the event was set — we consume it and re-run.
            woke = srv._warmup_wakeup.wait(timeout=max(5, interval))
            if woke:
                srv._warmup_wakeup.clear()
        except Exception as e:
            print(f"[warmup-keeper] loop error: {type(e).__name__}: {e}")
            time.sleep(30)

