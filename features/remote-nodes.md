# Feature Proposal: Remote Nodes

**Status:** Proposed
**Priority:** High
**Effort:** High (~12 days)
**Affects:** server.py, claude_cli.py, tools.md, web/index.html, tui.py, config.json, new node.py

---

## Problem

Brain Agent runs on a single machine. All tool execution (shell commands, file operations,
directory listings) happens on the same host as the server. There is no way for agents to:

- Run builds on a dedicated build server
- Read or write files on a NAS or media server
- Execute deployment commands on a production server
- Query databases on a remote host
- Collect system metrics from multiple machines

The only workaround is SSH via `execute_command`, which requires manual SSH key setup,
has no centralized management, provides no visibility into remote execution in the
activity viewer, and breaks the tool abstraction (agents must know about SSH syntax
instead of using `read_file` or `execute_command` naturally).

There is no unified way to manage, monitor, or restrict what remote machines can do.

---

## Proposed Solution

A lightweight remote node system with three components:

1. **`node.py`** -- A single-file Python agent (stdlib only) that runs on remote machines,
   connects back to the Brain Agent server, and executes commands on behalf of agents.

2. **Server-side node manager** -- Tracks connected nodes, routes tool calls to the
   correct node, enforces per-node tool restrictions, and exposes node status via API.

3. **UI integration** -- Nodes tab in Settings (Web UI), `/nodes` commands (TUI),
   live activity viewer entries for node executions, log viewer with node filtering.

---

## Architecture

### System Overview

```text
                        ┌─────────────────────────────────────────┐
                        │         Brain Agent Server (:8420)       │
                        │                                         │
                        │  ┌───────────────────────────────────┐  │
                        │  │         Node Manager               │  │
                        │  │  - Registry (config.json)          │  │
                        │  │  - Connection tracker               │  │
                        │  │  - Health monitor                   │  │
                        │  │  - Tool router                      │  │
                        │  └──────┬──────────┬──────────┬───────┘  │
                        │         │          │          │           │
                        └─────────┼──────────┼──────────┼───────────┘
                                  │          │          │
                        WebSocket │ WebSocket│ WebSocket│
                        + TLS     │ + TLS    │ + TLS    │
                                  │          │          │
                    ┌─────────────┘          │          └─────────────┐
                    │                        │                        │
           ┌────────┴────────┐    ┌──────────┴────────┐    ┌─────────┴───────┐
           │  Node A         │    │  Node B            │    │  Node C          │
           │  "build-server" │    │  "nas"             │    │  "production"    │
           │                 │    │                     │    │                  │
           │  Ubuntu 22.04   │    │  TrueNAS / FreeBSD │    │  Debian 12       │
           │  16 CPU / 64GB  │    │  4 CPU / 32GB      │    │  8 CPU / 16GB    │
           │  GPU: RTX 4090  │    │  120TB storage      │    │  SSD only        │
           │                 │    │                     │    │                  │
           │  Allowed tools: │    │  Allowed tools:     │    │  Allowed tools:  │
           │  - execute_cmd  │    │  - read_file        │    │  - execute_cmd   │
           │  - read_file    │    │  - write_file       │    │  - read_file     │
           │  - write_file   │    │  - list_directory   │    │  (no write)      │
           │  - list_dir     │    │  - search_files     │    │                  │
           │  - search_files │    │                     │    │                  │
           │                 │    │                     │    │                  │
           │  Tags: gpu,     │    │  Tags: storage,     │    │  Tags: prod,     │
           │        linux    │    │        backup        │    │        web       │
           └─────────────────┘    └─────────────────────┘    └──────────────────┘
```

### Connection Model

Nodes initiate outbound WebSocket connections to the Brain Agent server. This means:

- No inbound ports need to be opened on the remote machine
- Works behind NAT, firewalls, corporate networks
- The server is the single point of contact -- nodes connect to it, not the other way around
- Connection URL: `wss://brain.alexklinsky.dev/v1/nodes/ws?token=<node_token>`
- For local network: `ws://192.168.4.65:8420/v1/nodes/ws?token=<node_token>`

### Protocol

Communication uses JSON messages over WebSocket:

```text
Node → Server:
  {"type": "auth",      "token": "nd_abc123...", "hostname": "build-01", "os": "Linux 6.1", "arch": "x86_64"}
  {"type": "heartbeat", "cpu": 23.5, "mem_used": 48.2, "mem_total": 64.0, "disk_free": 120.5, "uptime": 86400}
  {"type": "result",    "request_id": "req_xyz", "exit_code": 0, "stdout": "...", "stderr": "...", "duration": 3.2}
  {"type": "stream",    "request_id": "req_xyz", "chunk": "Building layer 3/12..."}
  {"type": "error",     "request_id": "req_xyz", "error": "Permission denied: /etc/shadow"}

Server → Node:
  {"type": "auth_ok",   "node_name": "build-server"}
  {"type": "auth_fail", "reason": "Invalid token"}
  {"type": "execute",   "request_id": "req_xyz", "tool": "execute_command", "params": {"command": "make build"}}
  {"type": "cancel",    "request_id": "req_xyz"}
  {"type": "ping"}
```

