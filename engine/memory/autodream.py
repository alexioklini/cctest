# Extracted from claude_cli.py — memory summary, autodream, relationship discovery
# Cross-module deps resolved via claude_cli namespace:
#   _QMD_IGNORE_FILES, _scheduler, _sched_conn, _delegate_api_key, _models_config,
#   AGENTS_DIR, AgentConfig, list_agents, MemoryStore, _extract_json_from_llm,
#   _parse_frontmatter, _yaml_escape, _qmd_debounced_embed, _find_candidate_pairs,
#   _collect_agent_memories, _run_delegate, resolve_provider_for_model,
#   send_message_with_fallback, gdpr_pick_model_for_background, GDPRBlockedError

import os
import re
import sys
import json
import time
import logging
import sqlite3
import datetime
import threading
import subprocess


# ─── Memory Summary ────────────────────────────────────────────────
# Automatic periodic synthesis of chat history and scheduled task results.
# Configured per-agent in agent.json: { "memory_summary": { "enabled": true, "frequency": "every 24h", "start_time": "03:00" } }

MEMORY_SUMMARY_DEFAULTS = {
    "enabled": True,
    "paused": False,
    "frequency": "every 24h",
    "start_time": "03:00",
}


def _get_memory_summary_config(agent_id: str) -> dict:
    """Read memory_summary settings from agent.json, merging with defaults."""
    cfg = AgentConfig(agent_id).config
    ms_cfg = cfg.get("memory_summary", {})
    result = dict(MEMORY_SUMMARY_DEFAULTS)
    if isinstance(ms_cfg, dict):
        for k in ("enabled", "paused", "frequency", "start_time", "model", "model_fallback"):
            if k in ms_cfg:
                result[k] = ms_cfg[k]
    elif isinstance(ms_cfg, bool):
        result["enabled"] = ms_cfg
    return result


def _cleanup_orphaned_chat_index_files(agent_id: str):
    """Remove chats-indexed files for sessions that no longer exist in the DB."""
    chats_dir = os.path.join(AGENTS_DIR, agent_id, "chats-indexed")
    if not os.path.isdir(chats_dir):
        return
    chat_db_path = os.path.join(AGENTS_DIR, "main", "chats.db")
    if not os.path.exists(chat_db_path):
        return
    try:
        conn = sqlite3.connect(chat_db_path, timeout=5)
        existing_sids = {row[0] for row in conn.execute("SELECT id FROM sessions")}
        conn.close()
    except Exception:
        return
    removed = 0
    for fname in os.listdir(chats_dir):
        if not fname.startswith("chat-") or not fname.endswith(".md"):
            continue
        sid = fname.split("-", 1)[1].rsplit("-", 1)[0]
        if sid not in existing_sids:
            try:
                os.remove(os.path.join(chats_dir, fname))
                removed += 1
            except OSError:
                pass
    if removed:
        logging.info("Chat index cleanup for %s: removed %d orphaned files", agent_id, removed)
        _qmd_debounced_embed(agent_id)


def _memory_summary_schedule_name(agent_id: str) -> str:
    return f"_memory_summary_{agent_id}"


def _memory_summary_schedule_str(cfg: dict) -> str:
    """Convert memory_summary config to a scheduler schedule string.
    e.g. 'daily 03:00' or 'every 24h' depending on whether start_time is set."""
    freq = cfg.get("frequency", "every 24h").strip().lower()
    start_time = cfg.get("start_time", "").strip()
    # If frequency is a simple 'every Xh/Xd' and start_time is provided, use 'daily HH:MM'
    if start_time and re.match(r'^\d{1,2}:\d{2}$', start_time):
        # For 24h frequency, use daily at start_time
        m = re.match(r'every\s+(\d+)\s*(h|hour|d|day)', freq)
        if m:
            val, unit = int(m.group(1)), m.group(2)[0]
            if (unit == 'h' and val == 24) or (unit == 'd' and val == 1):
                return f"daily {start_time}"
            elif unit == 'h' and val < 24:
                return freq  # sub-daily interval, ignore start_time
            elif unit == 'd' and val > 1:
                return freq  # multi-day, keep as interval
    return freq


