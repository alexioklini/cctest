#!/usr/bin/env python3
"""Brain Agent Gateway — manage server, start frontends.

Usage:
  brain start              Start server daemon
  brain stop               Stop server daemon
  brain restart             Restart server daemon
  brain status              Show server status
  brain tui                 Launch TUI (starts server if needed)
  brain telegram            Launch Telegram bot (starts server if needed)
  brain config              Show current config
  brain providers           List providers and their models
"""

import argparse
import json
import os
import signal
import subprocess
import sys
import time
import urllib.request
import urllib.error

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
PID_FILE = os.path.expanduser("~/.brain-agent/server.pid")
LOG_FILE = os.path.expanduser("~/.brain-agent/server.log")


def load_config() -> dict:
    """Load config.json."""
    if not os.path.exists(CONFIG_PATH):
        print(f"Config not found: {CONFIG_PATH}")
        print("Create config.json with provider settings.")
        sys.exit(1)
    with open(CONFIG_PATH) as f:
        return json.load(f)


def get_provider(config: dict, name: str | None = None) -> tuple[str, dict]:
    """Get provider config by name or default."""
    providers = config.get("providers", {})
    if not providers:
        print("No providers configured in config.json")
        sys.exit(1)
    pname = name or config.get("default_provider")
    if not pname or pname not in providers:
        pname = next(iter(providers))
    return pname, providers[pname]


def server_url(config: dict) -> str:
    host = config.get("server", {}).get("host", "127.0.0.1")
    port = config.get("server", {}).get("port", 8420)
    return f"http://{host}:{port}"


def is_server_running(config: dict) -> bool:
    """Check if server is responding."""
    try:
        url = f"{server_url(config)}/v1/status"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=3) as resp:
            return resp.status == 200
    except Exception:
        return False


def get_server_pid() -> int | None:
    """Read PID from file."""
    if os.path.exists(PID_FILE):
        try:
            with open(PID_FILE) as f:
                return int(f.read().strip())
        except (ValueError, OSError):
            pass
    return None


def start_server(config: dict, foreground: bool = False):
    """Start the server."""
    if is_server_running(config):
        print("Server is already running.")
        return

    pname, provider = get_provider(config)
    srv = config.get("server", {})
    host = srv.get("host", "127.0.0.1")
    port = srv.get("port", 8420)
    max_ctx = config.get("max_context", 131072)

    cmd = [
        sys.executable, os.path.join(BASE_DIR, "server.py"),
        "--host", host,
        "--port", str(port),
        "--base-url", provider["base_url"],
        "--api-key", provider.get("api_key", ""),
        "-t", provider.get("type", "openai"),
        "-m", provider.get("default_model", ""),
        "--max-context", str(max_ctx),
    ]

    if foreground:
        print(f"Starting server (foreground) with provider '{pname}'...")
        os.execv(sys.executable, cmd)
    else:
        # Daemon mode
        os.makedirs(os.path.dirname(PID_FILE), exist_ok=True)
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)

        log_fd = open(LOG_FILE, "a")
        proc = subprocess.Popen(
            cmd, stdout=log_fd, stderr=log_fd,
            start_new_session=True,
            cwd=BASE_DIR,
        )
        with open(PID_FILE, "w") as f:
            f.write(str(proc.pid))

        # Wait for server to be ready
        print(f"Starting server with provider '{pname}'...", end="", flush=True)
        for _ in range(30):
            time.sleep(0.5)
            if is_server_running(config):
                print(f" ready (pid {proc.pid})")
                print(f"  {server_url(config)}")
                print(f"  Log: {LOG_FILE}")
                return
            print(".", end="", flush=True)

        print(" timeout!")
        print(f"Check logs: {LOG_FILE}")


def stop_server(config: dict):
    """Stop the server."""
    pid = get_server_pid()
    if pid:
        try:
            os.kill(pid, signal.SIGTERM)
            print(f"Stopped server (pid {pid})")
            # Wait for process to exit
            for _ in range(20):
                try:
                    os.kill(pid, 0)
                    time.sleep(0.25)
                except ProcessLookupError:
                    break
        except ProcessLookupError:
            print("Server process not found (stale pid)")
        try:
            os.remove(PID_FILE)
        except OSError:
            pass
    elif is_server_running(config):
        print("Server is running but no PID file found.")
        print(f"Try: curl -X POST {server_url(config)}/shutdown or kill manually.")
    else:
        print("Server is not running.")