### Heartbeat and Health

- Nodes send heartbeat every 30 seconds with system metrics
- Server marks node as `stale` after 60 seconds without heartbeat
- Server marks node as `offline` after 120 seconds without heartbeat
- Heartbeat includes: CPU %, memory used/total, disk free, system load, uptime
- Server stores last 10 heartbeats for trend display in UI

---

## Configuration

### config.json -- Nodes Section

```json
{
  "server": {"host": "0.0.0.0", "port": 8420},
  "providers": { "..." : "..." },
  "nodes": {
    "build-server": {
      "token": "nd_a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6",
      "description": "CI/CD build server",
      "allowed_tools": ["execute_command", "read_file", "write_file", "list_directory", "search_files"],
      "tags": ["gpu", "linux", "docker"],
      "ip_allowlist": ["10.0.1.0/24"],
      "max_concurrent": 10,
      "command_timeout": 600,
      "paused": false
    },
    "nas": {
      "token": "nd_q1w2e3r4t5y6u7i8o9p0a1s2d3f4g5h6",
      "description": "Network attached storage",
      "allowed_tools": ["read_file", "write_file", "list_directory", "search_files"],
      "tags": ["storage", "backup"],
      "ip_allowlist": [],
      "max_concurrent": 5,
      "command_timeout": 300,
      "paused": false
    },
    "production": {
      "token": "nd_z1x2c3v4b5n6m7k8j9h0g1f2d3s4a5q6",
      "description": "Production web server",
      "allowed_tools": ["execute_command", "read_file"],
      "tags": ["prod", "web"],
      "ip_allowlist": ["203.0.113.50/32"],
      "max_concurrent": 3,
      "command_timeout": 120,
      "paused": false
    }
  }
}
```

### Field Reference

| Field              | Type       | Description                                                       |
|--------------------|------------|-------------------------------------------------------------------|
| `token`            | string     | Authentication token (generated by server, `nd_` prefix + 32 hex) |
| `description`      | string     | Human-readable description shown in UI                            |
| `allowed_tools`    | string[]   | Tools this node can execute (whitelist)                           |
| `tags`             | string[]   | Labels for tag-based node selection                               |
| `ip_allowlist`     | string[]   | CIDR ranges allowed to connect (empty = any)                      |
| `max_concurrent`   | int        | Max simultaneous commands on this node                            |
| `command_timeout`  | int        | Default timeout in seconds for commands on this node              |
| `paused`           | bool       | If true, node rejects new commands                                |

---

## Node Agent (`node.py`)

### Design Goals

- **Single file** -- one Python script, no pip dependencies, copy and run
- **Stdlib only** -- uses `asyncio`, `websockets` (bundled or fallback to `http.client` long-polling)
- **Minimal footprint** -- runs as a background service with <10MB memory
- **Auto-reconnect** -- exponential backoff on connection loss (1s, 2s, 4s, ... 60s max)
- **Service install** -- `python3 node.py --install` generates systemd unit or launchd plist

### Supported Tools

The node agent implements these tool handlers:

| Tool              | Node Implementation                                              |
|-------------------|------------------------------------------------------------------|
| `execute_command` | Subprocess with timeout, stdout/stderr streaming, exit code      |
| `read_file`       | Read file contents, optional line range, binary detection        |
| `write_file`      | Write file contents, create parent directories                   |
| `list_directory`  | List files/dirs with glob patterns                               |
| `search_files`    | Regex search across files (mirrors local search_files behavior)  |

### Command-Line Interface

```text
$ python3 node.py --help
Brain Agent Remote Node v1.0

Usage:
  python3 node.py --server URL --token TOKEN [OPTIONS]

Required:
  --server URL       Brain Agent server URL (e.g., wss://brain.alexklinsky.dev)
  --token TOKEN      Node authentication token

Options:
  --name NAME        Override node name (default: hostname)
  --install          Install as system service (systemd/launchd)
  --uninstall        Remove system service
  --status           Show connection status
  --log-file PATH    Log to file (default: stdout)
  --log-level LEVEL  Log level: debug, info, warn, error (default: info)
  --allowed-paths    Comma-separated path prefixes the node can access
                     (default: / — unrestricted)
  --version          Show version

Examples:
  # Connect to local server
  python3 node.py --server ws://192.168.4.65:8420 --token nd_a1b2c3...

  # Connect to public server with TLS
  python3 node.py --server wss://brain.alexklinsky.dev --token nd_a1b2c3...

  # Install as systemd service
  sudo python3 node.py --server wss://brain.alexklinsky.dev --token nd_a1b2c3... --install
```

### Service Installation

**Linux (systemd):**

`python3 node.py --install` generates `/etc/systemd/system/brain-node.service`:

