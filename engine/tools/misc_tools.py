# Miscellaneous tool bodies (extracted from brain.py, E4).
#
# A grab-bag of small, mostly-independent tools that don't form a cohesive
# cluster of their own:
#   - use_skill            — load a skill's instructions into context
#   - list_nodes           — list registered remote nodes (HTTP self-call)
#   - mcp_connect / mcp_disconnect / mcp_servers — runtime MCP client control
#   - get_artifact_detail  — read a worker artifact's raw_result
#   - web_fetch            — HTTP GET/POST with HTML→markdown + cache
#   - exa_search           — web search via Exa AI (cloud, API key)
#   - searxng_search       — web search via self-hosted SearXNG (no key)
#   - tool_passes_purpose / tool_is_enabled / tool_is_deferred — the tool
#     resolver PREDICATES (NOT in TOOL_DISPATCH; despite the `tool_` prefix
#     they are not agent-callable tools — they answer "is this tool allowed
#     for this call?"). Moved here for cohesion with the other small helpers.
#
# Pure relocation: JSON envelopes + error strings byte-identical to pre-E4.
#
# Seams:
#   - `_ok` / `_err` from engine.tool_exec.
#   - `get_request_context` from engine.context.
#   - brain runtime symbols (`_global_tool_*`, `get_tool_config`, `_web_cache`,
#     `_html_to_markdown`, `_mcp_manager`, `MCPManager`, `AGENTS_DIR`,
#     `_current_agent`) reached lazily via `import brain as _brain`. NO
#     top-level `import brain` (cycle).
#
# brain.py re-exports everything here via
# `from engine.tools.misc_tools import (...)`.

from __future__ import annotations

import json
import os
import re
import tempfile
import urllib.request
import urllib.error
import urllib.parse

from engine.context import get_request_context
from engine.tool_exec import _ok, _err


# ─── Tool resolver predicates (NOT TOOL_DISPATCH entries) ────────────────────

def tool_passes_purpose(name: str, purpose: str) -> bool:
    """A tool passes the purpose filter when:
      - its global purposes list is empty (= all purposes), OR
      - the call's purpose is in its purposes list.

    Purpose filter is global-only — agents cannot override it (the purpose
    of a call is a property of the call, not the agent).
    """
    import brain as _brain
    purposes = _brain._global_tool_purposes(name)
    if not purposes:
        return True
    return purpose in purposes


# Legacy aliases — deprecated, kept for callers that haven't migrated yet.
def tool_is_enabled(name: str) -> bool:
    import brain as _brain
    return _brain._global_tool_enabled(name)


def tool_is_deferred(name: str) -> bool:
    import brain as _brain
    return _brain._global_tool_deferred(name)


# ─── use_skill ───────────────────────────────────────────────────────────────

def tool_use_skill(args: dict) -> str:
    """Load a skill's instructions into context."""
    import brain as _brain
    skill_name = args.get("skill", "")
    if not skill_name:
        return _err("use_skill: skill name is required")
    agent = get_request_context().current_agent or _brain._current_agent
    if not agent:
        return _err("use_skill: no active agent")

    # Resolve the caller so per-user (shared) skills can be access-gated.
    _user = _current_user_for_skills()

    body = agent.load_skill(skill_name)
    if body is None:
        # Fall back to the caller's visible per-user skills (own + shared).
        body = agent.load_user_skill_body(skill_name, _user)
    if body is None:
        available = [s.get("slug", s["name"]) for s in agent.list_skills()]
        available += [s.get("slug", s["name"]) for s in agent.list_user_skills(_user)]
        return _err(f"use_skill: skill '{skill_name}' not found. Available: {', '.join(available) or 'none'}")

    out = {"skill": skill_name, "instructions": body}
    # Surface the skill's companion pages with their EXACT absolute paths so the
    # model reads them via read_document instead of guessing relative paths
    # (the skill text references e.g. "06-user-manual.md" but the skill dir is
    # NOT the working dir — guessed relative reads fail and waste tool rounds).
    try:
        for sk in (agent.list_skills() + agent.list_user_skills(_user)):
            if sk.get("slug") == skill_name or sk.get("name") == skill_name:
                skill_md = sk.get("path") or ""
                skill_dir = os.path.dirname(skill_md)
                if skill_dir and os.path.isdir(skill_dir):
                    pages = {}
                    for fn in sorted(os.listdir(skill_dir)):
                        if fn.endswith(".md") and fn != "SKILL.md":
                            pages[fn] = os.path.join(skill_dir, fn)
                    if pages:
                        out["companion_pages"] = pages
                        out["read_pages_with"] = (
                            "Use read_document with one of these ABSOLUTE paths "
                            "to open a companion page — do not guess relative paths."
                        )
                break
    except OSError:
        pass
    return _ok(out)


def _current_user_for_skills():
    """Resolve the caller's user dict from the request context, for ACL-gating
    per-user skills. Returns None for anonymous/background turns."""
    try:
        uid = get_request_context().current_user_id or ""
        if not uid:
            return None
        from server_lib.auth import AuthDB as _AuthDB
        return _AuthDB.get_user(uid)
    except Exception:
        return None


def _score_skill(task_terms: set, skill: dict) -> int:
    """Keyword-overlap score of a task against one skill's name+description+slug.
    Deterministic, no embeddings — a term appearing in the skill's text scores 1,
    a term in the name/slug scores an extra point (title matches weigh more)."""
    name = (skill.get("name") or "").lower()
    slug = (skill.get("slug") or "").lower()
    desc = (skill.get("description") or "").lower()
    hay = f"{name} {slug} {desc}"
    strong = f"{name} {slug}"
    score = 0
    for t in task_terms:
        if t in hay:
            score += 1
            if t in strong:
                score += 1
    return score


def tool_find_skills(args: dict) -> str:
    """Search the caller's visible per-user skills for ones matching a task."""
    import brain as _brain
    task = (args.get("task") or "").strip()
    if not task:
        return _err("find_skills: task is required")
    agent = get_request_context().current_agent or _brain._current_agent
    if not agent:
        return _err("find_skills: no active agent")
    user = _current_user_for_skills()
    skills = agent.list_user_skills(user)  # already ACL-filtered (own + shared)
    if not skills:
        return _ok({"matches": [], "note": "Der Nutzer hat (noch) keine passenden "
                                            "persönlichen Skills — normal weitermachen."})
    by_slug = {s.get("slug", ""): s for s in skills}

    # (1) Semantic recall over EVERY skill visible to the user (own + shared) —
    # this catches cross-language / paraphrased tasks ("check a passport" ↔
    # "Ausweisprüfung") that keyword overlap misses. A shared skill's drawer
    # lives in its OWNER's wing, so we pass the visible {owner: {slugs}} map and
    # _search_skills_semantic queries across those wings, keeping only visible
    # (owner, slug) pairs. Best-effort: empty on any store failure.
    sem_score: dict[str, float] = {}
    if user:
        visible: dict[str, set] = {}
        for s in skills:
            ow = s.get("owner_user_id") or ""
            sl = s.get("slug") or ""
            if ow and sl:
                visible.setdefault(ow, set()).add(sl)
        try:
            for hit in _brain._search_skills_semantic(task, visible, limit=8):
                sl = hit.get("slug", "")
                if sl in by_slug:
                    # A slug is agent-global unique, so slug alone keys the score.
                    sem_score[sl] = max(sem_score.get(sl, 0.0), hit.get("score", 0.0))
        except Exception:
            pass

    # (2) Keyword overlap over the FULL visible set (own + shared).
    terms = {t for t in re.split(r"[^a-zA-Z0-9äöüßÄÖÜ]+", task.lower())
             if len(t) >= 3}
    kw_score = {sl: _score_skill(terms, s) for sl, s in by_slug.items()}

    # Merge: a skill ranks if EITHER signal fires. Sort by semantic similarity
    # first (0–1), then keyword overlap, then slug for stability.
    scored = []
    for sl, s in by_slug.items():
        sem = sem_score.get(sl, 0.0)
        kw = kw_score.get(sl, 0)
        if sem > 0 or kw > 0:
            scored.append((sem, kw, s))
    scored.sort(key=lambda x: (-x[0], -x[1], x[2].get("slug", "")))

    matches = [{
        "slug": s.get("slug", ""),
        "name": s.get("name", ""),
        "description": s.get("description", ""),
        "visibility": s.get("visibility", ""),
        "score": round(sem, 3) if sem > 0 else 0,
        "matched_via": ("semantic" if sem > 0 else "keyword"),
    } for sem, kw, s in scored[:8]]
    if not matches:
        # Neither signal fired — surface the full (small) visible set so the
        # model can still pick if the task wording just didn't overlap.
        matches = [{
            "slug": s.get("slug", ""), "name": s.get("name", ""),
            "description": s.get("description", ""),
            "visibility": s.get("visibility", ""), "score": 0,
            "matched_via": "listed",
        } for s in skills[:8]]
    return _ok({"matches": matches,
                "load_with": "Call use_skill(skill=\"<slug>\") to load a match's "
                             "full instructions before doing the task."})


