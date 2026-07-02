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
import threading
import time
from pathlib import Path  # mine() needs Path file lists (not str) — see _mine_batched

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

# Single in-process serializer for ALL palace-mutating operations across the
# three write daemons (miner, chat-sync, project-sync). The mempalace package's
# own `mine_palace_lock` is per-palace but NON-BLOCKING — a concurrent writer
# gets MineAlreadyRunning (a refused write), not a wait. Brain's daemons run on
# independent cycles and write the SAME palace, so without this lock a bulk
# delete (stale-purge) can race a concurrent upsert (the documented HNSW
# corruption trigger, project_chroma_bulk_delete_corruption) AND chat-sync
# writes get silently refused. Holding this lock around each mine()/add_drawer
# batch/stale-purge delete makes the daemons QUEUE instead of colliding. Coarse
# by design: a multi-second mine blocking a chat-sync cycle is the correct
# trade — serialized-but-late beats raced-and-lost/corrupt.
_palace_write_lock = threading.RLock()


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


def _project_source_fingerprint(pdir: str, project: dict, weburl_folder: str,
                                profile_folder: str = "") -> str:
    """Cheap fingerprint of ALL of a project's source files — ingested uploads,
    input folders, and fetched web-URL files. Pure os.scandir walk over (path,
    mtime_ns, size); NO Qdrant / DB / network. Used to skip the ENTIRE per-project
    sync (mining + KG + closet) in ~1s when nothing on disk changed since the last
    successful cycle. Returns a sha1 hex string (stable for an unchanged tree).

    Roots covered:
      - <pdir>/ingested   (uploaded attachments, chunked .md)
      - each input_folders[].path (user-mined folders)
      - weburl_folder     (<pdir>/web-urls, mined project web URLs)
    Hidden/.brain-extracted companion dirs are walked too — a re-extraction
    rewrites them, which SHOULD count as a change (re-mine warranted)."""
    h = hashlib.sha1()
    roots = []
    if pdir:
        roots.append(os.path.join(pdir, "ingested"))
    for fe in (project.get("input_folders") or []):
        p = (fe.get("path") or "").strip()
        if p:
            roots.append(os.path.expanduser(p))
    if weburl_folder:
        roots.append(weburl_folder)
    if profile_folder:
        roots.append(profile_folder)  # xlsx-profiles (v9.264.0)

    entries = []
    for root in roots:
        if not root or not os.path.isdir(root):
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames.sort()
            for fn in sorted(filenames):
                fp = os.path.join(dirpath, fn)
                try:
                    st = os.stat(fp)
                    entries.append(f"{fp}|{st.st_mtime_ns}|{st.st_size}")
                except OSError:
                    entries.append(f"{fp}|?")
    for e in entries:
        h.update(e.encode("utf-8", "replace"))
        h.update(b"\n")
    # Include the count so an empty tree vs a missing-root case differ cleanly.
    h.update(f"#count={len(entries)}".encode())
    return h.hexdigest()


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
                    with contextlib.redirect_stdout(buf), _palace_write_lock:
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
                        # Index the fresh brain-source clone into the GLOBAL
                        # codebase-memory tenant (cache_dir=None → cbm's _global
                        # cache), which is what Brainy's code_* tools read with an
                        # empty request context. Per-project code-mode indexes
                        # live under their project dir, built on code-mode entry —
                        # never here. cbm indexing is incremental + fast.
                        _cg_stats = engine.cbm_index_repository(clone_dir, cache_dir=None)
                        if isinstance(_cg_stats, dict) and _cg_stats.get("error"):
                            print(f"[mempalace-miner] code index: "
                                  f"{_cg_stats['error']}", flush=True)
                    except Exception as e:
                        print(f"[mempalace-miner] code index failed: "
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
                    with contextlib.redirect_stdout(buf), _palace_write_lock:
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
    # RETIRED (LLM Wiki): chat content no longer feeds MemPalace directly. Under
    # the wiki model, the ONLY feeder for user__/team__/wiki_global and
    # project_chat__<id> wings is the wiki (engine.wiki_store._mirror_page);
    # chats become searchable by being memorized into a wiki page, not by this
    # daemon mirroring raw turns. Ingested PROJECT knowledge (files/folders/
    # web-URLs → project__<id>) is unaffected — that's the project-sync daemon.
    # Body retained below but unreachable; the thread is also no longer launched
    # in server.py. Re-enabling would double-feed the wiki-fed wings.
    print("MemPalace chat-sync: retired (wiki is the sole feeder for chat-derived wings)")
    return
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
            # Startup-race guard: the daemon thread can begin a cycle before
            # ChatDB's class body has finished binding its mempalace_* staticmethods
            # (the class body is ~1300 lines; under the boot thread-storm a cycle
            # occasionally lands mid-binding → AttributeError, once). Skip quietly
            # until the methods exist instead of logging a scary cycle error.
            if not hasattr(ChatDB, "mempalace_sessions_needing_sync"):
                time.sleep(2)
                continue
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

            # Classifier gate config (LLM classifier removed; only the
            # min_turns short-chat gate remains referenced in this dead body).
            clf_cfg = sync_cfg2.get("classifier", {}) or {}
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

                # Genuine write-failure tracker (data-loss guard). A drawer write
                # can fail for two very different reasons:
                #   * dedup (`already_exists`) — the content is ALREADY in the
                #     palace; advancing the cursor past it is correct.
                #   * a real failure — an add_drawer exception, or success:False
                #     (e.g. the package's NON-BLOCKING mine_palace_lock refusing
                #     the write with MineAlreadyRunning while the miner/project-
                #     sync daemon holds it). The content is NOT in the palace; if
                #     the cursor advances past it the turn is PERMANENTLY lost.
                # `_wf["min_id"]` records the LOWEST message id that hit a real
                # failure this cycle so the cursor can be clamped below it and the
                # message retried next cycle. None = no genuine failure.
                # `_wf["summary"]` flags a genuine failure on the summary write
                # (which carries no message id) so its hash isn't advanced.
                _wf = {"min_id": None, "summary": False}

                def _note_write_failure(mid_):
                    if mid_ is None:
                        _wf["summary"] = True
                    elif mid_ and (_wf["min_id"] is None or mid_ < _wf["min_id"]):
                        _wf["min_id"] = int(mid_)

                def _file_drawer(w, r, content, source_file, mid=0):
                    if not content:
                        return False
                    content = content[:max_chars]
                    engine.mempalace_activity.store_begin()
                    try:
                        try:
                            # Serialize against the miner/project-sync write phases
                            # so this upsert isn't refused by the package's
                            # non-blocking mine_palace_lock (which would silently
                            # drop the drawer) and can't race a bulk delete.
                            with _palace_write_lock:
                                res = tool_add_drawer(
                                    wing=w,
                                    room=r,
                                    content=content,
                                    source_file=source_file,
                                    added_by="brain-chat-sync",
                                )
                        except Exception as ex:
                            print(f"[mempalace-chat-sync] add_drawer failed: {ex}")
                            _note_write_failure(mid)  # real failure → don't advance past it
                            return False
                    finally:
                        engine.mempalace_activity.store_end()
                    if not isinstance(res, dict) or not res.get("success"):
                        # success:False is a genuine non-write (lock-refused etc.) —
                        # NOT a dedup. Guard the cursor so the message is retried.
                        _note_write_failure(mid)
                        return False
                    if res.get("reason") == "already_exists":
                        return False  # dedup hit — already filed; safe to advance
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

                # The LLM memory-classifier gate was removed (the chat-sync
                # daemon is retired; the wiki is the sole feeder for chat-derived
                # wings). This body is unreachable — kept internally consistent
                # with an empty skip-set so the consume point below still resolves.
                _clf_skip_ids: set[int] = set()

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
                            if _file_drawer(wing, default_room, body, source_file, mid=mid):
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
                                if _file_drawer(wing, "chat_attachment", body, source_file, mid=mid):
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
                                if _file_drawer(wing, "reference", ref_body, source_file, mid=mid):
                                    total_new += 1

                # Session summary — low-frequency text worth indexing separately.
                # `summary_hash` becomes the cursor's last_summary_hash. Only set
                # it to the NEW hash when the summary drawer was actually filed (or
                # deduped) — a genuine write failure must keep the OLD hash so the
                # summary is retried next cycle (else it's permanently skipped).
                summary_hash = ""
                if include_summary:
                    summary = (session_row.get("summary") or "").strip()
                    if summary:
                        _new_summary_hash = hashlib.sha256(summary.encode("utf-8")).hexdigest()[:16]
                        if _new_summary_hash != (session_row.get("last_summary_hash") or ""):
                            body = f"[session summary for {sid}]\n{summary}"
                            source_file = f"session/{sid}#summary"
                            # mid=None routes a genuine failure to _wf["summary"].
                            filed = _file_drawer(wing, "chat_summary", body, source_file,
                                                 mid=None)
                            if filed:
                                total_new += 1
                                summary_hash = _new_summary_hash
                            elif _wf["summary"]:
                                summary_hash = ""  # real failure → keep old hash, retry
                            else:
                                summary_hash = _new_summary_hash  # dedup → advance
                        else:
                            summary_hash = _new_summary_hash  # unchanged → keep

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
                # otherwise we keep re-scanning the same tail forever. BUT never
                # advance PAST a message whose drawer write genuinely failed
                # (lock-refused / exception): clamp the cursor to just below the
                # lowest failed id so that message (and everything after it) is
                # retried next cycle. Without this clamp a transient write failure
                # silently drops the turn from memory forever (the v9.60.4 bug).
                _target = max(new_last_id, max_msg_id)
                if _wf["min_id"] is not None:
                    _target = min(_target, _wf["min_id"] - 1)
                    # Never move the cursor backwards below where we started.
                    _target = max(_target, after_id)
                ChatDB.mempalace_update_cursor(
                    sid,
                    _target,
                    last_summary_hash=summary_hash or session_row.get("last_summary_hash") or "",
                )

            if total_new:
                print(f"[mempalace-chat-sync] filed {total_new} new drawer(s) across {len(pending)} session(s)")
        except AttributeError as e:
            # Transient boot race (ChatDB.mempalace_* not yet bound) — the
            # hasattr guard above closes all but a microscopic window; if we still
            # land in it, treat as a skipped cycle (the next cycle succeeds), not
            # an error. Re-raise any OTHER AttributeError (a real bug).
            if "mempalace_" in str(e):
                time.sleep(2)
            else:
                print(f"[mempalace-chat-sync] cycle error: {type(e).__name__}: {e}")
        except Exception as e:
            print(f"[mempalace-chat-sync] cycle error: {type(e).__name__}: {e}")

        next_interval = int((engine._load_mempalace_config().get("chat_sync") or {}).get("interval_seconds", 60))
        time.sleep(max(15, next_interval))


# Default: only re-fetch a project web URL every 6h (overridable via config
# project_sync.web_url_refresh_seconds). Standing reference pages rarely change
# intra-day, so re-fetching all of a project's URLs every 30-min mining cycle is
# wasted work — most of all for projects nobody is touching. Combined with the
# conditional-GET (ETag/Last-Modified) below, a due-but-unchanged URL costs one
# 304 with no body, and an un-due URL costs nothing at all.
_WEBURL_DEFAULT_REFRESH_SECONDS = 6 * 3600
# Hard staleness ceiling: even when a server keeps answering 304 (or sends a
# sticky/buggy ETag that never changes), force a full body fetch once this much
# time has passed since the last REAL 200-body fetch. Defends against servers
# that honor conditional GET incorrectly. Time-based (not cycle-based) so it's
# stable across interval retunes and missed/slow cycles.
_WEBURL_DEFAULT_MAX_STALE_SECONDS = 24 * 3600
# Re-verify ceiling for VALIDATOR-bearing URLs: even when a server keeps
# answering a clean 304, force ONE full body fetch this often (default 7 days)
# as a safety net against a sticky/buggy ETag OR conversion drift (the upstream
# validator reflects the raw bytes, not necessarily what markitdown/crawl4ai
# renders). Distinct from the 24h no-validator ceiling — verified URLs are
# trustworthy, so they only need an occasional re-check, not a daily one.
_WEBURL_DEFAULT_REVERIFY_SECONDS = 7 * 24 * 3600
_WEBURL_STATE_FILE = ".fetch-state.json"


def _weburl_refresh_seconds():
    try:
        cfg = (engine._server_config().get("project_sync") or {})
        v = int(cfg.get("web_url_refresh_seconds", _WEBURL_DEFAULT_REFRESH_SECONDS))
        return max(0, v)  # 0 ⇒ always re-fetch (old behavior)
    except (TypeError, ValueError, AttributeError):
        return _WEBURL_DEFAULT_REFRESH_SECONDS


def _weburl_max_stale_seconds():
    try:
        cfg = (engine._server_config().get("project_sync") or {})
        v = int(cfg.get("web_url_max_stale_seconds", _WEBURL_DEFAULT_MAX_STALE_SECONDS))
        return max(0, v)  # 0 ⇒ never force (no-validator URLs trusted fully)
    except (TypeError, ValueError, AttributeError):
        return _WEBURL_DEFAULT_MAX_STALE_SECONDS


def _weburl_reverify_seconds():
    try:
        cfg = (engine._server_config().get("project_sync") or {})
        v = int(cfg.get("web_url_reverify_seconds", _WEBURL_DEFAULT_REVERIFY_SECONDS))
        return max(0, v)  # 0 ⇒ trust 304 indefinitely (never re-verify)
    except (TypeError, ValueError, AttributeError):
        return _WEBURL_DEFAULT_REVERIFY_SECONDS


def _load_weburl_state(folder):
    """slug → {etag, last_modified, last_fetch} for conditional GET + refresh
    gating. Best-effort: a missing/corrupt file just means 'no prior state'."""
    try:
        with open(os.path.join(folder, _WEBURL_STATE_FILE), "r", encoding="utf-8") as f:
            d = json.load(f)
            return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_weburl_state(folder, state):
    try:
        with open(os.path.join(folder, _WEBURL_STATE_FILE), "w", encoding="utf-8") as f:
            json.dump(state, f)
    except OSError:
        pass


def _conditional_fetch(url, etag, last_modified, timeout):
    """A direct conditional GET (Lever B). Returns one of:
      ("not_modified", None)        — server said 304; reuse the on-disk copy
      ("ok", {content,url,etag,last_modified}) — fresh body
      ("error", "<reason>")         — transient failure; keep the prior copy

    Done HERE rather than through tool_web_fetch because that tool returns a
    content dict (never a 304) and is shared by many callers — we don't want to
    teach the general fetch tool about conditional GET. JS-rendered pages still
    work: a 200 here hands the raw HTML to the same markitdown/crawl4ai pass via
    tool_web_fetch on the NON-304 path below (see caller)."""
    import urllib.request as _ur
    import urllib.error as _ue
    req_headers = {
        "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"),
    }
    if etag:
        req_headers["If-None-Match"] = etag
    if last_modified:
        req_headers["If-Modified-Since"] = last_modified
    try:
        req = _ur.Request(url, headers=req_headers, method="GET")
        with _ur.urlopen(req, timeout=timeout) as resp:
            new_etag = resp.headers.get("ETag", "") or ""
            new_lm = resp.headers.get("Last-Modified", "") or ""
            return "ok", {"etag": new_etag, "last_modified": new_lm, "status": resp.status}
    except _ue.HTTPError as e:
        if e.code == 304:
            return "not_modified", None
        return "error", f"HTTP {e.code}"
    except Exception as e:
        return "error", f"{type(e).__name__}: {e}"


