# Extracted from claude_cli.py — agent config, tool config, skills/plugin management
#
# Cross-module deps (still live in claude_cli.py):
#   - resolve_model(raw, purpose): resolves model aliases
#   - _models_config: global dict of loaded model configs
#   - get_model_max_context(model): returns max context window for a model
#   - _gmail_config(): reads gmail credentials
#
# Stdlib imports used in this module:

import datetime
import json
import os
import re
import shutil
import subprocess


# --- Agent System ---

AGENTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "agents")
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config.json")

# --- Tool Configuration ---

_TOOLS_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools_config.json")

_TOOLS_CONFIG_DEFAULTS = {
    "exa_search": {
        "enabled": True,
        "api_key": "",
        "default_num_results": 5,
    },
    "gmail": {
        "enabled": True,
        "email": "",
        "app_password": "",
    },
    "execute_command": {
        "enabled": True,
        "timeout": 120,
        "banned_commands": ["rm -rf /", "mkfs", "dd if="],
        "login_shell": True,       # Use login shell (sources ~/.zprofile, ~/.zshrc) for full PATH
        "shell_path": "",           # Shell binary path, empty = auto-detect from $SHELL (default: /bin/zsh)
    },
    "web_fetch": {
        "enabled": True,
        "timeout": 30,
        "max_size_mb": 10,
    },
    "refinement": {
        "enabled": True,
        "model": "",  # empty = auto-select (Haiku > Sonnet > cheapest)
    },
    "read_document": {
        "enabled": True,
        "max_file_size_mb": 50,
        "vision_model": "",  # for image description; empty = auto
    },
    "write_document": {
        "enabled": True,
    },
    "edit_document": {
        "enabled": True,
    },
    "code_graph": {
        "enabled": True,
        "exclude_dirs": "node_modules,.git,__pycache__,venv,.venv,dist,build",
        "max_file_size_kb": 500,
    },
    "python_exec": {
        "enabled": True,
        "timeout": 30,
        "max_output_chars": 50000,
        "venv_path": "",
    },
}


def get_tool_config() -> dict:
    """Load tool configuration from tools_config.json, falling back to defaults."""
    cfg = {}
    if os.path.exists(_TOOLS_CONFIG_PATH):
        try:
            with open(_TOOLS_CONFIG_PATH) as f:
                cfg = json.load(f)
        except (json.JSONDecodeError, OSError):
            cfg = {}
    # Merge with defaults (defaults provide missing keys)
    merged = {}
    for tool_name, defaults in _TOOLS_CONFIG_DEFAULTS.items():
        tool_cfg = cfg.get(tool_name, {})
        merged[tool_name] = {**defaults, **tool_cfg}
    return merged


def save_tool_config(cfg: dict) -> dict:
    """Save tool configuration to tools_config.json. Returns saved config."""
    # Merge with existing to preserve fields not in the incoming payload
    existing = get_tool_config()
    for tool_name, tool_cfg in cfg.items():
        if tool_name in existing:
            existing[tool_name].update(tool_cfg)
        else:
            existing[tool_name] = tool_cfg
    try:
        with open(_TOOLS_CONFIG_PATH, "w") as f:
            json.dump(existing, f, indent=2)
    except OSError as e:
        return {"error": str(e)}
    return existing


def get_tool_status() -> dict:
    """Return status of each configurable tool, checking all fallback sources."""
    cfg = get_tool_config()
    status = {}
    for tool_name, tool_cfg in cfg.items():
        tool_cfg = dict(tool_cfg)  # copy to avoid mutating defaults
        enabled = tool_cfg.get("enabled", True)
        if not enabled:
            s = "disabled"
        elif tool_name == "exa_search":
            exa_key = tool_cfg.get("api_key") or os.environ.get("EXA_API_KEY", "")
            # Check hardcoded fallback in tool function
            if not exa_key:
                exa_key = "97dbd594-f7b4-4866-9a8e-6a297e3df576"  # built-in default
                tool_cfg["_source"] = "built-in default"
            elif not tool_cfg.get("api_key") and os.environ.get("EXA_API_KEY"):
                tool_cfg["_source"] = "environment variable"
            s = "configured" if exa_key else "not configured"
        elif tool_name == "gmail":
            has_gmail = bool(tool_cfg.get("email") and tool_cfg.get("app_password"))
            if not has_gmail:
                gmail_fb = _gmail_config()
                if gmail_fb and gmail_fb.get("email") and gmail_fb.get("app_password"):
                    has_gmail = True
                    tool_cfg["email"] = gmail_fb["email"]
                    tool_cfg["_source"] = "gmail.json"
            s = "configured" if has_gmail else "not configured"
        elif tool_name == "refinement":
            s = "configured" if tool_cfg.get("model") else "auto (Haiku > cheapest)"
        else:
            s = "configured"
        status[tool_name] = {"enabled": enabled, "status": s, "config": tool_cfg}
    return status