# ─── Remote nodes ─────────────────────────────────────────────────────────────

def tool_list_nodes(args: dict) -> str:
    """List all registered remote nodes."""
    try:
        req = urllib.request.Request("http://127.0.0.1:8420/v1/nodes", method="GET")
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        nodes = data.get("nodes", [])
        if not nodes:
            return _ok({"nodes": [], "count": 0, "message": "No nodes registered"})
        return _ok({"nodes": nodes, "count": len(nodes)})
    except Exception as e:
        return _err(f"Failed to list nodes: {e}")


# ─── think ─────────────────────────────────────────────────────────────────

def tool_think(args: dict) -> str:
    """No-op scratchpad (Anthropic "think" tool).

    Obtains no information and changes nothing — the `thought` is simply
    appended to the wire history (as this tool_call/tool_result pair), so the
    model can re-read it on later rounds. The persistence IS the value: unlike
    a model's native reasoning field (generated per round, then discarded), a
    thought recorded here survives across tool rounds. See the think-tool
    handover for why this helps weak-tool-follow-through local models on long,
    policy-heavy tool chains.
    """
    thought = (args.get("thought") or "").strip()
    if not thought:
        return _err("`thought` is required and must be non-empty.")
    return _ok({"logged": True})


# ─── sequential_thinking ─────────────────────────────────────────────────────

def _coerce_bool(v) -> bool:
    """Zod-style boolean coercion — the upstream MCP server needs it because
    even capable models emit the string "false"/"true" for boolean fields."""
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        s = v.strip().lower()
        if s == "true":
            return True
        if s == "false":
            return False
    return bool(v)


def _coerce_int(v, default=None):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def tool_sequential_thinking(args: dict) -> str:
    """Full Anthropic-style sequential-thinking scratchpad (upstream MCP parity).

    Like `think` but with the upstream server's bookkeeping: numbered thoughts,
    a running total, an explicit "more thoughts needed?" flag, and revision /
    branch tracking. Obtains no information and changes nothing — the value is
    the persisted, structured chain in the wire history.

    Differs from the upstream MCP server in ONE deliberate way: upstream keeps
    thoughtHistory / branches as PROCESS-GLOBAL state (one shared log for every
    caller) — a bug in a multi-user server. Here the state lives per-request in
    RequestContext._dynamic (keyed under `_seqthink_state`), so two sessions
    never share a thought log. Returns the same status JSON shape upstream does.
    """
    thought = (args.get("thought") or "").strip()
    if not thought:
        return _err("`thought` is required and must be non-empty.")

    thought_number = _coerce_int(args.get("thoughtNumber", args.get("thought_number")), 1) or 1
    total_thoughts = _coerce_int(args.get("totalThoughts", args.get("total_thoughts")), 1) or 1
    next_needed = _coerce_bool(args.get("nextThoughtNeeded", args.get("next_thought_needed")))
    is_revision = _coerce_bool(args.get("isRevision", args.get("is_revision")))
    branch_from = _coerce_int(args.get("branchFromThought", args.get("branch_from_thought")), None)
    branch_id = args.get("branchId", args.get("branch_id")) or None

    # Adjust total up if the model overshot its own estimate (upstream behavior).
    if thought_number > total_thoughts:
        total_thoughts = thought_number

    # Per-request state (NOT process-global — the upstream multi-user footgun).
    ctx = get_request_context()
    state = ctx._dynamic.get("_seqthink_state")
    if state is None:
        state = {"history": [], "branches": {}}
        ctx._dynamic["_seqthink_state"] = state
    state["history"].append({
        "thought": thought, "thoughtNumber": thought_number,
        "totalThoughts": total_thoughts, "isRevision": is_revision,
        "branchId": branch_id,
    })
    if branch_from is not None and branch_id:
        state["branches"].setdefault(branch_id, []).append(thought_number)

    return _ok({
        "thoughtNumber": thought_number,
        "totalThoughts": total_thoughts,
        "nextThoughtNeeded": next_needed,
        "branches": list(state["branches"].keys()),
        "thoughtHistoryLength": len(state["history"]),
    })


# ─── calibrate ───────────────────────────────────────────────────────────────

def _coerce_str_list(v) -> list[str]:
    """Tolerant list-of-strings coercion for weak tool-callers: a bare string
    becomes a one-element list; non-string items are stringified; None → []."""
    if v is None:
        return []
    if isinstance(v, str):
        return [v] if v.strip() else []
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    return [str(v)]


def tool_calibrate(args: dict) -> str:
    """No-op calibration scratchpad (trimmed metacognitive-monitoring port).

    Like `think`, obtains nothing and changes nothing — the value is (a) the
    forced fact/inference/speculation/gaps split persisting in the wire
    history and (b) the model following its own recommendation. The one piece
    of real logic is a DETERMINISTIC consistency check (code, not model): an
    'answer' recommendation with zero facts is flagged back at the model so
    the final answer doesn't confidently guess.
    """
    task = (args.get("task") or "").strip()
    if not task:
        return _err("`task` is required and must be non-empty.")
    facts = _coerce_str_list(args.get("facts"))
    inferences = _coerce_str_list(args.get("inferences"))
    speculation = _coerce_str_list(args.get("speculation"))
    gaps = _coerce_str_list(args.get("gaps"))
    try:
        confidence = max(0.0, min(1.0, float(args.get("confidence", 0.0))))
    except (TypeError, ValueError):
        confidence = 0.0
    rec = (args.get("recommendation") or "").strip().lower()
    if rec not in ("answer", "answer_with_caveats", "refuse"):
        return _err("`recommendation` must be one of: answer, answer_with_caveats, refuse.")

    consistency = "ok"
    if rec == "answer" and not facts:
        consistency = ("inconsistent: recommendation is 'answer' but facts is empty — "
                       "the documents do not support an answer; refuse or state the gaps.")
    elif rec == "answer" and gaps:
        consistency = ("note: recommendation is 'answer' but gaps are listed — "
                       "consider answer_with_caveats and name the gaps.")

    return _ok({
        "logged": True,
        "facts": len(facts),
        "inferences": len(inferences),
        "speculation": len(speculation),
        "gaps": len(gaps),
        "confidence": confidence,
        "recommendation": rec,
        "consistency": consistency,
    })


# ─── MCP client tools ─────────────────────────────────────────────────────────