def weburl_base_slug(url: str) -> str:
    """Stable per-URL slug base used for the `web-urls/<slug>_<ts>.md` companion
    filenames. MUST stay byte-identical between the sync writer and any reader
    (e.g. the per-URL state endpoint) or the companion lookup misses."""
    from urllib.parse import urlparse as _up
    try:
        p = _up(url)
        base = (p.netloc + p.path).strip("/")
    except Exception:
        base = url
    s = re.sub(r"[^A-Za-z0-9]+", "-", base).strip("-").lower()
    return s[:60] or "url"


def weburl_slug(url: str, slug_counts: dict) -> str:
    """Final slug for a URL: the clean base, OR base + 8-hex hash when the base
    collides with another configured URL (slug_counts[base] > 1)."""
    b = weburl_base_slug(url)
    if slug_counts.get(b, 0) > 1:
        return f"{b}-{hashlib.sha256(url.encode('utf-8')).hexdigest()[:8]}"
    return b


def weburl_states(pdir: str, web_urls, indexed_source_files) -> dict:
    """url → 'indexed' | 'pending'. A URL is 'indexed' iff its
    `web-urls/<slug>_*.md` companion exists on disk AND that companion's path is
    in the MemPalace indexed set (drawer source_files). Used by the source-tree
    endpoint for per-URL state dots (web URLs are mined as a batch, so there's
    no per-URL sync-status item — we derive it from the companions instead)."""
    folder = os.path.join(pdir, "web-urls")
    urls = [(u.get("url") or "").strip() for u in (web_urls or [])
            if isinstance(u, dict) and (u.get("url") or "").strip()]
    counts = {}
    for u in urls:
        b = weburl_base_slug(u)
        counts[b] = counts.get(b, 0) + 1
    # Map slug → its newest companion .md path on disk.
    on_disk = {}
    try:
        for fn in os.listdir(folder):
            if fn.endswith(".md") and "_" in fn:
                slug = fn.rsplit("_", 1)[0]
                fp = os.path.join(folder, fn)
                # keep newest (lexical max of the timestamp suffix works here)
                if slug not in on_disk or fn > os.path.basename(on_disk[slug]):
                    on_disk[slug] = fp
    except OSError:
        pass
    idx = indexed_source_files or set()
    out = {}
    for u in urls:
        slug = weburl_slug(u, counts)
        comp = on_disk.get(slug)
        out[u] = "indexed" if (comp and os.path.realpath(comp) in idx) else "pending"
    return out


def _sync_project_xlsx_profiles(pdir, project):
    """v9.264.0: file one STRUCTURE PROFILE per project spreadsheet into
    `<pdir>/xlsx-profiles/` as a mined .md — the xlsx_inspect report (sheets,
    columns/types, join-key candidates, SQL identifiers) lands in the project
    wing, so 'welche Datei hat Spalte X / wie hängen die Blätter zusammen'
    answers from retrieval WITHOUT a live inspect call. Mirrors the web-urls
    pattern: mtime/size-gated per source (regenerate only on change), profiles
    of vanished sources are pruned (the loop's stale-path purge then drops
    their drawers). Returns the folder path or '' when no spreadsheets exist.

    NOTE the mining companion `.md` (doc_convert, byte-stable invariant) is
    NOT touched — the profile is an ADDITIONAL, separately-mined file."""
    import hashlib as _hl
    exts = (".xlsx", ".xlsm", ".xls", ".ods")
    folder = os.path.join(pdir, "xlsx-profiles")

    sources = []
    roots = [os.path.join(pdir, "ingested")]
    for fe in (project.get("input_folders") or []):
        p = (fe.get("path") or "").strip()
        if p:
            roots.append(os.path.expanduser(p))
    for root in roots:
        if not os.path.isdir(root):
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            for fn in sorted(filenames):
                if fn.lower().endswith(exts):
                    sources.append(os.path.join(dirpath, fn))

    if not sources:
        if os.path.isdir(folder):
            for fn in os.listdir(folder):
                if fn.startswith("xlsxprofile-") and fn.endswith(".md"):
                    try:
                        os.remove(os.path.join(folder, fn))
                    except OSError:
                        pass
        return folder if os.path.isdir(folder) else ""

    os.makedirs(folder, exist_ok=True)
    kept = set()
    for src in sources:
        try:
            st = os.stat(src)
        except OSError:
            continue
        slug = ("xlsxprofile-"
                + _hl.sha1(os.path.realpath(src).encode()).hexdigest()[:12])
        out_fn = f"{slug}.md"
        out_path = os.path.join(folder, out_fn)
        stamp = f"<!-- brain-profile-state: {int(st.st_mtime)}|{st.st_size} -->"
        if os.path.isfile(out_path):
            try:
                with open(out_path, encoding="utf-8") as f:
                    head = f.read(400)
                if stamp in head:
                    kept.add(out_fn)   # source unchanged → keep as-is
                    continue
            except OSError:
                pass
        try:
            from engine.tools.xlsx_tools import _inspect_report
            report = _inspect_report([src])
        except Exception as e:
            print(f"[project-sync.xlsxprofile] {os.path.basename(src)}: "
                  f"{type(e).__name__}: {e}", flush=True)
            continue
        body = (f"<!-- brain-source: {os.path.realpath(src)} -->\n"
                f"{stamp}\n"
                f"# Struktur-Profil: {os.path.basename(src)}\n\n"
                f"Automatisch erzeugtes Tabellen-Profil (xlsx_inspect) der "
                f"Projekt-Datei `{os.path.basename(src)}`.\n\n"
                + report + "\n")
        try:
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(body)
            kept.add(out_fn)
        except OSError as e:
            print(f"[project-sync.xlsxprofile] write {out_fn}: {e}", flush=True)
    # prune profiles whose source vanished
    for fn in os.listdir(folder):
        if (fn.startswith("xlsxprofile-") and fn.endswith(".md")
                and fn not in kept):
            try:
                os.remove(os.path.join(folder, fn))
            except OSError:
                pass
    return folder


