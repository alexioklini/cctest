"""Traces, audit, MCP, MemPalace, KG, and context-manager observability handlers.

Sub-mixin of AdminHandlerMixin (handlers/admin.py module-split refactor). Holds
ONLY this area's `_handle_*` methods (+ area-only private helpers).
AdminHandlerMixin inherits this class, so the combined BrainAgentHandler MRO is
unchanged.

Like admin.py, this module references `engine`, `brain`, `client`, `_db_conn`,
`sqlite3`, `subprocess`, etc. as BARE MODULE GLOBALS injected at runtime by
server._inject_server_globals(). This module's name is in that function's
injection list. All other helpers (`_send_json`, `_read_json`,
`_parse_agent_from_path`, …) resolve via `self.` against the combined MRO.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import time
import threading
import urllib.request
import urllib.error
import uuid
from urllib.parse import unquote, urlencode


class AdminObservabilityHandlers:
    """Traces, audit, MCP, MemPalace, KG, and context-manager observability handlers."""

    def _handle_traces_list(self):
        """GET /v1/traces?agent=X&hours=24&limit=50 — recent traces."""
        if not engine._trace_manager:
            self._send_json({"error": "Tracing not initialized"}, 503)
            return
        qs = self.path.split("?", 1)[1] if "?" in self.path else ""
        params = dict(p.split("=", 1) for p in qs.split("&") if "=" in p)
        from urllib.parse import unquote
        agent = unquote(params.get("agent", "")) or None
        hours = int(params.get("hours", "24"))
        limit = int(params.get("limit", "50"))
        traces = engine._trace_manager.get_traces(agent=agent, hours=hours, limit=limit)
        self._send_json({"traces": traces, "count": len(traces)})

    def _handle_trace_detail(self, path):
        """GET /v1/traces/{trace_id} — all spans for a trace."""
        if not engine._trace_manager:
            self._send_json({"error": "Tracing not initialized"}, 503)
            return
        trace_id = path.split("/")[-1]
        spans = engine._trace_manager.get_trace(trace_id)
        if not spans:
            self._send_json({"error": "Trace not found"}, 404)
            return
        total_duration = sum(s.get("duration_ms", 0) for s in spans)
        total_tokens_in = sum(s.get("tokens_in", 0) for s in spans)
        total_tokens_out = sum(s.get("tokens_out", 0) for s in spans)
        self._send_json({
            "trace_id": trace_id,
            "spans": spans,
            "span_count": len(spans),
            "total_duration_ms": total_duration,
            "total_tokens_in": total_tokens_in,
            "total_tokens_out": total_tokens_out,
        })

    def _handle_audit_list(self):
        """GET /v1/audit?agent=X&type=Y&from=Z&limit=50 — audit log."""
        if not engine._audit_log:
            self._send_json({"error": "Audit log not initialized"}, 503)
            return
        qs = self.path.split("?", 1)[1] if "?" in self.path else ""
        params = dict(p.split("=", 1) for p in qs.split("&") if "=" in p)
        from urllib.parse import unquote
        agent = unquote(params.get("agent", "")) or None
        action_type = unquote(params.get("type", "")) or None
        from_ts = unquote(params.get("from", "")) or None
        limit = int(params.get("limit", "50"))
        entries = engine._audit_log.query(agent=agent, action_type=action_type,
                                           from_ts=from_ts, limit=limit)
        self._send_json({"entries": entries, "count": len(entries)})

    def _handle_audit_export(self):
        """GET /v1/audit/export?agent=X&format=csv — CSV download."""
        if not engine._audit_log:
            self._send_json({"error": "Audit log not initialized"}, 503)
            return
        qs = self.path.split("?", 1)[1] if "?" in self.path else ""
        params = dict(p.split("=", 1) for p in qs.split("&") if "=" in p)
        from urllib.parse import unquote
        agent = unquote(params.get("agent", "")) or None
        from_ts = unquote(params.get("from", "")) or None
        to_ts = unquote(params.get("to", "")) or None
        fmt = params.get("format", "csv")
        if fmt == "csv":
            csv_data = engine._audit_log.export_csv(agent=agent, from_ts=from_ts, to_ts=to_ts)
            body = csv_data.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/csv")
            self.send_header("Content-Disposition", "attachment; filename=audit_log.csv")
            self.send_header("Content-Length", len(body))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)
        else:
            entries = engine._audit_log.query(agent=agent, from_ts=from_ts, limit=10000)
            self._send_json({"entries": entries, "count": len(entries)})

    # --- MCP Connection Handlers ---

    def _handle_mcp_list(self):
        """GET /v1/mcp/connections — list all MCP connections."""
        mcp = engine._mcp_manager
        if not mcp:
            self._send_json({"connections": []})
            return
        servers = mcp.list_servers()
        self._send_json({"connections": servers})

    def _handle_mcp_connect(self):
        """POST /v1/mcp/connect — connect to a new MCP server at runtime."""
        body = self._read_json()
        url = body.get("url", "")
        name = body.get("name", "")
        transport = body.get("transport", "sse")
        persist = body.get("persist", False)

        if not url or not name:
            self._send_json({"error": "Both 'url' and 'name' are required"}, 400)
            return

        mcp = engine._mcp_manager
        if not mcp:
            mcp = engine.MCPManager()
            engine._mcp_manager = mcp

        result = mcp.connect_runtime(url, name, transport)
        if result.get("error"):
            self._send_json({"error": result["error"]}, 400)
            return

        # Persist to mcp.json if requested
        if persist:
            mcp_json_path = os.path.join(engine.AGENTS_DIR, "main", "mcp.json")
            try:
                existing = {}
                if os.path.exists(mcp_json_path):
                    with open(mcp_json_path, "r") as f:
                        existing = json.load(f)
                if transport == "stdio":
                    parts = url.split()
                    existing[name] = {"transport": "stdio", "command": parts[0],
                                      "args": parts[1:] if len(parts) > 1 else []}
                else:
                    existing[name] = {"transport": "sse", "url": url}
                with open(mcp_json_path, "w") as f:
                    json.dump(existing, f, indent=2)
                result["persisted"] = True
            except Exception as e:
                result["persist_error"] = str(e)

        self._send_json(result)

    def _handle_mcp_disconnect(self):
        """POST /v1/mcp/disconnect — disconnect a runtime MCP server."""
        body = self._read_json()
        name = body.get("name", "")
        if not name:
            self._send_json({"error": "'name' is required"}, 400)
            return

        mcp = engine._mcp_manager
        if not mcp:
            self._send_json({"error": "No MCP manager available"}, 400)
            return

        result = mcp.disconnect_runtime(name)
        if result.get("error"):
            self._send_json({"error": result["error"]}, 400)
            return
        self._send_json(result)

    def _handle_mcp_registry(self):
        """GET /v1/mcp/registry?q=...&limit=... — search official MCP registry."""
        import urllib.request
        import urllib.parse
        params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        query = params.get("q", [""])[0]
        limit = params.get("limit", ["20"])[0]
        try:
            url = f"https://registry.modelcontextprotocol.io/v0/servers?search={urllib.parse.quote(query)}&limit={limit}"
            req = urllib.request.Request(url, headers={"User-Agent": "BrainAgent/1.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
            # Normalize into a flat list with install info — dedup by name
            servers = []
            seen = set()
            items = data if isinstance(data, list) else data.get("servers", [])
            for item in items:
                srv = item.get("server", item) if isinstance(item, dict) else item
                if not isinstance(srv, dict):
                    continue
                name = srv.get("name", "")
                if name in seen:
                    continue
                seen.add(name)
                desc = srv.get("description", "")
                repo = srv.get("repository", {})
                repo_url = repo.get("url", "") if isinstance(repo, dict) else ""
                packages = srv.get("packages", [])
                remotes = srv.get("remotes", [])
                pkg = packages[0] if packages else {}
                registry_type = pkg.get("registryType", "")
                identifier = pkg.get("identifier", "")
                transport = pkg.get("transport", {})
                transport_type = transport.get("type", "stdio") if isinstance(transport, dict) else "stdio"
                pkg_args = pkg.get("packageArguments", [])
                env_vars = pkg.get("environmentVariables", [])
                # Build install command from packages or remotes
                if registry_type == "npm":
                    command = "npx"
                    args = ["-y", identifier]
                elif registry_type == "pypi":
                    command = "uvx"
                    args = [identifier]
                elif remotes:
                    remote = remotes[0]
                    transport_type = remote.get("type", "sse")
                    command = remote.get("url", "")
                    args = []
                    registry_type = "remote"
                else:
                    command = identifier
                    args = []
                servers.append({
                    "name": name,
                    "description": desc,
                    "repo_url": repo_url,
                    "registry_type": registry_type,
                    "identifier": identifier,
                    "transport": transport_type,
                    "command": command,
                    "args": args,
                    "env_vars": [{"name": e.get("name",""), "description": e.get("description",""), "required": e.get("isRequired", False)} for e in env_vars],
                    "pkg_args": [{"name": a.get("name",""), "description": a.get("description",""), "required": a.get("isRequired", False), "format": a.get("format","")} for a in pkg_args],
                })
            self._send_json({"servers": servers})
        except Exception as e:
            self._send_json({"error": str(e), "servers": []})

    def _handle_mempalace_session_turns(self):
        """GET /v1/mempalace/session-turns?session_id=X — return the set of
        turn_ids currently memorized for this session, parsed from drawer
        source_file prefixes. The UI uses this to grey out menu items that
        would be a no-op (e.g. 'memorize this response' when it's already
        memorized, or 'remove' when nothing was stored)."""
        from urllib.parse import parse_qs, urlparse
        qs = parse_qs(urlparse(self.path).query)
        sid = (qs.get("session_id") or [""])[0]
        if not sid:
            self._send_json({"error": "session_id required"}, 400)
            return
        turn_ids: set[int] = set()
        legacy_count = 0  # drawers without #turn/<id> suffix
        try:
            mcfg = engine._load_mempalace_config()
            palace_path = mcfg.get("palace_path", "")
            if not palace_path or not os.path.isdir(palace_path):
                self._send_json({"session_id": sid, "turn_ids": [], "legacy_count": 0})
                return
            ok, _ = engine._ensure_mempalace_importable()
            if not ok:
                self._send_json({"session_id": sid, "turn_ids": [], "legacy_count": 0})
                return
            from mempalace.palace import get_collection
            col = get_collection(palace_path, create=False)
            if not col:
                self._send_json({"session_id": sid, "turn_ids": [], "legacy_count": 0})
                return
            result = col.get(include=["metadatas"])
            prefix = f"session/{sid}"
            for m in result.get("metadatas", []):
                sf = (m.get("source_file") or "")
                if not sf.startswith(prefix):
                    continue
                # Shape: session/<sid> or session/<sid>#turn/<id>[...] or legacy session/<sid>#...
                rest = sf[len(prefix):]
                if rest.startswith("#turn/"):
                    after = rest[len("#turn/"):]
                    tok = after.split("#", 1)[0].split("/", 1)[0]
                    if tok.isdigit():
                        turn_ids.add(int(tok))
                        continue
                legacy_count += 1
        except Exception as e:
            self._send_json({"error": str(e)}, 500)
            return
        self._send_json({
            "session_id": sid,
            "turn_ids": sorted(turn_ids),
            "legacy_count": legacy_count,
        })

    def _handle_mempalace_classifier_get(self):
        """GET /v1/mempalace/classifier — return classifier config."""
        mcfg = engine._load_mempalace_config()
        sync_cfg = mcfg.get("chat_sync", {}) or {}
        clf = sync_cfg.get("classifier", {}) or {}
        self._send_json({
            "enabled": clf.get("enabled", False),
            "model": clf.get("model", ""),
            "min_turns": clf.get("min_turns", 0),
            "default_mode": clf.get("default_mode", 0),
            "categories_to_file": clf.get("categories_to_file",
                ["fact", "preference", "decision", "reference"]),
        })

    def _handle_mempalace_classifier_save(self):
        """POST /v1/mempalace/classifier — save classifier config."""
        body = self._read_json()
        config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json")
        try:
            config = {}
            if os.path.exists(config_path):
                with open(config_path) as f:
                    config = json.load(f)
            mp = config.setdefault("mempalace", {})
            cs = mp.setdefault("chat_sync", {})
            clf = cs.setdefault("classifier", {})
            if "enabled" in body:
                clf["enabled"] = bool(body["enabled"])
            if "model" in body:
                clf["model"] = str(body["model"])
            if "categories_to_file" in body:
                clf["categories_to_file"] = list(body["categories_to_file"])
            if "min_turns" in body:
                clf["min_turns"] = max(0, int(body["min_turns"]))
            if "default_mode" in body:
                clf["default_mode"] = max(0, min(2, int(body["default_mode"])))
            with open(config_path, "w") as f:
                json.dump(config, f, indent=2)
            engine._mempalace_config_cache = None
            self._send_json({"status": "saved", "classifier": clf})
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    # ── Knowledge-graph endpoints ─────────────────────────────────────────
    #
    # Project-scoped KG produced by kg_extract.run_kg_post_pass during the
    # project-sync daemon cycle. All wing access is gated by
    # _project_access_check; the global stats endpoint filters by accessible
    # projects when called by a non-admin.

    def _kg_qs(self) -> dict:
        """Flatten URL query string to a single-value dict for KG endpoints."""
        from urllib.parse import parse_qs, urlparse
        raw = parse_qs(urlparse(self.path).query)
        return {k: (v[0] if v else "") for k, v in raw.items()}

    def _kg_resolve_project_from_query(self, params):
        """Pull (agent_id, proj_name, project, prefixes, palace_path) from
        ?agent_id=X&project=Y query params. Sends 400/404/403 on miss and
        returns None. `project` carries the loaded project dict.
        """
        agent_id = (params.get("agent_id") or "").strip()
        proj_name = (params.get("project") or "").strip()
        if not agent_id or not proj_name:
            self._send_json({"error": "agent_id and project required"}, 400)
            return None
        project = self._project_access_check(agent_id, proj_name)
        if project is None:
            return None
        pid = project.get("id") or ""
        if not pid:
            self._send_json({"error": "project has no id (run a sync first)"}, 400)
            return None
        mcfg = engine._load_mempalace_config()
        palace_path = mcfg.get("palace_path", "")
        if not palace_path or not os.path.isdir(palace_path):
            self._send_json({"error": "MemPalace palace_path missing"}, 503)
            return None
        # Collect every source_file prefix that belongs to this project.
        pdir = project.get("dir") or os.path.join(
            engine.AGENTS_DIR, agent_id, "projects", proj_name)
        def _norm(p: str) -> str:
            # Resolve symlinks (macOS /tmp → /private/tmp etc.) so prefix
            # filters match what the miner stored.
            try:
                r = os.path.realpath(p)
            except OSError:
                r = p
            if r and not r.endswith(os.sep):
                r += os.sep
            return r
        prefixes = [_norm(pdir)]
        for entry in (project.get("input_folders") or []):
            fp = (entry.get("path") or "").strip()
            if fp:
                prefixes.append(_norm(fp))
        return {
            "agent_id": agent_id,
            "proj_name": proj_name,
            "project": project,
            "wing": _project_wing(pid),
            "prefixes": prefixes,
            "palace_path": palace_path,
            "chats_db_path": os.path.join(engine.AGENTS_DIR, "main", "chats.db"),
        }

    def _handle_kg_stats_global(self):
        """GET /v1/mempalace/kg/stats — aggregate across all accessible
        projects. Admins see everything; non-admins see only projects they
        can access (per _project_access_check)."""
        user = self._require_auth()
        if user is None:
            return
        try:
            from engine import kg_extract
        except Exception as e:
            self._send_json({"error": f"kg_extract unavailable: {e}"}, 500)
            return
        mcfg = engine._load_mempalace_config()
        if not mcfg.get("enabled", True):
            self._send_json({"enabled": False, "projects": []})
            return
        palace_path = mcfg.get("palace_path", "")
        if not palace_path or not os.path.isdir(palace_path):
            self._send_json({"error": "palace_path missing"}, 503)
            return
        kg_cfg = mcfg.get("kg") or {}

        agg_entities = 0
        agg_triples = 0
        per_project = []
        try:
            for agent_id in sorted(os.listdir(engine.AGENTS_DIR)):
                proj_root = os.path.join(engine.AGENTS_DIR, agent_id, "projects")
                if not os.path.isdir(proj_root):
                    continue
                for proj_name in sorted(os.listdir(proj_root)):
                    if proj_name.startswith("."):
                        continue
                    project = engine.ProjectManager.get_project(agent_id, proj_name)
                    if not project or project.get("status") == "archived":
                        continue
                    if not _auth_mod.can_access_project(user, project):
                        continue
                    pid = project.get("id") or ""
                    if not pid:
                        continue
                    pdir = project.get("dir") or os.path.join(
                        proj_root, proj_name)
                    def _norm_p(p: str) -> str:
                        try:
                            r = os.path.realpath(p)
                        except OSError:
                            r = p
                        if r and not r.endswith(os.sep):
                            r += os.sep
                        return r
                    prefixes = [_norm_p(pdir)]
                    for entry in (project.get("input_folders") or []):
                        fp = (entry.get("path") or "").strip()
                        if fp:
                            prefixes.append(_norm_p(fp))
                    proj_entities = 0
                    proj_triples = 0
                    proj_top_predicates = {}
                    for prefix in prefixes:
                        try:
                            s = kg_extract.kg_stats_for_wing(
                                palace_path=palace_path,
                                source_prefix=prefix,
                                adapter_name="brain-project-kg")
                        except Exception:
                            continue
                        proj_entities += int(s.get("entities", 0))
                        proj_triples += int(s.get("triples", 0))
                        for p in s.get("top_predicates", []) or []:
                            k = p.get("predicate", "") or ""
                            if k:
                                proj_top_predicates[k] = (
                                    proj_top_predicates.get(k, 0)
                                    + int(p.get("count", 0)))
                    per_project.append({
                        "agent_id": agent_id,
                        "project": proj_name,
                        "project_id": pid,
                        "wing": _project_wing(pid),
                        "entities": proj_entities,
                        "triples": proj_triples,
                        "top_predicates": [
                            {"predicate": k, "count": v}
                            for k, v in sorted(proj_top_predicates.items(),
                                               key=lambda kv: -kv[1])[:10]
                        ],
                    })
                    agg_entities += proj_entities
                    agg_triples += proj_triples
        except Exception as e:
            self._send_json({"error": f"enumerate failed: {e}"}, 500)
            return
        self._send_json({
            "enabled": kg_cfg.get("enabled", True),
            "extraction_model": kg_cfg.get("extraction_model", ""),
            "profile": kg_cfg.get("profile", "normative"),
            "entities": agg_entities,
            "triples": agg_triples,
            "projects": sorted(per_project,
                               key=lambda p: -p["triples"]),
        })

    def _handle_kg_wing_detail(self, params):
        """GET /v1/mempalace/kg/wing?agent_id=X&project=Y — per-project
        stats + sample triples + recent extraction log."""
        ctx = self._kg_resolve_project_from_query(params)
        if ctx is None:
            return
        try:
            from engine import kg_extract
        except Exception as e:
            self._send_json({"error": f"kg_extract unavailable: {e}"}, 500)
            return
        # Aggregate across every prefix belonging to the project.
        agg_entities = 0
        agg_triples = 0
        agg_predicates: dict[str, int] = {}
        agg_entities_list: dict[str, dict] = {}
        for prefix in ctx["prefixes"]:
            try:
                s = kg_extract.kg_stats_for_wing(
                    palace_path=ctx["palace_path"],
                    source_prefix=prefix,
                    adapter_name="brain-project-kg")
            except Exception:
                continue
            agg_entities += int(s.get("entities", 0))
            agg_triples += int(s.get("triples", 0))
            for p in s.get("top_predicates", []) or []:
                k = p.get("predicate", "") or ""
                if k:
                    agg_predicates[k] = agg_predicates.get(k, 0) + int(p.get("count", 0))
            for e in s.get("top_entities", []) or []:
                eid = e.get("id", "") or ""
                if not eid:
                    continue
                cur = agg_entities_list.get(eid)
                if cur is None:
                    agg_entities_list[eid] = dict(e)
                else:
                    cur["degree"] = int(cur.get("degree", 0)) + int(e.get("degree", 0))

        # Sample triples — pull a small slice for the UI's "recent triples" list.
        sample_triples = self._kg_sample_triples(
            ctx["palace_path"], ctx["prefixes"], limit=50)

        # Extraction-log rows for this wing.
        try:
            log = kg_extract.list_kg_extraction_log(
                ctx["chats_db_path"], wing=ctx["wing"], limit=25)
        except Exception:
            log = []

        self._send_json({
            "agent_id": ctx["agent_id"],
            "project": ctx["proj_name"],
            "wing": ctx["wing"],
            "prefixes": ctx["prefixes"],
            "entities": agg_entities,
            "triples": agg_triples,
            "top_predicates": [
                {"predicate": k, "count": v}
                for k, v in sorted(agg_predicates.items(),
                                   key=lambda kv: -kv[1])[:30]
            ],
            "top_entities": sorted(agg_entities_list.values(),
                                   key=lambda e: -int(e.get("degree", 0)))[:30],
            "sample_triples": sample_triples,
            "extraction_log": log,
        })

    def _kg_sample_triples(self, palace_path: str, prefixes: list,
                           limit: int = 50) -> list:
        """Pull a small sample of triples (highest-confidence first) for any
        of the project's prefixes. Used by the UI as a quick spot-check."""
        kg_path = os.path.join(palace_path, "knowledge_graph.sqlite3")
        if not os.path.isfile(kg_path):
            return []
        import sqlite3 as _sql
        conn = _sql.connect(kg_path, timeout=5, check_same_thread=False)
        conn.row_factory = _sql.Row
        try:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(triples)")}
            has_adapter = "adapter_name" in cols
            has_drawer = "source_drawer_id" in cols
            scope_clause = " OR ".join(
                ["source_file LIKE ? || '%'"] * len(prefixes))
            params: list = list(prefixes)
            adapter_clause = " AND adapter_name = ? " if has_adapter else " "
            if has_adapter:
                params.append("brain-project-kg")
            sql = (
                "SELECT t.subject AS sub_id, e1.name AS sub_name, "
                "       t.predicate, "
                "       t.object AS obj_id, e2.name AS obj_name, "
                "       t.confidence, t.source_file, t.valid_from, "
                f"       {'t.source_drawer_id' if has_drawer else 'NULL'} AS source_drawer_id "
                "FROM triples t "
                "LEFT JOIN entities e1 ON t.subject = e1.id "
                "LEFT JOIN entities e2 ON t.object = e2.id "
                f"WHERE ({scope_clause}){adapter_clause}"
                "AND t.valid_to IS NULL "
                "ORDER BY t.confidence DESC, t.extracted_at DESC LIMIT ?"
            )
            params.append(int(limit))
            rows = conn.execute(sql, params).fetchall()
        finally:
            conn.close()
        return [{
            "subject": r["sub_name"] or r["sub_id"],
            "predicate": r["predicate"],
            "object": r["obj_name"] or r["obj_id"],
            "confidence": r["confidence"],
            "source_file": r["source_file"] or "",
            "source_drawer_id": r["source_drawer_id"] or "",
            "valid_from": r["valid_from"] or "",
        } for r in rows]

    def _handle_kg_entity_detail(self, params):
        """GET /v1/mempalace/kg/entity?agent_id=X&project=Y&name=Z —
        neighborhood for one entity, project-scoped."""
        ctx = self._kg_resolve_project_from_query(params)
        if ctx is None:
            return
        name = (params.get("name") or "").strip()
        if not name:
            self._send_json({"error": "name required"}, 400)
            return
        try:
            from mempalace.knowledge_graph import KnowledgeGraph
        except Exception as e:
            self._send_json({"error": f"KG import: {e}"}, 500)
            return
        kg_path = os.path.join(ctx["palace_path"], "knowledge_graph.sqlite3")
        if not os.path.isfile(kg_path):
            self._send_json({"error": "knowledge_graph.sqlite3 missing"}, 404)
            return
        kg = KnowledgeGraph(db_path=kg_path)
        try:
            triples = kg.query_entity(name, direction="both") or []
        except Exception as e:
            self._send_json({"error": f"query_entity: {e}"}, 500)
            return
        finally:
            try: kg.close()
            except Exception: pass
        prefixes = ctx["prefixes"]
        in_scope = []
        for t in triples:
            if not isinstance(t, dict):
                continue
            sf = t.get("source_file", "") or ""
            if not sf:
                continue
            if any(sf.startswith(p) for p in prefixes):
                in_scope.append(t)
        self._send_json({
            "entity": name,
            "project": ctx["proj_name"],
            "wing": ctx["wing"],
            "count": len(in_scope),
            "total_in_kg": len(triples),
            "triples": in_scope,
        })
    def _handle_kg_extraction_log(self, params):
        """GET /v1/mempalace/kg/extraction-log?agent_id=X&project=Y&limit=N
        — recent run log for the project's wing."""
        ctx = self._kg_resolve_project_from_query(params)
        if ctx is None:
            return
        try:
            from engine import kg_extract
        except Exception as e:
            self._send_json({"error": f"kg_extract unavailable: {e}"}, 500)
            return
        try:
            limit = max(1, min(500, int(params.get("limit") or 50)))
        except (TypeError, ValueError):
            limit = 50
        rows = kg_extract.list_kg_extraction_log(
            ctx["chats_db_path"], wing=ctx["wing"], limit=limit)
        self._send_json({
            "wing": ctx["wing"],
            "project": ctx["proj_name"],
            "count": len(rows),
            "rows": rows,
        })

    def _handle_kg_config_get(self):
        """GET /v1/mempalace/kg/config — current KG settings."""
        if self._require_auth() is None:
            return
        mcfg = engine._load_mempalace_config()
        kg_cfg = mcfg.get("kg") or {}
        self._send_json({
            "enabled": kg_cfg.get("enabled", True),
            "extraction_model": kg_cfg.get("extraction_model", ""),
            "profile": kg_cfg.get("profile", "normative"),
            # Project-wide default extraction method (per-project overridable
            # in the project view); wiki has its OWN method + profile knobs.
            "method": kg_cfg.get("method", "llm"),
            "wiki": bool(kg_cfg.get("wiki", False)),
            "wiki_method": kg_cfg.get("wiki_method", "llm"),
            "wiki_profile": kg_cfg.get("wiki_profile", "normative"),
            "scopes": kg_cfg.get("scopes") or ["projects"],
            "max_triples_per_drawer": kg_cfg.get("max_triples_per_drawer", 12),
            "min_confidence": kg_cfg.get("min_confidence", 0.5),
            "max_drawer_chars": kg_cfg.get("max_drawer_chars", 6000),
            "regenerate_closets": bool(kg_cfg.get("regenerate_closets", False)),
        })

    def _handle_kg_config_save(self):
        """POST /v1/mempalace/kg/config — save KG settings (admin).
        Invalidates extraction and/or closet cursors when relevant fields change."""
        user = self._require_role("admin")
        if user is None:
            return
        body = self._read_json() or {}
        config_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "config.json")
        try:
            cfg_disk = {}
            if os.path.exists(config_path):
                with open(config_path) as f:
                    cfg_disk = json.load(f)
            mp = cfg_disk.setdefault("mempalace", {})
            kg_old = dict(mp.get("kg") or {})
            kg = mp.setdefault("kg", {})
            if "enabled" in body:
                kg["enabled"] = bool(body["enabled"])
            if "extraction_model" in body:
                m = str(body["extraction_model"] or "").strip()
                if m:
                    models = cfg_disk.get("models") or {}
                    if m not in models:
                        self._send_json(
                            {"error": f"unknown model id: {m}"}, 400)
                        return
                kg["extraction_model"] = m
            if "profile" in body:
                p = str(body["profile"] or "").strip()
                if p not in ("normative", "generic"):
                    self._send_json({"error": f"unknown profile: {p}"}, 400)
                    return
                kg["profile"] = p
            # Project-wide default extraction method (llm|rules).
            if "method" in body:
                m = str(body["method"] or "").strip().lower()
                if m not in ("llm", "rules"):
                    self._send_json({"error": f"unknown method: {m}"}, 400)
                    return
                kg["method"] = m
            # Wiki KG knobs — independent of the project default.
            if "wiki" in body:
                kg["wiki"] = bool(body["wiki"])
            if "wiki_method" in body:
                wm = str(body["wiki_method"] or "").strip().lower()
                if wm not in ("llm", "rules"):
                    self._send_json({"error": f"unknown wiki_method: {wm}"}, 400)
                    return
                kg["wiki_method"] = wm
            if "wiki_profile" in body:
                wp = str(body["wiki_profile"] or "").strip().lower()
                if wp not in ("normative", "generic"):
                    self._send_json({"error": f"unknown wiki_profile: {wp}"}, 400)
                    return
                kg["wiki_profile"] = wp
            # Rule-based extraction can only emit generic predicates, so force
            # the matching profile when the method is rules (keeps config honest
            # with what the extractor actually produces; the UI greys it out too).
            if kg.get("method") == "rules":
                kg["profile"] = "generic"
            if kg.get("wiki_method") == "rules":
                kg["wiki_profile"] = "generic"
            if "max_triples_per_drawer" in body:
                kg["max_triples_per_drawer"] = max(
                    1, min(50, int(body["max_triples_per_drawer"])))
            if "min_confidence" in body:
                kg["min_confidence"] = max(
                    0.0, min(1.0, float(body["min_confidence"])))
            if "max_drawer_chars" in body:
                kg["max_drawer_chars"] = max(
                    500, min(20000, int(body["max_drawer_chars"])))
            if "scopes" in body:
                scopes = list(body["scopes"] or [])
                allowed = {"projects", "scheduled", "chats"}
                kg["scopes"] = [s for s in scopes if s in allowed] or ["projects"]
            if "regenerate_closets" in body:
                kg["regenerate_closets"] = bool(body["regenerate_closets"])
            with open(config_path, "w") as f:
                json.dump(cfg_disk, f, indent=2)
            engine._mempalace_config_cache = None

            # Invalidate cursors for fields that affect extraction quality.
            # Fields that change what triples get extracted → purge KG cursors.
            KG_FIELDS = {"extraction_model", "profile", "method",
                         "max_triples_per_drawer",
                         "min_confidence", "max_drawer_chars", "chunking_mode",
                         "source_chunk_chars"}
            # Fields that affect closet generation → purge closet cursor.
            CLOSET_FIELDS = {"extraction_model", "regenerate_closets"}
            kg_changed = any(kg_old.get(k) != kg.get(k) for k in KG_FIELDS)
            closet_changed = any(kg_old.get(k) != kg.get(k) for k in CLOSET_FIELDS)
            invalidated = {}
            if kg_changed or closet_changed:
                try:
                    from engine import kg_extract
                    chats_db = os.path.join(engine.AGENTS_DIR, "main", "chats.db")
                    palace_path = (mp.get("palace_path") or "")
                    # Walk all project wings and purge the relevant cursors.
                    for agent_dir in os.scandir(engine.AGENTS_DIR):
                        if not agent_dir.is_dir():
                            continue
                        proj_root = os.path.join(agent_dir.path, "projects")
                        if not os.path.isdir(proj_root):
                            continue
                        for pdir in os.scandir(proj_root):
                            if not pdir.is_dir():
                                continue
                            pjson = os.path.join(pdir.path, "project.json")
                            if not os.path.exists(pjson):
                                continue
                            try:
                                with open(pjson) as f:
                                    pdata = json.load(f)
                                pid = pdata.get("id") or ""
                                if not pid:
                                    continue
                                wing = f"project__{pid}"
                                if kg_changed:
                                    kg_extract.kg_purge_for_scope(
                                        palace_path=palace_path,
                                        source_prefix="",
                                        adapter_name="brain-project-kg",
                                        chats_db_path=chats_db,
                                        wing=wing,
                                    )
                                if closet_changed:
                                    kg_extract.closet_regen_purge_for_scope(
                                        chats_db_path=chats_db,
                                        palace_wing=wing,
                                    )
                            except Exception:
                                pass
                    invalidated = {
                        "kg_cursors_cleared": kg_changed,
                        "closet_cursors_cleared": closet_changed,
                    }
                except Exception as e:
                    invalidated = {"invalidation_error": str(e)}

            self._send_json({"status": "saved", "kg": kg, **invalidated})
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    def _handle_doctor(self):
        """GET /v1/doctor — static config-health checks (admin, read-only).
        Detects model→provider misconfig, provider gaps, MemPalace + KG health."""
        if self._require_role("admin") is None:
            return
        try:
            from engine import doctor
            findings = doctor.run_static_checks()
            self._send_json({"findings": findings,
                             "summary": doctor.summarize(findings),
                             "mode": "static"})
        except Exception as e:
            self._send_json({"error": f"{type(e).__name__}: {e}"}, 500)

    def _handle_lib_versions(self):
        """GET /v1/lib-versions — installed versions + install dates of the
        external libraries Brain depends on, across all four venvs (admin,
        read-only). Shells the SDK/crawl4ai venv interpreters for theirs."""
        if self._require_role("admin") is None:
            return
        try:
            from engine import lib_versions
            self._send_json(lib_versions.collect())
        except Exception as e:
            self._send_json({"error": f"{type(e).__name__}: {e}"}, 500)

    def _handle_doctor_live(self):
        """POST /v1/doctor/live — static checks PLUS on-demand live probes
        (test embedding, provider credential resolution). Slower (admin)."""
        if self._require_role("admin") is None:
            return
        try:
            from engine import doctor
            findings = doctor.run_static_checks() + doctor.run_live_checks()
            self._send_json({"findings": findings,
                             "summary": doctor.summarize(findings),
                             "mode": "live"})
        except Exception as e:
            self._send_json({"error": f"{type(e).__name__}: {e}"}, 500)

    # ── Service Models — unified editor for every service-model slot ─────────
    # The model-ref slots the Doctor checks live scattered across TWO config
    # files: most in config.json (default/summary/fan-out/KG/OCR), but TTS +
    # transcribe live in tools_config.json (via get_tool_config/save_tool_config).
    # This pair of handlers presents them as ONE editable surface. Saves write
    # back to the correct file per slot. FAIL-LOUD: a model id that isn't in
    # models{} (or, for OCR, a provider not in providers{}) is rejected 400 —
    # never silently coerced. Empty is allowed and surfaced as 'unset' so the
    # Doctor's config-model-ref check flags it.
    #
    # Slot registry: (key, label, file, capability-or-None). `file` is
    # 'config' (config.json) or 'tools' (tools_config.json). OCR is special-
    # cased (it has engine + provider + model, not a single model id).
    _SERVICE_MODEL_SLOTS = [
        ("default_model", "Server-Standardmodell", "config", None),
        ("chat_summary_model", "Chat-Zusammenfassung", "config", None),
        ("background_task_model", "Fan-out-Hintergrundmodell", "config", None),
        ("kg_extraction_model", "KG-Extraktion", "config", None),
        ("tts_model", "Text-to-Speech", "tools", "tts"),
        ("transcribe_model", "Transkription (STT)", "tools", "audio"),
    ]

    def _service_models_read(self):
        """Read every slot's current value + the OCR block from disk config."""
        import brain as _brain
        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "config.json")
        cfg = {}
        try:
            with open(config_path) as f:
                cfg = json.load(f)
        except (OSError, ValueError):
            cfg = {}
        tool_cfg = {}
        try:
            tool_cfg = _brain.get_tool_config() or {}
        except Exception:
            tool_cfg = {}
        kg = ((cfg.get("mempalace") or {}).get("kg") or {})
        values = {
            "default_model": cfg.get("default_model", "") or "",
            "chat_summary_model": cfg.get("chat_summary_model", "") or "",
            "background_task_model": cfg.get("background_task_model", "") or "",
            "kg_extraction_model": kg.get("extraction_model", "") or "",
            "tts_model": (tool_cfg.get("text_to_speech") or {}).get("default_model", "") or "",
            "transcribe_model": (tool_cfg.get("transcribe_audio") or {}).get("default_model", "") or "",
        }
        ocr = cfg.get("ocr") or {}
        ocr_block = {
            "engine": ocr.get("engine", "none"),
            "provider": ocr.get("provider", "") or "",
            "model": ocr.get("model", "") or "",
        }
        return cfg, values, ocr_block

    def _handle_service_models_get(self):
        """GET /v1/services/models — every service-model slot + resolve status
        + the option lists (enabled models, providers) for the dropdowns.
        Admin read-only."""
        if self._require_role("admin") is None:
            return
        try:
            import brain as _brain
            _cfg, values, ocr_block = self._service_models_read()
            models = getattr(_brain, "_models_config", None) or {}
            providers = (_cfg.get("providers") or {})

            def _resolve(ref):
                """('ok'|'unset'|'missing'|'disabled', why) — mirrors the
                Doctor's tolerant scoped/base-id resolution."""
                if not ref:
                    return "unset", ""
                prov = ref.split("/", 1)[0] if "/" in ref else None
                mid = ref.split("/", 1)[1] if "/" in ref else ref
                if prov and prov not in providers:
                    return "missing", f"Provider {prov!r} existiert nicht"
                cands = [mc for k, mc in models.items()
                         if isinstance(mc, dict)
                         and (k == ref or k == mid or mc.get("base_model_id") == mid)]
                if not cands:
                    return "missing", "Modell-ID nicht in models{}"
                if any(mc.get("enabled") is not False for mc in cands):
                    return "ok", ""
                return "disabled", "Modell ist deaktiviert"

            slots = []
            for key, label, _file, cap in self._SERVICE_MODEL_SLOTS:
                ref = values.get(key, "")
                status, why = _resolve(ref)
                slots.append({"key": key, "label": label, "value": ref,
                              "capability": cap, "status": status, "why": why})

            # Enabled model option list (id + display + capabilities + is_local).
            model_opts = []
            for mid, mc in models.items():
                if not isinstance(mc, dict) or mc.get("enabled") is False:
                    continue
                model_opts.append({
                    "id": mid,
                    "display": mc.get("display_name") or mid,
                    "is_local": bool(mc.get("is_local")),
                    "capabilities": list(mc.get("capabilities") or []),
                })
            model_opts.sort(key=lambda m: (-1 if m["is_local"] else 0, m["display"].lower()))

            # OCR resolve status (provider/model into one ref).
            ocr_status = "unset"
            ocr_why = ""
            if ocr_block["engine"] in ("mistral_ocr", "auto"):
                if not ocr_block["provider"] or not ocr_block["model"]:
                    ocr_status, ocr_why = "missing", "Provider und Modell erforderlich"
                elif ocr_block["provider"] not in providers:
                    ocr_status, ocr_why = "missing", f"Provider {ocr_block['provider']!r} existiert nicht"
                else:
                    ocr_status = "ok"
            elif ocr_block["engine"] == "local_vision":
                lv = (_cfg.get("ocr") or {}).get("local_vision_model") or ""
                ocr_status = "ok" if lv else "missing"
                ocr_why = "" if lv else "local_vision_model erforderlich"
            else:  # none
                ocr_status, ocr_why = "off", "OCR deaktiviert"

            self._send_json({
                "slots": slots,
                "ocr": {**ocr_block, "status": ocr_status, "why": ocr_why},
                "model_options": model_opts,
                "providers": sorted(providers.keys()),
            })
        except Exception as e:
            self._send_json({"error": f"{type(e).__name__}: {e}"}, 500)

    def _handle_service_models_save(self):
        """POST /v1/services/models — save any subset of slots (admin).
        Body keys: any of the slot keys (model id strings, '' to unset) and/or
        an `ocr` object {engine, provider, model}. FAIL-LOUD on unknown model/
        provider (400). Writes config.json slots + ocr; routes tts/transcribe
        through save_tool_config. Busts the relevant caches."""
        user = self._require_role("admin")
        if user is None:
            return
        import brain as _brain
        body = self._read_json() or {}
        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "config.json")
        try:
            cfg = {}
            if os.path.exists(config_path):
                with open(config_path) as f:
                    cfg = json.load(f)
            models = getattr(_brain, "_models_config", None) or cfg.get("models") or {}
            providers = cfg.get("providers") or {}

            def _validate_model(ref):
                """Empty ok (unset). Else must match an enabled-or-known model id."""
                ref = (ref or "").strip()
                if not ref:
                    return ""
                mid = ref.split("/", 1)[1] if "/" in ref else ref
                ok = any(k == ref or k == mid or
                         (isinstance(mc, dict) and mc.get("base_model_id") == mid)
                         for k, mc in models.items())
                if not ok:
                    raise ValueError(f"Unbekannte Modell-ID: {ref}")
                return ref

            # config.json slots
            if "default_model" in body:
                cfg["default_model"] = _validate_model(body["default_model"])
            if "chat_summary_model" in body:
                cfg["chat_summary_model"] = _validate_model(body["chat_summary_model"])
            if "background_task_model" in body:
                cfg["background_task_model"] = _validate_model(body["background_task_model"])
            if "kg_extraction_model" in body:
                mp = cfg.setdefault("mempalace", {})
                kg = mp.setdefault("kg", {})
                kg["extraction_model"] = _validate_model(body["kg_extraction_model"])
            # OCR block
            if "ocr" in body and isinstance(body["ocr"], dict):
                o = body["ocr"]
                ocr = cfg.setdefault("ocr", {})
                if "engine" in o:
                    eng = str(o["engine"] or "none").strip()
                    if eng not in ("mistral_ocr", "local_vision", "auto", "none"):
                        self._send_json({"error": f"Unbekannte OCR-Engine: {eng}"}, 400)
                        return
                    ocr["engine"] = eng
                if "provider" in o:
                    p = str(o["provider"] or "").strip()
                    if p and p not in providers:
                        self._send_json({"error": f"Unbekannter Provider: {p}"}, 400)
                        return
                    ocr["provider"] = p
                if "model" in o:
                    ocr["model"] = str(o["model"] or "").strip()

            # tools_config.json slots (tts/transcribe) — route through the
            # tool-config saver so we don't clobber the other tool integrations.
            tool_updates = {}
            if "tts_model" in body:
                tts = dict((_brain.get_tool_config().get("text_to_speech") or {}))
                tts["default_model"] = _validate_model(body["tts_model"])
                tool_updates["text_to_speech"] = tts
            if "transcribe_model" in body:
                ta = dict((_brain.get_tool_config().get("transcribe_audio") or {}))
                ta["default_model"] = _validate_model(body["transcribe_model"])
                tool_updates["transcribe_audio"] = ta

            with open(config_path, "w") as f:
                json.dump(cfg, f, indent=2)
            # Bust the mempalace cache (KG slot). The top-level slots
            # (default/summary/fan-out) + OCR are read from server_config /
            # config.json on demand and fully refresh on restart — same as the
            # existing server-config + KG-config save endpoints.
            engine._mempalace_config_cache = None
            if tool_updates:
                _brain.save_tool_config(tool_updates)

            self._send_json({"ok": True})
        except ValueError as e:
            self._send_json({"error": str(e)}, 400)
        except Exception as e:
            self._send_json({"error": f"{type(e).__name__}: {e}"}, 500)

    def _handle_kg_reextract(self):
        """POST /v1/mempalace/kg/reextract — purge a project's triples and
        kick the daemon to rebuild. Body: {agent_id, project, source_prefix?}.
        Admin or project owner."""
        user = self._require_auth()
        if user is None:
            return
        body = self._read_json() or {}
        agent_id = (body.get("agent_id") or "").strip()
        proj_name = (body.get("project") or "").strip()
        if not agent_id or not proj_name:
            self._send_json({"error": "agent_id and project required"}, 400)
            return
        project = self._project_access_check(agent_id, proj_name,
                                             require_manage=True)
        if project is None:
            return
        try:
            from engine import kg_extract
        except Exception as e:
            self._send_json({"error": f"kg_extract unavailable: {e}"}, 500)
            return
        mcfg = engine._load_mempalace_config()
        palace_path = mcfg.get("palace_path", "")
        chats_db_path = os.path.join(engine.AGENTS_DIR, "main", "chats.db")
        pid = project.get("id") or ""
        if not pid:
            self._send_json({"error": "project missing id"}, 400)
            return
        wing = _project_wing(pid)
        # Prefix(es) to purge: either the explicit one from the body, or the
        # union of project_dir + every input_folder. Resolve symlinks so the
        # purge matches what the miner actually stored in source_file
        # (macOS /tmp → /private/tmp, etc.).
        def _norm_p(p: str) -> str:
            try:
                r = os.path.realpath(p)
            except OSError:
                r = p
            if r and not r.endswith(os.sep):
                r += os.sep
            return r
        explicit_prefix = (body.get("source_prefix") or "").strip()
        if explicit_prefix:
            prefixes = [_norm_p(explicit_prefix)]
        else:
            pdir = project.get("dir") or os.path.join(
                engine.AGENTS_DIR, agent_id, "projects", proj_name)
            prefixes = [_norm_p(pdir)]
            for entry in (project.get("input_folders") or []):
                fp = (entry.get("path") or "").strip()
                if fp:
                    prefixes.append(_norm_p(fp))

        total_triples = 0
        total_progress = 0
        for prefix in prefixes:
            try:
                res = kg_extract.kg_purge_for_scope(
                    palace_path=palace_path,
                    source_prefix=prefix,
                    adapter_name="brain-project-kg",
                    chats_db_path=chats_db_path,
                    wing=wing,
                )
                total_triples += int(res.get("triples_deleted", 0))
                total_progress += int(res.get("progress_deleted", 0))
            except Exception as e:
                self._send_json({"error": f"purge {prefix} failed: {e}"}, 500)
                return
        # Kick the project-sync daemon to rebuild.
        try:
            with _project_sync_lock:
                _project_sync_requests.add((agent_id, proj_name))
            _project_sync_wakeup.set()
        except Exception:
            pass
        # Audit-log the manual reextract trigger.
        try:
            _audit_log.log_action(  # type: ignore[name-defined]
                user_id=user.get("user_id", ""),
                action_type="kg_reextract",
                tool_name="mempalace_kg",
                args_summary=f"{agent_id}/{proj_name} prefixes={len(prefixes)}",
                source="api",
            )
        except Exception:
            pass
        self._send_json({
            "status": "purged_and_queued",
            "triples_deleted": total_triples,
            "progress_deleted": total_progress,
            "prefixes": prefixes,
        })

    def _handle_mempalace_stats(self):
        """GET /v1/mempalace/stats — palace overview for admin dashboard."""
        mcfg = engine._load_mempalace_config()
        if not mcfg.get("enabled", True):
            self._send_json({"enabled": False, "error": "MemPalace disabled in config"})
            return
        palace_path = mcfg.get("palace_path", "")
        if not palace_path or not os.path.isdir(palace_path):
            self._send_json({"enabled": True, "error": f"Palace path not found: {palace_path}"})
            return

        ok, err = engine._ensure_mempalace_importable()
        if not ok:
            self._send_json({"enabled": True, "error": err})
            return

        try:
            from mempalace.mcp_server import tool_status, tool_get_taxonomy, tool_list_tunnels, tool_graph_stats, tool_kg_stats
            from mempalace.palace import get_closets_collection

            status = tool_status()
            taxonomy = tool_get_taxonomy()
            tunnels = tool_list_tunnels()
            graph = tool_graph_stats()

            # Closet count
            closet_count = 0
            try:
                closets_col = get_closets_collection(palace_path, create=False)
                if closets_col:
                    closet_count = closets_col.count()
            except Exception:
                pass

            # Knowledge graph stats
            kg = {}
            try:
                kg = tool_kg_stats()
            except Exception:
                pass

            # Chat sync stats from cursor table
            sync_stats = {"synced_sessions": 0, "total_drawers_filed": 0, "last_sync": None}
            try:
                with _db_conn() as conn:
                    row = conn.execute("""
                        SELECT COUNT(*) as cnt,
                               SUM(last_message_id) as total_msgs,
                               MAX(updated_at) as last_update
                        FROM chat_mempalace_sync
                    """).fetchone()
                    if row:
                        sync_stats["synced_sessions"] = row[0] or 0
                        sync_stats["total_drawers_filed"] = row[1] or 0
                        sync_stats["last_sync"] = row[2]
            except Exception:
                pass

            # Mining config summary
            mine_cfg = mcfg.get("mine", {})
            chat_sync_cfg = mcfg.get("chat_sync", {})

            # Palace file size
            palace_size_mb = 0
            try:
                db_path = os.path.join(palace_path, "chroma.sqlite3")
                if os.path.exists(db_path):
                    palace_size_mb = round(os.path.getsize(db_path) / (1024 * 1024), 2)
            except Exception:
                pass

            # WAL recent activity (last 100 entries)
            wal_activity = {"total_ops": 0, "recent_ops": [], "ops_by_type": {}}
            try:
                wal_path = os.path.join(os.path.dirname(palace_path), "wal", "write_log.jsonl")
                if os.path.exists(wal_path):
                    lines = []
                    with open(wal_path, "r") as f:
                        for line in f:
                            lines.append(line)
                    wal_activity["total_ops"] = len(lines)
                    for line in lines[-50:]:
                        try:
                            entry = json.loads(line)
                            wal_activity["recent_ops"].append({
                                "timestamp": entry.get("timestamp", ""),
                                "operation": entry.get("operation", ""),
                                "wing": (entry.get("params") or {}).get("wing", ""),
                                "room": (entry.get("params") or {}).get("room", ""),
                            })
                            op = entry.get("operation", "unknown")
                            wal_activity["ops_by_type"][op] = wal_activity["ops_by_type"].get(op, 0) + 1
                        except (json.JSONDecodeError, KeyError):
                            pass
                    wal_activity["recent_ops"] = wal_activity["recent_ops"][-20:]
            except Exception:
                pass

            # Wing breakdown with user isolation info
            wings_detail = {}
            tax = taxonomy.get("taxonomy", {})
            # Build user_id → display_name lookup
            _user_names = {}
            try:
                for u in _auth_mod.AuthDB.list_users():
                    _user_names[u["id"]] = u.get("display_name") or u.get("username") or u["id"]
            except Exception:
                pass
            for wing_name, rooms in tax.items():
                is_user_scoped = "--" in wing_name
                user_id = wing_name.split("--")[0] if is_user_scoped else None
                wings_detail[wing_name] = {
                    "rooms": rooms,
                    "drawer_count": sum(rooms.values()),
                    "room_count": len(rooms),
                    "user_scoped": is_user_scoped,
                    "user_id": user_id,
                    "user_name": _user_names.get(user_id, user_id) if user_id else None,
                }

            # Hall stats from drawer metadata
            halls = {}
            try:
                all_meta = status.get("_all_meta") or []
                if not all_meta:
                    from mempalace.palace import get_collection as _gc
                    _dcol = _gc(palace_path, create=False)
                    if _dcol:
                        _dr = _dcol.get(include=["metadatas"])
                        all_meta = _dr.get("metadatas", [])
                for m in all_meta:
                    h = m.get("hall", "")
                    if not h:
                        continue
                    if h not in halls:
                        halls[h] = {"count": 0, "rooms": {}}
                    halls[h]["count"] += 1
                    r = m.get("room", "")
                    if r:
                        halls[h]["rooms"][r] = halls[h]["rooms"].get(r, 0) + 1
            except Exception:
                pass

            self._send_json({
                "enabled": True,
                "palace_path": palace_path,
                "palace_size_mb": palace_size_mb,
                "total_drawers": status.get("total_drawers", 0),
                "total_closets": closet_count,
                "halls": halls,
                "wings": wings_detail,
                "wing_count": len(wings_detail),
                "room_count": status.get("total_rooms", len(set(r for rooms in tax.values() for r in rooms))),
                "graph": graph,
                "tunnels": tunnels,
                "knowledge_graph": kg,
                "chat_sync": sync_stats,
                "wal": wal_activity,
                "config": {
                    "mine_enabled": mine_cfg.get("enabled", True),
                    "mine_interval_s": mine_cfg.get("interval_seconds", 1800),
                    "mine_sources": len(mine_cfg.get("sources", [])),
                    "chat_sync_enabled": chat_sync_cfg.get("enabled", True),
                    "chat_sync_interval_s": chat_sync_cfg.get("interval_seconds", 60),
                    "chat_sync_build_closets": chat_sync_cfg.get("build_closets", True),
                },
            })
        except Exception as e:
            self._send_json({"enabled": True, "error": f"Failed to gather stats: {type(e).__name__}: {e}"}, 500)

    def _handle_mempalace_drawers(self):
        """GET /v1/mempalace/drawers?wing=X&room=Y — list drawers for treemap drill-down."""
        params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        wing = (params.get("wing") or [None])[0]
        room = (params.get("room") or [None])[0]

        mcfg = engine._load_mempalace_config()
        palace_path = mcfg.get("palace_path", "")
        if not palace_path or not os.path.isdir(palace_path):
            self._send_json({"error": "Palace not found"}, 404)
            return
        ok, err = engine._ensure_mempalace_importable()
        if not ok:
            self._send_json({"error": err}, 500)
            return

        try:
            from mempalace.palace import get_collection, get_closets_collection
            col = get_collection(palace_path, create=False)
            result = col.get(include=["metadatas", "documents"])
            drawers = []
            for did, meta, doc in zip(result["ids"], result["metadatas"], result["documents"]):
                m_wing = meta.get("wing", "")
                m_room = meta.get("room", "")
                if wing and m_wing != wing:
                    continue
                if room and m_room != room:
                    continue
                drawers.append({
                    "id": did,
                    "wing": m_wing,
                    "room": m_room,
                    "hall": meta.get("hall", ""),
                    "source_file": meta.get("source_file", ""),
                    "filed_at": meta.get("filed_at", ""),
                    "added_by": meta.get("added_by", ""),
                    "text": (doc or "")[:300],
                })
            closets = []
            try:
                ccol = get_closets_collection(palace_path, create=False)
                if ccol:
                    cresult = ccol.get(include=["metadatas", "documents"])
                    for cid, cmeta, cdoc in zip(cresult["ids"], cresult["metadatas"], cresult["documents"]):
                        c_wing = cmeta.get("wing", "")
                        c_room = cmeta.get("room", "")
                        if wing and c_wing != wing:
                            continue
                        if room and c_room != room:
                            continue
                        closets.append({
                            "id": cid,
                            "wing": c_wing,
                            "room": c_room,
                            "source_file": cmeta.get("source_file", ""),
                            "drawer_count": cmeta.get("drawer_count", 0),
                            "text": (cdoc or "")[:300],
                        })
            except Exception:
                pass
            self._send_json({"drawers": drawers, "count": len(drawers), "closets": closets})
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    # --- Context Management handlers ---

    def _handle_context_config_get(self):
        """GET /v1/context/config — return context management configuration."""
        if not engine._context_manager:
            self._send_json(engine._CONTEXT_CONFIG_DEFAULTS)
            return
        self._send_json(engine._context_manager.get_config())

    def _handle_context_config_save(self):
        """POST /v1/context/config — save context management configuration."""
        body = self._read_json()
        if not body:
            self._send_json({"error": "No config provided"}, 400)
            return
        if not engine._context_manager:
            engine._context_manager = engine.ContextManager()
        engine._context_manager.save_config(body)
        self._send_json({"status": "saved", "config": engine._context_manager.get_config()})

    def _handle_context_compact(self):
        """POST /v1/context/compact — manually trigger compaction for a session."""
        body = self._read_json()
        session_id = body.get("session_id", "")
        if not session_id:
            self._send_json({"error": "Missing session_id"}, 400)
            return
        if self._session_access_check(session_id, require_manage=True) is None:
            return
        session = sessions.get(session_id)
        if not session:
            self._send_json({"error": "Session not found"}, 404)
            return
        if not engine._context_manager:
            self._send_json({"error": "Context manager not initialized"}, 500)
            return
        try:
            before = engine._estimate_conversation_tokens(session.messages)
            # Force compaction regardless of threshold
            result = engine._context_manager.check_and_compact(
                session.messages, session.id, session.model,
                session.api_key, session.base_url,
                max_tokens=session.max_context,
                force=True,
            )
            with session.lock:
                session.messages = result[0]
            # Persist: mark old messages as compacted, insert new summary messages
            if result[1]:
                try:
                    with _db_conn() as conn:
                        # Mark ALL existing messages as compacted (preserves originals for search)
                        conn.execute(
                            "UPDATE messages SET compacted = 1 WHERE session_id = ? AND (compacted = 0 OR compacted IS NULL)",
                            (session_id,)
                        )
                        # Insert the new compacted message set (summaries + fresh tail).
                        # Tag every inserted row `lcm_inserted` so uncompact can delete
                        # exactly the LCM-produced rows — across multiple compaction
                        # rounds — without mistaking a prior round's synthetic block or
                        # re-rendered tail for an original message.
                        for msg in session.messages:
                            role = msg.get("role", "user")
                            content = msg.get("content", "")
                            c = json.dumps(content) if not isinstance(content, str) else content
                            md = dict(msg.get("metadata") or {})
                            md["lcm_inserted"] = True
                            meta = json.dumps(md)
                            conn.execute(
                                "INSERT INTO messages (session_id, role, content, metadata, compacted) VALUES (?, ?, ?, ?, 0)",
                                (session_id, role, c, meta)
                            )
                        conn.commit()
                except Exception as e:
                    print(f"  [WARN] Compact DB persist: {e}", flush=True)
            after = engine._estimate_conversation_tokens(session.messages)
            stats = engine._context_manager.get_stats(session_id)
            self._send_json({
                "status": "compacted" if result[1] else "no_change",
                "before_tokens": before,
                "after_tokens": after,
                "before_pct": int(before / session.max_context * 100) if session.max_context else 0,
                "after_pct": int(after / session.max_context * 100) if session.max_context else 0,
                "stats": stats,
            })
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    def _handle_context_uncompact(self):
        """POST /v1/context/uncompact — restore original messages for a session."""
        body = self._read_json()
        session_id = body.get("session_id", "")
        if not session_id:
            self._send_json({"error": "Missing session_id"}, 400)
            return
        if self._session_access_check(session_id, require_manage=True) is None:
            return
        try:
            with _db_conn() as conn:
                # LCM-inserted rows (synthetic summary blocks + re-rendered tails,
                # possibly from multiple compaction rounds) carry
                # metadata.lcm_inserted=True. Delete exactly those — never an
                # original — then restore every remaining row to compacted=0.
                # Legacy fallback: rows from before the tag existed have no marker,
                # so on a session with NO tagged rows fall back to the old
                # "delete ids above the highest compacted=1 id" heuristic.
                tagged = conn.execute(
                    "SELECT COUNT(*) FROM messages WHERE session_id = ? "
                    "AND metadata LIKE '%\"lcm_inserted\": true%'",
                    (session_id,)
                ).fetchone()[0]
                has_originals = conn.execute(
                    "SELECT COUNT(*) FROM messages WHERE session_id = ? AND compacted = 1",
                    (session_id,)
                ).fetchone()[0]
                if not has_originals:
                    self._send_json({"status": "no_originals"})
                    return
                if tagged:
                    conn.execute(
                        "DELETE FROM messages WHERE session_id = ? "
                        "AND metadata LIKE '%\"lcm_inserted\": true%'",
                        (session_id,)
                    )
                else:
                    # Legacy heuristic for sessions compacted before the tag existed.
                    row = conn.execute(
                        "SELECT MAX(id) FROM messages WHERE session_id = ? AND compacted = 1",
                        (session_id,)
                    ).fetchone()
                    max_orig_id = row[0] if row and row[0] else 0
                    conn.execute(
                        "DELETE FROM messages WHERE session_id = ? AND id > ?",
                        (session_id, max_orig_id)
                    )
                # Restore remaining rows as live originals
                conn.execute(
                    "UPDATE messages SET compacted = 0 WHERE session_id = ? AND compacted = 1",
                    (session_id,)
                )
                conn.commit()
                orig_count = conn.execute(
                    "SELECT COUNT(*) FROM messages WHERE session_id = ?",
                    (session_id,)
                ).fetchone()[0]
            # Clear summaries from context.db
            if engine._context_manager:
                try:
                    ctx_conn = engine._context_conn()
                    ctx_conn.execute("DELETE FROM summaries WHERE session_id = ?", (session_id,))
                    ctx_conn.commit()
                except Exception:
                    pass
            # Reload session messages in memory
            session = sessions.get(session_id)
            if session:
                fresh = ChatDB.load_messages(session_id)
                with session.lock:
                    session.messages = [
                        {"role": m["role"], "content": m["content"],
                         **({"metadata": m["metadata"]} if m.get("metadata") else {})}
                        for m in fresh
                    ]
            self._send_json({"status": "restored", "message_count": orig_count})
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    def _handle_context_stats(self):
        """GET /v1/context/stats?session_id=X — context stats for a session."""
        from urllib.parse import parse_qs, urlparse
        qs = parse_qs(urlparse(self.path).query)
        session_id = (qs.get("session_id") or [""])[0]
        if not engine._context_manager:
            self._send_json({"error": "Context manager not initialized"})
            return
        if not session_id:
            self._send_json({"error": "Missing session_id"}, 400)
            return
        stats = engine._context_manager.get_stats(session_id)
        self._send_json(stats)
