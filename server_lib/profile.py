# Extracted from server.py — user profile generation and sync
import datetime
import json
import os
import shutil
import sqlite3
import threading

import brain as engine
from server_lib import auth as _auth_mod
from server_lib.db import _db_conn, _user_wing

# ── User profile storage (module level) ────────────────────────────
# The auto-maintained "Memory from chat history" feature. One Markdown file
# per user under agents/main/user_profiles/<uid>.md, mirrored as per-section
# drawers in MemPalace (wing=<uid>--main, room=user_profile) so retrieval
# works the usual way. The file is the source of truth; MemPalace is
# rewritten from the file after every successful save.
#
# Lives at module level (not inside main()) because both the HTTP handler
# methods and the in-main daemon need to call these helpers.

_USER_PROFILE_SECTIONS = (
    "Work context",
    "Personal context",
    "Top of mind",
    "Recent months",
    "Earlier context",
    "Long-term background",
)

def _user_profile_dir() -> str:
    d = os.path.join(engine.AGENTS_DIR, "main", "user_profiles")
    os.makedirs(d, exist_ok=True)
    return d

def _user_profile_path(uid: str) -> str:
    # Defensive sanitize: uid is bcrypt-hex from auth (uuid4().hex[:12]) so
    # there are no path separators in practice. Strip just in case.
    safe = "".join(c for c in (uid or "") if c.isalnum() or c in "-_")
    return os.path.join(_user_profile_dir(), f"{safe}.md")

def _user_profile_history_dir(uid: str) -> str:
    safe = "".join(c for c in (uid or "") if c.isalnum() or c in "-_")
    d = os.path.join(_user_profile_dir(), f"{safe}.history")
    os.makedirs(d, exist_ok=True)
    return d

def _read_user_profile(uid: str) -> str:
    p = _user_profile_path(uid)
    try:
        with open(p, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return ""
    except OSError as e:
        print(f"[profile] read failed uid={uid}: {e}", flush=True)
        return ""

def _split_profile_sections(content: str) -> dict:
    """Parse a profile file into {section_title: body}. Sections are
    introduced by a level-2 heading (## Work context). Anything outside a
    recognized section goes under '_intro'."""
    out: dict = {}
    current = "_intro"
    buf: list = []
    for line in (content or "").splitlines():
        if line.startswith("## "):
            if buf:
                out[current] = "\n".join(buf).strip()
                buf = []
            current = line[3:].strip()
        else:
            buf.append(line)
    if buf:
        out[current] = "\n".join(buf).strip()
    return {k: v for k, v in out.items() if v}

def _purge_drawers_by_room_and_source(wing: str, room: str, source_prefix: str = "") -> int:
    """List drawers in (wing, room) and delete them. Returns count deleted.
    Idempotent.

    NOTE on source_prefix: tool_list_drawers' summary view does not include
    source_file, only content_preview + drawer_id, so we can't actually
    filter by prefix at list time. For the rooms we own (user_profile,
    user_daily_summary) the room itself is exclusively ours, so deleting
    everything in (wing, room) is the right behavior. The argument is kept
    for caller readability; an empty list result is still safe."""
    try:
        from mempalace.mcp_server import tool_list_drawers, tool_delete_drawer
    except ImportError:
        return 0
    deleted = 0
    while True:
        try:
            res = tool_list_drawers(wing=wing, room=room, limit=200, offset=0)
        except Exception as e:
            print(f"[profile-purge] list failed wing={wing} room={room}: {e}", flush=True)
            break
        # tool_list_drawers returns {drawers:[…], count, offset, limit}
        if isinstance(res, dict):
            rows = res.get("drawers") or []
        elif isinstance(res, list):
            rows = res
        else:
            rows = []
        if not rows:
            break
        for r in rows:
            did = (r.get("drawer_id") or r.get("id")) if isinstance(r, dict) else None
            if not did:
                continue
            try:
                tool_delete_drawer(drawer_id=did)
                deleted += 1
            except Exception as e:
                print(f"[profile-purge] delete failed id={did}: {e}", flush=True)
        # Re-list after deleting; if MemPalace pagination is offset-based
        # against a shrinking set, we'd skip rows. Fixed-offset 0 + delete-all
        # converges in O(rows/page) iterations.
        if len(rows) < 200:
            # Re-check whether we cleared everything (pagination edge case)
            try:
                check = tool_list_drawers(wing=wing, room=room, limit=1, offset=0)
                if isinstance(check, dict) and not check.get("drawers"):
                    break
                if isinstance(check, list) and not check:
                    break
            except Exception:
                break
    return deleted

def _purge_user_profile_drawers(uid: str) -> int:
    """Drop every drawer in (wing=user__<uid>, room=user_profile)."""
    return _purge_drawers_by_room_and_source(
        wing=_user_wing(uid),
        room="user_profile",
        source_prefix=f"user/{uid}#profile/",
    )

def _mirror_user_profile_to_mempalace(uid: str, content: str):
    """Rewrite the user_profile drawers from the current file content.
    Drops old drawers first so renamed/removed sections don't linger."""
    try:
        from mempalace.mcp_server import tool_add_drawer
    except ImportError:
        return
    try:
        _purge_user_profile_drawers(uid)
    except Exception:
        pass
    wing = _user_wing(uid)
    sections = _split_profile_sections(content)
    for title, body in sections.items():
        if title == "_intro" or not body:
            continue
        slug = "".join(c.lower() if c.isalnum() else "_" for c in title).strip("_")
        try:
            tool_add_drawer(
                wing=wing,
                room="user_profile",
                content=f"# {title}\n\n{body}"[:8000],
                source_file=f"user/{uid}#profile/{slug}",
                added_by="brain-user-profile",
            )
        except Exception as e:
            print(f"[profile] add_drawer {title!r} failed uid={uid}: {e}", flush=True)

def _write_user_profile_atomic(uid: str, content: str, *, source: str = "manual") -> dict:
    """Atomic write with versioned history. Returns {path, bytes, prior_kept}.
    `source` is logged ('manual', 'daemon', 'reset') for debugging only."""
    path = _user_profile_path(uid)
    prior_kept = False
    try:
        if os.path.isfile(path):
            stamp = datetime.datetime.now().strftime("%Y-%m-%dT%H%M%S")
            hist = os.path.join(_user_profile_history_dir(uid), f"{stamp}.md")
            n = 0
            while os.path.exists(hist):
                n += 1
                hist = os.path.join(_user_profile_history_dir(uid), f"{stamp}-{n}.md")
            shutil.copy2(path, hist)
            prior_kept = True
            # Cap history at 30 entries — disk-bounded but keeps recent rollback.
            try:
                entries = sorted(os.listdir(_user_profile_history_dir(uid)), reverse=True)
                for old in entries[30:]:
                    try:
                        os.remove(os.path.join(_user_profile_history_dir(uid), old))
                    except OSError:
                        pass
            except OSError:
                pass
    except Exception as e:
        print(f"[profile] history snapshot failed uid={uid}: {e}", flush=True)
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, path)
    except OSError as e:
        try:
            os.remove(tmp)
        except OSError:
            pass
        return {"error": f"write failed: {e}"}
    try:
        _mirror_user_profile_to_mempalace(uid, content)
    except Exception as e:
        print(f"[profile] mempalace mirror failed uid={uid}: {e}", flush=True)
    return {"path": path, "bytes": len(content.encode("utf-8")),
            "prior_kept": prior_kept, "source": source}