class AgentConfig:
    """Configuration and file management for a single agent."""

    def __init__(self, agent_id: str):
        self.agent_id = agent_id
        self.dir = os.path.join(AGENTS_DIR, agent_id)
        os.makedirs(self.dir, exist_ok=True)
        self._ensure_defaults()

    def _ensure_defaults(self):
        """Create default files if they don't exist."""
        soul_path = os.path.join(self.dir, "soul.md")
        if not os.path.exists(soul_path):
            with open(soul_path, "w") as f:
                f.write(f"""# {self.agent_id}

You are the **{self.agent_id}** agent.
Adapt your behavior to the tasks you are given.
""")
        config_path = os.path.join(self.dir, "agent.json")
        if not os.path.exists(config_path):
            with open(config_path, "w") as f:
                json.dump({
                    "description": f"{self.agent_id} agent",
                    "model": None,
                }, f, indent=2)

    @property
    def soul(self) -> str:
        """Load soul.md content."""
        path = os.path.join(self.dir, "soul.md")
        try:
            with open(path, "r") as f:
                return f.read()
        except OSError:
            return ""

    @property
    def tools_guide(self) -> str:
        """Load per-agent tools.md, falling back to global tools.md."""
        agent_tools = os.path.join(self.dir, "tools.md")
        if os.path.exists(agent_tools):
            try:
                with open(agent_tools, "r") as f:
                    return f.read()
            except OSError:
                pass
        # Fall back to global tools.md
        global_tools = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools.md")
        try:
            with open(global_tools, "r") as f:
                return f.read()
        except OSError:
            return ""

    @property
    def config(self) -> dict:
        """Load agent.json config."""
        path = os.path.join(self.dir, "agent.json")
        try:
            with open(path, "r") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return {}

    @property
    def description(self) -> str:
        return self.config.get("description", self.agent_id)

    @property
    def preferred_model(self) -> str | None:
        raw = self.config.get("model")
        if not raw:
            return None
        purpose = self.config.get("model_purpose")
        resolved = resolve_model(raw, purpose) if _models_config else raw
        return resolved or raw

    @property
    def max_context(self) -> int | None:
        """Return model's max_context if a preferred model is set."""
        model = self.preferred_model
        if model:
            return get_model_max_context(model)
        return None

    @property
    def memory_dir(self) -> str:
        return self.dir  # memory.db lives alongside soul.md

    @property
    def skills_dir(self) -> str:
        return os.path.join(self.dir, "skills")

    @property
    def mcp_config_path(self) -> str:
        return os.path.join(self.dir, "mcp.json")

    def load_commands(self) -> list[dict]:
        """Load custom slash commands from commands.json + .claude/commands/*.md.

        Supports both Brain Agent format (JSON) and Claude Code format (markdown
        with YAML frontmatter, $ARGUMENTS, !`command` interpolation).
        """
        commands = []

        # 1. Brain Agent format: commands.json
        path = os.path.join(self.dir, "commands.json")
        try:
            with open(path, "r") as f:
                data = json.load(f)
                if isinstance(data, list):
                    for cmd in data:
                        cmd["_format"] = "brain"
                    commands.extend(data)
        except (OSError, json.JSONDecodeError):
            pass

        # 2. Claude Code format: .claude/commands/*.md and agent commands/ dir
        for cmd_dir in [
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".claude", "commands"),
            os.path.join(self.dir, "commands"),
        ]:
            if not os.path.isdir(cmd_dir):
                continue
            for fname in sorted(os.listdir(cmd_dir)):
                if not fname.endswith(".md"):
                    continue
                fpath = os.path.join(cmd_dir, fname)
                if os.path.islink(fpath):
                    fpath = os.path.realpath(fpath)
                if not os.path.isfile(fpath):
                    continue
                try:
                    with open(fpath, "r") as f:
                        raw = f.read()
                    # Parse YAML frontmatter
                    fm = {}
                    body = raw
                    fm_match = re.match(r'^---\s*\n(.*?)\n---\s*\n(.*)$', raw, re.DOTALL)
                    if fm_match:
                        for line in fm_match.group(1).split("\n"):
                            if ":" in line:
                                k, v = line.split(":", 1)
                                fm[k.strip()] = v.strip().strip('"').strip("'")
                        body = fm_match.group(2).strip()
                    cmd_name = fname[:-3]  # strip .md
                    # Skip if already defined in commands.json (brain format takes priority)
                    if any(c.get("name") == cmd_name for c in commands):
                        continue
                    commands.append({
                        "name": cmd_name,
                        "description": fm.get("description", ""),
                        "prompt": body,
                        "allowed_tools": fm.get("allowed-tools", ""),
                        "_format": "claude-code",
                        "_path": fpath,
                    })
                except OSError:
                    continue

        return commands

    def save_commands(self, commands: list[dict]):
        """Save custom slash commands to commands.json (Brain Agent format only)."""
        path = os.path.join(self.dir, "commands.json")
        # Only save brain-format commands
        brain_cmds = [c for c in commands if c.get("_format") != "claude-code"]
        # Strip internal fields
        clean = [{k: v for k, v in c.items() if not k.startswith("_")} for c in brain_cmds]
        with open(path, "w") as f:
            json.dump(clean, f, indent=2)

    @staticmethod
    def expand_command(cmd: dict, args: str = "") -> str:
        """Expand a command template with arguments and dynamic content.

        Supports both formats:
        - Brain Agent: {{variable}} substitution
        - Claude Code: $ARGUMENTS substitution + !`command` interpolation
        """
        template = cmd.get("prompt", cmd.get("template", ""))
        fmt = cmd.get("_format", "brain")

        if fmt == "claude-code":
            # Replace $ARGUMENTS with user args
            result = template.replace("$ARGUMENTS", args)

            # Interpolate !`command` — runs shell command and injects output
            import subprocess
            def _run_interpolation(match):
                shell_cmd = match.group(1)
                try:
                    proc = subprocess.run(
                        shell_cmd, shell=True, capture_output=True, text=True,
                        timeout=10, cwd=os.getcwd(),
                        env={**os.environ, "TERM": "dumb"},
                    )
                    return proc.stdout.strip()
                except Exception as e:
                    return f"(error: {e})"

            result = re.sub(r'!`([^`]+)`', _run_interpolation, result)
            return result

        else:
            # Brain Agent: {{variable}} substitution
            if "{{" in template and args:
                var_match = re.search(r'\{\{(\w+)\}\}', template)
                if var_match:
                    return template.replace("{{" + var_match.group(1) + "}}", args)
            return template + (" " + args if args else "")

    def list_skills(self) -> list[dict]:
        """List all skills for this agent (own + main's global skills)."""
        skills = {}
        # Load main's skills first (global)
        if self.agent_id != "main":
            main_skills_dir = os.path.join(AGENTS_DIR, "main", "skills")
            skills.update(self._scan_skills(main_skills_dir, source="main"))
        # Load own skills (override globals if same name)
        skills.update(self._scan_skills(self.skills_dir, source=self.agent_id))
        return list(skills.values())

    def _scan_skills(self, skills_dir: str, source: str) -> dict[str, dict]:
        """Scan a skills directory and return {name: skill_info}."""
        result = {}
        if not os.path.isdir(skills_dir):
            return result
        for name in sorted(os.listdir(skills_dir)):
            skill_dir = os.path.join(skills_dir, name)
            skill_file = os.path.join(skill_dir, "SKILL.md")
            if not os.path.isfile(skill_file):
                continue
            try:
                with open(skill_file, "r") as f:
                    raw = f.read()
                # Parse YAML frontmatter
                fm_match = re.match(r'^---\s*\n(.*?)\n---\s*\n(.*)$', raw, re.DOTALL)
                if fm_match:
                    fm_text, body = fm_match.groups()
                    fm = {}
                    for line in fm_text.split("\n"):
                        if ":" in line:
                            k, v = line.split(":", 1)
                            fm[k.strip()] = v.strip().strip('"').strip("'")
                    result[name] = {
                        "name": fm.get("name", name),
                        "slug": name,
                        "description": fm.get("description", ""),
                        "source": source,
                        "path": skill_file,
                    }
                else:
                    result[name] = {
                        "name": name,
                        "slug": name,
                        "description": "",
                        "source": source,
                        "path": skill_file,
                    }
            except OSError:
                continue
        return result

    def load_skill(self, skill_name: str) -> str | None:
        """Load the full SKILL.md body for a specific skill.
        Accepts either the directory name (slug) or the display name."""
        # Try direct match first (slug = directory name)
        own_path = os.path.join(self.skills_dir, skill_name, "SKILL.md")
        if os.path.isfile(own_path):
            return self._read_skill_body(own_path)
        # Fall back to main's skills
        if self.agent_id != "main":
            main_path = os.path.join(AGENTS_DIR, "main", "skills", skill_name, "SKILL.md")
            if os.path.isfile(main_path):
                return self._read_skill_body(main_path)
        # Try matching by display name → slug lookup
        for s in self.list_skills():
            if s.get("name", "").lower() == skill_name.lower() or s.get("slug", "").lower() == skill_name.lower():
                slug = s.get("slug", "")
                if slug:
                    own_path = os.path.join(self.skills_dir, slug, "SKILL.md")
                    if os.path.isfile(own_path):
                        return self._read_skill_body(own_path)
                    if self.agent_id != "main":
                        main_path = os.path.join(AGENTS_DIR, "main", "skills", slug, "SKILL.md")
                        if os.path.isfile(main_path):
                            return self._read_skill_body(main_path)
        return None

    @staticmethod
    def _read_skill_body(path: str) -> str:
        """Read a SKILL.md and return just the body (after frontmatter)."""
        with open(path, "r") as f:
            raw = f.read()
        fm_match = re.match(r'^---\s*\n.*?\n---\s*\n(.*)$', raw, re.DOTALL)
        if fm_match:
            return fm_match.group(1).strip()
        return raw.strip()