def restart_server(config: dict):
    stop_server(config)
    time.sleep(1)
    start_server(config)


def show_status(config: dict):
    """Show server status."""
    if not is_server_running(config):
        print("Server: not running")
        pid = get_server_pid()
        if pid:
            print(f"  Stale PID file: {pid}")
        return

    try:
        url = f"{server_url(config)}/v1/status"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        pid = get_server_pid()
        print(f"Server: running (pid {pid or '?'})")
        print(f"  URL:       {server_url(config)}")
        print(f"  Version:   {data.get('version', '?')}")
        print(f"  Sessions:  {data.get('sessions', 0)}")
        print(f"  Agents:    {', '.join(data.get('agents', []))}")
        print(f"  Scheduled: {data.get('scheduler_tasks', 0)}")
    except Exception as e:
        print(f"Server: error ({e})")


def show_config(config: dict):
    """Show current configuration."""
    print(json.dumps(config, indent=2))


def show_providers(config: dict):
    """List providers and try to fetch their models."""
    providers = config.get("providers", {})
    default = config.get("default_provider", "")

    for name, p in providers.items():
        marker = "→ " if name == default else "  "
        print(f"{marker}{name}")
        print(f"    URL:   {p.get('base_url', '')}")
        print(f"    Type:  {p.get('type', 'openai')}")
        print(f"    Model: {p.get('default_model', '(none)')}")

        # Try to fetch models
        try:
            import claude_cli as engine
            models = engine.get_available_models(
                p.get("api_key", ""), p.get("base_url", ""), p.get("type", "openai"))
            if models:
                print(f"    Available models ({len(models)}):")
                for m in models[:10]:
                    print(f"      {m}")
                if len(models) > 10:
                    print(f"      ... +{len(models) - 10} more")
            else:
                print("    Available models: (none / unreachable)")
        except Exception:
            print("    Available models: (error fetching)")
        print()


def ensure_server(config: dict):
    """Start server if not running."""
    if not is_server_running(config):
        start_server(config)
        if not is_server_running(config):
            print("Failed to start server.")
            sys.exit(1)


def launch_tui(config: dict, extra_args: list[str]):
    """Launch TUI frontend."""
    ensure_server(config)
    pname, provider = get_provider(config)
    cmd = [
        sys.executable, os.path.join(BASE_DIR, "tui.py"),
        "-i",
        "--server", server_url(config),
        "-m", provider.get("default_model", ""),
    ] + extra_args
    os.execv(sys.executable, cmd)


def launch_telegram(config: dict, extra_args: list[str]):
    """Launch Telegram bot."""
    ensure_server(config)

    # Get token from config or args
    tg_config = config.get("telegram", {})
    token = tg_config.get("bot_token", "")

    # Check if --token is in extra_args
    for i, a in enumerate(extra_args):
        if a == "--token" and i + 1 < len(extra_args):
            token = extra_args[i + 1]
            break

    if not token:
        print("No Telegram bot token configured.")
        print("Set it in config.json under telegram.bot_token or pass --token TOKEN")
        sys.exit(1)

    pname, provider = get_provider(config)
    cmd = [
        sys.executable, os.path.join(BASE_DIR, "telegram.py"),
        "--token", token,
        "--server", server_url(config),
        "-m", provider.get("default_model", ""),
    ] + extra_args
    os.execv(sys.executable, cmd)


# --- Main ---

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(0)

    command = sys.argv[1]
    extra = sys.argv[2:]
    config = load_config()

    if command == "start":
        fg = "--foreground" in extra or "-f" in extra
        start_server(config, foreground=fg)
    elif command == "stop":
        stop_server(config)
    elif command == "restart":
        restart_server(config)
    elif command == "status":
        show_status(config)
    elif command == "config":
        show_config(config)
    elif command == "providers":
        show_providers(config)
    elif command == "tui":
        launch_tui(config, extra)
    elif command == "telegram":
        launch_telegram(config, extra)
    elif command == "help" or command == "--help" or command == "-h":
        print(__doc__)
    else:
        print(f"Unknown command: {command}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