def _delete_user_profile(uid: str) -> dict:
    """Remove the profile file + the user_profile drawers from MemPalace.
    History dir is intentionally KEPT — that's the recovery path after a
    hasty 'Reset profile'."""
    path = _user_profile_path(uid)
    removed = False
    try:
        os.remove(path)
        removed = True
    except FileNotFoundError:
        pass
    except OSError as e:
        return {"error": f"delete failed: {e}"}
    try:
        _purge_user_profile_drawers(uid)
    except Exception as e:
        print(f"[profile] mempalace purge failed uid={uid}: {e}", flush=True)
    return {"removed": removed, "path": path}


# ── Profile generation (module level so HTTP handler + daemon share) ─

_PROFILE_SECTION_INSTRUCTIONS = (
    "Schema (use exactly these section headings, in this order; if a section "
    "has nothing real to say, write `_(none)_`):\n"
    "## Work context\n"
    "  Role, employer, professional responsibilities. Inferred only from "
    "  what the user actually said about their work.\n"
    "## Personal context\n"
    "  Location, languages, recurring personal interests, household, pets, "
    "  hobbies. No speculation.\n"
    "## Top of mind\n"
    "  What the user has been working on or thinking about in the last 1–2 "
    "  weeks. Specific projects, decisions, open questions.\n"
    "## Recent months\n"
    "  Activity from the last ~3 months that's beyond top-of-mind but still "
    "  fresh. Concrete projects and outcomes.\n"
    "## Earlier context\n"
    "  Older but still relevant. Move things here once they leave Recent months.\n"
    "## Long-term background\n"
    "  Durable identity facts, long-running interests, infrastructure, "
    "  values that surface across many chats.\n"
)

