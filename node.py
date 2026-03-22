#!/usr/bin/env python3
"""Brain Agent Remote Node — connects to Brain Agent server via HTTP long-polling.

Single-file, stdlib-only agent that runs on remote machines to execute commands
on behalf of Brain Agent. Connects back to the server, receives commands, and
returns results.

Usage:
    python3 node.py --server http://192.168.4.65:8420 --token nd_abc123...
    python3 node.py --server http://192.168.4.65:8420 --token nd_abc123... --name build-server
"""

import argparse
import json
import os
import platform
import subprocess
import sys
import time
import threading
import urllib.request
import urllib.error
import uuid
import hashlib
import secrets

__version__ = "1.1.0"


# --- System Info ---

def get_system_info() -> dict:
    """Collect system metrics for heartbeat."""
    info = {
        "hostname": platform.node(),
        "os": f"{platform.system()} {platform.release()} {platform.machine()}",
        "python": platform.python_version(),
    }
    # CPU usage (rough estimate via load average on Unix)
    try:
        load = os.getloadavg()
        cpu_count = os.cpu_count() or 1
        info["cpu_percent"] = round(load[0] / cpu_count * 100, 1)
        info["load_avg"] = list(load)
    except (OSError, AttributeError):
        info["cpu_percent"] = 0.0

    # Memory (try /proc/meminfo on Linux, sysctl on macOS)
    try:
        if platform.system() == "Linux":
            with open("/proc/meminfo") as f:
                lines = f.read()
            total = int([l for l in lines.split("\n") if "MemTotal" in l][0].split()[1]) / 1024 / 1024
            avail = int([l for l in lines.split("\n") if "MemAvailable" in l][0].split()[1]) / 1024 / 1024
            info["mem_total_gb"] = round(total, 1)
            info["mem_used_gb"] = round(total - avail, 1)
        elif platform.system() == "Darwin":
            out = subprocess.check_output(["sysctl", "-n", "hw.memsize"], timeout=5).decode().strip()
            total = int(out) / 1024 / 1024 / 1024
            info["mem_total_gb"] = round(total, 1)
            # vm_stat for used memory (approximate)
            vm = subprocess.check_output(["vm_stat"], timeout=5).decode()
            page_size = 16384  # default on Apple Silicon
            for line in vm.split("\n"):
                if "page size of" in line:
                    page_size = int(line.split()[-2])
                    break
            active = 0
            wired = 0
            for line in vm.split("\n"):
                if "Pages active" in line:
                    active = int(line.split()[-1].rstrip("."))
                elif "Pages wired" in line:
                    wired = int(line.split()[-1].rstrip("."))
            used = (active + wired) * page_size / 1024 / 1024 / 1024
            info["mem_used_gb"] = round(used, 1)
    except Exception:
        info["mem_total_gb"] = 0.0
        info["mem_used_gb"] = 0.0

    # Disk free
    try:
        st = os.statvfs("/")
        free_gb = st.f_bavail * st.f_frsize / 1024 / 1024 / 1024
        info["disk_free_gb"] = round(free_gb, 1)
    except (OSError, AttributeError):
        info["disk_free_gb"] = 0.0

    # Uptime
    try:
        if platform.system() == "Linux":
            with open("/proc/uptime") as f:
                info["uptime_seconds"] = int(float(f.read().split()[0]))
        elif platform.system() == "Darwin":
            out = subprocess.check_output(["sysctl", "-n", "kern.boottime"], timeout=5).decode()
            # Format: { sec = 1234567890, usec = 0 }
            sec = int(out.split("sec =")[1].split(",")[0].strip())
            info["uptime_seconds"] = int(time.time()) - sec
    except Exception:
        info["uptime_seconds"] = 0

    return info


# --- Tool Handlers ---