def _sync_project_web_urls(pdir, web_urls):
    """Fetch the project's configured web URLs into a `web-urls/` subfolder as
    hash-gated `.md` companion files, so the existing project-sync mine + KG
    pass treats each URL's content like any other project source file.

    Refresh is now COST-GATED two ways (was: re-fetch every cycle):
      • interval (Lever A) — a URL with an on-disk copy fetched < refresh window
        ago is skipped entirely this cycle (web_url_refresh_seconds, default 6h);
      • conditional GET (Lever B) — when a URL IS due, a HEAD-cheap conditional
        GET (If-None-Match / If-Modified-Since) lets the server answer 304 and we
        reuse the on-disk copy without downloading the body.
    Only a real 200-with-changed-content rewrites the `.md` (→ re-mine), exactly
    as before. Per-URL fetch metadata lives in `web-urls/.fetch-state.json`.
    Files for URLs no longer configured are deleted (the loop's stale-path purge
    then drops their drawers/triples).

    Returns the absolute path of the `web-urls/` folder (created on demand), or
    "" when there are no URLs configured.
    """
    import hashlib as _hl
    import datetime as _dt
    import time as _time
    folder = os.path.join(pdir, "web-urls")
    urls = [(u.get("url") or "").strip() for u in (web_urls or []) if isinstance(u, dict)]
    urls = [u for u in urls if u]
    # Filename scheme: `<url-slug>_<YYYY-MM-DD-HHMM>.md`. The slug is the stable
    # per-URL identity (same URL → same slug every cycle); the timestamp marks
    # the LAST CONTENT CHANGE (= last mine), not the last fetch. So: unchanged
    # content keeps its file (name + timestamp) and is NOT re-mined; changed
    # content gets a fresh timestamped file and the old slug file(s) are
    # deleted (the loop's _is_stale_src then drops the old drawers because
    # their .md path no longer exists). The slug is clean by default
    # (www-macrumors-com); a short URL-hash is appended ONLY when two
    # configured URLs would otherwise slugify to the same name (http vs
    # https, trailing slash, long paths sharing a 60-char prefix) — so the
    # common case stays readable and collisions still never overwrite.
    _base_slug = weburl_base_slug
    # Which base slugs collide across the configured URL set → those need the
    # disambiguating hash; everyone else gets the clean slug.
    _all_urls = [(u.get("url") or "").strip() for u in (web_urls or [])
                 if isinstance(u, dict) and (u.get("url") or "").strip()]
    _slug_counts = {}
    for _u in _all_urls:
        _b = _base_slug(_u)
        _slug_counts[_b] = _slug_counts.get(_b, 0) + 1

    def _url_slug(_u):
        return weburl_slug(_u, _slug_counts)

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
    state = _load_weburl_state(folder)          # slug → {etag, last_modified, last_fetch, last_full_fetch}
    refresh_s = _weburl_refresh_seconds()
    max_stale_s = _weburl_max_stale_seconds()
    reverify_s = _weburl_reverify_seconds()
    now = _time.time()
    wf_timeout = engine.get_tool_config().get("web_fetch", {}).get("timeout", 30)
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
        st = state.get(slug) or {}

        # Lever A — interval gate. If we have an on-disk copy and it was fetched
        # within the refresh window, skip this URL entirely this cycle (no
        # network at all). refresh_s == 0 disables the gate (always re-fetch).
        if existing and refresh_s and (now - float(st.get("last_fetch", 0))) < refresh_s:
            kept_files.update(existing)
            continue

        _has_validator = bool(st.get("etag") or st.get("last_modified"))
        _full_age = now - float(st.get("last_full_fetch", 0))
        _ceiling_due = bool(max_stale_s) and _full_age >= max_stale_s

        # Two ceilings, each measured against the last REAL 200-body fetch
        # (last_full_fetch, never bumped by a 304):
        #   • no-validator URLs (can't verify cheaply) — full fetch every
        #     max_stale_s (default 24h).
        #   • validator URLs (304-verifiable) — a clean 304 is the server's
        #     certain "not modified", so we trust it and only re-verify with a
        #     full body fetch every reverify_s (default 7d), as a safety net
        #     against a sticky/buggy ETag or conversion drift.
        if existing and not _has_validator:
            # Can't verify cheaply: skip until the 24h ceiling forces a full
            # fetch. (No validator AND no last_full_fetch ⇒ ceiling-due, so it
            # gets one full fetch to (re)learn whether it has validators.)
            if not _ceiling_due and st.get("last_full_fetch"):
                kept_files.update(existing)
                continue
            # else: fall through to the full fetch below.

        _reverify_due = bool(reverify_s) and _full_age >= reverify_s

        # Lever B — conditional GET. When a validator-bearing URL is due AND it's
        # not yet time to re-verify, ask the server first; a 304 reuses the
        # on-disk copy with no body download / no re-mine. When the 7d re-verify
        # window has elapsed we skip the 304 path and force a full body fetch.
        if existing and _has_validator and not _reverify_due:
            kind, info = _conditional_fetch(url, st.get("etag"), st.get("last_modified"), wf_timeout)
            if kind == "not_modified":
                kept_files.update(existing)
                st["last_fetch"] = now
                state[slug] = st
                continue
            # "ok" → fall through to the full fetch below (we need the body to
            # markitdown/render + hash); "error" → also fall through, the full
            # fetch's own error handling keeps the prior copy.

        try:
            # max_length is web_fetch's PER-LLM-TURN char cap (default 50k) —
            # wrong for mining, which goes to disk + chunked embedding, NOT into
            # an LLM context. A 50k cap silently truncated long PDFs/pages
            # (e.g. a 520k-char annual report lost its balance-sheet tables),
            # so the mined companion .md — and thus the project memory — held
            # only the first ~50k chars. Mine the FULL document; the byte cap
            # (_wf_max_size_mb, 10 MB) still guards against pathological sizes.
            parsed = json.loads(engine.tool_web_fetch(
                {"url": url, "force_fresh": True, "max_length": 10_000_000}))
        except (ValueError, TypeError):
            parsed = {}
        if parsed.get("error") or "content" not in parsed:
            # Transient fetch failure: keep the prior good copy untouched.
            print(f"[project-sync.weburl] fetch failed {url}: "
                  f"{parsed.get('error', 'unknown')}", flush=True)
            kept_files.update(existing)
            continue
        # Real 200-body fetch succeeded — record fetch time, the validators
        # (captured from the SAME fetch, no extra round-trip), and reset the
        # full-fetch clock that the staleness ceiling measures against.
        st["last_fetch"] = now
        st["last_full_fetch"] = now
        st["etag"] = parsed.get("etag", "") or ""
        st["last_modified"] = parsed.get("last_modified", "") or ""
        state[slug] = st
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
    # Persist fetch state, dropping entries for URLs no longer configured (their
    # slugs aren't in this cycle's set) so the file doesn't grow unbounded.
    live_slugs = {_url_slug(u) for u in urls}
    state = {k: v for k, v in state.items() if k in live_slugs}
    _save_weburl_state(folder, state)
    return folder


# Marker line stamped into an ingested chunk's BODY so every mined drawer
# carries its source's virtual-group path verbatim — e.g. a query about
# "Kunde A" gets back chunks visibly tagged "[Projekt-Gruppe: Kunde A / …]" and
# the LLM never confuses them with Kunde B. Stable prefix so we can detect +
# strip our own prior lines on re-group without touching the real content.
_GROUP_PREFIX_MARK = "> [Projekt-Gruppe:"

# The miner re-chunks each file with an ~800-char sliding window (no per-chunk
# header carry), preferring to break on blank lines. So a SINGLE head marker
# only rides chunk 0 — later chunks of a long file would lose the customer
# context. To make EVERY resulting drawer self-identify, we repeat the marker
# before each paragraph AND, within an over-long paragraph, every
# _GROUP_REINJECT_CHARS at a whitespace boundary (well under the chunk window so
# each window catches at least one marker). Kept below 800 with margin.
_GROUP_REINJECT_CHARS = 600


def _resolve_group_path(groups_by_id, gid):
    """Walk the parent chain of group `gid` → 'Top / Sub / Leaf' (root-first).
    Cycle-safe (the on-disk shape is already sanitised, but be defensive)."""
    parts = []
    seen = set()
    while gid and gid not in seen:
        seen.add(gid)
        g = groups_by_id.get(gid)
        if not g:
            break
        name = (g.get("name") or "").strip()
        if name:
            parts.append(name)
        gid = (g.get("parent") or "").strip()
    parts.reverse()
    return " / ".join(parts)


def _strip_group_markers(body):
    """Remove every marker line we previously injected (so re-group replaces,
    not stacks) plus the blank line that follows each. Leaves real content."""
    out = []
    lines = body.split("\n")
    i = 0
    while i < len(lines):
        if lines[i].startswith(_GROUP_PREFIX_MARK):
            i += 1
            # swallow exactly one trailing blank separator if present
            if i < len(lines) and lines[i].strip() == "":
                i += 1
            continue
        out.append(lines[i])
        i += 1
    return "\n".join(out)