def scan_claude_code_skills() -> list[dict]:
    """Discover Claude Code skills/plugins from ~/.claude.

    Scans three sources:
    1. Plugin skills — cached plugins with SKILL.md files
    2. Plugin commands — marketplace plugin commands (slash commands)
    3. User commands — ~/.claude/commands/ markdown files

    Returns list of dicts with: name, slug, description, source, type, path, plugin, marketplace, enabled
    """
    home = os.path.expanduser("~")
    claude_dir = os.path.join(home, ".claude")
    results = []

    # Read installed_plugins.json to get install paths
    installed = {}
    try:
        with open(os.path.join(claude_dir, "plugins", "installed_plugins.json")) as f:
            data = json.load(f)
            installed = data.get("plugins", {})
    except (OSError, json.JSONDecodeError):
        pass

    # Read settings.json for enabled state
    enabled_plugins = {}
    try:
        with open(os.path.join(claude_dir, "settings.json")) as f:
            settings = json.load(f)
            enabled_plugins = settings.get("enabledPlugins", {})
    except (OSError, json.JSONDecodeError):
        pass

    # 1. Plugin skills — scan installed plugin cache dirs for skills/*/SKILL.md
    for plugin_key, installs in installed.items():
        if not installs:
            continue
        install = installs[0]  # Use first (latest) install
        install_path = install.get("installPath", "")
        if not os.path.isdir(install_path):
            continue

        plugin_name = plugin_key.split("@")[0] if "@" in plugin_key else plugin_key
        marketplace = plugin_key.split("@")[1] if "@" in plugin_key else ""
        is_enabled = enabled_plugins.get(plugin_key, False)

        skills_dir = os.path.join(install_path, "skills")
        if os.path.isdir(skills_dir):
            for skill_name in sorted(os.listdir(skills_dir)):
                skill_file = os.path.join(skills_dir, skill_name, "SKILL.md")
                if not os.path.isfile(skill_file):
                    continue
                # Parse frontmatter for name/description
                try:
                    with open(skill_file) as f:
                        raw = f.read(4096)
                    fm_match = re.match(r'^---\s*\n(.*?)\n---\s*\n', raw, re.DOTALL)
                    fm = {}
                    if fm_match:
                        for line in fm_match.group(1).split("\n"):
                            if ":" in line:
                                k, v = line.split(":", 1)
                                fm[k.strip()] = v.strip().strip('"').strip("'")
                except OSError:
                    fm = {}

                results.append({
                    "name": fm.get("name", skill_name),
                    "slug": f"{plugin_name}:{skill_name}",
                    "description": fm.get("description", ""),
                    "source": "claude-code",
                    "type": "skill",
                    "path": skill_file,
                    "plugin": plugin_name,
                    "marketplace": marketplace,
                    "enabled": is_enabled,
                })

        # 2. Plugin commands — commands/*.md in the marketplace plugin dir
        # Find commands in the marketplace source (not cache)
        for mp_dir in ["claude-plugins-official", "anthropic-agent-skills"]:
            cmd_dir = os.path.join(claude_dir, "plugins", "marketplaces", mp_dir, "plugins", plugin_name, "commands")
            if os.path.isdir(cmd_dir):
                for cmd_file in sorted(os.listdir(cmd_dir)):
                    if not cmd_file.endswith(".md"):
                        continue
                    cmd_path = os.path.join(cmd_dir, cmd_file)
                    cmd_name = cmd_file[:-3]  # strip .md
                    # Parse frontmatter
                    try:
                        with open(cmd_path) as f:
                            raw = f.read(4096)
                        fm_match = re.match(r'^---\s*\n(.*?)\n---\s*\n', raw, re.DOTALL)
                        fm = {}
                        if fm_match:
                            for line in fm_match.group(1).split("\n"):
                                if ":" in line:
                                    k, v = line.split(":", 1)
                                    fm[k.strip()] = v.strip().strip('"').strip("'")
                    except OSError:
                        fm = {}

                    results.append({
                        "name": fm.get("name", cmd_name),
                        "slug": f"{plugin_name}:{cmd_name}",
                        "description": fm.get("description", ""),
                        "source": "claude-code",
                        "type": "command",
                        "path": cmd_path,
                        "plugin": plugin_name,
                        "marketplace": mp_dir,
                        "enabled": is_enabled,
                    })

    # 3. User commands — ~/.claude/commands/*.md
    user_cmd_dir = os.path.join(claude_dir, "commands")
    if os.path.isdir(user_cmd_dir):
        for entry in sorted(os.listdir(user_cmd_dir)):
            if entry.startswith("."):
                continue
            entry_path = os.path.join(user_cmd_dir, entry)
            if os.path.islink(entry_path):
                # Resolve symlink
                entry_path = os.path.realpath(entry_path)
            if os.path.isfile(entry_path) and entry_path.endswith(".md"):
                cmd_name = entry[:-3]
                try:
                    with open(entry_path) as f:
                        raw = f.read(4096)
                    fm_match = re.match(r'^---\s*\n(.*?)\n---\s*\n', raw, re.DOTALL)
                    fm = {}
                    if fm_match:
                        for line in fm_match.group(1).split("\n"):
                            if ":" in line:
                                k, v = line.split(":", 1)
                                fm[k.strip()] = v.strip().strip('"').strip("'")
                except OSError:
                    fm = {}
                results.append({
                    "name": fm.get("name", cmd_name),
                    "slug": cmd_name,
                    "description": fm.get("description", ""),
                    "source": "claude-code",
                    "type": "user-command",
                    "path": entry_path,
                    "plugin": "",
                    "marketplace": "",
                    "enabled": True,  # User commands are always enabled
                })
            elif os.path.isdir(entry_path):
                # Directory with SKILL.md
                skill_file = os.path.join(entry_path, "SKILL.md")
                if os.path.isfile(skill_file):
                    try:
                        with open(skill_file) as f:
                            raw = f.read(4096)
                        fm_match = re.match(r'^---\s*\n(.*?)\n---\s*\n', raw, re.DOTALL)
                        fm = {}
                        if fm_match:
                            for line in fm_match.group(1).split("\n"):
                                if ":" in line:
                                    k, v = line.split(":", 1)
                                    fm[k.strip()] = v.strip().strip('"').strip("'")
                    except OSError:
                        fm = {}
                    results.append({
                        "name": fm.get("name", entry),
                        "slug": entry,
                        "description": fm.get("description", ""),
                        "source": "claude-code",
                        "type": "user-skill",
                        "path": skill_file,
                        "plugin": "",
                        "marketplace": "",
                        "enabled": True,
                    })

    # Deduplicate: same skill name across plugins → keep first occurrence
    seen_names = {}
    deduped = []
    for s in results:
        key = (s["name"], s["type"])
        if key in seen_names:
            continue
        seen_names[key] = True
        deduped.append(s)

    return deduped