def handle_execute_command(params: dict, allowed_paths: list[str] | None = None) -> dict:
    """Execute a shell command and return output."""
    command = params.get("command", "")
    cwd = params.get("cwd")
    timeout = min(params.get("timeout", 120), 600)  # cap at 10 minutes

    if not command:
        return {"error": "No command provided"}

    try:
        env = os.environ.copy()
        env["TERM"] = "dumb"
        env["NO_COLOR"] = "1"
        env["PAGER"] = "cat"
        env["COLUMNS"] = "200"
        env["LINES"] = "50"

        proc = subprocess.Popen(
            command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL, cwd=cwd, env=env,
            start_new_session=True,
        )
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            import signal as sig
            try:
                os.killpg(proc.pid, sig.SIGKILL)
            except OSError:
                proc.kill()
            stdout, stderr = proc.communicate(timeout=5)
            output = stdout.decode("utf-8", errors="replace")
            if stderr:
                output += "\n--- stderr ---\n" + stderr.decode("utf-8", errors="replace")
            return {"error": f"Timed out after {timeout}s", "output": output[:50000], "exit_code": -1}

        output = stdout.decode("utf-8", errors="replace")
        if stderr:
            err_text = stderr.decode("utf-8", errors="replace")
            output += ("\n--- stderr ---\n" + err_text) if output else err_text
        if len(output) > 50000:
            output = output[:50000] + "\n... (truncated)"

        return {"exit_code": proc.returncode, "output": output}
    except Exception as e:
        return {"error": str(e)}


def handle_read_file(params: dict, allowed_paths: list[str] | None = None) -> dict:
    """Read file contents."""
    path = params.get("path", "")
    offset = params.get("offset", 1)
    limit = params.get("limit")

    if not path:
        return {"error": "No path provided"}

    path = os.path.expanduser(path)
    if not os.path.isabs(path):
        path = os.path.abspath(path)

    if allowed_paths:
        if not any(path.startswith(p) for p in allowed_paths):
            return {"error": f"Path not in allowed paths: {path}"}

    try:
        with open(path, "r", errors="replace") as f:
            lines = f.readlines()
        total = len(lines)
        start = max(0, offset - 1)
        end = start + limit if limit else total
        selected = lines[start:end]
        numbered = []
        for i, line in enumerate(selected, start=start + 1):
            numbered.append(f"{i:>6}\t{line.rstrip()}")
        content = "\n".join(numbered)
        return {"path": path, "total_lines": total, "showing": f"{start+1}-{min(end, total)}", "content": content}
    except Exception as e:
        return {"error": str(e)}


def handle_write_file(params: dict, allowed_paths: list[str] | None = None) -> dict:
    """Write file contents."""
    path = params.get("path", "")
    content = params.get("content", "")

    if not path:
        return {"error": "No path provided"}

    path = os.path.expanduser(path)
    if not os.path.isabs(path):
        path = os.path.abspath(path)

    if allowed_paths:
        if not any(path.startswith(p) for p in allowed_paths):
            return {"error": f"Path not in allowed paths: {path}"}

    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w") as f:
            f.write(content)
        size = os.path.getsize(path)
        return {"path": path, "size": size, "status": "written"}
    except Exception as e:
        return {"error": str(e)}


def handle_list_directory(params: dict, allowed_paths: list[str] | None = None) -> dict:
    """List directory contents."""
    import glob as globmod

    path = params.get("path", ".")
    pattern = params.get("pattern")
    recursive = params.get("recursive", False)

    path = os.path.expanduser(path)
    if not os.path.isabs(path):
        path = os.path.abspath(path)

    if allowed_paths:
        if not any(path.startswith(p) for p in allowed_paths):
            return {"error": f"Path not in allowed paths: {path}"}

    try:
        if pattern:
            full_pattern = os.path.join(path, pattern)
            entries = globmod.glob(full_pattern, recursive=recursive or "**" in pattern)
        elif recursive:
            entries = globmod.glob(os.path.join(path, "**"), recursive=True)
        else:
            entries = [os.path.join(path, e) for e in os.listdir(path)]

        result = []
        for e in sorted(entries)[:200]:
            try:
                stat = os.stat(e)
                result.append({
                    "name": os.path.basename(e),
                    "path": e,
                    "type": "dir" if os.path.isdir(e) else "file",
                    "size": stat.st_size if not os.path.isdir(e) else 0,
                })
            except OSError:
                result.append({"name": os.path.basename(e), "path": e, "type": "unknown"})
        return {"path": path, "entries": result, "count": len(result)}
    except Exception as e:
        return {"error": str(e)}


TOOL_HANDLERS = {
    "execute_command": handle_execute_command,
    "read_file": handle_read_file,
    "write_file": handle_write_file,
    "list_directory": handle_list_directory,
}