def tool_mcp_connect(args: dict) -> str:
    """Connect to an MCP server at runtime."""
    import brain as _brain
    url = args.get("url", "")
    name = args.get("name", "")
    transport = args.get("transport", "sse")
    persist = args.get("persist", False)

    if not url or not name:
        return _err("Both 'url' and 'name' are required")

    # Use thread-local MCP manager if available, otherwise global
    mcp = get_request_context().mcp_manager or _brain._mcp_manager
    if not mcp:
        mcp = _brain.MCPManager()
        get_request_context().mcp_manager = mcp

    result = mcp.connect_runtime(url, name, transport)
    if result.get("error"):
        return _err(result["error"])

    # Persist to mcp.json if requested
    if persist:
        agent = get_request_context().current_agent or _brain._current_agent
        agent_id = agent.agent_id if agent else "main"
        mcp_json_path = os.path.join(_brain.AGENTS_DIR, agent_id, "mcp.json")
        try:
            existing = {}
            if os.path.exists(mcp_json_path):
                with open(mcp_json_path, "r") as f:
                    existing = json.load(f)
            if transport == "stdio":
                parts = url.split()
                existing[name] = {"transport": "stdio", "command": parts[0], "args": parts[1:] if len(parts) > 1 else []}
            else:
                existing[name] = {"transport": "sse", "url": url}
            with open(mcp_json_path, "w") as f:
                json.dump(existing, f, indent=2)
            result["persisted"] = True
        except Exception as e:
            result["persist_error"] = str(e)

    return _ok(result)


def tool_mcp_disconnect(args: dict) -> str:
    """Disconnect from an MCP server."""
    import brain as _brain
    name = args.get("name", "")
    if not name:
        return _err("'name' is required")

    mcp = get_request_context().mcp_manager or _brain._mcp_manager
    if not mcp:
        return _err("No MCP manager available")

    result = mcp.disconnect_runtime(name)
    if result.get("error"):
        return _err(result["error"])
    return _ok(result)


def tool_mcp_servers(args: dict) -> str:
    """List all connected MCP servers."""
    import brain as _brain
    mcp = get_request_context().mcp_manager or _brain._mcp_manager
    if not mcp:
        return _ok({"servers": [], "count": 0})
    servers = mcp.list_servers()
    return _ok({"servers": servers, "count": len(servers)})


# ─── Artifact detail ──────────────────────────────────────────────────────────

def tool_get_artifact_detail(args: dict) -> str:
    """Retrieve raw content from a worker artifact."""
    import brain as _brain
    artifact_id = args.get("artifact_id", "")
    query = args.get("query", "")
    offset = args.get("offset", 0)
    limit = args.get("limit", 16384)
    if not artifact_id:
        return _err("artifact_id is required")

    agent = get_request_context().current_agent or _brain._current_agent
    agent_id = agent.agent_id if agent else "main"
    artifacts_root = os.path.join(_brain.AGENTS_DIR, agent_id, "artifacts")

    # Search for the artifact file
    artifact_path = None
    if os.path.exists(artifacts_root):
        for root, dirs, files in os.walk(artifacts_root):
            if artifact_id in files:
                artifact_path = os.path.join(root, artifact_id)
                break
    if not artifact_path or not os.path.exists(artifact_path):
        return _err(f"Artifact '{artifact_id}' not found")

    try:
        with open(artifact_path) as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        return _err(f"Failed to read artifact: {e}")

    raw = data.get("raw_result", "")

    if query:
        lines = raw.splitlines()
        matches = []
        for i, line in enumerate(lines):
            if query.lower() in line.lower():
                start = max(0, i - 2)
                end = min(len(lines), i + 3)
                for j in range(start, end):
                    if j not in [m[0] for m in matches]:
                        matches.append((j, lines[j]))
        if matches:
            raw = "\n".join(f"{m[0]+1}: {m[1]}" for m in matches)
        else:
            raw = f"(no matches for '{query}' in {len(lines)} lines)"

    if offset:
        raw = raw[offset:]
    if len(raw) > limit:
        raw = raw[:limit] + f"\n\n[... truncated at {limit} chars, {len(data.get('raw_result',''))} total]"

    return _ok({
        "artifact_id": artifact_id,
        "tool": data.get("tool", ""),
        "content": raw,
        "total_size": data.get("size_bytes", len(raw)),
    })


# ─── web_fetch ────────────────────────────────────────────────────────────────

def _github_raw_repo_path(url: str) -> str:
    """raw.githubusercontent.com/<owner>/<repo>/<ref>/<path> → '<path>', else ''.
    Also handles github.com/<owner>/<repo>/(raw|blob)/<ref>/<path>."""
    import re
    m = re.match(r"https?://raw\.githubusercontent\.com/[^/]+/[^/]+/[^/]+/(.+)$", url)
    if m:
        return m.group(1).split("?")[0].split("#")[0]
    m = re.match(r"https?://github\.com/[^/]+/[^/]+/(?:raw|blob)/[^/]+/(.+)$", url)
    if m:
        return m.group(1).split("?")[0].split("#")[0]
    return ""


def _block_end_line(lines: list, start: int, max_span: int = 200) -> int:
    """If `lines[start]` opens an indentation-based code block (a def/class-style
    header ending in ':' at indent N), return the index just past the last line
    of its body (the run of lines indented deeper than N, blank lines tolerated).
    Otherwise return `start` (no extension). Bounded by `max_span` so a header
    with a huge body can't swallow the whole file. Indentation-based — covers
    Python/YAML-like sources; brace languages fall through to the fixed window."""
    header = lines[start] if 0 <= start < len(lines) else ""
    stripped = header.strip()
    if not stripped.endswith(":"):
        return start
    base_indent = len(header) - len(header.lstrip())
    end = start
    for j in range(start + 1, min(len(lines), start + 1 + max_span)):
        ln = lines[j]
        if not ln.strip():          # blank line — part of the block, keep scanning
            continue
        indent = len(ln) - len(ln.lstrip())
        if indent <= base_indent:   # dedented back to/under the header — block done
            break
        end = j
    return end


def _trim_to_brain_code_regions(text: str, chunks: list, ctx_lines: int = 8):
    """Return only the regions of `text` that contain the matched brain_code
    chunks (each ± ctx_lines of surrounding context), joined with gap markers.
    None when no chunk could be located (caller keeps the full text — never
    silently drops content)."""
    # Small-file shortcut: trimming a small source file buys nothing — return
    # None so the caller hands the model the whole file (full context, no gap
    # noise). Threshold matched to read_document's region path (~6 KB).
    if len(text) <= 6000:
        return None
    lines = text.splitlines()
    keep = set()
    found = 0
    for chunk in chunks:
        # Locate the chunk by its first non-trivial line (fingerprint), since
        # chunk boundaries may not align to line starts in the fetched file.
        anchor = next((ln.strip() for ln in chunk.splitlines() if len(ln.strip()) >= 12), "")
        if not anchor:
            continue
        chunk_len = max(1, len(chunk.splitlines()))
        for i, ln in enumerate(lines):
            if anchor in ln:
                found += 1
                lo = max(0, i - ctx_lines)
                # Extend the window to the END of the code block the anchor opens
                # so a longer method/class body isn't clipped mid-definition. When
                # the anchor line is a def/class header, keep through the last line
                # more-indented than the header (the body), then add context. This
                # is the fix for the "trim cuts the tail of a longer matched
                # method" failure — a fixed chunk_len window ended inside the body.
                block_end = _block_end_line(lines, i)
                hi = min(len(lines), max(i + chunk_len, block_end) + ctx_lines)
                keep.update(range(lo, hi))
                break
    if not found or not keep:
        return None
    out, prev = [], None
    for i in sorted(keep):
        if prev is not None and i > prev + 1:
            out.append(f"\n[... {i - prev - 1} line(s) omitted — not in matched region ...]\n")
        out.append(lines[i])
        prev = i
    trimmed = "\n".join(out)
    # Worth-it gate: many matched chunks (or wide context) make the kept regions
    # add up to ~the whole file — trimming then saves little once you count the
    # gap markers. Only trim when meaningfully smaller; else None (return full).
    if len(trimmed) >= 0.75 * len(text):
        return None
    return trimmed