def _gather_recent_chat_history(agent_id: str, hours: int = 48, max_sessions: int = 10,
                                 max_messages_per_session: int = 20) -> str:
    """Read recent chat sessions and messages for an agent directly from chats.db.
    Returns a formatted text digest suitable for inclusion in a prompt."""
    # Chat DB is always in main agent dir (shared across all agents)
    chat_db_path = os.path.join(AGENTS_DIR, "main", "chats.db")
    if not os.path.exists(chat_db_path):
        return ""
    conn = None
    try:
        conn = sqlite3.connect(chat_db_path, timeout=5)
        conn.row_factory = sqlite3.Row
        cutoff = time.time() - (hours * 3600)
        sessions = conn.execute(
            "SELECT id, title, model, status, created_at, last_active FROM sessions "
            "WHERE agent_id = ? AND last_active > ? "
            "AND status IN ('active', 'archived') "
            "ORDER BY last_active DESC LIMIT ?",
            (agent_id, cutoff, max_sessions)
        ).fetchall()
        # Note: 'incognito' status sessions are excluded by the IN clause above
        if not sessions:
            conn.close()
            return ""
        lines = []
        for sess in sessions:
            ts = datetime.datetime.fromtimestamp(sess["last_active"]).strftime("%Y-%m-%d %H:%M") if sess["last_active"] else "?"
            title = sess["title"] or "(untitled)"
            status_tag = " [archived]" if sess["status"] == "archived" else ""
            lines.append(f"\n### Session: {title} ({ts}){status_tag}")
            msgs = conn.execute(
                "SELECT role, content FROM messages WHERE session_id = ? ORDER BY id",
                (sess["id"],)
            ).fetchall()
            # Take first + last messages to stay within budget
            msg_list = list(msgs)
            if len(msg_list) > max_messages_per_session:
                selected = msg_list[:max_messages_per_session // 2] + msg_list[-(max_messages_per_session // 2):]
                lines.append(f"  ({len(msg_list)} messages total, showing first and last {max_messages_per_session // 2})")
            else:
                selected = msg_list
            for msg in selected:
                role = msg["role"].upper()
                content = msg["content"]
                # Parse JSON content (tool calls etc)
                try:
                    parsed = json.loads(content)
                    if isinstance(parsed, list):
                        # Multi-part message (text + tool_use blocks)
                        text_parts = [p.get("text", "") for p in parsed if isinstance(p, dict) and p.get("type") == "text"]
                        content = " ".join(text_parts).strip() or "(tool calls)"
                    elif isinstance(parsed, dict):
                        content = parsed.get("text", str(parsed))
                    elif isinstance(parsed, str):
                        content = parsed
                except (json.JSONDecodeError, TypeError):
                    pass
                # Truncate long messages
                if isinstance(content, str) and len(content) > 400:
                    content = content[:400] + "..."
                if content and content != "(tool calls)":
                    lines.append(f"  [{role}] {content}")
        return "\n".join(lines)
    except Exception as e:
        return f"(Error reading chat history: {e})"
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


def _gather_recent_schedule_history(agent_id: str, hours: int = 48, limit: int = 15) -> str:
    """Read recent scheduled task execution results for an agent from scheduler.db.
    Returns a formatted text digest."""
    if not _scheduler:
        return ""
    try:
        with _sched_conn() as conn:
            conn.row_factory = sqlite3.Row
            cutoff = (datetime.datetime.now() - datetime.timedelta(hours=hours)).isoformat()
            rows = conn.execute(
                "SELECT schedule_name, task, status, result, started_at, finished_at "
                "FROM schedule_history WHERE agent = ? AND finished_at > ? "
                "AND schedule_name NOT LIKE '_memory_summary_%' "
                "ORDER BY finished_at DESC LIMIT ?",
                (agent_id, cutoff, limit)
            ).fetchall()
        if not rows:
            return ""
        lines = []
        for r in rows:
            ts = r["finished_at"][:16] if r["finished_at"] else "?"
            status = r["status"] or "unknown"
            name = r["schedule_name"] or "(unnamed)"
            lines.append(f"\n### Task: {name} [{status}] ({ts})")
            lines.append(f"  Prompt: {(r['task'] or '')[:200]}")
            result = r["result"] or ""
            if len(result) > 1000:
                result = result[:1000] + "..."
            if result:
                lines.append(f"  Result: {result}")
        return "\n".join(lines)
    except Exception as e:
        return f"(Error reading schedule history: {e})"


def _build_memory_summary_prompt(agent_id: str) -> str:
    """Build the task prompt for the memory summary scheduled task.
    Gathers real chat and schedule history and injects it into the prompt."""
    now = datetime.datetime.now()
    # Determine lookback based on frequency config
    ms_cfg = _get_memory_summary_config(agent_id)
    freq = ms_cfg.get("frequency", "every 24h")
    # Parse hours from frequency for lookback window (double it for overlap)
    lookback_hours = 48  # default
    m = re.match(r'every\s+(\d+)\s*(h|hour|d|day)', freq.lower())
    if m:
        val, unit = int(m.group(1)), m.group(2)[0]
        if unit == 'h':
            lookback_hours = val * 2
        elif unit == 'd':
            lookback_hours = val * 48

    chat_digest = _gather_recent_chat_history(agent_id, hours=lookback_hours)
    schedule_digest = _gather_recent_schedule_history(agent_id, hours=lookback_hours)

    # Build the data section
    data_section = ""
    if chat_digest:
        data_section += f"\n## Recent Conversations (last {lookback_hours}h)\n{chat_digest}\n"
    else:
        data_section += "\n## Recent Conversations\nNo conversations found in this period.\n"

    if schedule_digest:
        data_section += f"\n## Recent Scheduled Task Results (last {lookback_hours}h)\n{schedule_digest}\n"
    else:
        data_section += "\n## Recent Scheduled Task Results\nNo scheduled task executions found in this period.\n"

    return f"""Perform a Memory Summary update for agent '{agent_id}'.

Your job is to create or update a structured synthesis of recent activity. Below is the actual data from your recent conversations and scheduled task executions.

{data_section}

---

Now do the following:

1. Use memory_recall with query "Memory Summary" to find any existing summary.

2. Analyze the conversation and task data above, then write or update the synthesis using the following structured sections. Keep each section concise — omit a section if there is nothing relevant for it.

   **## User Profile & Context**
   Who the user is, their role, responsibilities, and domain knowledge. What kind of collaboration they prefer.

   **## Communication & Working Style**
   Communication preferences, response style, level of detail they expect, what to avoid.

   **## Technical Preferences**
   Coding style, preferred languages/tools/frameworks, conventions, architecture decisions.

   **## Active Projects & Ongoing Work**
   Current projects, their status, key decisions made, open questions, deadlines.

   **## Task Execution Insights**
   Patterns from scheduled task outcomes — what works, what fails, recurring issues.

   **## Key Decisions & Context**
   Important decisions, user feedback, and context that should inform future interactions.

3. Store the updated synthesis using memory_store with:
   - name: "Memory Summary"
   - type: "general"
   - description: "Auto-generated synthesis of recent conversations and task executions, updated periodically"
   - content: The structured synthesis (300-800 words)

Focus on actionable insights, not a chronological log. If an existing summary exists, integrate new information — preserve important older context while adding recent developments. Remove information from conversations that no longer appear in the data above (they may have been deleted by the user). Drop stale details that are no longer relevant.

Current date and time: {now.strftime('%Y-%m-%d %H:%M')}"""


def ensure_memory_summary_schedules():
    """MemPalace migration: disabled. Memory summary pipeline replaced by mempalace MCP."""
    return
    # --- legacy implementation kept for reference ---
    if not _scheduler:
        return
    agents = list_agents()
    existing = {s["name"]: s for s in _scheduler.list_all()}

    # Remove orphaned schedules for agents that no longer exist
    for sched_name, sched in existing.items():
        if sched_name.startswith("_memory_summary_"):
            orphan_agent = sched_name[len("_memory_summary_"):]
            if orphan_agent not in agents:
                _scheduler.remove(sched_name)

    for agent_id in agents:
        sched_name = _memory_summary_schedule_name(agent_id)
        ms_cfg = _get_memory_summary_config(agent_id)

        if ms_cfg["enabled"] and not ms_cfg.get("paused"):
            schedule_str = _memory_summary_schedule_str(ms_cfg)
            task_prompt = _build_memory_summary_prompt(agent_id)
            # Use Crow-9B for memory summary (local, free); fallback to Sonnet
            primary = ms_cfg.get("model") or "Crow-9B-HERETIC-4.6-MLX-8bit"
            fallback = ms_cfg.get("model_fallback") or "claude-sonnet-4-6"
            summary_model, _ = _resolve_model_with_fallback(primary, fallback, "claude-sonnet-4-6")

            if sched_name in existing:
                old = existing[sched_name]
                if old["schedule"] != schedule_str or old.get("enabled") != 1 or old.get("model") != summary_model:
                    _scheduler.remove(sched_name)
                    _scheduler.add(sched_name, task_prompt, schedule_str,
                                   agent=agent_id, model=summary_model, timeout=600)
            else:
                _scheduler.add(sched_name, task_prompt, schedule_str,
                               agent=agent_id, model=summary_model, timeout=600)
        else:
            # Disabled or paused — remove schedule if exists
            if sched_name in existing:
                _scheduler.remove(sched_name)


def get_memory_summary(agent_id: str) -> str | None:
    """Load the latest memory summary for an agent, if it exists.
    Returns None if memory summary is paused."""
    # Check if paused
    ms_cfg = _get_memory_summary_config(agent_id)
    if ms_cfg.get("paused"):
        return None
    ms = MemoryStore(agent_id)
    # Try to find the memory summary file
    for fname in os.listdir(ms.dir):
        if not fname.endswith(".md") or fname in _QMD_IGNORE_FILES:
            continue
        fpath = os.path.join(ms.dir, fname)
        try:
            with open(fpath, "r") as f:
                raw = f.read()
            fm, body = _parse_frontmatter(raw)
            if fm.get("name") == "Memory Summary":
                return body.strip()
        except (UnicodeDecodeError, OSError) as e:
            logging.debug(f"Error reading memory file {fname}: {e}")
            continue
        except Exception:
            continue
    return None


def reset_memory_summary(agent_id: str) -> dict:
    """Delete the memory summary file for an agent (Gap 5: reset)."""
    ms = MemoryStore(agent_id)
    deleted = []
    for fname in os.listdir(ms.dir):
        if not fname.endswith(".md") or fname in _QMD_IGNORE_FILES:
            continue
        fpath = os.path.join(ms.dir, fname)
        try:
            with open(fpath, "r") as f:
                raw = f.read()
            fm, _ = _parse_frontmatter(raw)
            if fm.get("name") == "Memory Summary":
                os.remove(fpath)
                deleted.append(fname)
        except Exception:
            continue
    if deleted:
        _qmd_debounced_embed(agent_id)
    return {"agent": agent_id, "deleted": deleted, "status": "reset"}


def trigger_memory_summary_refresh(agent_id: str):
    """Trigger an immediate memory summary refresh for an agent.
    Runs directly in a background thread instead of waiting for the scheduler loop."""
    if not _scheduler:
        return
    ms_cfg = _get_memory_summary_config(agent_id)
    if not ms_cfg.get("enabled") or ms_cfg.get("paused"):
        return
    sched_name = _memory_summary_schedule_name(agent_id)
    # Check if the schedule exists
    existing = {s["name"]: s for s in _scheduler.list_all()}
    if sched_name not in existing:
        return
    # Execute immediately in a background thread instead of relying on scheduler loop
    task_row = existing[sched_name]
    def _run():
        try:
            _scheduler._execute_scheduled(task_row)
        except Exception as e:
            logging.warning(f"Memory summary refresh failed for {agent_id}: {e}")
    threading.Thread(target=_run, daemon=True, name=f"memory_summary_refresh_{agent_id}").start()


# ─── Relationship Discovery System ────────────────────────────────────

# --- Mechanism 2: Entity Extraction + Auto-linking ---

# Entity extraction regexes — tuned to reduce false positives
# Require at least 2 capitalized words with 3+ chars each (filters "The Quick", "In This")
_RE_CAPITALIZED = re.compile(r'\b[A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,})+\b')
_RE_MENTIONS = re.compile(r'@\w+')
_RE_URLS = re.compile(r'https?://[^\s<>"\']+')
_RE_FILEPATHS = re.compile(r'(?:/[\w.-]+){2,}\.\w+')  # require at least 2 path segments
_RE_HASHTAGS = re.compile(r'#\w+')
# Common false positive phrases to filter out
_ENTITY_STOP_PHRASES = frozenset({
    "The Following", "In This", "For Example", "As Well", "In Order",
    "This Case", "Each Time", "At Least", "Make Sure", "Right Now",
    "Based Upon", "Other Than",
})


def _extract_entities(text: str) -> set[str]:
    """Extract entities from text using fast regex heuristics (no LLM).
    Returns set of entity strings."""
    entities = set()
    for m in _RE_CAPITALIZED.finditer(text):
        phrase = m.group()
        if phrase not in _ENTITY_STOP_PHRASES:
            entities.add(phrase)
    for m in _RE_MENTIONS.finditer(text):
        entities.add(m.group())
    for m in _RE_URLS.finditer(text):
        entities.add(m.group().rstrip('.,;)'))
    for m in _RE_FILEPATHS.finditer(text):
        entities.add(m.group())
    for m in _RE_HASHTAGS.finditer(text):
        entities.add(m.group())
    return entities


# Entity index: maps entity string -> set of filenames mentioning it
# Keyed per agent: _entity_indices[agent_id] = {entity: {filename, ...}}
_entity_indices: dict[str, dict[str, set[str]]] = {}
_entity_index_lock = threading.Lock()
_entity_index_initialized: set[str] = set()


def _rebuild_entity_index(agent_id: str):
    """Full rescan of memory files to build entity index for an agent."""
    agent_dir = os.path.join(AGENTS_DIR, agent_id)
    if not os.path.isdir(agent_dir):
        return
    index: dict[str, set[str]] = {}
    # Scan top-level agent dir
    for fname in os.listdir(agent_dir):
        if not fname.endswith(".md") or fname in _QMD_IGNORE_FILES:
            continue
        # Skip ingested chunks — only agent-created memories
        if fname.startswith("ingest-"):
            continue
        fpath = os.path.join(agent_dir, fname)
        try:
            with open(fpath, "r") as f:
                raw = f.read()
            _, body = _parse_frontmatter(raw)
            entities = _extract_entities(body)
            for ent in entities:
                index.setdefault(ent, set()).add(fname)
        except Exception:
            continue
    # Also scan chats-indexed/ subdirectory for chat transcript entities
    chats_dir = os.path.join(agent_dir, "chats-indexed")
    if os.path.isdir(chats_dir):
        for fname in os.listdir(chats_dir):
            if not fname.endswith(".md"):
                continue
            fpath = os.path.join(chats_dir, fname)
            try:
                with open(fpath, "r") as f:
                    raw = f.read()
                _, body = _parse_frontmatter(raw)
                entities = _extract_entities(body)
                for ent in entities:
                    index.setdefault(ent, set()).add(fname)
            except Exception:
                continue
    with _entity_index_lock:
        _entity_indices[agent_id] = index
        _entity_index_initialized.add(agent_id)


def _ensure_entity_index(agent_id: str):
    """Lazy-initialize entity index on first access. Thread-safe with double-checked locking."""
    # Fast path: already initialized
    if agent_id in _entity_index_initialized:
        return
    # Slow path: acquire lock, check again, rebuild if needed
    with _entity_index_lock:
        if agent_id in _entity_index_initialized:
            return
        # Mark as initialized BEFORE rebuild (under lock) to prevent duplicate work.
        # _rebuild_entity_index stores the result under this same lock.
        _entity_index_initialized.add(agent_id)
    # Rebuild outside lock (I/O heavy) — only one thread gets here per agent_id
    _rebuild_entity_index(agent_id)


def _update_entity_index(agent_id: str, filename: str, entities: set[str]):
    """Incrementally update entity index when a memory is stored."""
    _ensure_entity_index(agent_id)
    with _entity_index_lock:
        idx = _entity_indices.get(agent_id, {})
        # Remove old entries for this filename
        for ent in list(idx.keys()):
            idx[ent].discard(filename)
            if not idx[ent]:
                del idx[ent]
        # Add new entries
        for ent in entities:
            idx.setdefault(ent, set()).add(filename)
        _entity_indices[agent_id] = idx


def _find_entity_matches(agent_id: str, filename: str, entities: set[str]) -> list[str]:
    """Find other files sharing entities with the given file. Returns list of filenames."""
    _ensure_entity_index(agent_id)
    matches = set()
    with _entity_index_lock:
        idx = _entity_indices.get(agent_id, {})
        for ent in entities:
            for other_file in idx.get(ent, set()):
                if other_file != filename:
                    matches.add(other_file)
    return list(matches)


def _add_related_to_file(file_path: str, rel_file: str, rel_type: str) -> bool:
    """Add a related entry to a memory file's frontmatter if not already present.
    Returns True if file was modified."""
    try:
        with open(file_path, "r") as f:
            raw = f.read()
    except Exception:
        return False

    # Check if relationship already exists
    existing_rels = re.findall(r'file:\s*(\S+\.md)', raw)
    if rel_file in existing_rels:
        return False

    # Find the end of frontmatter to insert before the closing ---
    fm_match = re.match(r'^(---\s*\n)(.*?)(\n---\s*\n)(.*)$', raw, re.DOTALL)
    if not fm_match:
        return False

    opener, fm_text, closer, body = fm_match.groups()

    # Check if related: section already exists
    new_entry = f"  - file: {rel_file}\n    type: {rel_type}"
    if "related:" in fm_text:
        # Append to existing related section
        fm_text = fm_text.rstrip() + "\n" + new_entry
    else:
        # Add new related section
        fm_text = fm_text.rstrip() + "\nrelated:\n" + new_entry

    new_raw = opener + fm_text + closer + body
    try:
        with open(file_path, "w") as f:
            f.write(new_raw)
        return True
    except Exception:
        return False


# --- Mechanism 3: Co-recall Linking ---

# Tracks co-occurrence of files recalled together
# Key: frozenset({file1, file2}), Value: count
_recall_cooccurrence: dict[frozenset, int] = {}
_recall_cooccurrence_lock = threading.Lock()
_CO_RECALL_THRESHOLD = 3  # Auto-link after this many co-recalls


def _record_recall_cooccurrence(result_files: list[str], agent_id: str, base_dir: str):
    """Record which files appeared together in a recall result.
    When threshold is reached, auto-add co_recalled relationship."""
    if len(result_files) < 2:
        return
    # Only consider first 10 results to limit combinatorial explosion
    files = result_files[:10]
    pairs_to_link = []
    with _recall_cooccurrence_lock:
        # Cap dict size to prevent unbounded memory growth
        if len(_recall_cooccurrence) > 50_000:
            _recall_cooccurrence.clear()
        for i in range(len(files)):
            for j in range(i + 1, len(files)):
                pair = frozenset({files[i], files[j]})
                _recall_cooccurrence[pair] = _recall_cooccurrence.get(pair, 0) + 1
                if _recall_cooccurrence[pair] == _CO_RECALL_THRESHOLD:
                    pairs_to_link.append((files[i], files[j]))

    # Auto-link pairs that reached threshold (outside lock)
    for f1, f2 in pairs_to_link:
        f1_path = os.path.join(base_dir, f1) if not os.path.isabs(f1) else f1
        f2_path = os.path.join(base_dir, f2) if not os.path.isabs(f2) else f2
        modified = False
        if os.path.exists(f1_path) and os.path.exists(f2_path):
            f1_name = os.path.basename(f1)
            f2_name = os.path.basename(f2)
            if _add_related_to_file(f1_path, f2_name, "co_recalled"):
                modified = True
            if _add_related_to_file(f2_path, f1_name, "co_recalled"):
                modified = True
        if modified:
            _qmd_debounced_embed(agent_id)


# --- Mechanism 1: LLM-based Relationship Discovery ---

RELATIONSHIP_DISCOVERY_DEFAULTS = {
    "enabled": True,
    "frequency": "every 24h",
    "start_time": "04:15",  # 15 min after memory summary default (03:00)
}


def _get_relationship_discovery_config(agent_id: str) -> dict:
    """Read relationship_discovery settings from agent.json, merging with defaults."""
    cfg = AgentConfig(agent_id).config
    rd_cfg = cfg.get("relationship_discovery", {})
    result = dict(RELATIONSHIP_DISCOVERY_DEFAULTS)
    if isinstance(rd_cfg, dict):
        for k in ("enabled", "frequency", "start_time", "model", "model_fallback"):
            if k in rd_cfg:
                result[k] = rd_cfg[k]
    elif isinstance(rd_cfg, bool):
        result["enabled"] = rd_cfg
    return result


AUTODREAM_DEFAULTS = {
    "enabled": True,
    "model": None,  # None = auto (cheapest/Haiku), or explicit model ID
    "stale_threshold_days": 30,
    "dedup_similarity_threshold": 0.85,
    "max_dedup_merges": 10,
    "max_conflict_checks": 30,
    "report_retention": 3,
}


def _get_autodream_config(agent_id: str) -> dict:
    """Read autodream settings from agent.json, merging with defaults."""
    cfg = AgentConfig(agent_id).config
    ad_cfg = cfg.get("autodream", {})
    result = dict(AUTODREAM_DEFAULTS)
    if isinstance(ad_cfg, dict):
        for k in list(AUTODREAM_DEFAULTS) + ["model_fallback"]:
            if k in ad_cfg:
                result[k] = ad_cfg[k]
    elif isinstance(ad_cfg, bool):
        result["enabled"] = ad_cfg
    return result


def _get_auto_memory_config(agent_id: str) -> dict:
    """Read auto_memory settings from agent.json, merging with defaults."""
    cfg = AgentConfig(agent_id).config
    am = cfg.get("auto_memory", {})
    return {
        "enabled": am.get("enabled", True),
        "min_message_length": am.get("min_message_length", 20),
        "model": am.get("model", ""),
        "model_fallback": am.get("model_fallback", ""),
    }


def _get_next_prompt_config(agent_id: str) -> dict:
    """Read next_prompt_suggestions settings from agent.json, merging with defaults.

    Fields:
      enabled: bool — feature toggle (default True)
      model: str — override model ID; empty = reuse session model
      max_words: int — soft cap on suggestion length (default 15)
    """
    cfg = AgentConfig(agent_id).config
    nps = cfg.get("next_prompt_suggestions", {})
    if not isinstance(nps, dict):
        nps = {}
    return {
        "enabled": nps.get("enabled", True),
        "model": nps.get("model", ""),
        "max_words": int(nps.get("max_words", 15)),
    }


def generate_next_prompt_suggestion(session) -> str | None:
    """Generate a short "next user prompt" suggestion based on the session's
    conversation so far. Returns the raw suggestion text, or None if disabled/unavailable.

    Reuses the session's current model by default; an override model can be set
    via agent.json `next_prompt_suggestions.model`.
    """
    try:
        cfg = _get_next_prompt_config(session.agent_id)
        if not cfg.get("enabled", True):
            return None

        messages = list(session.messages or [])
        if not messages:
            return None

        # Require at least one assistant response before suggesting
        if not any(m.get("role") == "assistant" for m in messages):
            return None

        max_words = max(3, min(40, cfg.get("max_words", 15)))
        # Match the user's caveman mode so the suggestion fits the style they're
        # already writing in. Refine-with-AI uses the same signal for the same
        # reason — suggestion + refined draft should land in the same register.
        cm = int(getattr(session, "caveman_mode", 0) or 0)
        style_hint = ""
        if cm == 1:
            style_hint = " Drop filler words and hedging; keep grammar intact."
        elif cm == 2:
            style_hint = " Telegraphic style: drop articles, filler, hedging; fragments OK; substance only."
        elif cm == 3:
            style_hint = " Ultra-telegraphic: max compression; no articles/filler/hedging; symbols (→ = > &) ok; no full sentences."
        instruction = (
            "Based on the conversation above, predict the user's most likely "
            f"next message. Respond with ONLY that message text (no quotes, no preamble, "
            f"no explanation). Keep it under {max_words} words.{style_hint} If nothing plausible "
            "comes to mind, respond with the single token: NONE"
        )

        # Strip metadata fields the API rejects (mirror augmented_messages pattern)
        clean_msgs = []
        for m in messages:
            if not isinstance(m, dict):
                continue
            role = m.get("role")
            content = m.get("content")
            if role in ("user", "assistant", "system") and content is not None:
                clean_msgs.append({"role": role, "content": content})

        if not clean_msgs:
            return None

        clean_msgs.append({"role": "user", "content": instruction})

        override_model = (cfg.get("model") or "").strip()
        model = override_model or session.model
        if not model:
            return None

        # GDPR auto-fallback: if the history contains PII and the chosen model
        # is cloud, swap to the configured local fallback. In hard-block mode
        # with no local fallback available, skip the suggestion entirely —
        # this call is ornamental and must not leak to cloud.
        try:
            _history_blobs = []
            for _m in clean_msgs:
                _c = _m.get("content")
                if isinstance(_c, str):
                    _history_blobs.append(_c)
            model = gdpr_pick_model_for_background(model, _history_blobs,
                                                   purpose="next_prompt_suggestion")
        except GDPRBlockedError:
            return None
        except Exception:
            pass

        prov = resolve_provider_for_model(model)

        _prev_uid = getattr(_thread_local, "current_user_id", None)
        _thread_local.current_user_id = (getattr(session, "user_id", "") or "")
        try:
            text = send_message_with_fallback(
                clean_msgs,
                model,
                prov.get("api_key", ""),
                prov.get("base_url", ""),
                silent=True,
                tools=False,
                event_callback=None,
                provider_resolver=resolve_provider_for_model,
                inference_params={"max_tokens": 200, "temperature": 0.7},
                purpose="next_prompt_suggestion",
                session_id=None,
            )
        finally:
            _thread_local.current_user_id = _prev_uid
        try:
            sys.stderr.write(f"[next_prompt] model={model} raw={text!r}\n")
        except Exception:
            pass
        if not text:
            return None
        # Strip <think>...</think> blocks that reasoning models (Qwen3, DeepSeek-R1,
        # etc.) emit before their answer. These aren't filtered at the wire level
        # for oMLX so we need to peel them off here or the "suggestion" becomes
        # a chain-of-thought paragraph.
        import re as _re
        text = _re.sub(r"<think>.*?</think>", "", text, flags=_re.DOTALL).strip()
        # Also drop a dangling unclosed <think> — some models stream thinking but
        # never close the tag within max_tokens.
        if "<think>" in text:
            text = text.split("<think>", 1)[0].strip()
        text = text.strip().strip('"').strip("'")
        if not text or text.upper().startswith("NONE"):
            return None
        # Soft word cap
        words = text.split()
        if len(words) > max_words * 2:
            text = " ".join(words[: max_words * 2])
        # Strip trailing punctuation artifacts from tiny models
        return text[:300]
    except Exception as e:
        try:
            sys.stderr.write(f"[next_prompt_suggestion] error: {e}\n")
        except Exception:
            pass
        return None


def _auto_memory_extract(agent_id: str, user_message: str, assistant_response: str):
    """MemPalace migration: disabled. Auto-memory extraction replaced by mempalace MCP
    (agents are expected to call mempalace_add_drawer / mempalace_diary_write themselves)."""
    return


def _auto_memory_extract_inner(agent_id: str, user_message: str, assistant_response: str):
    """Inner implementation of auto-memory extraction."""

    # Check config
    am_cfg = _get_auto_memory_config(agent_id)
    if not am_cfg.get("enabled", True):
        return

    min_len = am_cfg.get("min_message_length", 20)

    # Skip if messages are too short (small talk, acknowledgments)
    if len(user_message) < min_len or len(assistant_response) < 50:
        return

    # Skip if this looks like a tool-heavy response (already captured by tool actions)
    if assistant_response.count('tool_use') > 3:
        return

    # Heuristic detection of memorable content
    memorable_patterns = []

    # 1. User corrections / feedback
    correction_words = ['don\'t', 'stop', 'no,', 'actually', 'instead', 'wrong', 'not like that',
                        'prefer', 'always', 'never', 'remember that', 'keep in mind']
    user_lower = user_message.lower()
    for word in correction_words:
        if word in user_lower:
            memorable_patterns.append(('feedback', f'User correction/preference detected: "{word}"'))
            break

    # 2. User shares personal/role info
    identity_patterns = ['i am ', 'i\'m a', 'my role', 'i work ', 'my name', 'my team',
                         'my company', 'we use', 'our team', 'our project']
    for pat in identity_patterns:
        if pat in user_lower:
            memorable_patterns.append(('user', f'User identity/role info detected: "{pat}"'))
            break

    # 3. Decisions / commitments
    decision_patterns = ['let\'s go with', 'we decided', 'the plan is', 'going forward',
                         'from now on', 'the approach', 'we\'ll use', 'agreed']
    for pat in decision_patterns:
        if pat in user_lower:
            memorable_patterns.append(('project', f'Decision detected: "{pat}"'))
            break

    # 4. References to external resources
    if any(p in user_lower for p in ['http://', 'https://', 'the repo', 'the doc', 'the wiki',
                                      'slack channel', 'linear', 'jira', 'confluence']):
        memorable_patterns.append(('reference', 'External resource reference detected'))

    if not memorable_patterns:
        return  # Nothing worth storing

    # Use a quick LLM call to extract and format the memory
    mem_type, trigger = memorable_patterns[0]

    # Build a focused extraction prompt
    prompt = (
        f"Extract a concise, actionable memory from this conversation exchange.\n\n"
        f"USER: {user_message[:500]}\n\n"
        f"A: {assistant_response[:500]}\n\n"
        f"Trigger: {trigger}\n"
        f"Memory type: {mem_type}\n\n"
        f"Rules:\n"
        f'- Output ONLY a JSON object: {{"name": "short-title", "content": "the key fact/preference/decision", "type": "{mem_type}", "description": "one-line summary"}}\n'
        f"- Content should be 1-3 sentences, factual, no fluff\n"
        f'- If the exchange doesn\'t actually contain memorable info, output: {{"skip": true}}\n'
        f"- Do NOT output anything except the JSON object"
    )

    try:
        # Use configured model, or find cheapest
        model = None
        am_model = am_cfg.get("model", "")
        am_fallback = am_cfg.get("model_fallback", "")
        if am_model:
            model, _ = _resolve_model_with_fallback(am_model, am_fallback, "claude-haiku-4-5-20251001")
        if not model and _models_config:
            for mid, cfg in sorted(_models_config.items(), key=lambda x: x[1].get('cost_input', 999)):
                if cfg.get('enabled', True):
                    ml = mid.lower()
                    if 'haiku' in ml:
                        model = mid
                        break
            if not model:
                for mid, cfg in sorted(_models_config.items(), key=lambda x: x[1].get('priority', 0)):
                    if cfg.get('enabled', True):
                        model = mid
                        break

        if not model or not _delegate_api_key:
            return

        ms = MemoryStore(agent_id)
        result = _run_delegate(
            messages=[{"role": "user", "content": prompt}],
            model=model,
            system_prompt="You are a memory extraction assistant. Output only valid JSON.",
            memory_store=ms,
            inference_params={"max_tokens": 256, "temperature": 0.1},
            tools=False,
        )

        if not result or 'skip' in result.lower():
            return

        # Parse JSON
        data = _extract_json_from_llm(result)
        if not data or data.get('skip'):
            return

        name = data.get('name', '').strip()
        content = data.get('content', '').strip()
        mem_type_extracted = data.get('type', mem_type)
        description = data.get('description', '').strip()

        if not name or not content:
            return

        # Check if similar memory already exists — skip if content is substantially the same
        existing = ms.recall(name, limit=3)
        for e in existing:
            if e.get('name', '').lower() == name.lower():
                # Same name exists — update only if new content is meaningfully different
                old_content = e.get('content', '')
                if old_content and content.strip().lower() == old_content.strip().lower():
                    return  # Identical content, skip
                # Content differs — allow the store to overwrite
                break

        # Store it
        ms.store(name, content, description, mem_type_extracted)
        logging.info(f"Auto-memory stored for {agent_id}: {name} ({mem_type_extracted})")

    except (json.JSONDecodeError, ValueError, KeyError) as e:
        logging.debug(f"Auto-memory extraction parse error for {agent_id}: {e}")
    except Exception as e:
        logging.warning(f"Auto-memory extraction failed for {agent_id}: {e}")


def _collect_agent_memories(agent_id: str, max_content: int = 2000) -> list[dict]:
    """Collect all memory files for an agent, walking subdirectories.
    Content is truncated at max_content chars — callers never need more than 2000."""
    agent_dir = os.path.join(AGENTS_DIR, agent_id)
    if not os.path.isdir(agent_dir):
        return []
    memories = []
    ignore_dirs = {"skills", ".trash", "ingested"}
    for dirpath, dirnames, filenames in os.walk(agent_dir):
        dirnames[:] = [d for d in dirnames if d not in ignore_dirs and not d.startswith(".")]
        for fname in sorted(filenames):
            if not fname.endswith(".md") or fname in _QMD_IGNORE_FILES:
                continue
            if fname.startswith("ingest-"):
                continue
            fpath = os.path.join(dirpath, fname)
            rel = os.path.relpath(fpath, agent_dir)
            try:
                with open(fpath, "r") as f:
                    raw = f.read(max_content * 4)  # read only what we need (frontmatter + content)
                fm, body = _parse_frontmatter(raw)
                content = body.strip()
                memories.append({
                    "file": rel,
                    "name": fm.get("name", fname.replace(".md", "")),
                    "type": fm.get("type", "general"),
                    "description": fm.get("description", "").strip('"').strip("'"),
                    "content": content[:max_content],
                })
            except Exception:
                continue
    return memories


def _find_candidate_pairs(agent_id: str, memories: list[dict], max_candidates: int = 40) -> list[tuple[dict, dict, float]]:
    """Use QMD semantic search to find candidate pairs that might be related.
    For each memory, query QMD with its content and find similar files.
    Returns list of (mem_a, mem_b, score) tuples, deduplicated."""
    ms = MemoryStore(agent_id)
    seen_pairs = set()
    candidates = []

    for mem in memories:
        # Build a clean plaintext query for QMD
        def _clean(s):
            s = re.sub(r'["\'\(\)\[\]#*_|]', '', s)
            s = re.sub(r'\s*part\s+\d+/\d+\s*', '', s)
            s = s.replace('\n', ' ').replace('\r', ' ')
            return re.sub(r'\s+', ' ', s).strip()
        query = _clean(f"{mem['name']} {mem['description']} {mem['content'][:200]}")[:300]
        if not query:
            continue
        try:
            results = ms.recall(query, limit=5)
        except Exception:
            continue
        for r in results:
            other_file = os.path.relpath(r.get("file_path", ""), os.path.join(AGENTS_DIR, agent_id))
            if other_file == mem["file"]:
                continue
            # Find the matching memory dict
            other_mem = next((m for m in memories if m["file"] == other_file), None)
            if not other_mem:
                continue
            pair_key = tuple(sorted([mem["file"], other_mem["file"]]))
            if pair_key in seen_pairs:
                continue
            seen_pairs.add(pair_key)
            candidates.append((mem, other_mem, r.get("score", 0)))

    # Sort by score descending, take top candidates
    candidates.sort(key=lambda x: x[2], reverse=True)
    return candidates[:max_candidates]


def _build_relationship_discovery_prompt(agent_id: str) -> str:
    """Build prompt for LLM-based relationship discovery.
    Two-stage approach:
    1. QMD semantic search finds candidate pairs (scales to any number of files)
    2. LLM reads full content of candidates and classifies relationships
    """
    memories = _collect_agent_memories(agent_id)
    if len(memories) < 2:
        return ""

    # Stage 1: find candidate pairs via QMD embeddings
    candidates = _find_candidate_pairs(agent_id, memories)

    if not candidates:
        # Fallback: if QMD unavailable, send all files with truncated content
        listing = "\n\n".join(
            f"### {m['file']} (type: {m['type']})\n{m['content'][:500]}"
            for m in memories[:50]
        )
        return f"""Analyze these memories for agent '{agent_id}' and identify relationships.

{listing}

Output ONLY a JSON array. Each object: from_file, to_file, type (references|same_topic|depends_on|contradicts|extends), reason (under 20 words).
If no relationships, output []. Be selective — meaningful relationships only."""

    # Stage 2: build prompt with truncated content of candidate pairs
    _PAIR_CONTENT_LIMIT = 2000
    pair_sections = []
    for i, (a, b, score) in enumerate(candidates, 1):
        a_content = a['content'][:_PAIR_CONTENT_LIMIT] + ("…" if len(a['content']) > _PAIR_CONTENT_LIMIT else "")
        b_content = b['content'][:_PAIR_CONTENT_LIMIT] + ("…" if len(b['content']) > _PAIR_CONTENT_LIMIT else "")
        pair_sections.append(
            f"## Candidate Pair {i} (similarity: {score:.2f})\n\n"
            f"### File A: {a['file']} (type: {a['type']})\n{a_content}\n\n"
            f"### File B: {b['file']} (type: {b['type']})\n{b_content}"
        )

    pairs_text = "\n\n---\n\n".join(pair_sections)

    return f"""You are analyzing candidate pairs of memories for agent '{agent_id}'.
Each pair was identified as potentially related by semantic similarity.
Read the FULL content of each file and determine if a meaningful relationship exists.

{pairs_text}

## Task

For each pair where a meaningful relationship exists, output:
- from_file: filename of file A
- to_file: filename of file B
- type: one of (references, same_topic, depends_on, contradicts, extends)
- reason: brief explanation (under 20 words)

Skip pairs that are only superficially similar. Output ONLY a JSON array.
If no meaningful relationships, output [].

Example:
[{{"from_file": "notes/Meeting.md", "to_file": "chats-indexed/chat-abc-000.md", "type": "same_topic", "reason": "Both discuss macOS architecture on Apple Silicon"}}]"""


def _apply_discovered_relationships(agent_id: str, relationships: list[dict]):
    """Apply discovered relationships to memory files.
    Updates frontmatter of both files for each relationship."""
    if not relationships:
        return
    agent_dir = os.path.join(AGENTS_DIR, agent_id)
    modified = False
    for rel in relationships:
        from_file = rel.get("from_file", "")
        to_file = rel.get("to_file", "")
        rel_type = rel.get("type", "references")
        # Validate type
        if rel_type not in ("references", "same_topic", "depends_on", "contradicts", "extends"):
            rel_type = "references"
        if not from_file or not to_file:
            continue
        from_path = os.path.join(agent_dir, from_file)
        to_path = os.path.join(agent_dir, to_file)
        if not os.path.exists(from_path) or not os.path.exists(to_path):
            continue
        if _add_related_to_file(from_path, to_file, rel_type):
            modified = True
        # Add reverse link (bidirectional)
        reverse_type = rel_type
        if rel_type == "depends_on":
            reverse_type = "extends"  # reverse of depends_on
        elif rel_type == "extends":
            reverse_type = "depends_on"
        if _add_related_to_file(to_path, from_file, reverse_type):
            modified = True
    if modified:
        _qmd_debounced_embed(agent_id)


def trigger_relationship_discovery(agent_id: str):
    """Run LLM-based relationship discovery immediately in a background thread."""
    if not _delegate_api_key:
        logging.warning("Relationship discovery skipped: delegate API not configured")
        return
    prompt = _build_relationship_discovery_prompt(agent_id)
    if not prompt:
        return

    def _run():
        try:
            # Use Crow-4B for relationship discovery — JSON classification; fallback to Haiku
            target = AgentConfig(agent_id)
            rd_cfg = _get_relationship_discovery_config(agent_id)
            primary = rd_cfg.get("model") or "mistral-small"
            fallback = rd_cfg.get("model_fallback") or "claude-haiku-4-5-20251001"
            model, fallback_model = _resolve_model_with_fallback(primary, fallback, "claude-haiku-4-5-20251001")
            ms = MemoryStore(agent_id)
            logging.info(f"Relationship discovery starting for {agent_id} using {model} (fallback: {fallback_model})")
            result_text = _run_delegate_with_fallback(
                messages=[{"role": "user", "content": prompt}],
                primary_model=model, fallback_model=fallback_model,
                system_prompt="You are a relationship analysis assistant. Analyze the given memories and output ONLY a valid JSON array of relationships. No explanations, no markdown, just the JSON array.",
                memory_store=ms,
                inference_params={"max_tokens": 16384, "temperature": 0.2},
                tools=False,
            )
            if not result_text:
                logging.warning(f"Relationship discovery for {agent_id}: empty response")
                return
            if result_text.startswith("Delegation error:"):
                logging.warning(f"Relationship discovery for {agent_id}: {result_text}")
                return
            # Extract JSON from response (may have markdown code fences)
            relationships = _extract_json_from_llm(result_text, expect_array=True)
            if isinstance(relationships, list) and relationships:
                _apply_discovered_relationships(agent_id, relationships)
                logging.info(f"Relationship discovery for {agent_id}: found {len(relationships)} relationships")
            elif relationships is not None:
                logging.info(f"Relationship discovery for {agent_id}: no relationships found")
            else:
                logging.warning(f"Relationship discovery for {agent_id}: no JSON in response: {result_text[:200]}")
        except Exception as e:
            logging.exception(f"Relationship discovery failed for {agent_id}: {e}")

    threading.Thread(target=_run, daemon=True, name=f"rel_discovery_{agent_id}").start()


def _relationship_discovery_schedule_name(agent_id: str) -> str:
    return f"_relationship_discovery_{agent_id}"


def _relationship_discovery_schedule_str(cfg: dict) -> str:
    """Convert relationship_discovery config to a scheduler schedule string."""
    freq = cfg.get("frequency", "every 24h").strip().lower()
    start_time = cfg.get("start_time", "").strip()
    if start_time and re.match(r'^\d{1,2}:\d{2}$', start_time):
        m = re.match(r'every\s+(\d+)\s*(h|hour|d|day)', freq)
        if m:
            val, unit = int(m.group(1)), m.group(2)[0]
            if (unit == 'h' and val == 24) or (unit == 'd' and val == 1):
                return f"daily {start_time}"
            elif unit == 'h' and val < 24:
                return freq
            elif unit == 'd' and val > 1:
                return freq
    return freq


def _build_relationship_discovery_task_prompt(agent_id: str) -> str:
    """Build the scheduled task prompt for relationship discovery."""
    return f"""Perform relationship discovery for agent '{agent_id}'.

Use memory_recall with an empty query to list all memories, then analyze them for relationships.

For each pair of related memories, use memory_store to update the related frontmatter.
Focus on meaningful relationships: references, same_topic, depends_on, contradicts, extends.

Be selective — only report meaningful relationships, not superficial ones."""


def ensure_relationship_discovery_schedules():
    """MemPalace migration: disabled. Relationship discovery replaced by mempalace KG."""
    return
    # --- legacy implementation kept for reference ---
    if not _scheduler:
        return
    agents = list_agents()
    existing = {s["name"]: s for s in _scheduler.list_all()}

    # Remove orphaned schedules for agents that no longer exist
    for sched_name in list(existing):
        if sched_name.startswith("_relationship_discovery_"):
            orphan_agent = sched_name[len("_relationship_discovery_"):]
            if orphan_agent not in agents:
                _scheduler.remove(sched_name)

    for agent_id in agents:
        sched_name = _relationship_discovery_schedule_name(agent_id)
        rd_cfg = _get_relationship_discovery_config(agent_id)

        if rd_cfg["enabled"]:
            schedule_str = _relationship_discovery_schedule_str(rd_cfg)
            task_prompt = _build_relationship_discovery_task_prompt(agent_id)
            # Use Crow-4B for RD scheduled task; fallback Haiku
            primary = rd_cfg.get("model") or "mistral-small"
            fallback = rd_cfg.get("model_fallback") or "claude-haiku-4-5-20251001"
            rd_model, _ = _resolve_model_with_fallback(primary, fallback, "claude-haiku-4-5-20251001")

            if sched_name in existing:
                old = existing[sched_name]
                if old["schedule"] != schedule_str or old.get("enabled") != 1 or old.get("model") != rd_model:
                    _scheduler.remove(sched_name)
                    _scheduler.add(sched_name, task_prompt, schedule_str,
                                   agent=agent_id, model=rd_model, timeout=600)
            else:
                _scheduler.add(sched_name, task_prompt, schedule_str,
                               agent=agent_id, model=rd_model, timeout=600)
        else:
            if sched_name in existing:
                _scheduler.remove(sched_name)


# ─── Autodream: Memory Consolidation System ─────────────────────────────


def _autodream_dedup(agent_id: str, ms: MemoryStore, config: dict, memories: list[dict] | None = None) -> dict:
    """Scan memories for semantic duplicates via QMD, merge with LLM."""
    if memories is None:
        memories = _collect_agent_memories(agent_id)
    if len(memories) < 2:
        return {"duplicates_found": 0, "merged": 0, "skipped": 0, "merge_log": []}

    threshold = config.get("dedup_similarity_threshold", 0.85)
    max_merges = config.get("max_dedup_merges", 10)

    # Find candidate pairs via QMD semantic search
    try:
        pairs = _find_candidate_pairs(agent_id, memories, max_candidates=60)
    except Exception:
        return {"duplicates_found": 0, "merged": 0, "skipped": 0, "merge_log": [],
                "error": "Failed to find candidate pairs"}

    # Filter to high-similarity pairs
    high_sim = [(a, b, score) for a, b, score in pairs if score >= threshold]

    duplicates_found = 0
    merged = 0
    skipped = 0
    merge_log = []
    deleted_files = set()

    # Resolve model (configured primary + fallback)
    model, fallback_model = _resolve_autodream_model(config)
    if not model or not _delegate_api_key:
        return {"duplicates_found": len(high_sim), "merged": 0, "skipped": len(high_sim),
                "merge_log": ["No model available for merge confirmation"]}

    for mem_a, mem_b, score in high_sim:
        if mem_a["file"] in deleted_files or mem_b["file"] in deleted_files:
            continue

        duplicates_found += 1

        if merged >= max_merges:
            skipped += 1
            continue
        prompt = (
            f"Are these two memories duplicates or near-duplicates that should be merged?\n\n"
            f"MEMORY A ({mem_a['name']}):\n{mem_a['content'][:800]}\n\n"
            f"MEMORY B ({mem_b['name']}):\n{mem_b['content'][:800]}\n\n"
            f"If they are duplicates, produce a merged version that preserves all unique information.\n"
            f'Output ONLY JSON: {{"is_duplicate": true/false, "merged_name": "...", "merged_content": "...", '
            f'"merged_description": "..."}}\n'
            f'If not duplicates: {{"is_duplicate": false}}'
        )
        try:
            result = _run_delegate_with_fallback(
                messages=[{"role": "user", "content": prompt}],
                primary_model=model, fallback_model=fallback_model,
                system_prompt="You are a memory deduplication assistant. Output only valid JSON.",
                inference_params={"max_tokens": 1024, "temperature": 0.1},
                tools=False,
            )
            if not result:
                skipped += 1
                continue
            data = _extract_json_from_llm(result)
            if not data or not data.get("is_duplicate"):
                skipped += 1
                continue

            # Determine which file is older (keep newer filename)
            agent_dir = os.path.join(AGENTS_DIR, agent_id)
            path_a = os.path.join(agent_dir, mem_a["file"])
            path_b = os.path.join(agent_dir, mem_b["file"])
            mtime_a = os.path.getmtime(path_a) if os.path.exists(path_a) else 0
            mtime_b = os.path.getmtime(path_b) if os.path.exists(path_b) else 0

            # Store merged content under the newer file's name
            merged_name = data.get("merged_name", mem_a["name"] if mtime_a >= mtime_b else mem_b["name"])
            ms.store(merged_name, data.get("merged_content", ""),
                     data.get("merged_description", ""), mem_a.get("type", "general"))

            # Delete the older file
            older = mem_b if mtime_a >= mtime_b else mem_a
            ms.delete(older["name"])
            deleted_files.add(older["file"])
            merged += 1
            merge_log.append(f"Merged '{mem_a['name']}' + '{mem_b['name']}' → '{merged_name}'")
        except Exception as e:
            skipped += 1
            merge_log.append(f"Error merging '{mem_a['name']}' + '{mem_b['name']}': {str(e)[:100]}")

    return {"duplicates_found": duplicates_found, "merged": merged,
            "skipped": skipped, "merge_log": merge_log}


def _autodream_staleness(agent_id: str, ms: MemoryStore, config: dict) -> dict:
    """Flag memories not recalled in N days as stale. No LLM calls."""
    threshold_days = config.get("stale_threshold_days", 30)
    now = datetime.datetime.now()
    agent_dir = os.path.join(AGENTS_DIR, agent_id)

    total = 0
    stale_count = 0
    newly_stale = 0
    stale_names = []
    # Skip system files (Memory Summary, Memory Health Report)
    skip_prefixes = ("Memory Summary", "Memory Health Report")

    for fname in os.listdir(agent_dir):
        if not fname.endswith(".md") or fname in _QMD_IGNORE_FILES:
            continue
        if fname.startswith("ingest-"):
            continue
        fpath = os.path.join(agent_dir, fname)
        try:
            with open(fpath, "r") as f:
                raw = f.read()
            fm, body = _parse_frontmatter(raw)
        except Exception:
            continue

        name = fm.get("name", fname.replace(".md", ""))
        if any(name.startswith(p) for p in skip_prefixes):
            continue
        total += 1

        # Determine last activity date
        last_recalled_str = fm.get("last_recalled", "")
        if last_recalled_str:
            try:
                last_date = datetime.datetime.strptime(last_recalled_str.strip(), "%Y-%m-%d")
            except ValueError:
                last_date = datetime.datetime.fromtimestamp(os.path.getmtime(fpath))
        else:
            last_date = datetime.datetime.fromtimestamp(os.path.getmtime(fpath))

        days_since = (now - last_date).days
        already_stale = fm.get("stale", "").lower() == "true"

        if days_since > threshold_days:
            stale_count += 1
            stale_names.append(name)
            if not already_stale:
                # Stamp stale: true in frontmatter
                try:
                    fm_match = re.match(r'^(---\s*\n)(.*?)(\n---\s*\n)(.*)$', raw, re.DOTALL)
                    if fm_match:
                        opener, fm_text, closer, body_text = fm_match.groups()
                        if "stale:" not in fm_text:
                            fm_text = fm_text.rstrip() + "\nstale: true\n"
                        with open(fpath, "w") as f:
                            f.write(opener + fm_text + closer + body_text)
                        newly_stale += 1
                except Exception as e:
                    logging.debug(f"Failed to mark stale: {fpath}: {e}")
        elif already_stale:
            # Was stale but now recalled within threshold — remove stale flag
            try:
                fm_match = re.match(r'^(---\s*\n)(.*?)(\n---\s*\n)(.*)$', raw, re.DOTALL)
                if fm_match:
                    opener, fm_text, closer, body_text = fm_match.groups()
                    fm_text = re.sub(r'\nstale:.*', '', fm_text)
                    with open(fpath, "w") as f:
                        f.write(opener + fm_text + closer + body_text)
            except Exception:
                pass

    return {"total": total, "stale_count": stale_count, "stale_names": stale_names,
            "newly_stale": newly_stale}


def _autodream_conflicts(agent_id: str, ms: MemoryStore, config: dict, memories: list[dict] | None = None) -> dict:
    """Find contradicting memories using QMD similarity + LLM classification."""
    if memories is None:
        memories = _collect_agent_memories(agent_id)
    if len(memories) < 2:
        return {"conflicts_found": 0, "conflict_pairs": []}

    max_checks = config.get("max_conflict_checks", 30)

    try:
        pairs = _find_candidate_pairs(agent_id, memories, max_candidates=max_checks * 2)
    except Exception:
        return {"conflicts_found": 0, "conflict_pairs": [], "error": "Failed to find candidate pairs"}

    # Filter to same-type pairs (contradictions are most likely within same type)
    same_type_pairs = [(a, b, s) for a, b, s in pairs if a["type"] == b["type"]][:max_checks]

    model, fallback_model = _resolve_autodream_model(config)
    if not model or not _delegate_api_key:
        return {"conflicts_found": 0, "conflict_pairs": [],
                "error": "No model available for conflict detection"}

    conflicts_found = 0
    conflict_pairs = []

    for mem_a, mem_b, score in same_type_pairs:
        prompt = (
            f"Do these two memories contradict each other? Look for conflicting facts, "
            f"opposing instructions, or incompatible information.\n\n"
            f"MEMORY A ({mem_a['name']}, type={mem_a['type']}):\n{mem_a['content'][:600]}\n\n"
            f"MEMORY B ({mem_b['name']}, type={mem_b['type']}):\n{mem_b['content'][:600]}\n\n"
            f'Output ONLY JSON: {{"contradicts": true/false, "nature": "brief description of conflict"}}\n'
            f'If no contradiction: {{"contradicts": false}}'
        )
        try:
            result = _run_delegate_with_fallback(
                messages=[{"role": "user", "content": prompt}],
                primary_model=model, fallback_model=fallback_model,
                system_prompt="You are a memory conflict detector. Output only valid JSON.",
                inference_params={"max_tokens": 256, "temperature": 0.1},
                tools=False,
            )
            if not result:
                continue
            data = _extract_json_from_llm(result)
            if not data:
                continue
            if data.get("contradicts"):
                conflicts_found += 1
                conflict_pairs.append({
                    "a": mem_a["name"], "b": mem_b["name"],
                    "nature": data.get("nature", "Unknown conflict"),
                })
        except Exception:
            continue

    return {"conflicts_found": conflicts_found, "conflict_pairs": conflict_pairs}


def _autodream_skill_candidates(agent_id: str, ms: MemoryStore, config: dict, memories: list[dict] | None = None) -> dict:
    """Identify procedural memories that could become skills."""
    if memories is None:
        memories = _collect_agent_memories(agent_id)
    # Filter to feedback/general types
    candidates_pool = [m for m in memories if m["type"] in ("feedback", "general")]

    # Heuristic pre-filter: procedural patterns
    procedural_patterns = [
        r'\b\d+\.\s', r'always\b', r'never\b', r'when\s.*\bdo\b', r'steps?:',
        r'how to\b', r'make sure\b', r'first\b.*then\b', r'rule:',
    ]
    heuristic_matches = []
    for mem in candidates_pool:
        content_lower = mem["content"].lower()
        if any(re.search(p, content_lower) for p in procedural_patterns):
            heuristic_matches.append(mem)
    heuristic_matches = heuristic_matches[:15]  # Cap

    if not heuristic_matches:
        return {"candidates_found": 0, "candidate_names": [], "candidate_details": []}

    model, fallback_model = _resolve_autodream_model(config)
    if not model or not _delegate_api_key:
        return {"candidates_found": 0, "candidate_names": [], "candidate_details": [],
                "error": "No model available for skill detection"}

    candidates_found = 0
    candidate_names = []
    candidate_details = []

    for mem in heuristic_matches:
        prompt = (
            f"Is this memory a reusable procedure or workflow that should become a skill?\n"
            f"A skill is a repeatable process with clear steps that an AI agent would benefit from having as a template.\n\n"
            f"Memory ({mem['name']}, type={mem['type']}):\n{mem['content'][:800]}\n\n"
            f'Output ONLY JSON: {{"is_skill_candidate": true/false, "reason": "brief explanation"}}\n'
            f'If not a skill candidate: {{"is_skill_candidate": false}}'
        )
        try:
            result = _run_delegate_with_fallback(
                messages=[{"role": "user", "content": prompt}],
                primary_model=model, fallback_model=fallback_model,
                system_prompt="You are a skill detection assistant. Output only valid JSON.",
                inference_params={"max_tokens": 256, "temperature": 0.1},
                tools=False,
            )
            if not result:
                continue
            data = _extract_json_from_llm(result)
            if not data:
                continue
            if data.get("is_skill_candidate"):
                candidates_found += 1
                candidate_names.append(mem["name"])
                candidate_details.append({
                    "name": mem["name"],
                    "reason": data.get("reason", "Procedural content detected"),
                })
        except Exception:
            continue

    return {"candidates_found": candidates_found, "candidate_names": candidate_names,
            "candidate_details": candidate_details}


def _resolve_model_with_fallback(primary: str | None, fallback: str | None, hardcoded_default: str) -> tuple[str, str | None]:
    """Return (primary_to_use, fallback_to_use) after validating against enabled models.
    Falls back through the chain: configured primary → configured fallback → hardcoded default."""
    def _is_available(mid: str) -> bool:
        if not mid:
            return False
        if not _models_config:
            return True  # can't check, assume available
        mcfg = _models_config.get(mid, {})
        return mcfg.get("enabled", True)

    resolved_primary = primary if _is_available(primary) else None
    resolved_fallback = fallback if _is_available(fallback) else None

    if resolved_primary:
        return resolved_primary, resolved_fallback or hardcoded_default
    if resolved_fallback:
        return resolved_fallback, hardcoded_default
    return hardcoded_default, None


def _run_delegate_with_fallback(messages, primary_model, fallback_model, system_prompt,
                                 memory_store=None, inference_params=None,
                                 tools: bool = True) -> str | None:
    """Run _run_delegate with automatic fallback if primary model fails."""
    result = _run_delegate(
        messages=messages, model=primary_model, system_prompt=system_prompt,
        memory_store=memory_store, inference_params=inference_params, tools=tools,
    )
    if result and result.startswith("Delegation error:") and fallback_model and fallback_model != primary_model:
        logging.warning(f"Primary model {primary_model} failed, falling back to {fallback_model}")
        result = _run_delegate(
            messages=messages, model=fallback_model, system_prompt=system_prompt,
            memory_store=memory_store, inference_params=inference_params, tools=tools,
        )
    return result


def _resolve_autodream_model(config: dict) -> tuple[str, str | None]:
    """Resolve (primary, fallback) models for autodream."""
    primary = config.get("model") or "Crow-4B-Opus-4.6-Distill"
    fallback = config.get("model_fallback") or "claude-haiku-4-5-20251001"
    return _resolve_model_with_fallback(primary, fallback, "claude-haiku-4-5-20251001")


def _find_cheapest_model() -> str | None:
    """Find the cheapest available model (prefer local, then Haiku)."""
    if not _models_config:
        return None
    # Prefer local models first (priority <= 30)
    for mid, cfg in sorted(_models_config.items(), key=lambda x: x[1].get('priority', 50)):
        if cfg.get('enabled', True) and cfg.get('priority', 50) <= 30:
            return mid
    # Then Haiku
    for mid, cfg in _models_config.items():
        if cfg.get('enabled', True) and 'haiku' in mid.lower():
            return mid
    # Then lowest priority
    for mid, cfg in sorted(_models_config.items(), key=lambda x: x[1].get('priority', 0)):
        if cfg.get('enabled', True):
            return mid
    return None


def promote_memory_to_skill(agent_id: str, memory_name: str) -> dict:
    """Convert a memory into a skill by generating a SKILL.md via LLM."""
    ms = MemoryStore(agent_id)
    # Find the memory
    memories = _collect_agent_memories(agent_id)
    mem = next((m for m in memories if m["name"] == memory_name), None)
    if not mem:
        return {"error": f"Memory '{memory_name}' not found"}

    ad_cfg = _get_autodream_config(agent_id)
    model = _resolve_autodream_model(ad_cfg)
    if not model or not _delegate_api_key:
        return {"error": "No model available for skill generation"}

    # Generate SKILL.md content via LLM
    prompt = (
        f"Convert this memory into a well-structured SKILL.md file.\n\n"
        f"Memory name: {mem['name']}\n"
        f"Memory content:\n{mem['content']}\n\n"
        f"Generate a skill document with:\n"
        f"1. A clear, concise title\n"
        f"2. Step-by-step instructions or reference material\n"
        f"3. Examples where helpful\n"
        f"4. Keep it practical and actionable\n\n"
        f'Output ONLY JSON: {{"slug": "short-kebab-case-name", "name": "Display Name", '
        f'"description": "one-line description", "body": "the full skill body in markdown"}}'
    )
    try:
        result = _run_delegate(
            messages=[{"role": "user", "content": prompt}],
            model=model,
            system_prompt="You are a skill creation assistant. Output only valid JSON.",
            inference_params={"max_tokens": 2048, "temperature": 0.2},
            tools=False,
        )
        if not result:
            return {"error": "LLM returned empty response"}

        data = _extract_json_from_llm(result)
        if not data:
            return {"error": "Could not parse LLM response"}
        slug = re.sub(r'[^a-z0-9-]', '', data.get("slug", "").lower().replace(" ", "-"))[:40]
        if not slug:
            slug = re.sub(r'[^a-z0-9-]', '', memory_name.lower().replace(" ", "-"))[:40]
        name = data.get("name", memory_name)
        description = data.get("description", "")
        body = data.get("body", mem["content"])

        # Write SKILL.md
        skills_dir = os.path.join(AGENTS_DIR, agent_id, "skills", slug)
        os.makedirs(skills_dir, exist_ok=True)
        skill_path = os.path.join(skills_dir, "SKILL.md")

        skill_content = f"""---
name: {_yaml_escape(name)}
description: {_yaml_escape(description)}
---

{body}
"""
        with open(skill_path, "w") as f:
            f.write(skill_content)

        logging.info(f"Promoted memory '{memory_name}' to skill '{slug}' for {agent_id}")
        return {"status": "created", "slug": slug, "name": name, "path": skill_path}

    except Exception as e:
        return {"error": f"Skill generation failed: {str(e)[:200]}"}


def _build_autodream_report(agent_id: str, results: dict) -> str:
    """Compile autodream results into a structured markdown report."""
    now = datetime.datetime.now()
    dedup = results.get("dedup", {})
    stale = results.get("staleness", {})
    conflicts = results.get("conflicts", {})
    skills = results.get("skill_candidates", {})

    # Calculate health score
    total = stale.get("total", 1) or 1
    stale_pct = (stale.get("stale_count", 0) / total) * 100
    health_score = max(0, min(100, int(
        100
        - (stale_pct * 0.3)
        - (conflicts.get("conflicts_found", 0) * 5)
        - (max(0, dedup.get("duplicates_found", 0) - dedup.get("merged", 0)) * 2)
    )))
    results["health_score"] = health_score

    report = f"""# Memory Health Report — {now.strftime('%Y-%m-%d %H:%M')}

**Health Score: {health_score}/100**

## Deduplication
- Duplicates found: {dedup.get('duplicates_found', 0)}
- Merged: {dedup.get('merged', 0)}
- Skipped: {dedup.get('skipped', 0)}
"""
    for entry in dedup.get("merge_log", []):
        report += f"- {entry}\n"

    report += f"""
## Staleness
- Total memories: {stale.get('total', 0)}
- Stale (>{results.get('config', {}).get('stale_threshold_days', 30)}d): {stale.get('stale_count', 0)} ({stale_pct:.0f}%)
- Newly flagged: {stale.get('newly_stale', 0)}
"""
    for name in stale.get("stale_names", [])[:20]:
        report += f"- {name}\n"

    report += f"""
## Conflicts
- Conflicts detected: {conflicts.get('conflicts_found', 0)}
"""
    for pair in conflicts.get("conflict_pairs", []):
        report += f"- **{pair['a']}** ↔ **{pair['b']}**: {pair['nature']}\n"

    report += f"""
## Skill Candidates
- Candidates found: {skills.get('candidates_found', 0)}
"""
    for c in skills.get("candidate_details", []):
        report += f"- **{c['name']}**: {c['reason']}\n"

    return report


# Live autodream status per agent: {agent_id: {status, phase, started_at, finished_at, error, results}}
_autodream_status: dict[str, dict] = {}
_autodream_status_lock = threading.Lock()


def get_autodream_status(agent_id: str) -> dict | None:
    """Get live autodream status for an agent."""
    with _autodream_status_lock:
        return dict(_autodream_status.get(agent_id, {})) or None


def trigger_autodream(agent_id: str):
    """MemPalace migration: disabled. Autodream consolidation replaced by mempalace's
    own dedup/staleness/conflict passes (or simply the verbatim philosophy)."""
    return
    # --- legacy implementation kept for reference ---
    ad_cfg = _get_autodream_config(agent_id)
    if not ad_cfg.get("enabled"):
        return

    def _update_status(**kwargs):
        with _autodream_status_lock:
            if agent_id not in _autodream_status:
                _autodream_status[agent_id] = {}
            _autodream_status[agent_id].update(kwargs)

    def _run():
        started = datetime.datetime.now()
        _update_status(status="running", phase="starting", started_at=started.isoformat(),
                       finished_at=None, error=None, results=None)
        try:
            logging.info(f"Autodream starting for {agent_id}")
            ms = MemoryStore(agent_id)
            results = {"config": ad_cfg, "agent": agent_id}

            # Collect memories once — shared across all passes to avoid repeated filesystem walks
            memories = _collect_agent_memories(agent_id)

            # Run passes sequentially
            _update_status(phase="dedup")
            results["dedup"] = _autodream_dedup(agent_id, ms, ad_cfg, memories=memories)
            logging.info(f"Autodream dedup done for {agent_id}: {results['dedup'].get('merged', 0)} merged")

            _update_status(phase="staleness")
            results["staleness"] = _autodream_staleness(agent_id, ms, ad_cfg)
            logging.info(f"Autodream staleness done for {agent_id}: {results['staleness'].get('stale_count', 0)} stale")

            _update_status(phase="conflicts")
            results["conflicts"] = _autodream_conflicts(agent_id, ms, ad_cfg, memories=memories)
            logging.info(f"Autodream conflicts done for {agent_id}: {results['conflicts'].get('conflicts_found', 0)} found")

            _update_status(phase="skill_candidates")
            results["skill_candidates"] = _autodream_skill_candidates(agent_id, ms, ad_cfg, memories=memories)
            logging.info(f"Autodream skills done for {agent_id}: {results['skill_candidates'].get('candidates_found', 0)} candidates")

            # Build and store report
            _update_status(phase="report")
            report = _build_autodream_report(agent_id, results)
            now_str = datetime.datetime.now().strftime('%Y-%m-%d')
            ms.store(
                f"Memory Health Report — {now_str}",
                report,
                description=f"Autodream consolidation report for {now_str}",
                mem_type="system",
            )

            # Clean up old reports beyond retention limit
            retention = ad_cfg.get("report_retention", 3)
            _cleanup_autodream_reports(agent_id, ms, retention)

            finished = datetime.datetime.now()
            elapsed = (finished - started).total_seconds()
            logging.info(f"Autodream complete for {agent_id}: health_score={results.get('health_score', '?')} ({elapsed:.1f}s)")
            _update_status(status="completed", phase="done", finished_at=finished.isoformat(),
                           elapsed=round(elapsed, 1), results={
                               "health_score": results.get("health_score"),
                               "merged": results.get("dedup", {}).get("merged", 0),
                               "stale": results.get("staleness", {}).get("stale_count", 0),
                               "conflicts": results.get("conflicts", {}).get("conflicts_found", 0),
                               "skill_candidates": results.get("skill_candidates", {}).get("candidates_found", 0),
                           })

        except Exception as e:
            logging.error(f"Autodream failed for {agent_id}: {e}")
            _update_status(status="error", phase="failed",
                           finished_at=datetime.datetime.now().isoformat(),
                           error=str(e)[:300])

    threading.Thread(target=_run, daemon=True, name=f"autodream_{agent_id}").start()


def _cleanup_autodream_reports(agent_id: str, ms: MemoryStore, retention: int):
    """Delete old Memory Health Report files beyond the retention limit."""
    agent_dir = os.path.join(AGENTS_DIR, agent_id)
    reports = []
    for fname in os.listdir(agent_dir):
        if not fname.endswith(".md"):
            continue
        fpath = os.path.join(agent_dir, fname)
        try:
            with open(fpath, "r") as f:
                raw = f.read(500)
            fm, _ = _parse_frontmatter(raw)
            if fm.get("name", "").strip('"').strip("'").startswith("Memory Health Report"):
                reports.append((fpath, os.path.getmtime(fpath), fm.get("name", "")))
        except Exception:
            continue

    # Sort by mtime descending, delete older ones
    reports.sort(key=lambda x: x[1], reverse=True)
    for fpath, _, name in reports[retention:]:
        try:
            os.remove(fpath)
        except Exception:
            pass
    if len(reports) > retention:
        _qmd_debounced_embed(agent_id)


def get_memory_health(agent_id: str) -> dict:
    """Compute live memory health stats for an agent."""
    agent_dir = os.path.join(AGENTS_DIR, agent_id)
    if not os.path.isdir(agent_dir):
        return {"error": f"Agent not found: {agent_id}"}

    now = datetime.datetime.now()
    total = 0
    by_type: dict[str, int] = {}
    stale_count = 0
    stale_names = []
    ages = []
    recall_hot = 0     # recalled in last 7 days
    recall_warm = 0    # recalled in last 30 days
    recall_cold = 0    # recalled 30+ days ago
    recall_never = 0   # never recalled
    auto_24h = 0
    auto_7d = 0
    reports = []
    last_results = None
    last_run = None
    cutoff_24h = time.time() - 86400
    cutoff_7d = time.time() - 604800

    skip_prefixes = ("Memory Summary", "Memory Health Report")

    for fname in os.listdir(agent_dir):
        if not fname.endswith(".md") or fname in _QMD_IGNORE_FILES:
            continue
        if fname.startswith("ingest-"):
            continue
        fpath = os.path.join(agent_dir, fname)
        try:
            with open(fpath, "r") as f:
                raw = f.read()
            fm, body = _parse_frontmatter(raw)
        except Exception:
            continue

        name = fm.get("name", fname.replace(".md", "")).strip('"').strip("'")
        mem_type = fm.get("type", "general")
        mtime = os.path.getmtime(fpath)

        # Collect health reports
        if name.startswith("Memory Health Report"):
            # Parse health score from body
            score_match = re.search(r'Health Score:\s*(\d+)/100', body)
            score = int(score_match.group(1)) if score_match else 0
            date_match = re.search(r'(\d{4}-\d{2}-\d{2})', name)
            date_str = date_match.group(1) if date_match else datetime.datetime.fromtimestamp(mtime).strftime('%Y-%m-%d')
            reports.append({"date": date_str, "health_score": score, "mtime": mtime})
            if not last_run or mtime > last_run:
                last_run = mtime
                # Try to parse last results from report
                try:
                    last_results = _parse_health_report(body)
                except Exception:
                    last_results = None
            continue

        if any(name.startswith(p) for p in skip_prefixes):
            continue

        total += 1
        by_type[mem_type] = by_type.get(mem_type, 0) + 1

        # Age
        age_days = (now - datetime.datetime.fromtimestamp(mtime)).days
        ages.append(age_days)

        # Staleness
        if fm.get("stale", "").lower() == "true":
            stale_count += 1
            stale_names.append(name)

        # Recall frequency
        lr = fm.get("last_recalled", "")
        if lr:
            try:
                lr_date = datetime.datetime.strptime(lr.strip(), "%Y-%m-%d")
                days_since = (now - lr_date).days
                if days_since <= 7:
                    recall_hot += 1
                elif days_since <= 30:
                    recall_warm += 1
                else:
                    recall_cold += 1
            except ValueError:
                recall_never += 1
        else:
            recall_never += 1

        # Auto-memory rate (by creation time)
        if mtime > cutoff_24h:
            auto_24h += 1
        if mtime > cutoff_7d:
            auto_7d += 1

    # Sort reports by mtime
    reports.sort(key=lambda x: x.get("mtime", 0), reverse=True)
    for r in reports:
        r.pop("mtime", None)

    # QMD health
    qmd_health = {"indexed": 0, "embedded": 0, "stale": 0, "not_indexed": 0}
    try:
        r = subprocess.run(["qmd", "collection", "show", agent_id],
                           capture_output=True, text=True, timeout=5)
        if r.returncode == 0:
            # Count from the docs endpoint instead
            pass
    except Exception:
        pass

    # Health score from latest report, or compute a basic one
    if reports:
        health_score = reports[0].get("health_score", 0)
    else:
        stale_pct = (stale_count / max(total, 1)) * 100
        health_score = max(0, min(100, int(100 - (stale_pct * 0.3))))

    ad_cfg = _get_autodream_config(agent_id)

    return {
        "agent": agent_id,
        "total_memories": total,
        "by_type": by_type,
        "qmd_health": qmd_health,
        "stale_count": stale_count,
        "stale_names": stale_names[:50],
        "age_distribution": {
            "avg_days": round(sum(ages) / max(len(ages), 1), 1),
            "oldest_days": max(ages) if ages else 0,
            "newest_days": min(ages) if ages else 0,
        },
        "recall_frequency": {
            "hot": recall_hot, "warm": recall_warm,
            "cold": recall_cold, "never_recalled": recall_never,
        },
        "autodream": {
            "config": ad_cfg,
            "last_run": datetime.datetime.fromtimestamp(last_run).isoformat() if last_run else None,
            "last_results": last_results,
            "reports": reports[:ad_cfg.get("report_retention", 3)],
            "live": get_autodream_status(agent_id),
        },
        "auto_memory_rate": {"last_24h": auto_24h, "last_7d": auto_7d},
        "health_score": health_score,
    }


def _parse_health_report(body: str) -> dict:
    """Parse structured data from a Memory Health Report body."""
    results = {}
    # Dedup section
    m = re.search(r'Duplicates found:\s*(\d+)', body)
    results["duplicates_found"] = int(m.group(1)) if m else 0
    m = re.search(r'Merged:\s*(\d+)', body)
    results["merged"] = int(m.group(1)) if m else 0
    # Staleness
    m = re.search(r'Stale.*?:\s*(\d+)', body)
    results["stale_count"] = int(m.group(1)) if m else 0
    m = re.search(r'Newly flagged:\s*(\d+)', body)
    results["newly_stale"] = int(m.group(1)) if m else 0
    # Conflicts
    m = re.search(r'Conflicts detected:\s*(\d+)', body)
    results["conflicts_found"] = int(m.group(1)) if m else 0
    # Parse conflict pairs
    conflict_pairs = []
    for cm in re.finditer(r'\*\*(.+?)\*\* ↔ \*\*(.+?)\*\*:\s*(.+)', body):
        conflict_pairs.append({"a": cm.group(1), "b": cm.group(2), "nature": cm.group(3).strip()})
    results["conflict_pairs"] = conflict_pairs
    # Skill candidates
    m = re.search(r'Candidates found:\s*(\d+)', body)
    results["candidates_found"] = int(m.group(1)) if m else 0
    candidate_details = []
    for cm in re.finditer(r'- \*\*(.+?)\*\*:\s*(.+)', body):
        name = cm.group(1)
        if name not in [p["a"] for p in conflict_pairs] + [p["b"] for p in conflict_pairs]:
            candidate_details.append({"name": name, "reason": cm.group(2).strip()})
    results["candidate_details"] = candidate_details
    return results


def get_graph_stats(agent_id: str) -> dict:
    """Return knowledge graph statistics for an agent."""
    agent_dir = os.path.join(AGENTS_DIR, agent_id)
    if not os.path.isdir(agent_dir):
        return {"error": f"Agent not found: {agent_id}"}

    total_nodes = 0
    total_edges = 0
    auto_discovered_edges = 0
    edge_type_counts: dict[str, int] = {}
    entity_count = 0

    auto_edge_types = {"same_topic", "co_recalled", "depends_on", "contradicts", "extends"}

    for fname in os.listdir(agent_dir):
        if not fname.endswith(".md") or fname in _QMD_IGNORE_FILES:
            continue
        if fname.startswith("ingest-"):
            continue
        total_nodes += 1
        fpath = os.path.join(agent_dir, fname)
        try:
            with open(fpath, "r") as f:
                raw = f.read(2000)
            # Count edges (related entries)
            rel_files = re.findall(r'file:\s*(\S+\.md)', raw)
            rel_types = re.findall(r'type:\s*(\w+)', raw)
            for i, _ in enumerate(rel_files):
                total_edges += 1
                rtype = rel_types[i] if i < len(rel_types) else "references"
                edge_type_counts[rtype] = edge_type_counts.get(rtype, 0) + 1
                if rtype in auto_edge_types:
                    auto_discovered_edges += 1
        except Exception:
            continue

    # Count entities from entity index
    _ensure_entity_index(agent_id)
    with _entity_index_lock:
        idx = _entity_indices.get(agent_id, {})
        entity_count = len(idx)

    return {
        "agent": agent_id,
        "total_nodes": total_nodes,
        "total_edges": total_edges,
        "auto_discovered_edges": auto_discovered_edges,
        "entity_count": entity_count,
        "edge_types": edge_type_counts,
    }


_thread_local = threading.local()


class _MempalaceActivity:
    """Thread-safe tracker of in-flight MemPalace store/retrieve operations.
    Animates the palace icon in the web UI. TTL-based so a stalled pulse
    doesn't stick forever."""
    def __init__(self):
        self._lock = threading.Lock()
        self._store_inflight = 0
        self._retrieve_inflight = 0
        self._last_store_ts = 0.0
        self._last_retrieve_ts = 0.0
        self._pulse_ttl = 1.5  # seconds; keeps icon lit briefly after a fast op

    def store_begin(self):
        with self._lock:
            self._store_inflight += 1
            self._last_store_ts = time.time()

    def store_end(self):
        with self._lock:
            if self._store_inflight > 0:
                self._store_inflight -= 1
            self._last_store_ts = time.time()

    def retrieve_begin(self):
        with self._lock:
            self._retrieve_inflight += 1
            self._last_retrieve_ts = time.time()

    def retrieve_end(self):
        with self._lock:
            if self._retrieve_inflight > 0:
                self._retrieve_inflight -= 1
            self._last_retrieve_ts = time.time()

    def snapshot(self):
        with self._lock:
            now = time.time()
            return {
                "store_active": self._store_inflight > 0 or (now - self._last_store_ts) < self._pulse_ttl,
                "retrieve_active": self._retrieve_inflight > 0 or (now - self._last_retrieve_ts) < self._pulse_ttl,
                "last_store_ts": self._last_store_ts,
                "last_retrieve_ts": self._last_retrieve_ts,
                "store_inflight": self._store_inflight,
                "retrieve_inflight": self._retrieve_inflight,
            }


mempalace_activity = _MempalaceActivity()


MAX_DELEGATE_TOOL_ROUNDS = 10  # Limit for delegated/scheduled tasks (timeout is the real safety net)


_MEMORY_TOOL_NAMES = {"memory_store", "memory_recall", "memory_delete", "memory_shared"}