# --- Node Client ---

class NodeClient:
    """HTTP long-polling client that connects to Brain Agent server."""

    def __init__(self, server_url: str, token: str, name: str | None = None,
                 allowed_paths: list[str] | None = None):
        self.server_url = server_url.rstrip("/")
        self.token = token
        self.name = name or platform.node()
        self.allowed_paths = allowed_paths
        self.running = False
        self._stop_event = threading.Event()
        self._backoff = 1
        self._connected = False
        self._total_commands = 0
        self._active_commands = 0

    def _url(self, path: str) -> str:
        return f"{self.server_url}{path}"

    def _get(self, path: str, timeout: int = 35) -> dict:
        url = self._url(path)
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _post(self, path: str, data: dict, timeout: int = 10) -> dict:
        url = self._url(path)
        body = json.dumps(data).encode("utf-8")
        req = urllib.request.Request(
            url, data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def poll(self) -> dict | None:
        """Poll for pending commands. Returns a command dict or None."""
        try:
            sys_info = get_system_info()
            params = (
                f"token={self.token}"
                f"&name={urllib.request.quote(self.name)}"
                f"&hostname={urllib.request.quote(sys_info.get('hostname', ''))}"
                f"&os={urllib.request.quote(sys_info.get('os', ''))}"
                f"&cpu_percent={sys_info.get('cpu_percent', 0)}"
                f"&mem_used_gb={sys_info.get('mem_used_gb', 0)}"
                f"&mem_total_gb={sys_info.get('mem_total_gb', 0)}"
                f"&disk_free_gb={sys_info.get('disk_free_gb', 0)}"
                f"&uptime_seconds={sys_info.get('uptime_seconds', 0)}"
                f"&active_commands={self._active_commands}"
                f"&total_commands={self._total_commands}"
            )
            result = self._get(f"/v1/nodes/poll?{params}", timeout=35)
            if result.get("command"):
                return result["command"]
            return None
        except urllib.error.HTTPError as e:
            if e.code == 401:
                print(f"Authentication failed: invalid token", flush=True)
                self._stop_event.set()
                return None
            elif e.code == 403:
                print(f"Node is paused", flush=True)
                time.sleep(10)
                return None
            raise
        except Exception:
            raise

    def send_result(self, command_id: str, result: dict):
        """Send command execution result back to server."""
        try:
            self._post("/v1/nodes/result", {
                "token": self.token,
                "command_id": command_id,
                "result": result,
            })
        except Exception as e:
            print(f"Failed to send result: {e}", flush=True)

    def execute_command(self, command: dict):
        """Execute a command and send result back."""
        command_id = command.get("id", "")
        tool = command.get("tool", "")
        params = command.get("params", {})

        self._active_commands += 1
        start = time.time()

        try:
            handler = TOOL_HANDLERS.get(tool)
            if not handler:
                result = {"error": f"Unknown tool: {tool}"}
            else:
                result = handler(params, self.allowed_paths)
            result["duration"] = round(time.time() - start, 2)
            result["node"] = self.name
            self.send_result(command_id, result)
            self._total_commands += 1
        except Exception as e:
            self.send_result(command_id, {"error": str(e), "node": self.name})
        finally:
            self._active_commands -= 1

    def run(self):
        """Main polling loop."""
        self.running = True
        print(f"Brain Agent Node v{__version__}", flush=True)
        print(f"Server: {self.server_url}", flush=True)
        print(f"Name: {self.name}", flush=True)
        if self.allowed_paths:
            print(f"Allowed paths: {', '.join(self.allowed_paths)}", flush=True)
        print(f"Connecting...", flush=True)

        # Quick reachability check (GET /v1/nodes returns fast, no long-poll)
        while not self._stop_event.is_set():
            try:
                self._get("/v1/nodes", timeout=10)
                self._connected = True
                self._backoff = 1
                print(f"Connected to {self.server_url}", flush=True)
                break
            except Exception as e:
                wait = min(self._backoff, 60)
                print(f"  Connection failed: {e}. Retrying in {wait}s...", flush=True)
                self._stop_event.wait(wait)
                self._backoff = min(self._backoff * 2, 60)

        while not self._stop_event.is_set():
            try:
                command = self.poll()
                if not self._connected:
                    # Reconnected after a disconnect
                    self._connected = True
                    self._backoff = 1
                    print(f"Reconnected to {self.server_url}", flush=True)
                if command:
                    tool = command.get("tool", "?")
                    print(f"  [{time.strftime('%H:%M:%S')}] {tool}: {str(command.get('params', {}))[:100]}", flush=True)
                    threading.Thread(
                        target=self.execute_command, args=(command,),
                        daemon=True,
                    ).start()
                else:
                    self._stop_event.wait(2)
            except Exception as e:
                if not self._stop_event.is_set():
                    if self._connected:
                        print(f"Connection lost: {e}", flush=True)
                        self._connected = False
                    wait = min(self._backoff, 60)
                    print(f"  Reconnecting in {wait}s...", flush=True)
                    self._stop_event.wait(wait)
                    self._backoff = min(self._backoff * 2, 60)

        self.running = False
        print("Node stopped.", flush=True)

    def stop(self):
        self._stop_event.set()


# --- Token Generation ---

def generate_token() -> str:
    """Generate a node authentication token."""
    return f"nd_{secrets.token_hex(16)}"


# --- launchd Management ---

PLIST_LABEL_PREFIX = "com.brain-agent.node"

def _plist_label(name: str) -> str:
    """Generate launchd label for a node name."""
    safe = name.replace(" ", "-").replace("/", "-").lower()
    return f"{PLIST_LABEL_PREFIX}.{safe}"

def _plist_path(name: str) -> str:
    """Path to the launchd plist file."""
    return os.path.expanduser(f"~/Library/LaunchAgents/{_plist_label(name)}.plist")

def _log_dir() -> str:
    d = os.path.expanduser("~/.brain-agent")
    os.makedirs(d, exist_ok=True)
    return d

def _find_python() -> str:
    """Find the best python3 binary."""
    for p in ("/opt/homebrew/bin/python3", "/usr/local/bin/python3", "/usr/bin/python3"):
        if os.path.exists(p):
            return p
    return sys.executable

def _generate_plist(name: str, server: str, token: str, allowed_paths: list[str] | None = None) -> str:
    """Generate launchd plist XML."""
    label = _plist_label(name)
    python = _find_python()
    node_py = os.path.abspath(__file__)
    log_dir = _log_dir()
    safe = name.replace(" ", "-").replace("/", "-").lower()

    args_xml = f"""        <string>{python}</string>
        <string>{node_py}</string>
        <string>--server</string>
        <string>{server}</string>
        <string>--token</string>
        <string>{token}</string>
        <string>--name</string>
        <string>{name}</string>"""

    if allowed_paths:
        args_xml += f"""
        <string>--allowed-paths</string>
        <string>{','.join(allowed_paths)}</string>"""

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{label}</string>
    <key>ProgramArguments</key>
    <array>
{args_xml}
    </array>
    <key>WorkingDirectory</key>
    <string>{os.path.expanduser("~")}</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>{log_dir}/node-{safe}.log</string>
    <key>StandardErrorPath</key>
    <string>{log_dir}/node-{safe}.error.log</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
    </dict>
</dict>
</plist>
"""

def cmd_install(args):
    """Install node as a launchd service."""
    if platform.system() != "Darwin":
        print("Error: launchd is macOS only. Use systemd on Linux.", flush=True)
        sys.exit(1)

    if not args.server or not args.token:
        print("Error: --server and --token are required for install", flush=True)
        sys.exit(1)

    name = args.name or platform.node()
    allowed_paths = None
    if args.allowed_paths:
        allowed_paths = [p.strip() for p in args.allowed_paths.split(",") if p.strip()]

    plist_file = _plist_path(name)
    label = _plist_label(name)

    # Unload existing if present
    if os.path.exists(plist_file):
        subprocess.run(["launchctl", "unload", plist_file], capture_output=True)

    plist_content = _generate_plist(name, args.server, args.token, allowed_paths)
    with open(plist_file, "w") as f:
        f.write(plist_content)

    result = subprocess.run(["launchctl", "load", plist_file], capture_output=True, text=True)
    if result.returncode != 0:
        print(f"Error loading plist: {result.stderr.strip()}", flush=True)
        sys.exit(1)

    safe = name.replace(" ", "-").replace("/", "-").lower()
    log_dir = _log_dir()
    print(f"Installed and started: {label}", flush=True)
    print(f"  Plist:  {plist_file}", flush=True)
    print(f"  Log:    {log_dir}/node-{safe}.log", flush=True)
    print(f"  Errors: {log_dir}/node-{safe}.error.log", flush=True)
    print(f"  Stop:   python3 node.py --uninstall --name {name}", flush=True)

def cmd_uninstall(args):
    """Uninstall node launchd service."""
    name = args.name or platform.node()
    plist_file = _plist_path(name)
    label = _plist_label(name)

    if not os.path.exists(plist_file):
        print(f"No plist found at {plist_file}", flush=True)
        sys.exit(1)

    subprocess.run(["launchctl", "unload", plist_file], capture_output=True)
    os.remove(plist_file)
    print(f"Uninstalled: {label}", flush=True)
    print(f"  Removed: {plist_file}", flush=True)

def cmd_status(args):
    """Show status of installed node services."""
    agents_dir = os.path.expanduser("~/Library/LaunchAgents")
    if not os.path.isdir(agents_dir):
        print("No LaunchAgents directory found.", flush=True)
        return

    found = False
    for f in sorted(os.listdir(agents_dir)):
        if f.startswith(PLIST_LABEL_PREFIX) and f.endswith(".plist"):
            label = f[:-6]  # strip .plist
            plist_file = os.path.join(agents_dir, f)
            # Check if loaded
            result = subprocess.run(
                ["launchctl", "list", label],
                capture_output=True, text=True,
            )
            if result.returncode == 0:
                # Parse PID from output
                lines = result.stdout.strip().split("\n")
                pid = "-"
                for line in lines:
                    if '"PID"' in line:
                        pid = line.split("=")[-1].strip().rstrip(";")
                status = f"running (PID {pid})" if pid != "-" and pid != "0" else "loaded"
            else:
                status = "stopped"

            name = label.replace(PLIST_LABEL_PREFIX + ".", "")
            print(f"  {name}: {status}", flush=True)
            print(f"    Plist: {plist_file}", flush=True)
            found = True

    if not found:
        print("No Brain Agent node services installed.", flush=True)


# --- Main ---

def main():
    parser = argparse.ArgumentParser(
        description=f"Brain Agent Remote Node v{__version__}",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  # Run directly (foreground):
  python3 node.py --server http://192.168.4.65:8420 --token nd_abc123...

  # Install as launchd service (auto-start on boot):
  python3 node.py --install --server http://192.168.4.65:8420 --token nd_abc123... --name build-server

  # Check installed services:
  python3 node.py --status

  # Uninstall launchd service:
  python3 node.py --uninstall --name build-server

  # Generate a token:
  python3 node.py --generate-token
""",
    )
    parser.add_argument("--server", help="Brain Agent server URL")
    parser.add_argument("--token", help="Node authentication token")
    parser.add_argument("--name", help="Node name (default: hostname)")
    parser.add_argument("--allowed-paths", help="Comma-separated allowed path prefixes")
    parser.add_argument("--generate-token", action="store_true", help="Generate a new token and exit")
    parser.add_argument("--install", action="store_true", help="Install as launchd service (macOS)")
    parser.add_argument("--uninstall", action="store_true", help="Uninstall launchd service")
    parser.add_argument("--status", action="store_true", help="Show status of installed node services")
    parser.add_argument("--version", action="version", version=f"Brain Agent Node v{__version__}")
    args = parser.parse_args()

    if args.generate_token:
        print(generate_token())
        sys.exit(0)

    if args.status:
        cmd_status(args)
        sys.exit(0)

    if args.install:
        cmd_install(args)
        sys.exit(0)

    if args.uninstall:
        cmd_uninstall(args)
        sys.exit(0)

    if not args.server or not args.token:
        parser.error("--server and --token are required")

    allowed_paths = None
    if args.allowed_paths:
        allowed_paths = [p.strip() for p in args.allowed_paths.split(",") if p.strip()]

    node = NodeClient(
        server_url=args.server,
        token=args.token,
        name=args.name,
        allowed_paths=allowed_paths,
    )

    try:
        node.run()
    except KeyboardInterrupt:
        print("\nStopping...")
        node.stop()


if __name__ == "__main__":
    main()