def browse_claude_code_plugins(query: str = "") -> list[dict]:
    """Browse available Claude Code plugins from local marketplace manifests.

    Returns list of dicts with: name, description, category, marketplace, source, homepage, installed
    """
    home = os.path.expanduser("~")
    claude_dir = os.path.join(home, ".claude")
    results = []

    # Read installed_plugins.json to check install state
    installed_keys = set()
    try:
        with open(os.path.join(claude_dir, "plugins", "installed_plugins.json")) as f:
            data = json.load(f)
            installed_keys = set(data.get("plugins", {}).keys())
    except (OSError, json.JSONDecodeError):
        pass

    # Scan all marketplace manifests
    mp_dir = os.path.join(claude_dir, "plugins", "marketplaces")
    if not os.path.isdir(mp_dir):
        return results

    for mp_name in sorted(os.listdir(mp_dir)):
        manifest_path = os.path.join(mp_dir, mp_name, ".claude-plugin", "marketplace.json")
        if not os.path.isfile(manifest_path):
            continue
        try:
            with open(manifest_path) as f:
                manifest = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue

        for plugin in manifest.get("plugins", []):
            name = plugin.get("name", "")
            description = plugin.get("description", "")
            # Filter by query
            if query:
                q = query.lower()
                if q not in name.lower() and q not in description.lower():
                    continue

            plugin_key = f"{name}@{mp_name}"
            results.append({
                "name": name,
                "description": description,
                "category": plugin.get("category", ""),
                "marketplace": mp_name,
                "homepage": plugin.get("homepage", ""),
                "source": plugin.get("source", {}),
                "installed": plugin_key in installed_keys,
            })

    return results