```ini
[Unit]
Description=Brain Agent Remote Node
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=brain-node
ExecStart=/usr/bin/python3 /opt/brain-agent/node.py --server wss://brain.alexklinsky.dev --token nd_a1b2c3...
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

**macOS (launchd):**

`python3 node.py --install` generates `~/Library/LaunchAgents/com.brain-agent.node.plist`
with `KeepAlive=true` and `RunAtLoad=true`.

### Output Streaming

For long-running commands, the node streams stdout/stderr chunks back to the server
in real time via `stream` messages. The server forwards these to the activity viewer
so the user can watch command output as it happens:

```text
Node                         Server                       Web UI Activity Viewer
 │                            │                            │
 │◄── execute: make build ────│                            │
 │                            │                            │
 │── stream: "Compiling..." ─►│── SSE: activity update ──►│  build-server: make build
 │── stream: "Linking..."   ─►│── SSE: activity update ──►│  [=====>    ] 2.3s
 │── stream: "Done."        ─►│── SSE: activity update ──►│  ✓ completed (4.1s)
 │                            │                            │
 │── result: exit=0, 4.1s  ─►│                            │
```

---

## Server-Side Implementation

### Node Manager (`_node_manager`)

A singleton object in `claude_cli.py` that manages all node connections:

```python
class NodeManager:
    """Manages remote node connections, health, and command routing."""

    def __init__(self):
        self._nodes = {}          # name -> NodeConnection
        self._registry = {}       # name -> config from config.json
        self._lock = threading.Lock()

    def register(self, name, config):
        """Register a node from config.json."""

    def handle_connect(self, ws, token):
        """Authenticate incoming WebSocket and track connection."""

    def handle_disconnect(self, name):
        """Mark node as offline, cancel pending commands."""

    def route_command(self, node_selector, tool, params, request_id):
        """Route a tool call to the specified node.
        node_selector: "build-server" or "tag:gpu" or "tag:storage"
        """

    def get_status(self):
        """Return status of all nodes for API/UI."""

    def cancel_command(self, request_id):
        """Cancel a running command on a node."""
```

### Tool Routing

When an agent calls a tool with the `node` parameter, the server intercepts it before
local execution and routes it to the appropriate node:

```python
# In the tool dispatch logic (claude_cli.py)
def _execute_tool(tool_name, params):
    node_selector = params.pop("node", None)
    if node_selector:
        # Validate node exists and is connected
        node = _node_manager.resolve(node_selector)
        if not node:
            return {"error": f"Node '{node_selector}' not found or offline"}
        if node.paused:
            return {"error": f"Node '{node_selector}' is paused for maintenance"}
        if tool_name not in node.allowed_tools:
            return {"error": f"Tool '{tool_name}' not allowed on node '{node_selector}'"}
        # Route to remote node
        return _node_manager.route_command(node_selector, tool_name, params)
    else:
        # Execute locally (existing behavior)
        return _execute_local(tool_name, params)
```

### Tag-Based Node Selection

When `node` starts with `tag:`, the server selects the best available node with that tag:

```text
execute_command(node="tag:gpu", command="python train.py")
```

Selection strategy:
1. Filter nodes that have the tag and are connected + not paused
2. Filter nodes that allow the requested tool
3. Pick the node with the lowest current command count (least busy)
4. If tied, pick the node with the lowest CPU usage (from last heartbeat)

### API Endpoints

| Method | Path                    | Description                                      |
|--------|-------------------------|--------------------------------------------------|
| GET    | `/v1/nodes`             | List all nodes with status and last heartbeat    |
| POST   | `/v1/nodes`             | Add, remove, pause, resume, or update a node     |
| GET    | `/v1/nodes/<name>`      | Detailed node info: health history, recent cmds  |
| GET    | `/v1/nodes/ws`          | WebSocket endpoint for node connections          |
| GET    | `/v1/nodes/<name>/logs` | Recent command log for a specific node           |

#### POST /v1/nodes -- Actions

```json
// Add a new node (generates token)
{"action": "add", "name": "staging", "description": "Staging server", "allowed_tools": ["execute_command", "read_file"], "tags": ["staging"]}
// Response: {"ok": true, "token": "nd_...", "install_command": "python3 node.py --server wss://brain.alexklinsky.dev --token nd_..."}

// Remove a node
{"action": "remove", "name": "staging"}

// Pause a node (rejects new commands, existing commands finish)
{"action": "pause", "name": "build-server"}

// Resume a node
{"action": "resume", "name": "build-server"}