# ─── Academic-source inlining ────────────────────────────────────────────────
#
# Academic sites hide the real paper behind a landing/abstract page at a
# DIFFERENT URL than the full-text PDF (arxiv /abs vs /pdf; PubMed abstract vs
# the free PMC full text; a publisher HTML wrapper vs its `.full.pdf`). A naive
# fetch of the URL the user pastes returns the wrapper — cookie banners, a
# paywall teaser, "Download" buttons — not the science. `_academic_pdf_url`
# rewrites a known-academic landing URL to its full-text PDF location so
# web_fetch returns the actual paper. Pure URL routing: the rewritten PDF then
# flows through the SAME doc_convert pipeline every other PDF read uses (fitz +
# pdfplumber, OCR fallback) — strictly better than a bare text decode, which on
# PDF bytes returns garbage. Returns None when the URL isn't a recognised
# academic landing page (web_fetch then proceeds exactly as before).

# (host-suffix regex, rewrite fn url->url-or-None). First match wins. Each fn
# returns the full-text PDF URL, or None when the specific URL shape doesn't
# match (e.g. an arxiv listing page, not an /abs/ page) so we fall through.
def _arxiv_pdf(u: str):
    m = re.search(r"arxiv\.org/(?:abs|pdf)/([^?#/]+?)(?:\.pdf)?(?:[?#].*)?$", u)
    return f"https://arxiv.org/pdf/{m.group(1)}" if m else None

def _biorxiv_pdf(u: str):
    # biorxiv/medrxiv content URL → append .full.pdf (idempotent)
    m = re.search(r"((?:bio|med)rxiv\.org/content/[^?#]+?)(?:\.full(?:\.pdf)?)?(?:[?#].*)?$", u)
    return f"https://www.{m.group(1)}.full.pdf" if m and "/content/" in u else None

def _pmc_pdf(u: str):
    m = re.search(r"(?:ncbi\.nlm\.nih\.gov/pmc|pmc\.ncbi\.nlm\.nih\.gov)/articles/(PMC\d+)", u)
    return f"https://pmc.ncbi.nlm.nih.gov/articles/{m.group(1)}/pdf/" if m else None

_ACADEMIC_REWRITES = [
    (r"(^|\.)arxiv\.org$", _arxiv_pdf),
    (r"(^|\.)(bio|med)rxiv\.org$", _biorxiv_pdf),
    (r"(^|\.)(ncbi\.nlm\.nih\.gov|pmc\.ncbi\.nlm\.nih\.gov)$", _pmc_pdf),
]


def _academic_pdf_url(url: str):
    """If `url` is a known academic landing/abstract page, return the URL of its
    full-text PDF; else None. Host-matched so a random page that merely contains
    'arxiv.org' in a query string isn't rewritten."""
    try:
        host = (urllib.parse.urlparse(url).hostname or "").lower()
    except ValueError:
        return None
    if not host:
        return None
    for host_re, fn in _ACADEMIC_REWRITES:
        if re.search(host_re, host):
            try:
                return fn(url)
            except Exception:
                return None
    return None


# content-type → file extension for the binary/document types we can ingest.
# Mirrors doc_convert.SUPPORTED_EXTS (documents) + the image types the vision
# describer handles. HTML/text/json/xml are deliberately ABSENT — those keep the
# existing text-decode + markitdown path untouched.
_CTYPE_TO_EXT = {
    "application/pdf": ".pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.ms-excel.sheet.macroenabled.12": ".xlsm",
    "application/vnd.ms-excel.sheet.binary.macroenabled.12": ".xlsb",
    "application/vnd.ms-excel": ".xls",
    "application/msword": ".docx",
    "application/vnd.ms-powerpoint": ".pptx",
    "application/epub+zip": ".epub",
    "application/zip": ".zip",
    "message/rfc822": ".eml",
    "text/csv": ".csv",
    "text/tab-separated-values": ".tsv",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/bmp": ".bmp",
    "image/tiff": ".tiff",
}
_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tiff"}


def _binary_ext_for(url: str, content_type: str, raw: bytes) -> str | None:
    """Decide the file extension for a NON-HTML response we should ingest as a
    file rather than decode as text. Resolution order: URL path extension (most
    reliable for a direct `…/foo.pdf` link, even when the server mislabels the
    Content-Type as octet-stream) → Content-Type map → `%PDF` magic bytes.
    Returns a supported extension, or None when the response is plain
    text/HTML/JSON/etc. (caller keeps the existing text path)."""
    from engine import doc_convert
    ctype = (content_type or "").lower().split(";")[0].strip()
    # text/* and html/json/xml are NOT files — leave them on the text path.
    if ctype.startswith("text/") and ctype not in ("text/csv", "text/tab-separated-values"):
        return None
    if ctype in ("application/json", "application/xml", "application/xhtml+xml") or "html" in ctype:
        return None
    known = doc_convert.SUPPORTED_EXTS | _IMAGE_EXTS
    try:
        path = urllib.parse.urlparse(url).path
    except ValueError:
        path = ""
    url_ext = os.path.splitext(path)[1].lower()
    if url_ext in known:
        return url_ext
    ct_ext = _CTYPE_TO_EXT.get(ctype)
    if ct_ext:
        return ct_ext
    # Last resort: PDF magic bytes (covers octet-stream / missing Content-Type).
    if raw[:5].startswith(b"%PDF"):
        return ".pdf"
    return None


def _fetch_as_file_result(raw: bytes, ext: str, final_url: str, status: int,
                          max_length: int) -> dict | None:
    """Turn already-downloaded bytes into a web_fetch-shaped result by ingesting
    them like an uploaded file. Documents go through the shared doc_convert
    pipeline (fitz/pdfplumber + OCR — the SAME path as read_document); images go
    through the vision describer. Spills to a uniquely-named tempfile (doc_convert
    reads a path, not bytes). Returns None on any failure so the caller falls back
    to the raw text path (graceful degradation)."""
    from engine import doc_convert
    if ext in _IMAGE_EXTS:
        # Image → vision description (same helper the chat attachment path uses).
        import base64
        import brain as _brain
        media = next((k for k, v in _CTYPE_TO_EXT.items() if v == ext and k.startswith("image/")), "image/jpeg")
        try:
            text = _brain._describe_image_with_vision(
                base64.b64encode(raw).decode("ascii"), media,
                os.path.basename(urllib.parse.urlparse(final_url).path) or "image")
        except Exception:
            return None
        if not text or not text.strip():
            return None
        if len(text) > max_length:
            text = text[:max_length] + "\n... (truncated)"
        return {"url": final_url, "status": status, "length": len(text),
                "content": text, "fetch_method": "image"}
    # Document → doc_convert extraction.
    tmp_path = None
    try:
        fd, tmp_path = tempfile.mkstemp(suffix=ext, prefix="brain-webfetch-")
        with os.fdopen(fd, "wb") as fh:
            fh.write(raw)
        # _do_extract returns a 3-tuple (text, backend, error) — NOT a string.
        text, _backend, _err = doc_convert._do_extract(tmp_path, caps=False)
        if _err or not text or not text.strip():
            return None
        if len(text) > max_length:
            text = text[:max_length] + "\n... (truncated)"
        # Surface the ACTUAL extraction backend (pymupdf4llm / fitz/legacy /
        # markitdown / mistral-ocr / local-vision) as the fetch_method so the
        # chat view's badge shows HOW the PDF/doc was read — e.g. an OCR'd scan
        # vs a clean text-layer read. Prefix with `document:` so it stays
        # recognisable as a binary-doc fetch.
        _bk = (_backend or "").strip()
        method = f"document:{_bk}" if _bk else "document"
        return {"url": final_url, "status": status, "length": len(text),
                "content": text, "fetch_method": method}
    except Exception:
        return None
    finally:
        if tmp_path:
            try:
                os.remove(tmp_path)
            except OSError:
                pass