def _inject_group_markers(body, marker):
    """Prepend `marker` before each paragraph; additionally re-emit it inside
    any paragraph longer than _GROUP_REINJECT_CHARS at a whitespace boundary, so
    the miner's ~800-char chunks each begin with the customer context."""
    clean = _strip_group_markers(body).strip()
    if not clean:
        return f"{marker}\n"
    paras = re.split(r'\n\s*\n', clean)
    out_paras = []
    for para in paras:
        para = para.strip()
        if not para:
            continue
        if len(para) <= _GROUP_REINJECT_CHARS:
            out_paras.append(f"{marker}\n\n{para}")
            continue
        # Long single paragraph: slice at whitespace near the interval, marker
        # before each slice.
        pieces = []
        pos = 0
        while pos < len(para):
            end = min(pos + _GROUP_REINJECT_CHARS, len(para))
            if end < len(para):
                sp = para.rfind(" ", pos + _GROUP_REINJECT_CHARS // 2, end)
                if sp > pos:
                    end = sp
            pieces.append(para[pos:end].strip())
            pos = end
        out_paras.append("\n\n".join(f"{marker}\n\n{pc}" for pc in pieces if pc))
    return "\n\n".join(out_paras) + "\n"


def _apply_group_prefixes(ingested_dir, project):
    """Before mining, stamp each ingested chunk file's BODY with its source's
    virtual-group path (from project.json source_groups.files). The marker
    becomes part of the drawer text — repeated densely enough that EVERY chunk
    the miner produces self-identifies its group/customer — so mempalace_query
    results never let the LLM confuse Kunde A with Kunde B. Idempotent: rewrites
    only when the desired body differs from what's there (mtime stays put
    otherwise, so the miner's mtime-gated skip keeps incremental sync cheap).
    Re-grouping a file in the UI changes its assign → next sync rewrites the
    markers → the miner re-mines that file with the new context.

    Files not assigned to any group get NO marker (and any stale marker from a
    prior grouping is stripped). Best-effort per file — an unreadable/oddly
    shaped chunk is skipped, never fatal to the sync."""
    sg = (project.get("source_groups") or {}).get("files") or {}
    assign = sg.get("assign") or {}
    groups_by_id = {g.get("id"): g for g in (sg.get("groups") or []) if isinstance(g, dict)}
    try:
        names = os.listdir(ingested_dir)
    except OSError:
        return
    for fn in names:
        key = engine.IngestManager._key_from_filename(fn)
        if not key:
            continue
        gid = assign.get(key)
        path = _resolve_group_path(groups_by_id, gid) if gid else ""
        marker = f"{_GROUP_PREFIX_MARK} {path}]" if path else ""
        fpath = os.path.join(ingested_dir, fn)
        try:
            with open(fpath, "r") as f:
                content = f.read()
        except OSError:
            continue
        # Split frontmatter (--- … ---) from body so the marker rides in the
        # BODY, never inside the YAML header.
        head, body = "", content
        if content.startswith("---"):
            end = content.find("\n---", 3)
            if end != -1:
                nl = content.find("\n", end + 1)
                if nl != -1:
                    head = content[:nl + 1]
                    body = content[nl + 1:]
        if marker:
            new_body = "\n" + _inject_group_markers(body, marker)
        else:
            # Ungrouped → strip any markers we may have left previously.
            new_body = "\n" + _strip_group_markers(body).strip() + "\n"
        new_content = head + new_body
        if new_content != content:
            try:
                with open(fpath, "w") as f:
                    f.write(new_content)
            except OSError:
                continue


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

    # Per-project KG override holder: set at the top of each project iteration
    # to {"method":..,"profile":..} from project.json (empty values = inherit the
    # global default). _run_kg_for reads it at call time. Attachment/folder KG
    # runs all belong to the project currently being synced, so one holder is
    # correct (the loop is single-threaded per project).
    _cur_project_kg: dict = {}

    def _parse_drawers_filed(mine_stdout: str) -> int:
        """Pull the integer from the miner's 'Drawers filed: N' summary line."""
        for line in mine_stdout.splitlines():
            s = line.strip()
            if s.startswith("Drawers filed"):
                try:
                    return int(s.split(":")[-1].strip().split()[0])
                except Exception:
                    return 0
        return 0

    def _mine_batched(folder: str, wing: str, agent_name: str, *,
                      progress_cb=None, batch_size: int = 25,
                      respect_gitignore: bool = False) -> int:
        """Mine `folder` into `wing` in batches so the UI sees STEADY progress.

        mp_miner.mine() over a whole folder is one opaque blocking call that
        emits nothing until done — a big project then shows a frozen "syncing"
        for minutes. Instead we pre-scan the file list and feed it to mine() in
        `batch_size` chunks (mine accepts a `files=` subset), calling
        `progress_cb(done, total, drawers_so_far)` after each batch.

        Progress is reported in DOCUMENTS, not chunk files: one uploaded doc is
        split into several `<key>__<idx>.md` chunks, so a 258-doc project has
        ~547 chunk files — counting chunks would show a misleading total. We map
        each file to its document key (IngestManager._key_from_filename) and
        report distinct-keys-seen / distinct-keys-total. The miner is idempotent
        + mtime-cursored, so batching changes nothing about the result — only the
        feedback cadence. Returns total drawers filed. Holds _palace_write_lock
        per batch (not across the whole mine) so other daemons interleave.

        `respect_gitignore` mirrors mp_miner.mine()'s flag: the ingested/ path
        passes False (uploaded chunks, no .gitignore), input_folders pass True
        (user dirs may carry .gitignore). The pre-scan and the per-batch mine()
        MUST use the same value or the scanned set and mine()'s own walk diverge."""
        try:
            files = mp_miner.scan_project(folder, respect_gitignore=respect_gitignore)
            # scan_project yields pathlib.PosixPath, but drawer source_file
            # metadata + mine()'s file list are plain strings. A PosixPath never
            # equals a str as a dict key, so without this the bulk pre-filter's
            # `_mined.get(f)` ALWAYS missed → every file looked "changed" → all
            # ~195 files were handed to mine(), which then paid the per-file
            # file_already_mined() Qdrant skip-check (the ~264s indexing cost on
            # a 1-file change). Normalising to str makes the lookup match.
            if files:
                files = [os.fspath(f) for f in files]
        except Exception as e:
            print(f"[project-sync] scan_project failed {folder}: "
                  f"{type(e).__name__}: {e}", flush=True)
            files = None
        if files is None:
            # scan_project RAISED — we don't know the file set, so fall back to a
            # whole-folder mine() to never regress below the original single call.
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf), _palace_write_lock:
                mp_miner.mine(project_dir=folder, palace_path=palace_path,
                              wing_override=wing, agent=agent_name,
                              respect_gitignore=respect_gitignore)
            return _parse_drawers_filed(buf.getvalue())
        if not files:
            # scan_project returned an EMPTY list — the folder has nothing
            # minable (e.g. kg-real-policies/ingested holds only mempalace.yaml).
            # Do NOT call mine(): an empty mine() still runs the wing-WIDE
            # entity-link rebuild (hallways + tunnels, ~165s on a large wing) for
            # ZERO drawers. Skipping it here is what makes a no-change folder
            # sync actually cheap. (The old code's whole-folder fallback fired
            # the rebuild on every empty ingested/ mine — the 168s ghost step.)
            return 0

        # BULK pre-filter so an unchanged project costs ~one Qdrant scan instead
        # of one file_already_mined() query PER FILE inside mine(). mine() skips
        # unchanged files correctly, but each skip still issues a paginated
        # collection.get(where={source_file}) — with ~200 files that made the
        # "indexing" phase take ~2 min even when NOTHING changed. Here we fetch
        # the wing's {source_file: source_mtime} map ONCE and drop files whose
        # mtime already matches; only genuinely new/changed files reach mine().
        # When nothing changed, `files` becomes empty and mine() is never called.
        #
        # WING-SCOPED: upstream bulk_check_mined() paginates the ENTIRE shared
        # drawers collection (ALL projects) — O(total_corpus) per project, which
        # at hundreds of projects made even a 1-file change spend ~170s scanning
        # everyone else's drawers. We instead do ONE `get(where={wing})` so we
        # read only this project's ~few-thousand drawers. (Same wing-filter the
        # rest of the daemon already uses, e.g. _iter_wing_source_files.)
        # CAVEAT: this matches on mtime only, so it does NOT trigger the rare
        # normalize_version-upgrade re-mine that file_already_mined() would (a
        # mempalace schema bump). That self-corrects on the file's next real edit
        # and can be forced via Full-Resync; the steady-state no-op cost is what
        # matters here.
        def _doc_key(fp):
            # Map a chunk file path to its uploaded-document key; fall back to
            # the path itself for non-chunk files (so they still count as 1 doc).
            try:
                k = engine.IngestManager._key_from_filename(os.path.basename(fp))
            except Exception:
                k = None
            return k or fp

        # Grand total of DISTINCT documents on disk (changed + unchanged), in the
        # same doc-key space as total_docs below. Captured BEFORE the pre-filter
        # narrows `files` to the changed set, so the UI can show
        # "N need updating · M already up to date" instead of a bare N that reads
        # like the whole project.
        total_all_docs = len({_doc_key(f) for f in files})
        unchanged_docs = 0
        try:
            _col = _get_drawers_col(palace_path, create=False)
            if _col is not None:
                # Wing-scoped {source_file: source_mtime} map — one Qdrant fetch
                # filtered to this project's drawers, not the whole corpus.
                _mined = {}
                _got = _col.get(where={"wing": wing}, include=["metadatas"])
                for _m in (_got.get("metadatas") or []):
                    _m = _m or {}
                    _src = _m.get("source_file")
                    _mt = _m.get("source_mtime")
                    if _src and _mt is not None:
                        _mined[_src] = float(_mt)
                _changed = []
                for f in files:
                    try:
                        _prev = _mined.get(f)
                        if _prev is None:
                            _changed.append(f)            # never mined → mine it
                        elif abs(float(_prev) - os.path.getmtime(f)) >= 0.001:
                            _changed.append(f)            # mtime moved → re-mine
                        # else: unchanged → skip (don't hand to mine())
                    except OSError:
                        _changed.append(f)                # stat failed → let mine() decide
                if len(_changed) != len(files):
                    print(f"[project-sync] pre-filter {os.path.basename(folder)}: "
                          f"{len(_changed)}/{len(files)} file(s) changed, "
                          f"{len(files) - len(_changed)} unchanged skipped",
                          flush=True)
                # Unchanged DOCUMENT count = grand total minus the docs that
                # survived to the changed set (chunk-count differences collapse
                # to the same doc key, so compute in doc-key space, not file
                # count).
                unchanged_docs = total_all_docs - len({_doc_key(f) for f in _changed})
                files = _changed
        except Exception as _e:
            # Best-effort optimisation — on any error fall through to the full
            # (correct, just slower) per-file path below.
            print(f"[project-sync] mine pre-filter skipped ({type(_e).__name__}: "
                  f"{_e}) — full mine", flush=True)
        if not files:
            return 0  # nothing changed → mine() not needed at all

        total_docs = len({_doc_key(f) for f in files})
        filed = 0

        def _emit(done, total, drawers):
            # Forward optional total_all/unchanged so the UI can distinguish
            # "changed docs being mined" from "docs already up to date on disk".
            # Older callbacks that only take (done, total, filed) still work.
            if not progress_cb:
                return
            try:
                progress_cb(done, total, drawers,
                            total_all=total_all_docs, unchanged=unchanged_docs)
            except TypeError:
                try: progress_cb(done, total, drawers)
                except Exception: pass
            except Exception:
                pass

        _emit(0, total_docs, 0)
        # ONE mine() over ALL changed files — NOT per-batch. mp_miner.mine()
        # runs a wing-WIDE entity-link rebuild (hallways + cross-wing topic +
        # entity tunnels) at the END of every call, and that rebuild scans the
        # WHOLE wing (cost ∝ wing size, ~165s on a 1500-drawer wing) regardless
        # of how many files changed. Batching the mine therefore multiplied that
        # fixed cost by the batch count. The pre-filter already shrank `files` to
        # just the changed set, so a single mine() call does the minimum drawer
        # work AND pays the entity-link rebuild exactly once. When nothing
        # changed we returned above and mine() (hence the rebuild) never runs.
        # We keep batch_size only as a safety ceiling: if a huge number of files
        # genuinely changed at once, fall back to chunked calls (rare; the extra
        # rebuilds are acceptable vs one pathologically large mine()).
        if len(files) <= max(batch_size, 200):
            buf = io.StringIO()
            try:
                with contextlib.redirect_stdout(buf), _palace_write_lock:
                    # mine() iterates `files` and calls Path methods (.read_text,
                    # .suffix) on each, so it needs pathlib.Path — but the
                    # pre-filter above keys on str (drawer source_file is str).
                    # Convert back to Path ONLY at the mine() boundary.
                    mp_miner.mine(project_dir=folder, palace_path=palace_path,
                                  wing_override=wing, agent=agent_name,
                                  respect_gitignore=respect_gitignore,
                                  files=[Path(f) for f in files])
                filed += _parse_drawers_filed(buf.getvalue())
            except SystemExit:
                pass
            except Exception as e:
                print(f"[project-sync] mine failed {folder} "
                      f"({len(files)} file(s)): {type(e).__name__}: {e}",
                      flush=True)
            _emit(total_docs, total_docs, filed)
        else:
            seen_docs: set = set()
            for i in range(0, len(files), batch_size):
                batch = files[i:i + batch_size]
                buf = io.StringIO()
                try:
                    with contextlib.redirect_stdout(buf), _palace_write_lock:
                        mp_miner.mine(project_dir=folder, palace_path=palace_path,
                                      wing_override=wing, agent=agent_name,
                                      respect_gitignore=respect_gitignore,
                                      files=[Path(f) for f in batch])
                    filed += _parse_drawers_filed(buf.getvalue())
                except SystemExit:
                    pass
                except Exception as e:
                    print(f"[project-sync] mine batch failed {folder} "
                          f"[{i}:{i+len(batch)}]: {type(e).__name__}: {e}",
                          flush=True)
                for f in batch:
                    seen_docs.add(_doc_key(f))
                _emit(len(seen_docs), total_docs, filed)
        return filed

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
        max_triples = int(kg_cfg.get("max_triples_per_drawer", 12))
        max_drawer_chars = int(kg_cfg.get("max_drawer_chars", 6000))
        min_conf = float(kg_cfg.get("min_confidence", 0.5))
        chunk_mode = (kg_cfg.get("chunking_mode") or "source_file").strip()
        if chunk_mode not in ("source_file", "per_drawer"):
            chunk_mode = "source_file"
        source_chunk_chars = int(kg_cfg.get("source_chunk_chars", 3500))
        # method + profile resolve project-wide default → per-project override.
        # The per-project override (project.json kg_method/kg_profile) is set on
        # `_cur_project_kg` at the top of each project iteration; empty = inherit.
        ov = _cur_project_kg or {}
        method = (ov.get("method") or kg_cfg.get("method", "llm") or "llm").strip().lower()
        if method not in ("llm", "rules"):
            method = "llm"
        profile_name = (ov.get("profile") or kg_cfg.get("profile", "normative")
                        or "normative").strip().lower()
        if profile_name not in ("normative", "generic"):
            profile_name = "normative"
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
                    # Per-CHUNK live tick so the top-level KG counter advances
                    # smoothly WITHIN a document too (not just once per doc), and
                    # keeps moving even when a chunk yields 0 triples. The shared
                    # cross-document totals live on srv._project_sync_live via the
                    # loop below; here we just bump the in-flight chunk delta.
                    try:
                        base = int(srv._project_sync_live_status(
                            agent_id, proj_name).get("kg_done_base") or 0)
                        srv._project_sync_set_live(
                            agent_id, proj_name,
                            state="syncing", mining_phase="kg",
                            kg_done=base + _kg_chunks_done[0],
                            kg_triples_live=info.get("running_total", 0))
                    except Exception:
                        pass

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
                method=method,
                log_prefix="[project-sync.kg]",
                progress_cb=_kg_progress_cb,
            )
            _kg_chunks_total[0] = res.drawers_processed + res.drawers_skipped
            # LOUD detection of a BROKEN extraction model (vs. benign parse
            # misses). The 2026-06 policy-KG incident — extraction_model pointed
            # at a non-existent provider → every call "sidecar returned no reply"
            # → 0 triples — was invisible because per-chunk errors are treated as
            # normal noise. Surface it explicitly: a transport-class error string,
            # OR errors with zero successful triples on a non-empty run, means the
            # MODEL/PROVIDER is broken, not the content. This is the signal an
            # operator needs to catch a dead extraction model during re-mine.
            _emsg = (res.error_msg or "").lower()
            _transport_broken = any(s in _emsg for s in (
                "no reply", "could not resolve", "connection", "auth",
                "timeout", "unauthorized", "not found", "no provider"))
            if res.errors and (_transport_broken or
                               (res.triples_extracted == 0 and res.drawers_processed == 0
                                and res.errors >= 3)):
                print(f"[project-sync.kg] *** EXTRACTION MODEL APPEARS BROKEN *** "
                      f"wing={wing} model={model} errors={res.errors} "
                      f"triples={res.triples_extracted} last_error={res.error_msg!r} "
                      f"— check that the extraction_model's provider exists + is "
                      f"reachable (Settings → KG). Cursor NOT advanced; will retry.",
                      flush=True)
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
            # kg_state reflects whether the PASS itself failed, NOT whether it
            # found triples. Per-chunk parse errors (res.errors) on content with
            # no extractable relations — e.g. news articles under the normative
            # (policy) profile — are normal noise, not a failure; flagging them
            # as 'error' lit a false red bullet that never cleared on all-skip
            # re-runs. Only the exception path below (the pass crashed) is a real
            # item error. A completed pass = idle, even with 0 triples.
            item_set_fn(item_kind, item_id,
                kg_state="idle",
                triples_extracted=triples_cumulative,
                triples_last_cycle=int(res.triples_extracted),
                kg_drawers_processed=int(res.drawers_processed),
                kg_parse_errors=int(res.errors),
                # Source files GDPR/classification skipped this pass — the doc
                # would be blocked/anonymised, so KG extraction was deliberately
                # not attempted. Surfaced per-doc in the project view.
                kg_gdpr_skipped=int(getattr(res, "gdpr_skipped", 0)),
                kg_last_error=res.error_msg or "",
                kg_elapsed_s=round(res.elapsed_s, 1))
        except Exception as e:
            item_set_fn(item_kind, item_id,
                kg_state="error",
                kg_last_error=f"{type(e).__name__}: {e}")
            print(f"[project-sync.kg] wing={wing} prefix={source_prefix} "
                  f"failed: {type(e).__name__}: {e}", flush=True)

    def _run_closet_regen_for(wing: str, source_prefix: str = "",
                              progress_cb=None):
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
        and only rebuilds the sources that changed since the last
        cycle. With 400 unchanged PDFs the wrapper short-circuits in
        milliseconds; with one edited PDF it rebuilds only that one
        source's closets (per-source purge+upsert is idempotent, so
        untouched sources keep theirs) and refreshes only its cursor.
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
                    # Parallel fan-out width resolved from the KG model's
                    # provider concurrency cap (same policy as KG extraction).
                    model_for_workers=model,
                    progress_cb=progress_cb,
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

    # NOTE: the per-source drawer counters (_count_wing_drawers_by_source /
    # _count_wing_drawers_by_prefixes) were removed in 9.185.0. They each did a
    # full-wing `col.get(where={wing})` metadata fetch to populate a cosmetic
    # per-item "(N Schubladen)" badge, and ran in the hot path BETWEEN indexing
    # and KG — on a ~1900-drawer wing that stalled the pipeline for tens of
    # seconds with no phase shown ("stuck at 2/2"). The badge now degrades to
    # "Indexiert". Only the end-of-cycle wing totals below survive (they run
    # once after all work, off the hot path).

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
                # Defined before the try so the finally can always close an open
                # run row — even if start_run or any early step raises. Without
                # this, an exception between start_run and finish_run left the
                # run stuck in state='running' forever (orphaned phantom mines).
                _run_id = None
                _run_finished = False
                try:
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
                    # Due-gate: skip a SCHEDULED pass when this project's last
                    # successful sync is still within the interval. This makes the
                    # interval survive a server RESTART (the loop runs its first
                    # pass immediately on boot + keeps no in-memory clock, so
                    # without this every restart re-triggered a full pass). Manual
                    # "Sync now" (is_manual) always runs; a never-synced project
                    # (last=None) always runs.
                    if not is_manual:
                        _last = _sync_log.last_completed_at(chats_db_path, project_id)
                        if _last is not None and (time.time() - _last) < interval:
                            _age = int(time.time() - _last)
                            print(f"[project-sync] skip {agent_id}/{proj_name}: "
                                  f"not due ({_age}s since last sync < {interval}s "
                                  f"interval)", flush=True)
                            continue
                    wing = _project_wing(project_id)
                    # Per-project KG method/profile override (project.json) for
                    # the _run_kg_for closure this iteration. Empty = inherit the
                    # global default from config.json mempalace.kg.
                    _cur_project_kg = {
                        "method": (project.get("kg_method") or "").strip().lower(),
                        "profile": (project.get("kg_profile") or "").strip().lower(),
                    }

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

                    # Spreadsheet structure profiles (v9.264.0) → one mined
                    # .md per project xlsx/xlsm/xls/ods (mtime-gated, stale-
                    # pruned) so structure questions answer from the wing.
                    _xlsxprof_folder = ""
                    try:
                        _xlsxprof_folder = _sync_project_xlsx_profiles(
                            pdir, project)
                    except Exception as _e_xp:
                        print(f"[project-sync.xlsxprofile] {agent_id}/"
                              f"{proj_name}: {type(_e_xp).__name__}: {_e_xp}",
                              flush=True)

                    # ── FAST no-change gate ──────────────────────────────────
                    # Fingerprint every source file (ingested + input folders +
                    # web-urls) via a pure os.stat walk. If it matches the last
                    # SUCCESSFUL cycle's fingerprint, NOTHING on disk changed —
                    # skip the whole project (mining + KG + closet) in ~1s instead
                    # of probing Qdrant per file/drawer. Computed AFTER the web-URL
                    # fetch (which may rewrite web-urls/ files → new fingerprint).
                    # Manual "Sync now" with no change still skips (the user gets a
                    # near-instant idle, which is the honest answer). Full-Resync
                    # uses a different path and is unaffected.
                    _prev_sync = (project.get("sync_status") or {})
                    _prev_fp = _prev_sync.get("source_fingerprint") or ""
                    _prev_state = _prev_sync.get("state") or ""
                    try:
                        _cur_fp = _project_source_fingerprint(
                            pdir, project, _weburl_folder, _xlsxprof_folder)
                    except Exception as _e_fp:
                        _cur_fp = ""  # fingerprint failed → don't skip, do full sync
                        print(f"[project-sync] fingerprint failed "
                              f"{agent_id}/{proj_name}: {type(_e_fp).__name__}: "
                              f"{_e_fp}", flush=True)
                    # Success state for a completed sync is "idle" (set far below).
                    if _cur_fp and _cur_fp == _prev_fp and _prev_state == "idle":
                        # Nothing changed since the last good sync → fast idle.
                        _finished = datetime.datetime.now(
                            datetime.timezone.utc).isoformat()
                        _row = dict(_prev_sync)
                        _row["state"] = "idle"
                        _row["last_run_started"] = _finished
                        _row["last_run_finished"] = _finished
                        _row["last_triggered_by"] = _trigger
                        _row["last_files_filed"] = 0
                        _row["last_error"] = ""
                        _row["source_fingerprint"] = _cur_fp
                        try:
                            engine.ProjectManager.update_project(
                                agent_id, proj_name,
                                {"sync_status": _row,
                                 "input_folders_last_scan": _finished})
                        except Exception:
                            pass
                        # Record a completed (no-op) run so the due-gate clock +
                        # history advance, but skip ALL phase work.
                        try:
                            _nr = _sync_log.start_run(
                                chats_db_path, project_id, triggered_by=_trigger)
                            _sync_log.finish_run(
                                chats_db_path, _nr, "idle",
                                {"final_state": "idle", "files_filed_this_cycle": 0,
                                 "total_files": _prev_sync.get("total_files", 0),
                                 "total_indexed": _prev_sync.get("total_indexed", 0),
                                 "total_triples": _prev_sync.get("total_triples", 0),
                                 "folders_seen": 0, "elapsed_s": 0.0,
                                 "skipped_unchanged": True, "errors": []})
                        except Exception:
                            pass
                        srv._project_sync_clear_live(agent_id, proj_name)
                        print(f"[project-sync] {agent_id}/{proj_name}: unchanged "
                              f"(fingerprint match) — skipped all phases", flush=True)
                        continue

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
                                # every current input folder/pdir prefix, OR it's an
                                # absolute-path source whose file no longer exists
                                # (single deleted file — web-url companion or
                                # ordinary input-folder doc — while its folder stays
                                # configured; prefix alone can't catch that since
                                # the folder is a kept prefix).
                                def _is_stale_src(_src):
                                    _src = _src or ""
                                    # (a) Outside every current input folder / pdir
                                    # prefix → the folder was removed.
                                    if not any(_src.startswith(_p) for _p in _current_folder_prefixes):
                                        return True
                                    # (b) A drawer whose source_file is an absolute
                                    # path (a real file was expected) but the file
                                    # no longer exists → the single file was deleted
                                    # while its folder stayed configured. Covers
                                    # web-urls AND ordinary input-folder files, so a
                                    # deleted file leaves no orphan drawers. Guarded
                                    # on a leading '/' so synthetic markers
                                    # (session/...#..., user/...#profile) — which are
                                    # never files — are NEVER treated as stale.
                                    if _src.startswith("/") and not os.path.exists(_src):
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
                                        # Serialize the bulk delete against concurrent
                                        # daemon upserts (the corruption trigger).
                                        with _palace_write_lock:
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
                                        with _palace_write_lock:
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
                                k = engine.IngestManager._key_from_filename(fn)
                                if k:
                                    seen_hashes.add(k)
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
                    #    Each chunk file is named <stem>__<idx>.md (stem = the
                    #    original upload filename, == the group key); we group by
                    #    that key so each upload appears as one item.
                    ingested_dir = os.path.join(pdir, "ingested")
                    if os.path.isdir(ingested_dir):
                        folders_seen += 1
                        # Discover all unique source keys in the folder so we
                        # can mark each as "syncing" before mining begins.
                        hashes: set[str] = set()
                        try:
                            for fn in os.listdir(ingested_dir):
                                k = engine.IngestManager._key_from_filename(fn)
                                if k:
                                    hashes.add(k)
                        except OSError:
                            pass
                        for h in hashes:
                            _set_item("attachment", h,
                                state="syncing",
                                last_run_started=started_at)
                        if _ensure_mempalace_yaml(ingested_dir, wing):
                            # Stamp each chunk's body with its virtual-group path
                            # (source_groups.files) BEFORE mining, so every mined
                            # drawer self-identifies its group/customer in the
                            # text. Idempotent + mtime-gated (only rewrites on a
                            # real change) so it composes with the miner's
                            # mtime-skip and doesn't churn the corpus.
                            try:
                                _apply_group_prefixes(ingested_dir, project)
                            except Exception as e:
                                print(f"[project-sync.group] {ingested_dir}: "
                                      f"{type(e).__name__}: {e}", flush=True)
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
                            try:
                                # Batched mine → steady UI progress. Push a live
                                # mining_done/total counter after each batch so
                                # the client polling /sync-status sees the bar
                                # advance instead of a frozen "syncing".
                                def _mine_prog(done, total, filed,
                                               total_all=None, unchanged=None):
                                    # mining_total = changed docs being mined;
                                    # mining_total_all = all docs on disk;
                                    # mining_unchanged = already-up-to-date docs.
                                    # The UI shows "done/total · unchanged
                                    # unverändert" so `total` no longer reads as
                                    # the whole project.
                                    _live = dict(
                                        state="syncing",
                                        mining_phase="indexing",
                                        mining_done=done,
                                        mining_total=total,
                                        mining_drawers=filed)
                                    if total_all is not None:
                                        _live["mining_total_all"] = total_all
                                    if unchanged is not None:
                                        _live["mining_unchanged"] = unchanged
                                    srv._project_sync_set_live(
                                        agent_id, proj_name, **_live)
                                    if _run_id:
                                        _sync_log.step_update(
                                            chats_db_path, _run_id, "indexing",
                                            folder=ingested_dir,
                                            files_done=done, files_total=total,
                                            drawers_created=filed)
                                ingest_filed = _mine_batched(
                                    ingested_dir, wing, "brain-project-sync",
                                    progress_cb=_mine_prog)
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
                            # Mark each uploaded source indexed. We deliberately
                            # do NOT pull a per-source drawer count here: it
                            # required a full-wing metadata fetch
                            # (_count_wing_drawers_by_prefixes) that, on a large
                            # wing (~1900 drawers), stalled the pipeline for tens
                            # of seconds BETWEEN "indexing 2/2" and KG start with
                            # no phase shown — and the number only fed a cosmetic
                            # "(N Schubladen)" badge. Dropped (the badge simply
                            # shows "Indexiert"). The KG run below knows its own
                            # chunk counts from the work it actually does.
                            for h in hashes:
                                _set_item("attachment", h,
                                    state=("error" if ingest_err else "indexed"),
                                    last_run_finished=finished_at_attach,
                                    error=ingest_err)
                            # Mark every uploaded source as processed in the
                            # cycle progress. Done as a batch since the miner
                            # call covers them all in one pass.
                            _bump_processed(len(hashes))
                            # KG extraction post-pass for each ingested
                            # attachment key. Drawers carry source_file
                            # like .../ingested/<key>__<idx>.md (legacy:
                            # ingest-<hash>-<idx>.md), so we can scope
                            # precisely per attachment.
                            #
                            # KG progress: switch the phase to "kg" + reset the
                            # cross-document base, but WITHOUT a kg_total — that
                            # denominator used to come from a second full-wing
                            # fetch (_count_wing_drawers_total) that cost about as
                            # long as the (now-parallel, ~45s) KG phase it was
                            # measuring. The UI bar degrades to a live chunk
                            # count (kg_done) with no fixed total/ETA, which is
                            # honest for a fast phase and removes the stall.
                            if not ingest_err:
                                srv._project_sync_set_live(
                                    agent_id, proj_name,
                                    state="syncing", mining_phase="kg",
                                    kg_done=0, kg_done_base=0,
                                    kg_total=0,
                                    kg_started_at=time.time())
                            for h in hashes:
                                if ingest_err:
                                    continue
                                _run_kg_for(
                                    wing=wing,
                                    source_prefix=os.path.join(
                                        ingested_dir,
                                        engine.IngestManager.chunk_filename_prefix(
                                            ingested_dir, h)),
                                    item_set_fn=_set_item,
                                    item_kind="attachment", item_id=h)
                                # Fold this document's completed drawers into the
                                # cross-document base so the next doc's per-chunk
                                # ticks continue from here (monotonic).
                                try:
                                    _ia = item_states.get(
                                        _item_key("attachment", h)) or {}
                                    _done_doc = int(_ia.get("kg_chunks_done") or 0)
                                    _cur = srv._project_sync_live_status(
                                        agent_id, proj_name)
                                    _new_base = int(_cur.get("kg_done_base") or 0) + _done_doc
                                    srv._project_sync_set_live(
                                        agent_id, proj_name,
                                        state="syncing", mining_phase="kg",
                                        kg_done_base=_new_base, kg_done=_new_base)
                                except Exception:
                                    pass
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
                            # Route through _mine_batched so web-URL .md files get
                            # the wing-scoped pre-filter too (skips the per-file
                            # file_already_mined() check + the wing-wide entity-link
                            # rebuild when nothing changed). web-urls/ is hash-gated
                            # upstream so most cycles have zero changed files here.
                            try:
                                _wu_filed = _mine_batched(
                                    _weburl_folder, wing, "brain-project-sync",
                                    respect_gitignore=False)
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

                    # 1c. Spreadsheet structure profiles (v9.264.0) — generated
                    #     into pdir/xlsx-profiles/ above (mtime-gated). Already
                    #     markdown; mine the folder like web-urls so structure
                    #     questions ("welche Datei hat Spalte X?") answer from
                    #     the wing. No KG pass — profiles are metadata, not
                    #     facts; triples would only duplicate the source doc's.
                    if _xlsxprof_folder and os.path.isdir(_xlsxprof_folder):
                        _xp_files = [fn for fn in os.listdir(_xlsxprof_folder)
                                     if fn.startswith("xlsxprofile-")
                                     and fn.endswith(".md")]
                        if _xp_files and _ensure_mempalace_yaml(
                                _xlsxprof_folder, wing):
                            folders_seen += 1
                            _xp_err = ""
                            _xp_filed = 0
                            try:
                                _xp_filed = _mine_batched(
                                    _xlsxprof_folder, wing,
                                    "brain-project-sync",
                                    respect_gitignore=False)
                            except SystemExit:
                                pass
                            except Exception as e:
                                _xp_err = f"{type(e).__name__}: {e}"
                                last_error = _xp_err
                                print(f"[project-sync] {agent_id}/{proj_name} "
                                      f"xlsx-profiles: {_xp_err}", flush=True)
                            files_filed += _xp_filed
                            _bump_processed(len(_xp_files))

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
                        # GDPR/classification review refresh — re-run the review
                        # for each file in this folder so badges stay current
                        # after a content change. UNCHANGED files are a cheap
                        # hash-compare no-op (review_file_to_db skips), so the
                        # user's prior overrules/anonymisation are reused, never
                        # re-prompted. Owner = project creator (badges are
                        # per-user; this refreshes the owner's view). Best-effort.
                        try:
                            _rev_owner = (project.get("created_by") or "").strip()
                            if _rev_owner:
                                from engine import doc_review as _dr
                                from engine.doc_convert import SUPPORTED_EXTS as _SE
                                _accept = set(_SE) | {".md", ".markdown", ".txt",
                                                      ".html", ".htm", ".csv"}
                                _rev_n = 0
                                with engine.request_context(current_user_id=_rev_owner):
                                    for _rroot, _rdirs, _rnames in os.walk(fpath):
                                        _rdirs[:] = [d for d in _rdirs
                                                     if not d.startswith(".")]
                                        for _rn in _rnames:
                                            if _rn.startswith("."):
                                                continue
                                            if os.path.splitext(_rn)[1].lower() not in _accept:
                                                continue
                                            _rp = os.path.join(_rroot, _rn)
                                            _dr.review_file_to_db(
                                                _rp, user_id=_rev_owner,
                                                source_kind="project_path",
                                                source_ref=os.path.realpath(_rp),
                                                filename=_rn)
                                            _rev_n += 1
                                            if _rev_n >= 500:
                                                break
                                        if _rev_n >= 500:
                                            break
                        except Exception as _re:
                            print(f"[project-sync] review refresh {fpath}: "
                                  f"{_re}", flush=True)
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
                        # Route through _mine_batched so input_folders get the
                        # SAME wing-scoped bulk pre-filter as ingested/ — a raw
                        # mp_miner.mine() here bypassed it, so every file paid the
                        # per-file file_already_mined() skip-check inside mine()
                        # (the folder/binary-project hotspot). respect_gitignore
                        # stays True for user dirs.
                        def _folder_prog(done, total, filed_so_far,
                                         total_all=None, unchanged=None):
                            _live = dict(
                                state="syncing",
                                current_folder=fpath, mining_phase="indexing",
                                mining_done=done, mining_total=total,
                                mining_drawers=filed_so_far)
                            if total_all is not None:
                                _live["mining_total_all"] = total_all
                            if unchanged is not None:
                                _live["mining_unchanged"] = unchanged
                            srv._project_sync_set_live(
                                agent_id, proj_name, **_live)
                        try:
                            folder_filed = _mine_batched(
                                fpath, wing, "brain-project-sync",
                                progress_cb=_folder_prog,
                                respect_gitignore=True)
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
                        # No per-folder drawer count here: like the attachment
                        # path, _count_wing_drawers_by_source was a full-wing
                        # metadata fetch feeding only a cosmetic "(N Schubladen)"
                        # badge, and it stalled the pipeline before KG on large
                        # wings. Dropped — badge shows "Indexiert".
                        _set_item("folder", fpath,
                            state=("error" if folder_err else "indexed"),
                            last_run_finished=datetime.datetime.now(
                                datetime.timezone.utc).isoformat(),
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
                        # Live phase D progress: push mining_phase="closet" +
                        # closet_done/total after each source's closet is
                        # (re)built so the project view shows the Closet-Rerank
                        # row advancing instead of sitting silent (this phase
                        # was previously invisible — the ~76%-of-cycle gap).
                        _closet_phase_t0 = time.time()
                        def _closet_prog(done, total):
                            srv._project_sync_set_live(
                                agent_id, proj_name,
                                state="syncing",
                                mining_phase="closet",
                                closet_done=done,
                                closet_total=total,
                                closet_started_at=_closet_phase_t0)
                        _closet_out = _run_closet_regen_for(
                            wing, progress_cb=_closet_prog) or {}
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
                        # Fingerprint of the source tree this cycle processed.
                        # Next cycle compares against it for the fast no-change
                        # skip. Only meaningful when the run succeeded (the gate
                        # also requires state=='idle'), but stored regardless so a
                        # later successful cycle has a baseline.
                        #
                        # RECOMPUTE at completion (not _cur_fp from iteration
                        # start): doc_convert regenerates the .brain-extracted/
                        # companion .md files DURING this sync, which moves their
                        # mtimes AFTER _cur_fp was sampled. Storing the start-of-
                        # iteration fp would mismatch the now-settled tree, so the
                        # NEXT cycle would re-do a full sync once before converging
                        # (observed on folder/binary projects: one wasted ~55s
                        # cycle after every real change). Re-stat'ing here captures
                        # the post-conversion tree so a true no-change cycle skips
                        # immediately. On a successful run only; a failed/cancelled
                        # run keeps _cur_fp so it doesn't accidentally skip next time.
                        "source_fingerprint": (
                            _project_source_fingerprint(pdir, project,
                                                        _weburl_folder,
                                                        _xlsxprof_folder)
                            if final_state == "idle" else _cur_fp),
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
                        _run_finished = True
                    srv._project_sync_clear_live(agent_id, proj_name)
                    cycle_filed += files_filed
                except Exception as _pe:
                    # Per-project isolation: one project's unhandled error must not
                    # starve every project after it for the whole cycle interval.
                    # Cheap sub-steps inside already guard themselves; this is the
                    # backstop so the loop always advances to the next project.
                    print(f"[project-sync] project error {agent_id}/{proj_name}: "
                          f"{type(_pe).__name__}: {_pe}", flush=True)
                    try:
                        srv._project_sync_clear_live(agent_id, proj_name)
                    except Exception:
                        pass
                    continue
                finally:
                    # Always close an open run row. If the body raised before the
                    # normal finish_run (or start_run succeeded but a later step
                    # threw), the run would otherwise stay 'running' forever and
                    # surface as a phantom "mining in progress". Mark it 'error'.
                    if _run_id and not _run_finished:
                        try:
                            _sync_log.finish_run(
                                chats_db_path, _run_id, "error",
                                {"final_state": "error",
                                 "errors": ["sync run did not complete "
                                            "(closed by finally guard)"]})
                        except Exception as _fe:
                            print(f"[project-sync] finally finish_run failed "
                                  f"(run={_run_id}): {type(_fe).__name__}: {_fe}",
                                  flush=True)

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


_BGTASK_GROUP_SWEEP_INTERVAL_SEC = 120  # check stalled fan-out groups every 2 min


def _bgtask_group_timeout_loop(srv):
    """Force-deliver fan-out groups stalled on a straggler past the group
    deadline (engine.background_tasks._GROUP_TIMEOUT_S). The per-task 1h timeout
    is the absolute backstop; this loop delivers a partial much sooner once a
    group's other members are done. Cheap query, runs every 2 min."""
    import engine.background_tasks as _bgt
    time.sleep(45)
    while True:
        try:
            n = _bgt.background_task_runner.sweep_group_timeouts()
            if n:
                print(f"[bgtask-group-timeout] delivered {n} stalled group(s) as partial", flush=True)
        except Exception as e:
            print(f"[bgtask-group-timeout] cycle error: {type(e).__name__}: {e}", flush=True)
        time.sleep(_BGTASK_GROUP_SWEEP_INTERVAL_SEC)


# ── Code-index sync (codebase-memory) ────────────────────────────────────────
# Mirrors the project-sync daemon's shape: polls every code-mode project's
# working_dir via an mtime/size fingerprint and re-indexes (cbm, incremental,
# ~2s) when it changes — debounced by the poll interval. Repos may not be git,
# so we DON'T rely on cbm's git-watcher. Manual "Refresh" requests (Phase B UI)
# queue in _code_index_requests and are drained at the top of each cycle.
_CODE_INDEX_POLL_SEC = 20
_code_index_fp: dict[tuple[str, str], str] = {}     # (agent,proj) -> last fingerprint
_code_index_live: dict[tuple[str, str], dict] = {}  # (agent,proj) -> {state, indexed_at, ...}
_code_index_requests: set[tuple[str, str]] = set()  # manual refresh / force
_code_index_force: set[tuple[str, str]] = set()     # clean+rebuild (drop cache first)
_code_index_history: dict[tuple[str, str], list] = {}  # (agent,proj) -> [run,…] newest-first
_CODE_INDEX_HISTORY_MAX = 20
_code_index_lock = threading.Lock()


def _code_index_record_run(agent_id: str, project_name: str, run: dict):
    """Append a finished index run to the per-project ring buffer (newest first)."""
    key = (agent_id, project_name)
    with _code_index_lock:
        hist = _code_index_history.setdefault(key, [])
        hist.insert(0, run)
        del hist[_CODE_INDEX_HISTORY_MAX:]


def _code_index_runs(agent_id: str, project_name: str) -> list:
    with _code_index_lock:
        return list(_code_index_history.get((agent_id, project_name), []))


_CODE_INDEX_SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv",
                         ".cbm-cache", ".brain-extracted", ".trash", "dist", "build"}