// Update node config
{"action": "update", "name": "build-server", "allowed_tools": ["execute_command", "read_file"], "tags": ["linux"]}
```

#### GET /v1/nodes -- Response

```json
{
  "nodes": [
    {
      "name": "build-server",
      "description": "CI/CD build server",
      "status": "connected",
      "paused": false,
      "hostname": "build-01.internal",
      "os": "Linux 6.1.0 x86_64",
      "tags": ["gpu", "linux", "docker"],
      "allowed_tools": ["execute_command", "read_file", "write_file", "list_directory", "search_files"],
      "last_heartbeat": "2026-03-20T14:32:15Z",
      "cpu_percent": 23.5,
      "mem_used_gb": 30.8,
      "mem_total_gb": 64.0,
      "disk_free_gb": 120.5,
      "uptime_seconds": 864000,
      "active_commands": 2,
      "total_commands": 1847,
      "connected_since": "2026-03-19T08:00:00Z"
    },
    {
      "name": "nas",
      "description": "Network attached storage",
      "status": "connected",
      "paused": false,
      "hostname": "truenas.local",
      "os": "FreeBSD 13.2 amd64",
      "tags": ["storage", "backup"],
      "allowed_tools": ["read_file", "write_file", "list_directory", "search_files"],
      "last_heartbeat": "2026-03-20T14:32:10Z",
      "cpu_percent": 5.1,
      "mem_used_gb": 12.3,
      "mem_total_gb": 32.0,
      "disk_free_gb": 45000.0,
      "uptime_seconds": 2592000,
      "active_commands": 0,
      "total_commands": 523,
      "connected_since": "2026-03-18T12:00:00Z"
    },
    {
      "name": "production",
      "description": "Production web server",
      "status": "disconnected",
      "paused": false,
      "hostname": "web-prod-01",
      "os": "Debian 12.4 x86_64",
      "tags": ["prod", "web"],
      "allowed_tools": ["execute_command", "read_file"],
      "last_heartbeat": "2026-03-20T13:15:00Z",
      "cpu_percent": null,
      "mem_used_gb": null,
      "mem_total_gb": 16.0,
      "disk_free_gb": null,
      "uptime_seconds": null,
      "active_commands": 0,
      "total_commands": 89,
      "connected_since": null
    }
  ]
}
```

---

## Web UI

### Settings -- Nodes Tab

```text
┌─ Settings ──────────────────────────────────────────────────────────────────┐
│  Server │ QMD │ Models │ Nodes │ Telegram │ Providers                       │
│─────────────────────────────────────────────────────────────────────────────│
│                                                                             │
│  Remote Nodes                                              [+ Add Node]     │
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │  ● build-server              CI/CD build server                    │    │
│  │    Linux 6.1.0 · build-01.internal · Connected 1d 6h               │    │
│  │    CPU 23% · RAM 30.8/64 GB · Disk 120 GB free                     │    │
│  │    Tags: gpu, linux, docker                                        │    │
│  │    Tools: execute_command, read_file, write_file, list_directory    │    │
│  │    Active: 2 commands · Total: 1,847                               │    │
│  │                                        [Pause]  [Edit]  [Remove]   │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │  ● nas                       Network attached storage              │    │
│  │    FreeBSD 13.2 · truenas.local · Connected 2d 2h                  │    │
│  │    CPU 5% · RAM 12.3/32 GB · Disk 45 TB free                      │    │
│  │    Tags: storage, backup                                           │    │
│  │    Tools: read_file, write_file, list_directory, search_files      │    │
│  │    Active: 0 commands · Total: 523                                 │    │
│  │                                        [Pause]  [Edit]  [Remove]   │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │  ○ production                Production web server                 │    │
│  │    Debian 12.4 · web-prod-01 · Disconnected since 1h 17m ago      │    │
│  │    Tags: prod, web                                                 │    │
│  │    Tools: execute_command, read_file                                │    │
│  │    Total: 89 commands                                              │    │
│  │                                       [Resume]  [Edit]  [Remove]   │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                                                             │
│  Legend: ● Connected  ◐ Stale  ○ Disconnected  ⏸ Paused                    │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Add Node Dialog

