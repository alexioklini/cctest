"""Agent, team, and skill management handlers.

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


class AdminAgentsHandlers:
    """Agent, team, and skill management handlers."""

    def _handle_agents_activity(self):
        """GET /v1/agents/activity — which agents are currently doing something."""
        activity = {}  # agent_id -> list of activity types

        # 1. Streaming chat sessions
        with sessions._lock:
            for s in sessions._sessions.values():
                if hasattr(s, 'cancel_token') and not s.cancel_token.cancelled:
                    # Check if session has an active worker thread
                    # A session is "streaming" if it was recently active and not cancelled
                    pass

        # Simpler: check which sessions are in streaming state via agentChats client-side
        # Instead, track streaming sessions server-side
        with sessions._lock:
            for s in sessions._sessions.values():
                if not isinstance(s, Session):
                    continue  # skip loading sentinels
                if hasattr(s, '_streaming') and s._streaming:
                    activity.setdefault(s.agent_id, []).append("chat")

        # 2. Running delegated tasks
        if engine._task_runner:
            for t in engine._task_runner.list_tasks():
                if t.get("status") == "running":
                    aid = t.get("agent", "main")
                    if "delegate" not in activity.get(aid, []):
                        activity.setdefault(aid, []).append("delegate")

        # 3. Running scheduled tasks
        if engine._scheduler:
            for r in engine._scheduler.get_running_tasks():
                aid = r.get("agent", "main")
                if "schedule" not in activity.get(aid, []):
                    activity.setdefault(aid, []).append("schedule")

        self._send_json({"activity": activity})

    def _handle_teams_get(self):
        """GET /v1/teams — return team structure."""
        self._send_json(engine.get_team_structure())

    def _handle_teams_post(self):
        """POST /v1/teams — create, update, dissolve, or move teams."""
        body = self._read_json()
        action = body.get("action", "")

        if action == "create":
            members = body.get("members", [])
            head_id = body.get("head", "")
            if not members:
                self._send_json({"error": "members is required (at least one agent)"}, 400)
                return
            if not head_id:
                head_id = members[0]
            # Ensure head is in members
            if head_id not in members:
                members.insert(0, head_id)
            # Validate members exist
            available = engine.list_agents()
            invalid = [m for m in members if m not in available]
            if invalid:
                self._send_json({"error": f"Unknown agents: {', '.join(invalid)}"}, 400)
                return
            if head_id not in available:
                self._send_json({"error": f"Head agent '{head_id}' not found"}, 404)
                return
            # Store team config on the head agent
            team_name = body.get("name", "")
            team_desc = body.get("description", "")
            team_avatar = body.get("avatar", "")
            cfg_path = os.path.join(engine.AGENTS_DIR, head_id, "agent.json")
            try:
                with open(cfg_path, "r") as f:
                    cfg = json.load(f)
                team_data = {"members": members, "head": head_id}
                if team_name:
                    team_data["name"] = team_name
                if team_desc:
                    team_data["description"] = team_desc
                if team_avatar:
                    team_data["avatar"] = team_avatar
                cfg["team"] = team_data
                with open(cfg_path, "w") as f:
                    json.dump(cfg, f, indent=2)
                self._send_json({"status": "created", "head": head_id, "members": members})
            except Exception as e:
                self._send_json({"error": str(e)}, 500)

        elif action == "update":
            team_id = body.get("team_id", body.get("team_head", ""))
            if not team_id:
                self._send_json({"error": "team_id is required"}, 400)
                return
            cfg_path = os.path.join(engine.AGENTS_DIR, team_id, "agent.json")
            try:
                with open(cfg_path, "r") as f:
                    cfg = json.load(f)
                if not isinstance(cfg.get("team"), dict):
                    self._send_json({"error": f"'{team_id}' does not hold a team config"}, 400)
                    return
                # Validate members exist
                available = engine.list_agents()
                if "members" in body:
                    members = body["members"]
                    invalid = [m for m in members if m not in available]
                    if invalid:
                        self._send_json({"error": f"Unknown agents: {', '.join(invalid)}"}, 400)
                        return
                    cfg["team"]["members"] = members
                if "head" in body:
                    new_head = body["head"]
                    # Ensure head is in members
                    if new_head not in cfg["team"].get("members", []):
                        cfg["team"]["members"].insert(0, new_head)
                    cfg["team"]["head"] = new_head
                    # If head changed, need to move team config to new head agent
                    old_head = cfg["team"].get("head", team_id)
                    if new_head != team_id:
                        # Move team config to new head's agent.json
                        new_cfg_path = os.path.join(engine.AGENTS_DIR, new_head, "agent.json")
                        with open(new_cfg_path, "r") as f:
                            new_cfg = json.load(f)
                        new_cfg["team"] = cfg.pop("team")
                        with open(new_cfg_path, "w") as f:
                            json.dump(new_cfg, f, indent=2)
                        with open(cfg_path, "w") as f:
                            json.dump(cfg, f, indent=2)
                        self._send_json({"status": "updated", "team_id": new_head, "head": new_head})
                        return
                if "name" in body:
                    cfg["team"]["name"] = body["name"]
                if "description" in body:
                    cfg["team"]["description"] = body["description"]
                if "avatar" in body:
                    cfg["team"]["avatar"] = body["avatar"]
                with open(cfg_path, "w") as f:
                    json.dump(cfg, f, indent=2)
                self._send_json({"status": "updated", "team_id": team_id})
            except Exception as e:
                self._send_json({"error": str(e)}, 500)

        elif action == "dissolve":
            team_id = body.get("team_id", body.get("team_head", body.get("agent", "")))
            if not team_id:
                self._send_json({"error": "team_id is required"}, 400)
                return
            cfg_path = os.path.join(engine.AGENTS_DIR, team_id, "agent.json")
            try:
                with open(cfg_path, "r") as f:
                    cfg = json.load(f)
                cfg.pop("team", None)
                with open(cfg_path, "w") as f:
                    json.dump(cfg, f, indent=2)
                self._send_json({"status": "dissolved", "team_id": team_id})
            except Exception as e:
                self._send_json({"error": str(e)}, 500)

        elif action == "move":
            agent_id = body.get("agent", "")
            from_team = body.get("from_team", "")
            to_team = body.get("to_team", "")
            if not agent_id:
                self._send_json({"error": "agent is required"}, 400)
                return
            try:
                # Remove from source team
                if from_team:
                    src_path = os.path.join(engine.AGENTS_DIR, from_team, "agent.json")
                    with open(src_path, "r") as f:
                        src_cfg = json.load(f)
                    if isinstance(src_cfg.get("team"), dict):
                        members = src_cfg["team"].get("members", [])
                        if agent_id in members:
                            members.remove(agent_id)
                            src_cfg["team"]["members"] = members
                        with open(src_path, "w") as f:
                            json.dump(src_cfg, f, indent=2)

                # Add to destination team
                if to_team:
                    dst_path = os.path.join(engine.AGENTS_DIR, to_team, "agent.json")
                    with open(dst_path, "r") as f:
                        dst_cfg = json.load(f)
                    if isinstance(dst_cfg.get("team"), dict):
                        members = dst_cfg["team"].get("members", [])
                        if agent_id not in members:
                            members.append(agent_id)
                            dst_cfg["team"]["members"] = members
                        with open(dst_path, "w") as f:
                            json.dump(dst_cfg, f, indent=2)

                self._send_json({"status": "moved", "agent": agent_id, "from": from_team, "to": to_team})
            except Exception as e:
                self._send_json({"error": str(e)}, 500)
        else:
            self._send_json({"error": f"Unknown action: {action}"}, 400)

    # --- Agent file management ---

    def _handle_agent_files(self, path):
        """GET /v1/agents/<id>/files — list agent files."""
        parts = path.split("/")
        agent_id = parts[3]
        agent = engine.AgentConfig(agent_id)
        files = []
        if os.path.isdir(agent.dir):
            for f in sorted(os.listdir(agent.dir)):
                fp = os.path.join(agent.dir, f)
                if os.path.isfile(fp):
                    files.append({"name": f, "size": os.path.getsize(fp)})
        skills = agent.list_skills()
        self._send_json({"agent": agent_id, "files": files, "skills": skills})

    def _handle_agent_file_read(self, path):
        """GET /v1/agents/<id>/file?name=soul.md — read a file."""
        from urllib.parse import unquote
        parts = path.split("/")
        agent_id = parts[3]
        # Parse query string
        qs = self.path.split("?", 1)[1] if "?" in self.path else ""
        params = dict(p.split("=", 1) for p in qs.split("&") if "=" in p)
        filename = unquote(params.get("name", ""))
        if not filename or ".." in filename:
            self._send_json({"error": "Invalid filename"}, 400)
            return
        agent = engine.AgentConfig(agent_id)
        filepath = os.path.join(agent.dir, filename)
        if not os.path.isfile(filepath):
            self._send_json({"error": "File not found"}, 404)
            return
        try:
            with open(filepath, "r") as f:
                content = f.read()
            self._send_json({"name": filename, "content": content})
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    def _handle_agent_file_write(self, path):
        """POST /v1/agents/<id>/file — write a file."""
        parts = path.split("/")
        agent_id = parts[3]
        body = self._read_json()
        filename = body.get("name", "")
        content = body.get("content", "")
        if not filename or ".." in filename:
            self._send_json({"error": "Invalid filename"}, 400)
            return
        agent = engine.AgentConfig(agent_id)
        filepath = os.path.join(agent.dir, filename)
        try:
            with open(filepath, "w") as f:
                f.write(content)
            self._send_json({"status": "saved", "name": filename})
            # Invalidate warm pool if the main agent's system-prompt inputs
            # changed — the pooled KV prefix would no longer match the real
            # first-turn payload.
            if (agent_id == WarmSessionPool.POOL_AGENT
                    and filename in ("soul.md", "agent.json", "tools.md")):
                warm_pool.invalidate_all(f"{agent_id}/{filename} edited")
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    def _handle_create_agent(self):
        """POST /v1/agents/create — create a new agent."""
        body = self._read_json()
        agent_id = body.get("agent", "")
        if not agent_id or ".." in agent_id:
            self._send_json({"error": "Invalid agent name"}, 400)
            return
        agent = engine.AgentConfig(agent_id)  # auto-creates defaults
        cfg_dirty = False
        cfg = agent.config
        for field in ("description", "model", "display_name"):
            if body.get(field):
                cfg[field] = body[field]
                cfg_dirty = True
        if cfg_dirty:
            with open(os.path.join(agent.dir, "agent.json"), "w") as f:
                json.dump(cfg, f, indent=2)
        if body.get("soul"):
            with open(os.path.join(agent.dir, "soul.md"), "w") as f:
                f.write(body["soul"])
        # Register QMD collection for the new agent
        self._qmd_register_collection(agent_id, agent.dir)
        self._send_json({"status": "created", "agent": agent_id})

    def _handle_delete_agent(self):
        """POST /v1/agents/delete — soft-delete an agent (move to .trash)."""
        body = self._read_json()
        agent_id = body.get("agent", "")
        if not agent_id or agent_id == "main" or ".." in agent_id:
            self._send_json({"error": "Cannot delete this agent"}, 400)
            return
        agent_dir = os.path.join(engine.AGENTS_DIR, agent_id)
        if not os.path.isdir(agent_dir):
            self._send_json({"error": f"Agent '{agent_id}' not found"}, 404)
            return
        try:
            trash_dir = os.path.join(engine.AGENTS_DIR, ".trash")
            os.makedirs(trash_dir, exist_ok=True)
            import shutil
            dest = os.path.join(trash_dir, f"{agent_id}_{int(time.time())}")
            shutil.move(agent_dir, dest)
            # Remove QMD collection for deleted agent
            self._qmd_remove_collection(agent_id)
            # Remove scheduled tasks for deleted agent
            try:
                if engine._scheduler:
                    for s in engine._scheduler.list_all():
                        if s.get("agent") == agent_id:
                            engine._scheduler.remove(s["name"])
            except Exception:
                pass
            self._send_json({"status": "deleted", "agent": agent_id, "moved_to": dest})
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    def _handle_rename_agent(self):
        """POST /v1/agents/rename — rename an agent directory and update QMD collection."""
        body = self._read_json()
        old_id = body.get("agent", "")
        new_id = body.get("new_name", "").strip()
        if not old_id or not new_id or ".." in old_id or ".." in new_id:
            self._send_json({"error": "Invalid agent name"}, 400)
            return
        if old_id == new_id:
            self._send_json({"status": "ok", "agent": new_id})
            return
        if old_id == "main":
            self._send_json({"error": "Cannot rename the main agent"}, 400)
            return
        # Validate new_id: alphanumeric + hyphens/underscores only
        import re as _re
        if not _re.match(r'^[a-zA-Z0-9_-]+$', new_id):
            self._send_json({"error": "Agent name must be alphanumeric (hyphens/underscores allowed)"}, 400)
            return
        old_dir = os.path.join(engine.AGENTS_DIR, old_id)
        new_dir = os.path.join(engine.AGENTS_DIR, new_id)
        if not os.path.isdir(old_dir):
            self._send_json({"error": f"Agent '{old_id}' not found"}, 404)
            return
        if os.path.exists(new_dir):
            self._send_json({"error": f"Agent '{new_id}' already exists"}, 409)
            return
        try:
            os.rename(old_dir, new_dir)
            # Update QMD: remove old collection, add new one, re-index in background
            if self._is_qmd_running():
                self._qmd_run(["collection", "remove", old_id])
                self._qmd_run(["collection", "add", new_dir, "--name", new_id])
                self._qmd_trigger_update(delay=1.0)
            self._send_json({"status": "renamed", "agent": new_id, "old_name": old_id})
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    # --- Skill installation (Claude SKILL.md format, zip upload) ---

    def _handle_install_skill_zip(self):
        """POST /v1/skills/install-zip — install skill from uploaded zip (base64 in JSON)."""
        body = self._read_json()
        agent_id = body.get("agent", "main")
        zip_data_b64 = body.get("zip_data", "")
        skill_name = body.get("name", "")

        if not zip_data_b64:
            self._send_json({"error": "No zip data"}, 400)
            return

        try:
            import base64
            import zipfile
            import io

            zip_bytes = base64.b64decode(zip_data_b64)
            zf = zipfile.ZipFile(io.BytesIO(zip_bytes))

            # Find SKILL.md in the zip
            skill_md_path = None
            for name in zf.namelist():
                if name.endswith("SKILL.md"):
                    skill_md_path = name
                    break

            if not skill_md_path:
                self._send_json({"error": "No SKILL.md found in zip"}, 400)
                return

            # Determine skill name from path or provided name
            parts = skill_md_path.split("/")
            if not skill_name:
                # Use parent directory name, or filename prefix
                if len(parts) >= 2:
                    skill_name = parts[-2]
                else:
                    skill_name = "imported-skill"

            # Extract all files to agent's skills directory
            agent = engine.AgentConfig(agent_id)
            skill_dir = os.path.join(agent.skills_dir, skill_name)
            os.makedirs(skill_dir, exist_ok=True)

            # Find the common prefix to strip
            prefix = "/".join(parts[:-1]) + "/" if len(parts) > 1 else ""

            for zpath in zf.namelist():
                if zpath.endswith("/"):
                    continue
                # Strip prefix to get relative path
                rel = zpath[len(prefix):] if zpath.startswith(prefix) else zpath.split("/")[-1]
                dest = os.path.join(skill_dir, rel)
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with open(dest, "wb") as f:
                    f.write(zf.read(zpath))

            self._send_json({
                "status": "installed",
                "skill": skill_name,
                "agent": agent_id,
                "files": [n for n in zf.namelist() if not n.endswith("/")],
            })
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    def _handle_remove_skill(self):
        """POST /v1/skills/remove — remove a skill from an agent."""
        body = self._read_json()
        skill_name = body.get("skill", "")
        agent_id = body.get("agent", "main")
        if not skill_name:
            self._send_json({"error": "Skill name required"}, 400)
            return
        agent = engine.AgentConfig(agent_id)
        skill_dir = os.path.join(agent.skills_dir, skill_name)
        if not os.path.isdir(skill_dir):
            self._send_json({"error": f"Skill '{skill_name}' not found"}, 404)
            return
        try:
            import shutil
            shutil.rmtree(skill_dir)
            self._send_json({"status": "removed", "skill": skill_name, "agent": agent_id})
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    def _handle_cc_skills_list(self):
        """GET /v1/skills/claude-code — list all Claude Code skills/plugins."""
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        agent_id = query.get("agent", ["main"])[0]

        # Get all CC skills from the scanner
        all_skills = engine.scan_claude_code_skills()

        # Get agent's enabled CC skills list
        agent_cfg = engine.AgentConfig(agent_id)
        agent_cc = agent_cfg.config.get("claude_code_skills", [])

        # Annotate with per-agent enabled state
        for skill in all_skills:
            skill["agent_enabled"] = skill["slug"] in agent_cc

        self._send_json({"skills": all_skills, "agent": agent_id})

    def _handle_cc_skills_manage(self):
        """POST /v1/skills/claude-code — enable/disable CC skill for an agent.
        Body: {agent, slug, enabled}"""
        body = self._read_json()
        agent_id = body.get("agent", "main")
        slug = body.get("slug", "")
        enabled = body.get("enabled", True)

        if not slug:
            self._send_json({"error": "slug required"}, 400)
            return

        agent_cfg = engine.AgentConfig(agent_id)
        config = dict(agent_cfg.config)
        cc_skills = list(config.get("claude_code_skills", []))

        if enabled and slug not in cc_skills:
            cc_skills.append(slug)
        elif not enabled and slug in cc_skills:
            cc_skills.remove(slug)

        config["claude_code_skills"] = cc_skills
        agent_cfg.save_config(config)

        self._send_json({"status": "ok", "agent": agent_id, "slug": slug,
                         "enabled": enabled, "claude_code_skills": cc_skills})

    def _handle_cc_browse(self):
        """POST /v1/skills/claude-code/browse — search CC plugin marketplace.
        Body: {query}"""
        body = self._read_json()
        query = body.get("query", "")
        plugins = engine.browse_claude_code_plugins(query)
        self._send_json({"plugins": plugins, "count": len(plugins)})

    def _handle_cc_install(self):
        """POST /v1/skills/claude-code/install — install a CC plugin.
        Body: {plugin, marketplace}"""
        body = self._read_json()
        plugin_name = body.get("plugin", "")
        marketplace = body.get("marketplace", "claude-plugins-official")
        if not plugin_name:
            self._send_json({"error": "plugin name required"}, 400)
            return
        result = engine.install_claude_code_plugin(plugin_name, marketplace)
        status = 200 if "status" in result else 500
        self._send_json(result, status)

    # --- Service Management ---

    @staticmethod
    def _find_qmd() -> str | None:
        """Find the qmd binary."""
        qmd = shutil.which("qmd")
        if qmd:
            return qmd
        for p in [os.path.expanduser("~/.nvm/versions/node"), "/usr/local/bin", "/opt/homebrew/bin"]:
            if os.path.isdir(p):
                for d in sorted(os.listdir(p), reverse=True):
                    candidate = os.path.join(p, d, "bin", "qmd") if "node" in p else os.path.join(p, "qmd")
                    if os.path.isfile(candidate):
                        return candidate
        return None

    # Debounced QMD update: coalesce rapid file writes into one qmd update+embed run
    _qmd_update_timer: threading.Timer | None = None
    _qmd_update_lock = threading.Lock()

    @classmethod
    def _qmd_trigger_update(cls, delay: float = 2.0) -> None:
        """MemPalace migration: no-op. QMD is no longer used."""
        return

    @staticmethod
    def _qmd_run(args: list, timeout: int = 10) -> bool:
        """MemPalace migration: no-op. QMD is no longer used."""
        return False

    def _qmd_register_collection(self, agent_id: str, agent_dir: str) -> None:
        """Add a QMD collection for an agent if QMD is running and collection doesn't exist.
        Runs qmd update in a background thread so files are indexed promptly."""
        if not self._is_qmd_running():
            return
        existing = {(c["name"] if isinstance(c, dict) else c) for c in self._qmd_collections()}
        if agent_id not in existing:
            self._qmd_run(["collection", "add", agent_dir, "--name", agent_id])
            self._qmd_trigger_update(delay=1.0)

    def _qmd_remove_collection(self, agent_id: str) -> None:
        """Remove a QMD collection for a deleted agent."""
        if not self._is_qmd_running():
            return
        self._qmd_run(["collection", "remove", agent_id])

    @staticmethod
    def _is_qmd_running() -> bool:
        """MemPalace migration: QMD is no longer used; always return False so all
        QMD-dependent code paths short-circuit silently."""
        return False

    @staticmethod
    def _is_telegram_running() -> bool:
        try:
            return _telegram_mod.telegram_service.running
        except AttributeError:
            return False

    @staticmethod
    def _qmd_collections() -> list[dict]:
        try:
            qmd_bin = BrainAgentHandler._find_qmd()
            if not qmd_bin:
                return []
            qmd_env = os.environ.copy()
            qmd_env["PATH"] = os.path.dirname(qmd_bin) + ":" + qmd_env.get("PATH", "")
            r = subprocess.run([qmd_bin, "collection", "list"],
                               capture_output=True, text=True, timeout=5, env=qmd_env)
            if r.returncode != 0:
                return []
            collections = []
            current = None
            for line in r.stdout.split("\n"):
                line = line.strip()
                if line and not line.startswith(("Collections", "Pattern", "Files", "Updated", "Ignore")) and "(" in line:
                    name = line.split("(")[0].strip()
                    if name:
                        current = {"name": name}
                        collections.append(current)
                elif current and line.startswith("Files:"):
                    current["files"] = line.split(":")[1].strip().split()[0]

            # Enrich with index health stats from QMD SQLite
            try:
                import sqlite3 as _sq3, hashlib as _hl
                idx_path = os.path.expanduser("~/.cache/qmd/index.sqlite")
                if os.path.isfile(idx_path):
                    conn = _sq3.connect(idx_path, timeout=2)
                    conn.row_factory = _sq3.Row
                    for coll in collections:
                        name = coll["name"]
                        agent_dir = os.path.join(engine.AGENTS_DIR, name)
                        if not os.path.isdir(agent_dir):
                            continue
                        # Build index of QMD docs for this collection
                        rows = conn.execute(
                            "SELECT d.path, d.hash, "
                            "  (SELECT cv.embedded_at FROM content_vectors cv WHERE cv.hash = d.hash LIMIT 1) AS embedded_at "
                            "FROM documents d WHERE d.collection = ? AND d.active = 1",
                            (name,),
                        ).fetchall()
                        qmd_idx = {}
                        for row in rows:
                            qmd_idx[row["path"].lower()] = {"hash": row["hash"], "embedded_at": row["embedded_at"]}

                        # Walk filesystem and compute stats
                        total = 0
                        indexed = 0
                        embedded = 0
                        stale = 0
                        not_indexed = 0
                        for dirpath, _, filenames in os.walk(agent_dir):
                            for fname in filenames:
                                if not fname.endswith(".md"):
                                    continue
                                total += 1
                                fpath = os.path.join(dirpath, fname)
                                rel = os.path.relpath(fpath, agent_dir)
                                # QMD normalizes: lowercase + underscores→hyphens
                                norm = rel.lower().replace("_", "-")
                                idx = qmd_idx.get(norm)
                                if not idx:
                                    not_indexed += 1
                                    continue
                                # Check hash freshness
                                try:
                                    with open(fpath, "rb") as fh:
                                        file_hash = _hl.sha256(fh.read()).hexdigest()
                                    is_current = (file_hash == idx["hash"])
                                except OSError:
                                    is_current = None
                                if is_current:
                                    indexed += 1
                                else:
                                    stale += 1
                                if idx["embedded_at"] and is_current:
                                    embedded += 1

                        coll["total"] = total
                        coll["indexed"] = indexed
                        coll["embedded"] = embedded
                        coll["stale"] = stale
                        coll["not_indexed"] = not_indexed
                    conn.close()
            except Exception:
                pass

            return collections
        except Exception:
            pass
        return []

    def _handle_agent_commands_get(self, path):
        """GET /v1/agents/{id}/commands — list custom commands."""
        parts = path.split("/")
        agent_id = parts[3] if len(parts) > 3 else "main"
        from urllib.parse import unquote
        agent_id = unquote(agent_id)
        agent = engine.AgentConfig(agent_id)
        self._send_json({"commands": agent.load_commands()})

    def _handle_agent_commands_post(self, path):
        """POST /v1/agents/{id}/commands — save custom commands."""
        parts = path.split("/")
        agent_id = parts[3] if len(parts) > 3 else "main"
        from urllib.parse import unquote
        agent_id = unquote(agent_id)
        body = self._read_json()
        commands = body.get("commands", [])
        agent = engine.AgentConfig(agent_id)
        agent.save_commands(commands)
        self._send_json({"status": "saved", "count": len(commands)})

    # --- Traces & Audit Handlers ---