def install_claude_code_plugin(plugin_name: str, marketplace: str = "claude-plugins-official") -> dict:
    """Install a Claude Code plugin from a marketplace.

    Uses `claude plugins add` CLI if available, otherwise clones from git source.
    Returns dict with status/error.
    """
    import subprocess
    import shutil

    home = os.path.expanduser("~")
    claude_dir = os.path.join(home, ".claude")

    # Find the plugin in the marketplace manifest
    manifest_path = os.path.join(claude_dir, "plugins", "marketplaces", marketplace,
                                  ".claude-plugin", "marketplace.json")
    if not os.path.isfile(manifest_path):
        return {"error": f"Marketplace '{marketplace}' not found"}

    try:
        with open(manifest_path) as f:
            manifest = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        return {"error": f"Failed to read manifest: {e}"}

    plugin_info = None
    for p in manifest.get("plugins", []):
        if p.get("name") == plugin_name:
            plugin_info = p
            break
    if not plugin_info:
        return {"error": f"Plugin '{plugin_name}' not found in {marketplace}"}

    # Try using claude CLI first
    claude_bin = shutil.which("claude")
    if claude_bin:
        try:
            result = subprocess.run(
                [claude_bin, "plugins", "add", f"{plugin_name}@{marketplace}"],
                capture_output=True, text=True, timeout=60,
                env={**os.environ, "TERM": "dumb"},
            )
            if result.returncode == 0:
                return {"status": "installed", "plugin": plugin_name, "marketplace": marketplace,
                        "method": "claude-cli"}
        except (subprocess.TimeoutExpired, OSError):
            pass  # Fall through to manual install

    # Manual install: clone from git source
    source = plugin_info.get("source", {})
    if isinstance(source, str):
        # Local source (relative path in marketplace)
        src_dir = os.path.join(claude_dir, "plugins", "marketplaces", marketplace, source)
        if os.path.isdir(src_dir):
            # Copy to cache
            cache_dir = os.path.join(claude_dir, "plugins", "cache", marketplace,
                                      plugin_name, "local")
            os.makedirs(cache_dir, exist_ok=True)
            shutil.copytree(src_dir, cache_dir, dirs_exist_ok=True)
            # Register in installed_plugins.json
            _register_cc_plugin(plugin_name, marketplace, cache_dir)
            return {"status": "installed", "plugin": plugin_name, "marketplace": marketplace,
                    "method": "copy", "path": cache_dir}
        return {"error": f"Local source '{source}' not found"}

    elif isinstance(source, dict):
        git_url = source.get("url", "")
        if not git_url:
            return {"error": "No git URL in plugin source"}

        # Clone to cache
        cache_dir = os.path.join(claude_dir, "plugins", "cache", marketplace,
                                  plugin_name, "latest")
        if os.path.isdir(cache_dir):
            shutil.rmtree(cache_dir)
        os.makedirs(os.path.dirname(cache_dir), exist_ok=True)

        try:
            # For git-subdir sources, clone then extract subdir
            subdir = source.get("path", "")
            ref = source.get("ref", "main")

            result = subprocess.run(
                ["git", "clone", "--depth", "1", "--branch", ref,
                 git_url if git_url.endswith(".git") else f"https://github.com/{git_url}.git",
                 cache_dir],
                capture_output=True, text=True, timeout=60,
            )
            if result.returncode != 0:
                return {"error": f"git clone failed: {result.stderr[:200]}"}

            # If subdir specified, move it up
            if subdir:
                subdir_path = os.path.join(cache_dir, subdir)
                if os.path.isdir(subdir_path):
                    import tempfile
                    tmp = tempfile.mkdtemp()
                    shutil.copytree(subdir_path, os.path.join(tmp, plugin_name), dirs_exist_ok=True)
                    shutil.rmtree(cache_dir)
                    shutil.copytree(os.path.join(tmp, plugin_name), cache_dir, dirs_exist_ok=True)
                    shutil.rmtree(tmp)

            _register_cc_plugin(plugin_name, marketplace, cache_dir)
            return {"status": "installed", "plugin": plugin_name, "marketplace": marketplace,
                    "method": "git", "path": cache_dir}

        except (subprocess.TimeoutExpired, OSError) as e:
            return {"error": f"Install failed: {e}"}

    return {"error": "Unknown source format"}