```text
┌─ Add Remote Node ───────────────────────────────────────────────────────────┐
│                                                                             │
│  Node Name:     [staging-server          ]                                  │
│  Description:   [Staging environment      ]                                 │
│                                                                             │
│  Allowed Tools:                                                             │
│    [x] execute_command                                                      │
│    [x] read_file                                                            │
│    [x] write_file                                                           │
│    [x] list_directory                                                       │
│    [ ] search_files                                                         │
│                                                                             │
│  Tags:          [staging, linux           ]                                 │
│  IP Allowlist:  [10.0.0.0/8              ]   (blank = any IP)              │
│  Max Concurrent: [5  ]                                                      │
│  Cmd Timeout:    [300] seconds                                              │
│                                                                             │
│                                                   [Cancel]  [Create Node]   │
│─────────────────────────────────────────────────────────────────────────────│
│                                                                             │
│  ┌─ Install Command ──────────────────────────────────────────────────┐     │
│  │                                                                    │     │
│  │  # Run this on the remote machine:                                 │     │
│  │  curl -sO https://brain.alexklinsky.dev/node.py                    │     │
│  │  python3 node.py \                                                 │     │
│  │    --server wss://brain.alexklinsky.dev \                          │     │
│  │    --token nd_7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2c                    │     │
│  │                                                                    │     │
│  │  # Or install as a service:                                        │     │
│  │  sudo python3 node.py \                                            │     │
│  │    --server wss://brain.alexklinsky.dev \                          │     │
│  │    --token nd_7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2c \                  │     │
│  │    --install                                                       │     │
│  │                                                        [Copy]      │     │
│  └────────────────────────────────────────────────────────────────────┘     │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Node Detail View

```text
┌─ Node: build-server ────────────────────────────────────────────────────────┐
│                                                                             │
│  Status: ● Connected                          [Pause]  [Edit]  [Remove]     │
│                                                                             │
│  ┌─ System Info ──────────────────────────────────────────────────────┐     │
│  │  Hostname:    build-01.internal                                    │     │
│  │  OS:          Linux 6.1.0 x86_64 (Ubuntu 22.04)                    │     │
│  │  Uptime:      10 days 0 hours                                      │     │
│  │  Connected:   1 day 6 hours ago                                    │     │
│  │  Last Beat:   2 seconds ago                                        │     │
│  └────────────────────────────────────────────────────────────────────┘     │
│                                                                             │
│  ┌─ Resources ────────────────────────────────────────────────────────┐     │
│  │  CPU:     [=========>                     ] 23.5%                   │     │
│  │  Memory:  [==================>            ] 48.1%  (30.8 / 64 GB)  │     │
│  │  Disk:    [======>                        ] 21.3%  (120 GB free)   │     │
│  └────────────────────────────────────────────────────────────────────┘     │
│                                                                             │
│  ┌─ Configuration ────────────────────────────────────────────────────┐     │
│  │  Allowed Tools:   execute_command, read_file, write_file,          │     │
│  │                   list_directory, search_files                      │     │
│  │  Tags:            gpu, linux, docker                               │     │
│  │  IP Allowlist:    10.0.1.0/24                                      │     │
│  │  Max Concurrent:  10                                               │     │
│  │  Cmd Timeout:     600s                                             │     │
│  └────────────────────────────────────────────────────────────────────┘     │
│                                                                             │
│  ┌─ Recent Commands ─────────────────────────────────────────────────┐      │
│  │  Time          Tool              Command / Path         Duration  │      │
│  │  14:32:01      execute_command   docker build .         4.1s  ✓  │      │
│  │  14:31:45      read_file         /app/Dockerfile        0.1s  ✓  │      │
│  │  14:30:12      execute_command   make test              12.3s ✓  │      │
│  │  14:28:55      list_directory    /app/src/              0.2s  ✓  │      │
│  │  14:25:00      execute_command   git pull origin main   1.8s  ✓  │      │
│  │  14:20:33      execute_command   npm install            45.2s ✓  │      │
│  │  14:15:10      search_files      "TODO|FIXME" /app/     3.1s  ✓  │      │
│  │  14:10:00      execute_command   df -h                  0.3s  ✓  │      │
│  │                                                                    │     │
│  │  Showing 8 of 1,847 commands                    [Load More]        │     │
│  └────────────────────────────────────────────────────────────────────┘     │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Live Activity Viewer -- Node Executions

The existing activity viewer gains node awareness. Node commands appear as entries
with a node badge, real-time output streaming, and expandable details:

```text
┌─ Activity ──────────────────────────────────────────────────────────────────┐
│                                                          [Filter: All ▼]    │
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │  ⟳ build-server  execute_command          Running · 2.3s           │    │
│  │    docker build -t myapp:latest .                                   │    │
│  │    ┌──────────────────────────────────────────────────────────┐     │    │
│  │    │ Step 3/12 : RUN npm install                              │     │    │
│  │    │ ---> Running in 4a2b3c4d5e6f                             │     │    │
│  │    │ added 847 packages in 12.4s                              │     │    │
│  │    │ Step 4/12 : COPY . .                                     │     │    │
│  │    │ ▌                                                        │     │    │
│  │    └──────────────────────────────────────────────────────────┘     │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │  ✓ nas            read_file               Done · 0.4s              │    │
│  │    /media/documents/quarterly-report.pdf                            │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │  ✓ local          execute_command          Done · 1.2s              │    │
│  │    python3 analyze.py --input report.csv                            │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │  ✗ production     execute_command          Failed · 0.0s            │    │
│  │    systemctl restart nginx                                          │    │
│  │    Error: Node 'production' is disconnected                         │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │  ⏸ build-server  execute_command          Queued                    │    │
│  │    make deploy ENVIRONMENT=staging                                  │    │
│  │    Waiting: node at max_concurrent (10/10)                          │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Log Viewer -- Node Filter

```text
┌─ Logs ──────────────────────────────────────────────────────────────────────┐
│  Node: [All Nodes    ▼]  Tool: [All Tools ▼]  Status: [All ▼]  [Search]    │
│────────────────────────────────────────────────────────────────────────────│
│  2026-03-20 14:32:05  build-server  execute_command  ✓  4.1s              │
│    docker build -t myapp:latest .                                          │
│                                                                             │
│  2026-03-20 14:31:45  nas           read_file        ✓  0.4s              │
│    /media/documents/quarterly-report.pdf (245 KB)                          │
│                                                                             │
│  2026-03-20 14:30:12  build-server  execute_command  ✓  12.3s             │
│    make test                                                               │
│                                                                             │
│  2026-03-20 14:28:55  local         list_directory   ✓  0.1s              │
│    /Users/alexander/Documents/dev/cctest/                                   │
│                                                                             │
│  2026-03-20 14:25:00  production    execute_command  ✗  0.0s              │
│    systemctl restart nginx                                                 │
│    Error: Node 'production' disconnected                                   │
│                                                                             │
│  Showing 50 of 2,459 entries                           [Load More]         │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## TUI

### /nodes Command