_PROFILE_SYSTEM_PROMPT = (
    "You maintain a user-context profile that an AI assistant reads at the "
    "start of every chat. Output ONLY the profile in Markdown, nothing else "
    "— no preface, no commentary, no JSON, no code fences.\n\n"
    + _PROFILE_SECTION_INSTRUCTIONS +
    "\nHARD RULES:\n"
    "- Never invent facts. If you don't have evidence, leave the section as "
    "  `_(none)_`.\n"
    "- Write in third person about the user (they / their / Alexander…).\n"
    "- Match the user's predominant language (German chats → German profile, "
    "  English chats → English).\n"
    "- Each section is 2–6 sentences max. No bullet lists unless they're a "
    "  natural list of items (places, tools, etc.).\n"
    "- Treat the existing profile (if any) as ground truth. New chat samples "
    "  ADD or DEMOTE facts; do not delete a fact unless a new chat clearly "
    "  contradicts it.\n"
    "- Demote staleness: things in 'Top of mind' that have no fresh evidence "
    "  in the new samples should move to 'Recent months'.\n"
    "- No timestamps. No 'as of <date>' markers. The profile is a snapshot.\n"
    "- Do not include personal data the user shared in passing as 'top of mind' "
    "  (e.g. one-off addresses, IDs, account numbers).\n"
)

def _profile_pick_model() -> str:
    """Prefer the configured refinement model (already proven to follow
    polish-style prompts on this install), fall back to cheapest enabled.
    GDPR auto-fallback applies on top via gdpr_pick_model_for_background."""
    try:
        tc = engine.get_tool_config()
        ref = (tc.get("refinement", {}) or {}).get("model", "")
        if ref and engine._models_config and ref in engine._models_config \
           and engine._models_config[ref].get("enabled", True):
            return ref
    except Exception:
        pass
    if engine._models_config:
        for mid, cfg in engine._models_config.items():
            if cfg.get("enabled", True) and "haiku" in mid.lower():
                return mid
        for mid, cfg in sorted(
            engine._models_config.items(),
            key=lambda x: x[1].get("cost_input", 999),
        ):
            if cfg.get("enabled", True):
                return mid
    try:
        import server as _srv
        return _srv.server_config.get("default_model", "")
    except Exception:
        return ""

def _gather_user_chat_samples(uid: str, since_ts: float, *,
                               max_chats: int = 100,
                               sample_chars: int = 250) -> list:
    """Per-chat compact samples ('### title\\nuser: …\\nassistant: …'),
    most-recently-active first. Capped at max_chats."""
    out: list = []
    try:
        with _db_conn() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT id, title, last_active FROM sessions "
                "WHERE user_id = ? AND last_active >= ? "
                "AND (status = '' OR status IS NULL OR status = 'active') "
                "ORDER BY last_active DESC LIMIT ?",
                (uid, since_ts, max_chats),
            ).fetchall()
            for s in rows:
                sid = s["id"]
                title = (s["title"] or "(untitled)").strip()
                first_user = conn.execute(
                    "SELECT content FROM messages WHERE session_id = ? "
                    "AND role = 'user' ORDER BY id ASC LIMIT 1",
                    (sid,),
                ).fetchone()
                last_asst = conn.execute(
                    "SELECT content FROM messages WHERE session_id = ? "
                    "AND role = 'assistant' AND (compacted = 0 OR compacted IS NULL) "
                    "ORDER BY id DESC LIMIT 1",
                    (sid,),
                ).fetchone()
                def _extract(row, cap):
                    if not row:
                        return ""
                    c = row[0]
                    if isinstance(c, (bytes, bytearray)):
                        try:
                            c = c.decode("utf-8", errors="replace")
                        except Exception:
                            c = str(c)
                    if not isinstance(c, str):
                        try:
                            obj = json.loads(c) if isinstance(c, (bytes, str)) else c
                            if isinstance(obj, list):
                                parts = [b.get("text", "") for b in obj
                                         if isinstance(b, dict) and b.get("type") == "text"]
                                c = " ".join(p for p in parts if p)
                            else:
                                c = str(obj)
                        except Exception:
                            c = str(c)
                    return (c or "").strip().replace("\n", " ")[:cap]
                fu = _extract(first_user, sample_chars)
                la = _extract(last_asst, sample_chars)
                if not fu and not la:
                    continue
                out.append(f"### {title}\nuser: {fu}\nassistant: {la}")
    except Exception as e:
        print(f"[profile] chat-sample gather uid={uid} failed: {e}", flush=True)
    return out