def _register_cc_plugin(plugin_name: str, marketplace: str, install_path: str):
    """Register a plugin in ~/.claude/plugins/installed_plugins.json."""
    import datetime
    home = os.path.expanduser("~")
    ip_path = os.path.join(home, ".claude", "plugins", "installed_plugins.json")

    try:
        with open(ip_path) as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        data = {"version": 2, "plugins": {}}

    plugin_key = f"{plugin_name}@{marketplace}"
    now = datetime.datetime.utcnow().isoformat() + "Z"
    data["plugins"][plugin_key] = [{
        "scope": "user",
        "installPath": install_path,
        "version": "latest",
        "installedAt": now,
        "lastUpdated": now,
        "isLocal": True,
    }]

    with open(ip_path, "w") as f:
        json.dump(data, f, indent=4)

    # Also enable in settings.json
    settings_path = os.path.join(home, ".claude", "settings.json")
    try:
        with open(settings_path) as f:
            settings = json.load(f)
    except (OSError, json.JSONDecodeError):
        settings = {}
    if "enabledPlugins" not in settings:
        settings["enabledPlugins"] = {}
    settings["enabledPlugins"][plugin_key] = True
    with open(settings_path, "w") as f:
        json.dump(settings, f, indent=4)


def list_agents() -> list[str]:
    """List all available agent IDs."""
    if not os.path.isdir(AGENTS_DIR):
        return ["main"]
    agents = []
    for name in sorted(os.listdir(AGENTS_DIR)):
        if os.path.isdir(os.path.join(AGENTS_DIR, name)) and not name.startswith("."):
            agents.append(name)
    if not agents:
        agents = ["main"]
    return agents