def _code_dir_fingerprint(root: str) -> str:
    """mtime/size fingerprint of a working dir's source tree (skips heavy/derived
    dirs). Same approach as the project source fingerprint, scoped to one dir."""
    h = hashlib.sha256()
    n = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in _CODE_INDEX_SKIP_DIRS
                             and not d.startswith("."))
        for fn in sorted(filenames):
            fp = os.path.join(dirpath, fn)
            try:
                st = os.stat(fp)
                h.update(f"{fp}|{st.st_mtime_ns}|{st.st_size}\n".encode("utf-8", "replace"))
                n += 1
            except OSError:
                h.update(f"{fp}|?\n".encode())
    h.update(f"#count={n}".encode())
    return h.hexdigest()


def _code_index_request(agent_id: str, project_name: str, *, force: bool = False):
    """Queue a manual re-index (Refresh) or clean rebuild (force) for a project."""
    with _code_index_lock:
        _code_index_requests.add((agent_id, project_name))
        if force:
            _code_index_force.add((agent_id, project_name))


def _code_index_status(agent_id: str, project_name: str) -> dict | None:
    with _code_index_lock:
        r = _code_index_live.get((agent_id, project_name))
        return dict(r) if r else None


def _iter_code_mode_projects():
    """Yield (agent_id, project_name, working_dir, project_dir) for every
    code-mode project with a real working dir."""
    try:
        agents = engine.list_agents()
    except Exception:
        agents = ["main"]
    for agent_id in agents:
        try:
            projs = engine.ProjectManager.list_projects(agent_id)
        except Exception:
            continue
        for p in projs:
            name = p.get("name") or ""
            try:
                cfg = engine.ProjectManager.get_project(agent_id, name) or {}
            except Exception:
                continue
            if not cfg.get("code_mode"):
                continue
            wd = (cfg.get("working_dir") or "").strip()
            if not wd or not os.path.isdir(wd):
                continue
            pdir = cfg.get("dir") or engine.ProjectManager._project_dir(agent_id, name)
            yield agent_id, name, wd, pdir


