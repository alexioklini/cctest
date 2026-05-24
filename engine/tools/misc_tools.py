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

    body = agent.load_skill(skill_name)
    if body is None:
        available = [s.get("slug", s["name"]) for s in agent.list_skills()]
        return _err(f"use_skill: skill '{skill_name}' not found. Available: {', '.join(available) or 'none'}")

    return _ok({"skill": skill_name, "instructions": body})


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

def tool_web_fetch(args: dict) -> str:
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

    # Check cache for GET requests without body
    cache_key = url if method == "GET" and not body else None
    if cache_key and not force_fresh:
        cached = _brain._web_cache.get(cache_key)
        if cached is not None:
            cached["cached"] = True
            return _ok(cached)

    try:
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
        if "html" in content_type or text.lstrip().startswith(("<html", "<!doc", "<!DOC")):
            text = _brain._html_to_markdown(text) or text
        if len(text) > max_length:
            text = text[:max_length] + "\n... (truncated)"
        result = {"url": final_url, "status": resp.status, "length": len(text), "content": text}
        if cache_key:
            _brain._web_cache.put(cache_key, dict(result))
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

def tool_searxng_search(args: dict) -> str:
    """Web search via a self-hosted SearXNG instance. No API key, on-prem.

    Talks to the bundled self-hosted SearXNG instance (config.json ->
    searxng.url, managed by the SearxngSupervisor); an admin can override to
    an external instance via tools_config.searxng_search.url. Hits
    <url>/search?format=json, mapping results to the same {title, link} shape
    exa_search returns. Only SearXNG's real `news` category is mapped;
    other categories have no SearXNG equivalent and are dropped (passing an
    unknown category returns zero results)."""
    import brain as _brain
    query = args.get("query", "")
    num_results = args.get("num_results", 5)
    category = args.get("category")
    force_fresh = args.get("force_fresh", False)

    _tcfg = _brain.get_tool_config().get("searxng_search", {})
    base = _brain._searxng_base_url()
    if not base:
        return json.dumps({
            "query": query, "results": [],
            "error": "searxng_search: no SearXNG instance configured "
                     "(set config.json -> searxng.url, or override with "
                     "tools_config.searxng_search.url for an external instance)",
        })

    cache_key = f"searxng:{base}:{query}:{num_results}:{category or ''}"
    if not force_fresh:
        cached = _brain._web_cache.get(cache_key)
        if cached is not None:
            cached["cached"] = True
            return json.dumps(cached, indent=1)

    params = {"q": query, "format": "json"}
    if category == "news":
        params["categories"] = "news"
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
        # score + snippet so the model can triage which URLs are worth fetching
        # (it gets bare title+link otherwise and fetches blindly).
        raw = response_data.get("results", [])
        ranked = [r for r in raw if r.get("score", 0) >= 0.3] or raw[:1]
        results = []
        for r in ranked[:num_results]:
            results.append({
                "title": r.get("title", ""),
                "link": r.get("url", ""),
                "score": round(r.get("score", 0), 2),
                "snippet": (r.get("content") or "")[:300],
            })

        search_info = {"query": query, "results": results, "result_count": len(results)}
        if category:
            search_info["category"] = category

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
        return json.dumps(search_info, indent=1)

    except urllib.error.HTTPError as e:
        error_body = ""
        try:
            error_body = e.read().decode("utf-8")
        except Exception:
            pass
        return json.dumps({"query": query, "results": [], "error": f"SearXNG HTTP {e.code}: {error_body}"})
    except Exception as e:
        return json.dumps({"query": query, "results": [], "error": f"SearXNG: {e}"})


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
            return json.dumps(cached, indent=1)

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
        return json.dumps(search_info, indent=1)

    except urllib.error.HTTPError as e:
        error_body = ""
        try:
            error_body = e.read().decode("utf-8")
        except Exception:
            pass
        return json.dumps({"query": query, "results": [], "error": f"HTTP {e.code}: {error_body}"})
    except Exception as e:
        return json.dumps({"query": query, "results": [], "error": str(e)})