def _user_profile_run_llm(uid: str, prior_profile: str, samples: list,
                          greeting_name: str = ""):
    """Call the LLM to (re)build the profile. Returns the new content, or
    None on hard failure (caller falls back to prior_profile).

    The daily build is never caveman-compressed — the on-disk profile is
    always clean prose. Compression happens at read-time in send_message
    when the preamble is injected (driven by profile_preamble_caveman).
    """
    if not samples:
        return None
    model = _profile_pick_model()
    if not model:
        print(f"[profile] no model available", flush=True)
        return None
    # GDPR auto-fallback to local on PII findings; raises GDPRBlockedError
    # in hard-block mode without a usable local route — fail closed.
    try:
        model = engine.gdpr_pick_model_for_background(
            model, samples + [prior_profile], purpose="user_profile",
        )
    except Exception as e:
        print(f"[profile] GDPR gate refused uid={uid}: {e}", flush=True)
        return None
    if not model:
        return None
    joined_samples = "\n\n".join(samples)
    if len(joined_samples) > 12000:
        joined_samples = joined_samples[:12000] + "\n\n[…older chats truncated]"
    if prior_profile.strip():
        user_msg = (
            "EXISTING PROFILE (treat as ground truth, edit in place):\n"
            f"```\n{prior_profile.strip()}\n```\n\n"
            "NEW CHAT SAMPLES SINCE LAST UPDATE:\n"
            f"{joined_samples}\n\n"
            "Update the profile. Move stale 'Top of mind' items to "
            "'Recent months' if no fresh evidence appears. Add new facts "
            "from the new samples. Output the COMPLETE new profile."
        )
    else:
        user_msg = (
            "Build the profile from scratch. The user's preferred name "
            f"is {greeting_name or 'unknown'}.\n\n"
            "CHAT SAMPLES (most recent first):\n"
            f"{joined_samples}\n\n"
            "Output the COMPLETE profile using the schema above."
        )
    try:
        # Use the same delegate path as _generate_chat_summary (the existing
        # in-tree pattern for background LLM calls). Returns the assistant's
        # text or a "Delegation error: …" string we filter out.
        # current_agent must be an AgentConfig object, not just the agent id.
        engine._thread_local.current_agent = engine.AgentConfig("main")
        engine._thread_local.current_user_id = ""
        engine._thread_local.memory_store = None
        result = engine._run_delegate(
            messages=[{"role": "user", "content": user_msg}],
            model=model,
            system_prompt=_PROFILE_SYSTEM_PROMPT,
            memory_store=None,
            inference_params={"max_tokens": 2000, "temperature": 0.2},
            tools=False,
        )
        if not result:
            return None
        if isinstance(result, str) and (
            result.startswith("Delegation error") or
            "There's an issue with the selected model" in result
        ):
            print(f"[profile] delegate returned error: {result[:200]}", flush=True)
            return None
        return result.strip()
    except Exception as e:
        print(f"[profile] LLM call uid={uid} failed: {type(e).__name__}: {e}", flush=True)
        return None
    finally:
        engine._thread_local.current_agent = None
        engine._thread_local.memory_store = None

def _profile_run_synchronous(user: dict, since_ts: float, now: float):
    """Run a profile update for one user. Used by the daemon and by the
    on-demand HTTP endpoint. Returns a status dict."""
    uid = user["id"]
    if not since_ts:
        since_ts = now - 90 * 24 * 3600
    # Always pull last 90 days of samples — even an incremental update needs
    # enough context to demote stale items.
    samples = _gather_user_chat_samples(uid, since_ts=now - 90 * 24 * 3600)
    if not samples:
        _auth_mod.AuthDB.set_daily_summary_cursor(uid, now, "no_activity", "")
        return {"status": "no_activity"}
    prior = _read_user_profile(uid)
    prefs = user.get("preferences") or {}
    greeting = (prefs.get("greeting_name") or "").strip() \
               or (user.get("display_name") or "").strip() \
               or (user.get("username") or "")
    new_profile = _user_profile_run_llm(uid, prior, samples,
                                         greeting_name=greeting)
    if not new_profile:
        _auth_mod.AuthDB.set_daily_summary_cursor(uid, now, "error:llm_no_output", "")
        return {"status": "error", "error": "LLM produced no output"}
    if new_profile.startswith("```"):
        new_profile = new_profile.lstrip("`").lstrip("markdown").lstrip("md").lstrip()
        if new_profile.endswith("```"):
            new_profile = new_profile[: -3].rstrip()
    write_res = _write_user_profile_atomic(uid, new_profile, source="daemon")
    if write_res.get("error"):
        _auth_mod.AuthDB.set_daily_summary_cursor(uid, now, f"error:{write_res['error']}"[:80], "")
        return {"status": "error", "error": write_res["error"]}
    _auth_mod.AuthDB.set_daily_summary_cursor(uid, now, "filed", write_res.get("path", ""))
    print(f"[profile] uid={uid} updated ({write_res.get('bytes')} bytes, "
          f"{len(samples)} samples)", flush=True)
    return {"status": "filed", "path": write_res.get("path"),
            "bytes": write_res.get("bytes"), "samples": len(samples)}