def get_agent_summaries() -> list[dict]:
    """Get agent ID + description + soul summary for all agents, with team metadata."""
    # First pass: collect raw summaries and scan for teams
    raw = []
    # team_id (the agent whose config holds the team) -> team config
    teams_cfg: dict[str, dict] = {}
    for agent_id in list_agents():
        cfg = AgentConfig(agent_id)
        config = cfg.config
        soul = cfg.soul.strip()
        summary = ""
        for line in soul.split("\n"):
            line = line.strip()
            if line and not line.startswith("#") and not line.startswith("---"):
                summary = line
                break
        entry = {
            "id": agent_id,
            "display_name": config.get("display_name", ""),
            "description": cfg.description,
            "soul_summary": summary,
            "model": cfg.preferred_model,
            "avatar": config.get("avatar"),
            "paused": config.get("paused", False),
        }
        team_cfg = config.get("team")
        if isinstance(team_cfg, dict) and team_cfg.get("members"):
            teams_cfg[agent_id] = team_cfg
        raw.append(entry)

    # Second pass: compute team metadata per agent
    for entry in raw:
        aid = entry["id"]
        # Which teams is this agent a member of?
        member_of = []
        is_head_of = None
        for cfg_holder, tcfg in teams_cfg.items():
            members = tcfg.get("members", [])
            head_id = tcfg.get("head", members[0] if members else cfg_holder)
            if aid in members:
                team_name = tcfg.get("name", cfg_holder)
                member_of.append({"team_id": cfg_holder, "team_name": team_name})
                if aid == head_id:
                    is_head_of = cfg_holder
        entry["teams"] = member_of
        entry["is_team_head"] = is_head_of is not None
        if is_head_of:
            tcfg = teams_cfg[is_head_of]
            entry["team_config_holder"] = is_head_of
            entry["team_members"] = list(tcfg.get("members", []))
            entry["team_head"] = tcfg.get("head", entry["team_members"][0] if entry["team_members"] else is_head_of)
            entry["team_name"] = tcfg.get("name", "")
            entry["team_description"] = tcfg.get("description", "")
            entry["team_avatar"] = tcfg.get("avatar", "")
    return raw


def get_team_structure() -> dict:
    """Return hierarchical team structure for UI and API consumption."""
    agents = get_agent_summaries()
    agent_map = {a["id"]: a for a in agents}
    teams = {}
    in_team = set()  # agents that appear in at least one team

    # Find all agents that hold team configs
    for agent_id in list_agents():
        cfg = AgentConfig(agent_id).config
        team_cfg = cfg.get("team")
        if not isinstance(team_cfg, dict) or not team_cfg.get("members"):
            continue
        members_ids = team_cfg["members"]
        head_id = team_cfg.get("head", members_ids[0] if members_ids else agent_id)
        members = []
        for mid in members_ids:
            if mid in agent_map:
                members.append(agent_map[mid])
                in_team.add(mid)
        teams[agent_id] = {
            "head": head_id,
            "head_agent": agent_map.get(head_id),
            "members": members,
            "name": team_cfg.get("name") or agent_id,
            "description": team_cfg.get("description", ""),
            "avatar": team_cfg.get("avatar", ""),
            "config_holder": agent_id,
        }

    standalone = [a for a in agents if a["id"] not in in_team and a["id"] != "main"]
    main_agent = agent_map.get("main")
    return {"teams": teams, "standalone": standalone, "main": main_agent}