def _code_index_sync_loop(srv):
    """Keep each code-mode project's codebase-memory index fresh."""
    cm = (engine._server_config() or {}).get("codebase_memory", {}) or {}
    if not cm.get("enabled"):
        print("[code-index] disabled (codebase_memory.enabled = false)", flush=True)
        return
    if not (cm.get("bin") and os.path.exists(cm["bin"])):
        print("[code-index] disabled (codebase_memory.bin missing)", flush=True)
        return
    import shutil
    time.sleep(30)
    while True:
        try:
            with _code_index_lock:
                manual = set(_code_index_requests)
                forced = set(_code_index_force)
                _code_index_requests.clear()
                _code_index_force.clear()
            for agent_id, name, wd, pdir in _iter_code_mode_projects():
                key = (agent_id, name)
                cache = os.path.join(pdir, ".cbm-cache")
                try:
                    fp = _code_dir_fingerprint(wd)
                except Exception:
                    continue
                changed = _code_index_fp.get(key) != fp
                is_forced = key in forced
                if is_forced:
                    # clean + start fresh: drop the tenant cache before reindex
                    try:
                        shutil.rmtree(cache, ignore_errors=True)
                    except Exception:
                        pass
                if not (changed or key in manual or is_forced):
                    continue
                trigger = ("full_rebuild" if is_forced
                           else "manual" if key in manual else "auto")
                _t0 = time.time()
                with _code_index_lock:
                    _code_index_live[key] = {"state": "indexing", "started_at": _t0}
                try:
                    res = engine.cbm_index_repository(wd, cache_dir=cache) or {}
                    ok = not res.get("error")
                    _code_index_fp[key] = fp
                    _done = time.time()
                    with _code_index_lock:
                        _code_index_live[key] = {
                            "state": "indexed" if ok else "error",
                            "indexed_at": _done,
                            "nodes": res.get("nodes"), "edges": res.get("edges"),
                            "error": res.get("error"),
                        }
                    _code_index_record_run(agent_id, name, {
                        "state": "indexed" if ok else "error",
                        "finished_at": _done, "duration": round(_done - _t0, 1),
                        "trigger": trigger, "nodes": res.get("nodes"),
                        "edges": res.get("edges"), "error": res.get("error"),
                    })
                    if ok:
                        print(f"[code-index] {agent_id}/{name}: reindexed "
                              f"nodes={res.get('nodes')} edges={res.get('edges')}", flush=True)
                    else:
                        print(f"[code-index] {agent_id}/{name}: {res.get('error')}", flush=True)
                except Exception as e:
                    _done = time.time()
                    with _code_index_lock:
                        _code_index_live[key] = {"state": "error", "error": str(e),
                                                 "indexed_at": _done}
                    _code_index_record_run(agent_id, name, {
                        "state": "error", "finished_at": _done,
                        "duration": round(_done - _t0, 1), "trigger": trigger,
                        "error": f"{type(e).__name__}: {e}"})
                    print(f"[code-index] {agent_id}/{name} failed: "
                          f"{type(e).__name__}: {e}", flush=True)
        except Exception as e:
            print(f"[code-index] cycle error: {type(e).__name__}: {e}", flush=True)
        time.sleep(_CODE_INDEX_POLL_SEC)


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