# ── Audio / YouTube → transcript (v9.307.0) ─────────────────────────────────
# Direct audio-file URLs and YouTube links become TRANSCRIPTS instead of
# garbage bytes / useless watch-page HTML. Both routes go through the shared
# STT pipeline (server_lib/translate/media.transcribe_and_translate — the same
# resolver + cost logging as the Übersetzen tab; default = local Whisper).
_AUDIO_CTYPES = {
    "audio/mpeg": ".mp3", "audio/mp3": ".mp3", "audio/mp4": ".m4a",
    "audio/x-m4a": ".m4a", "audio/aac": ".aac", "audio/wav": ".wav",
    "audio/x-wav": ".wav", "audio/ogg": ".ogg", "audio/flac": ".flac",
    "audio/opus": ".opus", "audio/webm": ".webm",
}
_AUDIO_EXTS = {".mp3", ".m4a", ".wav", ".ogg", ".flac", ".aac", ".opus"}


def _audio_ext_for(url: str, content_type: str) -> str | None:
    """Audio detection for the transcript branch: audio/* Content-Type first,
    else a clearly-audio URL extension. `.webm` only via Content-Type — the
    extension alone is ambiguous (it's also a video container)."""
    ctype = (content_type or "").lower().split(";")[0].strip()
    if ctype in _AUDIO_CTYPES:
        return _AUDIO_CTYPES[ctype]
    try:
        path = urllib.parse.urlparse(url).path
    except ValueError:
        path = ""
    ext = os.path.splitext(path)[1].lower()
    return ext if ext in _AUDIO_EXTS else None


def _transcript_result_from_file(path: str, final_url: str, max_length: int, *,
                                 title: str = "", method: str = "audio-transcript") -> dict | None:
    """Transcribe a downloaded audio/video file via the shared STT pipeline and
    shape it like a web_fetch result. None on failure (caller degrades)."""
    try:
        from server_lib.translate import media as _media
        res = _media.transcribe_and_translate(path, target_lang="")
    except Exception as e:
        print(f"[web_fetch] transcription failed for {final_url}: {e}", flush=True)
        return None
    text = (res.get("transcript") or "").strip()
    if not text:
        return None
    dur = float(res.get("duration_s") or 0)
    head = f"# {title}\n\n" if title else ""
    meta = (f"[Audio-Transkript · {int(dur // 60)}:{int(dur % 60):02d} min · "
            f"Sprache: {res.get('language') or '?'}]\n\n")
    text = head + meta + text
    if len(text) > max_length:
        text = text[:max_length] + "\n... (truncated)"
    return {"url": final_url, "status": 200, "length": len(text),
            "content": text, "fetch_method": method}


_YT_URL_RE = re.compile(
    r"^https?://(?:www\.|m\.)?(?:youtube\.com/(?:watch\?|shorts/|live/)|youtu\.be/)",
    re.IGNORECASE)


def _fetch_youtube_transcript(url: str, max_length: int) -> dict:
    """YouTube URL → yt-dlp bestaudio download → STT transcript. Returns a
    web_fetch-shaped dict, or {'error': …}. Bounded: --max-filesize 80m
    (≈80 min audio), 300s download timeout; temp files always removed.
    yt-dlp is a host dependency (brew) — a clear error when it's missing."""
    import shutil as _shutil
    import subprocess as _sp
    ytdlp = _shutil.which("yt-dlp") or "/opt/homebrew/bin/yt-dlp"
    if not os.path.exists(ytdlp):
        return {"error": "YouTube-Transkription nicht verfügbar: yt-dlp ist "
                         "nicht installiert (brew install yt-dlp)"}
    tmpdir = tempfile.mkdtemp(prefix="brain-yt-")
    try:
        try:
            from engine.context import report_tool_progress
            report_tool_progress(phase="YouTube-Audio laden", note=url)
        except Exception:
            pass
        proc = _sp.run(
            [ytdlp, "--no-playlist", "-f", "bestaudio[ext=m4a]/bestaudio",
             "--max-filesize", "80m", "--no-simulate", "--print", "%(title)s",
             "-o", os.path.join(tmpdir, "audio.%(ext)s"), url],
            capture_output=True, text=True, timeout=300)
        title = (proc.stdout or "").strip().splitlines()[0] if (proc.stdout or "").strip() else ""
        files = [os.path.join(tmpdir, f) for f in os.listdir(tmpdir)]
        if proc.returncode != 0 or not files:
            _errlines = (proc.stderr or "").strip().splitlines()
            return {"error": "YouTube-Download fehlgeschlagen: "
                             + (_errlines[-1][:300] if _errlines else "unbekannter Fehler")}
        try:
            from engine.context import report_tool_progress as _rtp
            _rtp(phase="Transkribieren", note=title or url)
        except Exception:
            pass
        res = _transcript_result_from_file(files[0], url, max_length,
                                           title=title, method="youtube-transcript")
        if res is None:
            return {"error": "YouTube-Audio konnte nicht transkribiert werden."}
        return res
    except _sp.TimeoutExpired:
        return {"error": "YouTube-Download-Timeout (300s) — Video zu lang?"}
    finally:
        _shutil.rmtree(tmpdir, ignore_errors=True)


