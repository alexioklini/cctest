# Node Manager (in-memory registry for remote nodes).
# Extracted from server_lib/db.py — self-contained: own module-level state
# (no DB table), config.json read/write helpers, and command dispatch.
# Re-exported by server_lib/db.py (and from there by server.py) so handler
# mixins resolving names via globals inheritance keep working.
import json
import os
import threading
import uuid

_node_registry: dict[str, dict] = {}  # token -> node info
_node_commands: dict[str, dict] = {}  # command_id -> {command, result_event, result}
_node_lock = threading.Lock()


def _load_node_config() -> dict:
    """Load nodes config from config.json (repo root, one level up from server_lib/)."""
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config.json")
    try:
        with open(config_path) as f:
            config = json.load(f)
        return config.get("nodes", {})
    except Exception:
        return {}


def _save_node_config(nodes: dict):
    """Save nodes config to config.json (repo root, one level up from server_lib/)."""
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config.json")
    try:
        config = {}
        if os.path.exists(config_path):
            with open(config_path) as f:
                config = json.load(f)
        config["nodes"] = nodes
        with open(config_path, "w") as f:
            json.dump(config, f, indent=2)
    except Exception as e:
        print(f"Failed to save node config: {e}", flush=True)


def _init_node_registry():
    """Initialize node registry from config."""
    global _node_registry
    nodes_cfg = _load_node_config()
    with _node_lock:
        for name, cfg in nodes_cfg.items():
            token = cfg.get("token", "")
            if token:
                _node_registry[token] = {
                    "name": name,
                    "config": cfg,
                    "status": "disconnected",
                    "last_heartbeat": None,
                    "hostname": "",
                    "os": "",
                    "cpu_percent": None,
                    "mem_used_gb": None,
                    "mem_total_gb": None,
                    "disk_free_gb": None,
                    "uptime_seconds": None,
                    "active_commands": 0,
                    "total_commands": 0,
                    "connected_since": None,
                    "pending_commands": [],
                }


def _node_submit_command(node_selector: str, tool: str, params: dict) -> dict:
    """Submit a command to a remote node. Returns the result."""
    with _node_lock:
        target_node = None
        target_token = None

        if node_selector.startswith("tag:"):
            tag = node_selector[4:]
            candidates = []
            for token, info in _node_registry.items():
                cfg = info.get("config", {})
                if tag in cfg.get("tags", []) and info["status"] == "connected" and not cfg.get("paused"):
                    if tool in cfg.get("allowed_tools", []):
                        candidates.append((token, info))
            if candidates:
                candidates.sort(key=lambda x: x[1].get("active_commands", 0))
                target_token, target_node = candidates[0]
        else:
            for token, info in _node_registry.items():
                if info["name"] == node_selector:
                    target_token = token
                    target_node = info
                    break

        if not target_node:
            return {"error": f"Node '{node_selector}' not found"}
        if target_node["status"] != "connected":
            return {"error": f"Node '{node_selector}' is not connected"}
        cfg = target_node.get("config", {})
        if cfg.get("paused"):
            return {"error": f"Node '{node_selector}' is paused"}
        if tool not in cfg.get("allowed_tools", []):
            return {"error": f"Tool '{tool}' not allowed on node '{node_selector}'"}

        command_id = uuid.uuid4().hex[:12]
        cmd = {"id": command_id, "tool": tool, "params": params}
        result_event = threading.Event()
        _node_commands[command_id] = {"command": cmd, "result_event": result_event, "result": None}
        target_node["pending_commands"].append(cmd)

    timeout = params.get("timeout", 120)
    if result_event.wait(timeout=timeout + 5):
        with _node_lock:
            entry = _node_commands.pop(command_id, {})
            return entry.get("result", {"error": "No result"})
    else:
        with _node_lock:
            _node_commands.pop(command_id, None)
        return {"error": f"Timeout waiting for node '{node_selector}'"}