```text
$ /nodes list

┌─ Remote Nodes ──────────────────────────────────────────────────────────────┐
│ Name           │ Status       │ OS              │ CPU  │ RAM       │ Tags   │
│────────────────┼──────────────┼─────────────────┼──────┼───────────┼────────│
│ build-server   │ ● Connected  │ Linux 6.1.0     │ 23%  │ 30.8/64G │ gpu    │
│ nas            │ ● Connected  │ FreeBSD 13.2    │  5%  │ 12.3/32G │ storage│
│ production     │ ○ Offline    │ Debian 12.4     │   -  │    -/16G │ prod   │
└────────────────┴──────────────┴─────────────────┴──────┴───────────┴────────┘

$ /nodes add staging "Staging environment" --tools execute_command,read_file --tags staging
Node 'staging' created.
Token: nd_7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2c

Install on remote machine:
  python3 node.py --server wss://brain.alexklinsky.dev --token nd_7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2c

$ /nodes pause build-server
Node 'build-server' paused.

$ /nodes resume build-server
Node 'build-server' resumed.

$ /nodes remove staging
Node 'staging' removed.
```

---

## Tool Call Examples

### execute_command with node

```json
{
  "tool": "execute_command",
  "params": {
    "node": "build-server",
    "command": "docker build -t myapp:latest .",
    "timeout": 300
  }
}
```

Result:
```json
{
  "exit_code": 0,
  "stdout": "Successfully built 4a2b3c4d5e6f\nSuccessfully tagged myapp:latest\n",
  "stderr": "",
  "duration": 4.1,
  "node": "build-server"
}
```

### read_file with node

```json
{
  "tool": "read_file",
  "params": {
    "node": "nas",
    "path": "/media/documents/report.pdf"
  }
}
```

### Tag-based node selection

```json
{
  "tool": "execute_command",
  "params": {
    "node": "tag:gpu",
    "command": "python3 train.py --epochs 100"
  }
}
```

The server resolves `tag:gpu` to the least-busy connected node tagged `gpu`
(in this case, `build-server`).

---

## Sequence Diagrams

### Full Command Flow

```text
Agent              Server               NodeManager          Node (build-server)     Activity Viewer
  │                  │                      │                       │                      │
  │  execute_command │                      │                       │                      │
  │  node="build-server"                    │                       │                      │
  │  command="make build"                   │                       │                      │
  │─────────────────►│                      │                       │                      │
  │                  │  route_command()      │                       │                      │
  │                  │─────────────────────►│                       │                      │
  │                  │                      │  validate:             │                      │
  │                  │                      │  - node connected?     │                      │
  │                  │                      │  - node paused?        │                      │
  │                  │                      │  - tool allowed?       │                      │
  │                  │                      │  - under max_concurrent?                      │
  │                  │                      │                       │                      │
  │                  │                      │  WS: execute           │                      │
  │                  │                      │──────────────────────►│                      │
  │                  │                      │                       │  subprocess.run()    │
  │                  │                      │                       │                      │
  │                  │  activity_update      │                       │                      │
  │                  │  (node="build-server", status="running")     │                      │
  │                  │──────────────────────────────────────────────────────────────────────►
  │                  │                      │                       │                      │
  │                  │                      │  WS: stream chunk      │                      │
  │                  │                      │◄──────────────────────│  "Compiling..."      │
  │                  │  SSE: activity update │                       │                      │
  │                  │──────────────────────────────────────────────────────────────────────►
  │                  │                      │                       │                      │
  │                  │                      │  WS: stream chunk      │                      │
  │                  │                      │◄──────────────────────│  "Linking..."        │
  │                  │  SSE: activity update │                       │                      │
  │                  │──────────────────────────────────────────────────────────────────────►
  │                  │                      │                       │                      │
  │                  │                      │  WS: result            │                      │
  │                  │                      │◄──────────────────────│  exit=0, 3.2s        │
  │                  │                      │                       │                      │
  │                  │  activity_update      │                       │                      │
  │                  │  (status="completed", duration=3.2s)         │                      │
  │                  │──────────────────────────────────────────────────────────────────────►
  │                  │                      │                       │                      │
  │◄─────────────────│  tool result          │                       │                      │
  │  {exit_code: 0,  │                      │                       │                      │
  │   stdout: "...", │                      │                       │                      │
  │   node: "build-server"}                 │                       │                      │
```

### Node Connection Flow

```text
Remote Machine                    Server                         config.json
     │                              │                                │
     │  python3 node.py             │                                │
     │  --server wss://...          │                                │
     │  --token nd_abc123           │                                │
     │                              │                                │
     │  WS connect                  │                                │
     │─────────────────────────────►│                                │
     │                              │  lookup token                  │
     │                              │───────────────────────────────►│
     │                              │◄───────────────────────────────│
     │                              │  found: "build-server"         │
     │                              │                                │
     │                              │  check IP allowlist            │
     │                              │  ✓ 10.0.1.15 in 10.0.1.0/24   │
     │                              │                                │
     │  WS: auth_ok                 │                                │
     │◄─────────────────────────────│                                │
     │  node_name="build-server"    │                                │
     │                              │                                │
     │  WS: heartbeat (every 30s)  │                                │
     │─────────────────────────────►│  update node status            │
     │                              │  broadcast to UI clients       │
     │                              │                                │
```

---

## Workflows

### 1. Admin Adds a New Node via Web UI