_CHAT_CLEANUP_DEFAULT_INTERVAL_SEC = 3600


def _chat_cleanup_loop(srv):
    """Auto-archive idle private chats, then auto-delete long-archived ones.

    Two independent stages, each gated by a config day-count (0 = stage off):
      - archive: a chat idle >= archive_after_days that is purely private, not
        memorized (no session/<id> wiki page, save_to_memory=0), and not
        referenced anywhere (favourite / unfinished bg task / in-flight turn /
        workflow) → status='archived' (stamps archived_at). Conservative: any
        exclusion leaves it active. See ChatDB.list_auto_archivable.
      - delete: a chat archived >= delete_after_days ago (by archived_at) →
        fully deleted, INCLUDING its wiki page + MemPalace drawer (the cascade
        lives in ChatDB.delete_session via wiki_store.delete_page_for_session).

    Config is read live each cycle from server_config['chat_cleanup'] so GUI
    edits take effect without a restart. The whole feature no-ops when the block
    is absent or enabled=false."""
    time.sleep(30)  # let boot settle before the first sweep
    while True:
        slept = _CHAT_CLEANUP_DEFAULT_INTERVAL_SEC
        try:
            cfg = (engine._server_config().get("chat_cleanup") or {})
            slept = max(300, int(cfg.get("run_interval_seconds",
                                         _CHAT_CLEANUP_DEFAULT_INTERVAL_SEC)))
            if not cfg.get("enabled", False):
                time.sleep(slept)
                continue
            now = time.time()
            archive_days = int(cfg.get("archive_after_days", 0) or 0)
            delete_days = int(cfg.get("delete_after_days", 0) or 0)

            # Stage 1 — archive idle, private, unreferenced chats.
            if archive_days > 0:
                cutoff = now - archive_days * 86400
                ids = ChatDB.list_auto_archivable(cutoff) or []
                n = 0
                for sid in ids:
                    try:
                        ChatDB.archive_session(sid)
                        n += 1
                    except Exception as e:
                        print(f"[chat-cleanup] archive {sid[:8]} failed: "
                              f"{type(e).__name__}: {e}", flush=True)
                if n:
                    print(f"[chat-cleanup] archived {n} idle chat(s) "
                          f"(>{archive_days}d)", flush=True)

            # Stage 2 — delete chats archived past the delete window.
            if delete_days > 0:
                cutoff = now - delete_days * 86400
                ids = ChatDB.list_auto_deletable(cutoff) or []
                n = 0
                for sid in ids:
                    try:
                        srv.sessions.delete(sid)  # → ChatDB.delete_session (+ wiki + mempalace)
                        n += 1
                    except Exception as e:
                        print(f"[chat-cleanup] delete {sid[:8]} failed: "
                              f"{type(e).__name__}: {e}", flush=True)
                if n:
                    print(f"[chat-cleanup] deleted {n} chat(s) archived "
                          f">{delete_days}d (incl. wikis)", flush=True)
        except Exception as e:
            print(f"[chat-cleanup] cycle error: {type(e).__name__}: {e}", flush=True)
        time.sleep(slept)


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
                # Prefix-keyed decision: is the prefix this prime would create
                # already warm? For a full prime that's the model's bare full
                # prefix; for a minimal prime the subset rule means ANY warm full
                # prefix on the model already covers it (weights loaded). This
                # replaces the old mode-string compare that ping-ponged whenever
                # session warmup (full) and the keeper (minimal) disagreed.
                want_minimal = (desired_mode == "minimal")
                if want_minimal:
                    target_pid = engine.MINIMAL_PREFIX_ID
                else:
                    target_pid = engine._bare_full_prefix_id(mid, "main")
                    if target_pid is None:
                        # Can't build the prefix (transient) — skip this cycle.
                        continue
                if engine.prefix_is_warm(mid, target_pid, minimal=want_minimal):
                    continue
                st = engine.get_warmup_state(mid, target_pid if not want_minimal else None)
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
                # Build the pool once the model is warm under ANY prefix. A
                # pooled slot is just a pre-created bare main session (try_build
                # fires NO prefill) that lets "new chat" skip the cold session
                # setup; it reuses whatever the GPU already has resident. For a
                # full-mode model that's the bare full KV prefix; for a
                # minimal-mode model it's the loaded weights (no KV prefix, but
                # still warmer than cold). Gating on the bare-FULL prefix here
                # was wrong: a minimal-mode model (e.g. gemma-4-12B) never warms
                # a full prefix, so the pool stayed empty (0/N in the status bar)
                # even though the model was warm (green dot). Use the model-level
                # best state instead.
                if engine.get_warmup_state(mid).get("state") != "warm":
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