def _fetch_academic_pdf(pdf_url: str, max_length: int, timeout: int, max_size_mb: int) -> dict | None:
    """Download an academic PDF and extract its text via the shared doc_convert
    pipeline (same path as every other PDF read — fitz/pdfplumber + OCR). Spills
    to a uniquely-named tempfile (doc_convert reads a path, not bytes), extracts,
    deletes. Returns a web_fetch-shaped result dict, or None on any failure so
    the caller falls back to the normal HTTP fetch (graceful degradation)."""
    req_headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Accept": "application/pdf,*/*",
    }
    try:
        req = urllib.request.Request(pdf_url, headers=req_headers, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            ctype = (resp.headers.get_content_type() or "").lower()
            raw = resp.read(max_size_mb * 1024 * 1024)
            final_url = resp.url if hasattr(resp, "url") else pdf_url
        # Only treat as academic-PDF when the server actually served a PDF —
        # a paywall/HTML redirect means our rewrite missed; fall back.
        if "pdf" not in ctype and not raw[:5].startswith(b"%PDF"):
            return None
        res = _fetch_as_file_result(raw, ".pdf", final_url, 200, max_length)
        if res is not None:
            res["fetch_method"] = "academic"
        return res
    except Exception:
        return None


def _note_turn_fetched_url(url: str) -> None:
    """Record a successfully fetched HTML page URL on the turn's RequestContext
    (dynamic key `turn_fetched_urls`, capped). write_document's style='report'
    hero-image mining falls back to these when the report markdown itself cites
    no links (chat 5142a07f: 6 pages fetched, report written link-less → banner
    instead of a real hero). Turn-scoped by construction — the context is
    re-entered per turn, so the list never leaks across turns."""
    try:
        if not (url or "").lower().startswith("https://"):
            return
        from engine.context import get_request_context
        lst = get_request_context()._dynamic.setdefault("turn_fetched_urls", [])
        if url not in lst and len(lst) < 10:
            lst.append(url)
    except Exception:
        pass


def _web_result_anon(payload: str, src: str) -> str:
    """L3b results-anonymisation seam (dispatch symmetry): INBOUND web
    content is scanned + pseudonymised before it reaches the model — a web
    hit about the real person maps onto the SAME fake identity as the local
    files, so the web join works without the cloud model ever seeing the
    clear name (PII_ANALYSIS_PARITY_HANDOVER.md L3b / F4-inbound / F5).

    Only the `content` field is rewritten (URLs/status stay verbatim — the
    egress side is the web gate's job). No-op without an active anonymise
    mapping; the classification gate inside _gdpr_anon_tool_text may raise
    (same contract as every other read seam)."""
    import brain as _brain
    try:
        obj = json.loads(payload)
    except (ValueError, TypeError):
        return payload
    if not isinstance(obj, dict):
        return payload
    content = obj.get("content")
    if not isinstance(content, str) or not content:
        return payload
    anon = _brain._gdpr_anon_tool_text(content, src)
    if anon == content:
        return payload
    obj["content"] = anon
    return json.dumps(obj, ensure_ascii=False)


def tool_web_fetch(args: dict) -> str:
    """Fetch a URL; inbound content passes the L3b anonymisation seam."""
    return _web_result_anon(_tool_web_fetch_impl(args),
                            f"web_fetch:{args.get('url', '')[:120]}")


def _tool_web_fetch_impl(args: dict) -> str:
    import brain as _brain
    url = args.get("url", "")
    method = args.get("method", "GET")
    headers = args.get("headers", {})
    body = args.get("body")
    max_length = args.get("max_length", 50000)
    force_fresh = args.get("force_fresh", False)
    # Read timeout and max_size from tools_config
    _wf_cfg = _brain.get_tool_config().get("web_fetch", {})
    _wf_timeout = _wf_cfg.get("timeout", 30)
    _wf_max_size_mb = _wf_cfg.get("max_size_mb", 10)

    # Check cache for GET requests without body.
    cache_key = url if method == "GET" and not body else None
    if cache_key and not force_fresh:
        cached = _brain._web_cache.get(cache_key)
        if cached is not None:
            cached["cached"] = True
            # The cache holds the FULL file. Apply the brain_code region-trim
            # here too so a cached hit returns just the matched regions to the
            # LLM (same as a fresh fetch). Trims a copy — cache stays full.
            _rp = _github_raw_repo_path(cached.get("url") or url) or _github_raw_repo_path(url)
            if _rp:
                _bcc = _brain._get_brain_code_regions(_rp)
                if _bcc:
                    _tr = _trim_to_brain_code_regions(cached.get("content") or "", _bcc)
                    if _tr is not None:
                        cached = dict(cached, content=_tr, length=len(_tr),
                                      fetch_method=f"{cached.get('fetch_method','raw')}+brain_code_regions")
            # A cached hit is still a hero-image candidate for this turn — but
            # only when it was an HTML page (these fetch_methods imply HTML).
            if (cached.get("fetch_method") or "").split("+")[0] in ("markitdown", "crawl4ai", "scrapling"):
                _note_turn_fetched_url(cached.get("url") or url)
            return _ok(cached)

    # Academic inlining: if this is a known academic landing/abstract page,
    # rewrite to its full-text PDF and extract via doc_convert instead of the
    # raw HTTP text decode (which returns garbage on PDF bytes). GET-only, no
    # custom body. Falls back to the normal fetch when the rewrite misses or the
    # server doesn't actually serve a PDF (paywall/redirect). Cached like any
    # other GET so a re-fetch is free.
    if method == "GET" and not body:
        _pdf_url = _academic_pdf_url(url)
        if _pdf_url:
            _ac = _fetch_academic_pdf(_pdf_url, max_length, _wf_timeout,
                                      _wf_max_size_mb)
            if _ac is not None:
                if cache_key:
                    _brain._web_cache.put(cache_key, dict(_ac))
                return _ok(_ac)
        # YouTube → transcript (v9.307.0): the watch page's HTML is useless to
        # an LLM; download the audio via yt-dlp and transcribe it through the
        # shared STT pipeline instead. Cached like any GET (transcripts are
        # expensive to redo); project web_urls reach this branch via the miner.
        if _YT_URL_RE.match(url):
            _yt = _fetch_youtube_transcript(url, max_length)
            if _yt.get("error"):
                return _err(f"web_fetch: {_yt['error']}")
            if cache_key:
                _brain._web_cache.put(cache_key, dict(_yt))
            return _ok(_yt)

    try:
        from engine.context import report_tool_progress
        try:
            report_tool_progress(phase="Abrufen", note=urllib.parse.urlparse(url).netloc or url)
        except Exception:
            pass
        req_headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
        req_headers.update(headers)
        data = body.encode("utf-8") if body else None
        req = urllib.request.Request(url, data=data, headers=req_headers, method=method)
        with urllib.request.urlopen(req, timeout=_wf_timeout) as resp:
            raw = resp.read(_wf_max_size_mb * 1024 * 1024)
            encoding = resp.headers.get("Content-Encoding", "")
            if encoding == "gzip":
                import gzip
                raw = gzip.decompress(raw)
            charset = resp.headers.get_content_charset() or "utf-8"
            text = raw.decode(charset, errors="replace")
        final_url = resp.url if hasattr(resp, 'url') else url
        content_type = resp.headers.get_content_type() or ""
        # Non-HTML file response (PDF/DOCX/XLSX/PPTX/CSV/image/…): ingest the
        # bytes through the shared doc_convert / vision pipeline instead of
        # decoding them as text (which dumped raw `%PDF…` binary into the result
        # — the project web-url miner then stored that garbage). HTML/text/JSON
        # keep the existing path below. Falls back to the text path on any
        # extraction failure (graceful degradation).
        # Direct audio-file URL → transcript (v9.307.0): route the bytes
        # through the shared STT pipeline instead of doc_convert (which can't
        # read audio) or the text decode (which dumped MP3 bytes as garbage).
        _audio_ext = _audio_ext_for(final_url, content_type)
        if _audio_ext:
            _afd, _tmp_audio = tempfile.mkstemp(suffix=_audio_ext, prefix="brain-webaudio-")
            try:
                with os.fdopen(_afd, "wb") as _fh:
                    _fh.write(raw)
                _ares = _transcript_result_from_file(_tmp_audio, final_url, max_length)
            finally:
                try:
                    os.remove(_tmp_audio)
                except OSError:
                    pass
            if _ares is not None:
                _ares["status"] = resp.status
                _ares["etag"] = resp.headers.get("ETag", "") or ""
                _ares["last_modified"] = resp.headers.get("Last-Modified", "") or ""
                if cache_key:
                    _brain._web_cache.put(cache_key, dict(_ares))
                return _ok(_ares)
        _file_ext = _binary_ext_for(final_url, content_type, raw)
        if _file_ext:
            _fres = _fetch_as_file_result(raw, _file_ext, final_url, resp.status, max_length)
            if _fres is not None:
                _fres["etag"] = resp.headers.get("ETag", "") or ""
                _fres["last_modified"] = resp.headers.get("Last-Modified", "") or ""
                if cache_key:
                    _brain._web_cache.put(cache_key, dict(_fres))
                return _ok(_fres)
        # fetch_method records how the returned content was produced, surfaced
        # as a badge in the chat view so it's clear what the LLM actually saw:
        #   "raw"       — non-HTML, or HTML returned verbatim (no conversion)
        #   "markitdown"— HTML converted to markdown by _html_to_markdown
        #   "crawl4ai"  — rendered in a headless browser (JS-built pages)
        #   "scrapling" — stealth render (Cloudflare/anti-bot bypass), 2nd fallback
        fetch_method = "raw"
        is_html = "html" in content_type or text.lstrip().startswith(("<html", "<!doc", "<!DOC"))
        # `usable` = the text we'd actually hand the model. For HTML that's the
        # markdown conversion; raw HTML doesn't count as usable content (it's
        # what we fall back to only when nothing better exists). This is what
        # the JS-shell gate measures — NOT the raw byte length.
        usable = text
        if is_html:
            md = _brain._html_to_markdown(text)
            if md:
                text = md
                usable = md
                fetch_method = "markitdown"
            else:
                usable = ""  # conversion produced nothing — raw HTML isn't usable

        # JS-rendered / consent-wall fallback: re-fetch through the crawl4ai
        # headless render service when the plain HTTP+markitdown result is not
        # the real article. Three triggers (all GET, no body):
        #   1. Thin content — markitdown yielded essentially nothing (empty
        #      shell) OR only a stub (< 600 chars). The old < 30 gate missed
        #      consent-walled pages that convert to a ~1–2 KB teaser: the model
        #      then quoted the teaser as if it were the full piece (chat
        #      766e3575 — derstandard.at /consent/tcf/ gave 1351 chars).
        #   2. Consent / cookie interstitial — the final URL was redirected to a
        #      consent path (/consent/, /tcf/, cookie-wall). The page is real
        #      but gated; a headless render clicks past it / loads the article.
        #   3. JS SHELL — the static fetch is long enough to pass gate 1 but is
        #      almost all navigation/link chrome with little running PROSE (the
        #      real content is JS-rendered). Measured as prose words OUTSIDE
        #      markdown links: a genuine article is mostly prose, a shell is
        #      mostly link-blocks. Caught chat 0948e5e6 — wetter.orf.at/wien/
        #      returned a 2201-char shell (86 prose words, only current-conditions
        #      district tiles, NO forecast) → "tomorrow's weather not found",
        #      while a headless render yields the JS-loaded Prognose (8186 chars).
        _consent_wall = any(seg in (final_url or "").lower()
                            for seg in ("/consent", "/tcf/", "cookie", "/datenschutz/zustimmung"))
        _u = usable.strip()
        _thin = len(_u) < 600
        # Prose words = word tokens with markdown links stripped out. A shell
        # (link-heavy, prose-poor) trips this even when its char length is high.
        # Guarded to the 600–8000 char band so we don't render huge real pages
        # that merely happen to be link-dense (they have plenty of prose too).
        _shell = False
        if not _thin and 600 <= len(_u) <= 8000:
            _prose = re.sub(r"\[[^\]]*\]\([^)]*\)", "", _u)  # drop [text](url)
            _prose_words = len(re.findall(r"[A-Za-zÄÖÜäöüßÀ-ÿ]{3,}", _prose))
            _shell = _prose_words < 120
        if is_html and method == "GET" and not body and (_thin or _consent_wall or _shell):
            try:
                report_tool_progress(phase="Rendern", note="Headless-Browser (JS-Seite)")
            except Exception:
                pass
            rendered = _brain._crawl4ai_render(final_url)
            _md = (rendered.get("markdown") or "").strip()
            # Only take the render if it's an improvement — a longer body than
            # what HTTP gave us. Guards against a render that itself hits the
            # wall and returns even less.
            if rendered.get("success") and len(_md) > len(usable.strip()):
                text = _md
                usable = _md
                fetch_method = "crawl4ai"

            # SECOND fallback — Scrapling StealthyFetcher (stealth Firefox,
            # Cloudflare Turnstile bypass). The plain crawl4ai render is headless
            # Chromium; anti-bot pages (Cloudflare challenge, bot detection) stop
            # it cold so the content is STILL thin. Try the stealth render only
            # when we're still below the usable-content bar, and again only keep
            # it if it strictly beats what we have. Same graceful degradation:
            # service down / scrapling absent → success=False → we keep the best
            # result so far.
            if len(usable.strip()) < 600:
                try:
                    report_tool_progress(phase="Stealth-Render",
                                         note="Anti-Bot-Umgehung (Scrapling)")
                except Exception:
                    pass
                stealth = _brain._crawl4ai_render_stealth(final_url)
                _smd = (stealth.get("markdown") or "").strip()
                if stealth.get("success") and len(_smd) > len(usable.strip()):
                    text = _smd
                    usable = _smd
                    fetch_method = "scrapling"

        if len(text) > max_length:
            text = text[:max_length] + "\n... (truncated)"
        # brain_code fetch-trim: if this is a GitHub-raw URL for a file the
        # model just found via mempalace_query(brain_code), return ONLY the
        # matched region(s) of the fetched source — Brainy gets the live file
        # (full fetch, current `main`) but the LLM sees just the relevant code,
        # not the whole module. Falls back to full content when the URL isn't a
        # recorded brain_code hit or no chunk could be located. The trim applies
        # ONLY to the returned content — the cache keeps the FULL file so a
        # later non-Brainy fetch (or a fetch without a recorded hit) of the same
        # URL still gets everything.
        if is_html:
            _note_turn_fetched_url(final_url or url)
        result = {"url": final_url, "status": resp.status, "length": len(text),
                  "content": text, "fetch_method": fetch_method,
                  # Caching validators — additive; ignored by every caller except
                  # the project web-url sync, which stores them for conditional
                  # GET (If-None-Match / If-Modified-Since) on the next refresh.
                  "etag": resp.headers.get("ETag", "") or "",
                  "last_modified": resp.headers.get("Last-Modified", "") or ""}
        if cache_key:
            _brain._web_cache.put(cache_key, dict(result))
        _repo_path = _github_raw_repo_path(final_url) or _github_raw_repo_path(url)
        if _repo_path:
            _bc_chunks = _brain._get_brain_code_regions(_repo_path)
            if _bc_chunks:
                _trimmed = _trim_to_brain_code_regions(text, _bc_chunks)
                if _trimmed is not None:
                    result = dict(result, content=_trimmed, length=len(_trimmed),
                                  fetch_method=f"{fetch_method}+brain_code_regions")
        return _ok(result)
    except urllib.error.HTTPError as e:
        body_text = ""
        try:
            body_text = e.read().decode("utf-8", errors="replace")[:5000]
        except Exception:
            pass
        return _err(f"web_fetch: HTTP {e.code} {e.reason}\n{body_text}")
    except Exception as e:
        return _err(f"web_fetch: {e}")


# ─── exa_search / searxng_search (two independent web-search tools) ───────────
#
# Both return raw JSON strings (not _ok/_err envelopes) — the pre-existing
# {query, results:[{title,link}], result_count} contract every caller + the UI
# reference-extraction relies on. They are SEPARATE tools, each with its own
# tools_config block; the admin enables exa, searxng, or both via
# tool_settings.enabled and the LLM picks from whatever is in its tool list.
# No backend flag, no cross-tool routing.

def _searxng_query(query: str, num_results: int = 5, *, category: str = "",
                   include_snippets: bool = False, force_fresh: bool = False,
                   want_images: bool = False) -> str:
    """Shared SearXNG query core for all searxng_* tools.

    Hits the bundled self-hosted instance (config.json -> searxng.url via
    brain._searxng_base_url(); admin override tools_config.searxng_search.url)
    at <url>/search?format=json, maps results to the {title, link, score} shape
    exa_search returns, drops near-zero-score noise, and surfaces the
    Wikipedia/Wikidata infobox when present.

    `category` selects SearXNG's search category (general/science/it/images/
    news/…). Empty = SearXNG's own default (general). `want_images` additionally
    passes through each result's `img_src` (the direct image URL) — image
    results are URLs to pictures, not web pages, so the caller needs that field.

    This is the single choke point (feedback_single_fix_point): tool_searxng_search
    + the 4 specialized search tools all call it, so a fix here reaches all of
    them. See the per-tool wrappers for the category rationale."""
    import brain as _brain
    base = _brain._searxng_base_url()
    if not base:
        return json.dumps({
            "query": query, "results": [],
            "error": "searxng_search: no SearXNG instance configured "
                     "(set config.json -> searxng.url, or override with "
                     "tools_config.searxng_search.url for an external instance)",
        })

    cache_key = f"searxng:{base}:{category}:{query}:{num_results}:{int(include_snippets)}"
    if not force_fresh:
        cached = _brain._web_cache.get(cache_key)
        if cached is not None:
            cached["cached"] = True
            # L3b: SERP titles/snippets/infobox are inbound web content.
            return _brain._gdpr_anon_tool_text(
                json.dumps(cached, indent=1), "searxng_search")

    params = {"q": query, "format": "json"}
    if category:
        params["categories"] = category
    url = base + "/search?" + urllib.parse.urlencode(params)

    headers = {
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    }
    try:
        req = urllib.request.Request(url, headers=headers, method="GET")
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            if resp.headers.get("Content-Encoding", "") == "gzip":
                import gzip
                raw = gzip.decompress(raw)
            response_data = json.loads(raw.decode("utf-8"))

        # SearXNG's JSON `results` are already sorted by relevance score
        # (consensus across engines). Drop near-zero-score noise — single weak
        # engine, no agreement — so the top num_results stay dense with real
        # matches; keep at least the best one if everything scored low. Expose
        # score so the model can rank which URLs are worth fetching. Snippets are
        # included ONLY for the human Websuche panel (include_snippets) — for the
        # LLM we deliberately omit them (see include_snippets note above).
        raw = response_data.get("results", [])
        ranked = [r for r in raw if r.get("score", 0) >= 0.3] or raw[:1]
        results = []
        for r in ranked[:num_results]:
            entry = {
                "title": r.get("title", ""),
                "link": r.get("url", ""),
                "score": round(r.get("score", 0), 2),
            }
            if want_images and r.get("img_src"):
                entry["image_url"] = r.get("img_src")
            if include_snippets:
                entry["snippet"] = (r.get("content") or "")[:300]
            results.append(entry)

        search_info = {"query": query, "results": results, "result_count": len(results)}

        # Wikipedia/Wikidata return a structured infobox (authoritative summary
        # + canonical URL) on encyclopedic queries — surface it so the model can
        # answer "who/what is X" directly, without a web_fetch round-trip.
        infoboxes = response_data.get("infoboxes", []) or []
        if infoboxes:
            ib = infoboxes[0]
            ib_url = ib.get("id") or ib.get("url") or ""
            if not ib_url:
                for u in ib.get("urls", []):
                    if "wikipedia.org" in (u.get("url") or ""):
                        ib_url = u["url"]
                        break
            content = (ib.get("content") or "").strip()
            if content:
                search_info["infobox"] = {
                    "title": ib.get("infobox") or ib.get("title", ""),
                    "content": content[:600],
                    "url": ib_url,
                }

        if not results and "infobox" not in search_info:
            search_info["message"] = "No search results found. Try a different query."
        if results:
            _brain._web_cache.put(cache_key, dict(search_info))
        # L3b results-anonymisation seam: SERP titles/snippets/infobox are
        # inbound web content — anonymise before the model sees them (the
        # cache above keeps the raw copy; mappings are per-session).
        return _brain._gdpr_anon_tool_text(
            json.dumps(search_info, indent=1), "searxng_search")

    except urllib.error.HTTPError as e:
        error_body = ""
        try:
            error_body = e.read().decode("utf-8")
        except Exception:
            pass
        return json.dumps({"query": query, "results": [], "error": f"SearXNG HTTP {e.code}: {error_body}"})
    except Exception as e:
        return json.dumps({"query": query, "results": [], "error": f"SearXNG: {e}"})


def tool_searxng_search(args: dict) -> str:
    """Web search via a self-hosted SearXNG instance. No API key, on-prem.

    Searches SearXNG's broad `general` category (default — no category param):
    general already surfaces news outlets AND the authoritative source pages for
    news-y queries, ranked sensibly. The old opt-in `news` category was dropped
    here — it buried the authoritative source (e.g. the Bundesbank page under
    press coverage) on news queries and returned stale regional noise on non-news
    ones. For deliberately-scoped retrieval use the specialized tools instead:
    science_search (papers), code_search (programming), image_search (pictures),
    news_search (dated reporting)."""
    # Snippets are surfaced ONLY to the human-curation Websuche panel
    # (POST /v1/web/search sets include_snippets=True). The LLM-facing path
    # gets bare title+link+score: SERP snippets are short, stale, and biased
    # the model's fetch choice toward whichever result had a tempting blurb
    # instead of the most on-topic URL (chat 766e3575 — it fetched two news
    # articles over the #1 weather page because their snippets read better).
    return _searxng_query(
        args.get("query", ""),
        args.get("num_results", 5),
        category="general",
        include_snippets=args.get("include_snippets", False),
        force_fresh=args.get("force_fresh", False),
    )


def tool_science_search(args: dict) -> str:
    """Scientific-literature search via SearXNG's `science` category — arxiv,
    pubmed, google scholar, semantic scholar. Returns papers (title + link +
    score); many carry publication dates. Then web_fetch the paper/abstract
    pages for the actual content. Use for research papers, studies, academic
    or medical literature — NOT general web (use searxng_search for that)."""
    return _searxng_query(args.get("query", ""), args.get("num_results", 5),
                          category="science")


def tool_dev_search(args: dict) -> str:
    """Programming/technical WEB search via SearXNG's `it` category —
    stackoverflow, mdn, github, askubuntu, superuser, pypi, docker hub. Returns
    Q&A + docs (title + link + score); web_fetch the best pages for the answer
    text. Use for coding questions, API/library docs, error messages, dev
    tooling. NOTE: this searches the public web — distinct from code_search,
    which queries THIS codebase's own code-structure graph."""
    return _searxng_query(args.get("query", ""), args.get("num_results", 5),
                          category="it")


def tool_image_search(args: dict) -> str:
    """Image search via SearXNG's `images` category — google/bing/qwant/brave
    images, flickr, openverse. Each result carries an `image_url` (the DIRECT
    picture URL) alongside `link` (the source page). Returns picture URLs, not
    web pages — use when the user wants images/photos/pictures/diagrams of
    something. To describe or analyse a picture, web_fetch its image_url."""
    return _searxng_query(args.get("query", ""), args.get("num_results", 5),
                          category="images", want_images=True)


def tool_news_search(args: dict) -> str:
    """News search via SearXNG's `news` category — google/bing/ddg/qwant news,
    reuters. Returns recent, dated news items (title + link + score); web_fetch
    the articles for the reporting. Use ONLY when the user actually wants news
    coverage / recent events / press reporting on a topic. For factual or live
    data prefer searxng_search (general) — news engines bury authoritative
    primary sources under press coverage."""
    return _searxng_query(args.get("query", ""), args.get("num_results", 5),
                          category="news")


def exa_search(query: str, num_results: int = 5, category: str | None = None,
               force_fresh: bool = False) -> str:
    """Execute an Exa web search and return JSON results ({title, link} per
    result). Uses stdlib only."""
    import brain as _brain
    cache_key = f"exa:{query}:{num_results}:{category or ''}"
    if not force_fresh:
        cached = _brain._web_cache.get(cache_key)
        if cached is not None:
            cached["cached"] = True
            # L3b: result titles are inbound web content.
            return _brain._gdpr_anon_tool_text(
                json.dumps(cached, indent=1), "exa_search")

    # Read API key from tools_config, fall back to env var. No hardcoded
    # default — an unconfigured key surfaces as an Exa 401 the model sees.
    _tcfg = _brain.get_tool_config().get("exa_search", {})
    api_key = _tcfg.get("api_key") or os.environ.get("EXA_API_KEY", "")

    body = {
        "query": query,
        "type": "auto",
        "num_results": num_results,
    }
    if category:
        body["category"] = category

    headers = {
        "x-api-key": api_key,
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
    }

    try:
        req = urllib.request.Request(
            "https://api.exa.ai/search",
            data=json.dumps(body).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            # Handle gzip encoding if server sends it anyway
            encoding = resp.headers.get("Content-Encoding", "")
            if encoding == "gzip":
                import gzip
                raw = gzip.decompress(raw)
            response_data = json.loads(raw.decode("utf-8"))

        results = []
        for r in response_data.get("results", []):
            results.append({
                "title": r.get("title", ""),
                "link": r.get("url", ""),
            })

        search_info = {"query": query, "results": results, "result_count": len(results)}
        if category:
            search_info["category"] = category
        if not results:
            search_info["message"] = "No search results found. Try a different query."
        if results:
            _brain._web_cache.put(cache_key, dict(search_info))
        # L3b results-anonymisation seam (see _searxng_query).
        return _brain._gdpr_anon_tool_text(
            json.dumps(search_info, indent=1), "exa_search")

    except urllib.error.HTTPError as e:
        error_body = ""
        try:
            error_body = e.read().decode("utf-8")
        except Exception:
            pass
        return json.dumps({"query": query, "results": [], "error": f"HTTP {e.code}: {error_body}"})
    except Exception as e:
        return json.dumps({"query": query, "results": [], "error": str(e)})