1. Admin opens Settings > Nodes tab in the Web UI.
2. Clicks [+ Add Node].
3. Fills in: Name = `staging-server`, Description = `Staging environment`,
   checks `execute_command` and `read_file`, adds tag `staging`.
4. Clicks [Create Node].
5. Server generates token `nd_7f8a9b0c...`, saves to `config.json`.
6. Dialog shows install command with the token.
7. Admin copies the command and SSHes into the staging server.
8. Runs: `curl -sO https://brain.alexklinsky.dev/node.py && python3 node.py --server wss://brain.alexklinsky.dev --token nd_7f8a9b0c... --install`
9. Node connects, authenticates, sends first heartbeat.
10. Nodes tab updates in real time: `staging-server` appears as `Connected`.

### 2. Agent Executes a Build on Remote Server

1. User asks: "Build the Docker image on the build server."
2. Agent calls `execute_command(node="build-server", command="docker build -t myapp:latest .")`.
3. Server validates: node is connected, not paused, `execute_command` is allowed.
4. Server sends `execute` message to the build-server node via WebSocket.
5. Activity viewer shows: `build-server: docker build -t myapp:latest .` with `Running` status.
6. Node streams stdout chunks as the build progresses.
7. Activity viewer updates in real time with streaming output.
8. Build completes (exit code 0, 45.2s). Result returned to agent.
9. Activity viewer shows: green checkmark, `Done - 45.2s`.
10. Agent reports: "Docker image built successfully on build-server in 45 seconds."

### 3. Agent Reads a File on the NAS

1. User asks: "Read the quarterly report from the NAS."
2. Agent calls `read_file(node="nas", path="/media/documents/quarterly-report.pdf")`.
3. Server routes to the `nas` node.
4. Node reads the file, returns contents (with size metadata).
5. Agent receives the file contents and summarizes them.

### 4. Admin Pauses a Node for Maintenance

1. Admin clicks [Pause] on `build-server` in the Nodes tab.
2. Server sets `paused: true` in config.json and node manager.
3. Currently running commands on that node continue to completion.
4. New commands targeting `build-server` return: `{"error": "Node 'build-server' is paused for maintenance"}`.
5. Tag-based selection (`tag:gpu`) skips the paused node.
6. Nodes tab shows the pause icon next to `build-server`.
7. After maintenance, admin clicks [Resume]. Node accepts commands again.

### 5. Node Disconnects Unexpectedly

1. `production` node loses network connectivity.
2. Server detects no heartbeat for 60 seconds, marks node as `stale`.
3. After 120 seconds, server marks node as `offline`.
4. Activity viewer shows `production` with `Disconnected` badge.
5. Any pending commands on `production` are marked as failed:
   `{"error": "Node 'production' disconnected during execution"}`.
6. Agent receives the error and informs the user.
7. When the node reconnects, it re-authenticates and resumes accepting commands.
8. Nodes tab updates to `Connected` in real time.

### 6. Admin Monitors Node Activity

1. Admin opens the Activity viewer panel.
2. Sees all tool executions across all nodes in real time.
3. Uses the Node filter dropdown to show only `build-server` commands.
4. Sees a long-running `make test` with streaming output.
5. Clicks to expand the entry and sees full stdout.
6. Switches to Log viewer for historical commands.
7. Filters by node = `nas`, sees all file operations.

---

## Security Considerations

### Authentication

- Each node has a unique token (`nd_` prefix + 32 hex characters).
- Tokens are stored in `config.json` (server side) and passed as CLI arg (node side).
- Token is sent once during WebSocket handshake, not repeated per message.
- Tokens can be regenerated via the API (forces reconnection with new token).

### Transport Security

- Production deployments should use `wss://` (WebSocket over TLS).
- The Cloudflare Zero Trust tunnel already provides TLS termination.
- Local network connections can use `ws://` for simplicity.

### IP Allowlisting

- Each node can have an `ip_allowlist` of CIDR ranges.
- If set, the server rejects WebSocket connections from non-matching IPs.
- Empty allowlist means any IP can connect with a valid token.

### Tool Restrictions

- Each node has an explicit `allowed_tools` whitelist.
- The server enforces this before routing -- the node never sees disallowed commands.
- A NAS node might only allow `read_file` and `list_directory` (no command execution).
- A production node might allow `read_file` only (monitoring, not modification).

### Path Restrictions (Node-Side)

- The node agent supports `--allowed-paths` to restrict filesystem access.
- Example: `--allowed-paths /app,/var/log` prevents reading `/etc/shadow`.
- Enforced locally on the node, independent of server-side restrictions.

### Command Sandboxing

- `execute_command` on nodes inherits the same restrictions as local execution:
  no TTY, no stdin, TERM=dumb, configurable timeout.
- Node-side timeout takes precedence over server-side `command_timeout`.
- Nodes run as a dedicated low-privilege user (`brain-node`) when installed as a service.

---

## Performance Considerations

### Large File Transfers

- Files over 1 MB are chunked (64 KB chunks) and streamed over WebSocket.
- The server reassembles chunks before returning to the agent.
- Binary files are base64-encoded for WebSocket transport.
- Very large files (>100 MB) should use `execute_command` with `scp` or `rsync` instead.