def _get_delegation_scope(caller_agent_id: str) -> list[str]:
    """Return list of agent IDs the caller is allowed to delegate to."""
    all_agents = list_agents()

    # Collect all team configs
    team_cfgs = {}  # config_holder_id -> team_cfg
    for aid in all_agents:
        cfg = AgentConfig(aid).config
        team_cfg = cfg.get("team")
        if isinstance(team_cfg, dict) and team_cfg.get("members"):
            team_cfgs[aid] = team_cfg

    in_team = set()
    head_ids = set()
    for holder, tcfg in team_cfgs.items():
        head_id = tcfg.get("head", tcfg["members"][0] if tcfg["members"] else holder)
        head_ids.add(head_id)
        for m in tcfg["members"]:
            in_team.add(m)

    if caller_agent_id == "main":
        # Main can delegate to team heads and standalone agents (not regular members)
        return [a for a in all_agents if a != "main" and (a in head_ids or a not in in_team)]

    # Check if caller is a team head
    for holder, tcfg in team_cfgs.items():
        head_id = tcfg.get("head", tcfg["members"][0] if tcfg["members"] else holder)
        if caller_agent_id == head_id:
            # Team head can delegate to its members (excluding self)
            return [m for m in tcfg["members"] if m != caller_agent_id and m in all_agents]

    # Regular member: can delegate to peers in same team + team head
    reachable = set()
    for holder, tcfg in team_cfgs.items():
        if caller_agent_id in tcfg["members"]:
            for m in tcfg["members"]:
                if m != caller_agent_id and m in all_agents:
                    reachable.add(m)
    return list(reachable) if reachable else [a for a in all_agents if a != caller_agent_id]


def _find_team_head(agent_id: str) -> str | None:
    """Find the team head for a given agent. Returns None if not in any team."""
    for aid in list_agents():
        cfg = AgentConfig(aid).config
        team_cfg = cfg.get("team")
        if isinstance(team_cfg, dict) and team_cfg.get("members"):
            if agent_id in team_cfg["members"]:
                return team_cfg.get("head", team_cfg["members"][0] if team_cfg["members"] else aid)
    return None


def _get_agent_team_info(agent_id: str) -> dict | None:
    """Get team info for an agent. Returns dict with name, head, members, is_head, or None."""
    for aid in list_agents():
        cfg = AgentConfig(aid).config
        team_cfg = cfg.get("team")
        if isinstance(team_cfg, dict) and team_cfg.get("members"):
            if agent_id in team_cfg["members"]:
                head = team_cfg.get("head", team_cfg["members"][0])
                return {
                    "name": team_cfg.get("name", aid),
                    "head": head,
                    "members": team_cfg["members"],
                    "is_head": agent_id == head,
                    "config_holder": aid,
                }
    return None


def build_agent_registry(for_agent_id: str | None = None) -> str:
    """Build a text block describing available agents for injection into system prompts.
    Respects team hierarchy: team heads see members, main sees heads + standalone."""
    agents = get_agent_summaries()
    if len(agents) <= 1:
        return ""

    caller = for_agent_id or "main"
    scope = _get_delegation_scope(caller)
    agent_map = {a["id"]: a for a in agents}

    # Check if caller is a team head
    caller_team_info = _get_agent_team_info(caller) if caller != "main" else None
    caller_is_head = caller_team_info and caller_team_info["is_head"]

    lines = ["AGENT REGISTRY — use delegate_task to send tasks to these agents:"]

    if caller == "main":
        # Group by teams + standalone
        struct = get_team_structure()
        if struct["teams"]:
            lines.append("  TEAMS:")
            for tid, team in struct["teams"].items():
                team_name = team.get("name", tid)
                head_id = team["head"]
                head_agent = team.get("head_agent", {})
                detail = head_agent.get("soul_summary") or head_agent.get("description", "")
                model_note = f" (model: {head_agent.get('model')})" if head_agent.get("model") else ""
                member_names = ", ".join(m["id"] for m in team["members"])
                lines.append(f"    - {head_id} (head of '{team_name}'): {detail}{model_note}")
                lines.append(f"      Members: {member_names}")
        if struct["standalone"]:
            lines.append("  STANDALONE AGENTS:")
            for a in struct["standalone"]:
                detail = a.get("soul_summary") or a.get("description", "")
                model_note = f" (model: {a['model']})" if a.get("model") else ""
                lines.append(f"    - {a['id']}: {detail}{model_note}")
    elif caller_is_head:
        lines.append("  YOUR TEAM MEMBERS:")
        for mid in scope:
            a = agent_map.get(mid, {})
            detail = a.get("soul_summary") or a.get("description", mid)
            model_note = f" (model: {a.get('model')})" if a.get("model") else ""
            lines.append(f"    - {mid}: {detail}{model_note}")
    else:
        for aid in scope:
            a = agent_map.get(aid, {})
            detail = a.get("soul_summary") or a.get("description", aid)
            model_note = f" (model: {a.get('model')})" if a.get("model") else ""
            lines.append(f"  - {aid}: {detail}{model_note}")

    lines.append("")
    lines.append(
        "Before performing a task, consider if another agent is better suited. "
        "Delegate when the task clearly matches another agent's specialty. "
        "Do NOT delegate simple tasks you can handle yourself."
    )
    return "\n".join(lines)


# Current active agent (set in _run_interactive)
_current_agent: AgentConfig | None = None