### Command Output Buffering

- Stdout/stderr are buffered in 4 KB chunks on the node.
- Chunks are sent every 100ms or when the buffer is full (whichever comes first).
- The server stores the last 100 lines of output for the activity viewer.
- Full output is returned in the final result message.

### Concurrent Commands

- Each node has a `max_concurrent` limit.
- Commands exceeding the limit are queued server-side.
- Queue position is shown in the activity viewer.
- If the queue exceeds 20 commands, new commands are rejected immediately.

### WebSocket Keepalive

- Server sends `ping` frames every 30 seconds to detect dead connections.
- Node responds with `pong` (WebSocket protocol level, automatic).
- Application-level heartbeats (node to server) carry system metrics.

---

## Resilience

### Node Offline During Command

- If a node disconnects while a command is running, the server waits 30 seconds
  for reconnection.
- If the node reconnects within 30 seconds, it reports the command result
  (the command continues running on the node regardless of connection state).
- If the node does not reconnect, the command is marked as failed with
  `"Node disconnected during execution"`.

### Server Restart

- On restart, the server reloads node registry from config.json.
- Connected nodes detect the WebSocket close and auto-reconnect.
- Reconnection uses exponential backoff: 1s, 2s, 4s, 8s, 16s, 32s, 60s max.
- In-flight commands at the time of server restart are lost (no persistence).

### Node Restart

- Installed as a service, the node auto-restarts on crash (systemd Restart=always).
- On restart, the node reconnects and re-authenticates.
- Any commands that were running are lost (the node starts fresh).

---

## Effort Estimate

**Total: ~12 working days**

### Phase 1: Node Agent + Server Core (5 days)

| Task                                         | Days |
|----------------------------------------------|------|
| `node.py` -- WebSocket client, auth, heartbeat | 1.5  |
| `node.py` -- Tool handlers (execute, read, write, list, search) | 1.0  |
| `node.py` -- Auto-reconnect, service install | 0.5  |
| `server.py` -- WebSocket endpoint, NodeManager | 1.0  |
| `claude_cli.py` -- Tool routing with `node` parameter | 0.5  |
| `config.json` -- Nodes section, token generation | 0.5  |

**Milestone:** A node can connect, authenticate, and execute commands routed
by the server. `execute_command(node="X", command="...")` works end-to-end.

### Phase 2: API + Activity Integration (3 days)

| Task                                          | Days |
|-----------------------------------------------|------|
| REST API: GET/POST /v1/nodes, node detail     | 1.0  |
| Activity viewer integration (SSE events)      | 1.0  |
| Output streaming (node -> server -> UI)       | 0.5  |
| Command logging (node tag in log entries)     | 0.5  |

**Milestone:** Nodes can be managed via API. Activity viewer shows node
commands in real time with streaming output.

### Phase 3: Web UI (3 days)

| Task                                          | Days |
|-----------------------------------------------|------|
| Settings > Nodes tab (list, status, metrics)  | 1.0  |
| Add Node dialog with install command          | 0.5  |
| Node detail view (system info, recent cmds)   | 0.5  |
| Activity viewer: node badges, streaming       | 0.5  |
| Log viewer: node filter dropdown              | 0.5  |

**Milestone:** Full Web UI for node management, monitoring, and activity tracking.

### Phase 4: TUI + Polish (1 day)

| Task                                          | Days |
|-----------------------------------------------|------|
| `/nodes` slash commands (list, add, remove, pause) | 0.5  |
| Tag-based node selection (`tag:gpu`)          | 0.25 |
| IP allowlist enforcement                      | 0.25 |

**Milestone:** Feature-complete across all frontends.

---

## Future Extensions (Not in Scope)

- **File sync** -- Bidirectional file sync between server and nodes (rsync-like)
- **Node groups** -- Named groups of nodes for batch operations
- **Node plugins** -- Custom tool handlers that extend beyond the 5 built-in tools
- **Node-to-node** -- Direct communication between nodes (mesh topology)
- **Node auto-discovery** -- mDNS/Bonjour discovery on local networks
- **Web terminal** -- Interactive terminal session to a node via the Web UI
- **Audit log** -- Immutable log of all commands executed on all nodes
- **Rate limiting** -- Per-node rate limits beyond max_concurrent

---

## Open Questions

1. **WebSocket vs long-polling HTTP fallback** -- Should `node.py` bundle a pure-Python
   WebSocket client, or should there be an HTTP long-polling fallback for environments
   where WebSocket is blocked? Bundling a minimal WebSocket implementation (~200 lines)
   in stdlib-only Python is feasible.

2. **Binary file encoding** -- Base64 increases transfer size by ~33%. Should large binary
   files use a separate HTTP upload/download endpoint instead of WebSocket?

3. **Command persistence** -- Should in-flight commands survive a server restart? This
   would require a `nodes.db` SQLite database to track pending commands, adding complexity.

4. **Multi-server** -- If Brain Agent is ever deployed in a multi-server setup (load
   balancer), node connections would need sticky sessions or a shared connection registry.

5. **Node authentication rotation** -- Should tokens expire and auto-rotate, or is
   manual regeneration sufficient?
