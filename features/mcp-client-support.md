# Feature Proposal: Enhanced MCP Client Support

**Status:** Proposed
**Priority:** Medium
**Effort:** High (4-5 days)
**Affects:** `claude_cli.py`, `server.py`, `web/index.html`, `agents/*/mcp.json`

---

## Problem

MCP (Model Context Protocol) server connections in Brain Agent are currently static.
They are defined in `mcp.json` files and loaded at server startup. This creates
several limitations:

1. **No runtime discovery** -- agents cannot connect to new MCP servers during a
   conversation. If a user spins up a new MCP server, the server must be restarted
   to pick it up.

2. **No on-demand connection** -- an agent working on a GitHub task cannot say
   "I need the GitHub MCP server" and connect to it. The connection must already
   exist in `mcp.json`.

3. **No visibility** -- the Web UI shows which MCP servers are configured but not
   their runtime status (connected, disconnected, latency, available tools).

4. **No connection management** -- cannot disconnect from an MCP server without
   editing `mcp.json` and restarting. Cannot temporarily connect for a single task.

5. **No multi-transport flexibility** -- agents are locked into whatever transport
   (stdio/SSE) was configured at setup time, with no ability to switch or try
   alternatives.

### Current Static Configuration

```json
// agents/main/mcp.json (must be edited manually, requires restart)
{
  "filesystem": {
    "transport": "stdio",
    "command": "npx",
    "args": ["-y", "@modelcontextprotocol/server-filesystem", "/path"]
  },
  "remote-server": {
    "transport": "sse",
    "url": "http://host:3000/mcp"
  }
}
```

---

## Proposed Solution

Add dynamic MCP client capabilities that allow agents to discover, connect to,
and manage MCP server connections at runtime through tool calls and the Web UI.

### New Tool: `mcp_connect`

```text
Tool: mcp_connect

Parameters:
  url:        string (required) - MCP server URL or stdio command
  transport:  string (optional) - "sse" (default) or "stdio"
  name:       string (optional) - friendly name for the connection
  persist:    bool   (optional) - save to mcp.json (default: false)

Returns:
  {
    "status": "connected",
    "name": "github",
    "transport": "sse",
    "url": "http://localhost:3001/mcp",
    "tools": [
      {"name": "github_list_repos", "description": "List repositories"},
      {"name": "github_list_prs", "description": "List pull requests"},
      {"name": "github_create_issue", "description": "Create an issue"},
      {"name": "github_read_file", "description": "Read file from repo"},
      {"name": "github_search_code", "description": "Search code in repos"}
    ],
    "tool_count": 5,
    "latency_ms": 42
  }
```

### New Tool: `mcp_disconnect`

```text
Tool: mcp_disconnect

Parameters:
  name:    string (required) - connection name to disconnect
  remove:  bool   (optional) - also remove from mcp.json (default: false)

Returns:
  {"status": "disconnected", "name": "github"}
```

### New Tool: `mcp_status`

```text
Tool: mcp_status

Parameters:
  name:    string (optional) - specific connection, or all if omitted

Returns:
  {
    "connections": [
      {
        "name": "filesystem",
        "transport": "stdio",
        "status": "connected",
        "tools": 5,
        "uptime_seconds": 3600,
        "last_call": "2026-03-20T10:15:00",
        "latency_ms": 12,
        "persisted": true
      },
      {
        "name": "github",
        "transport": "sse",
        "status": "connected",
        "tools": 5,
        "uptime_seconds": 120,
        "last_call": "2026-03-20T10:14:30",
        "latency_ms": 42,
        "persisted": false
      }
    ]
  }
```

### Architecture Diagram

```text
+-------------------------------------------------------------------+
|                        Brain Agent Server                          |
|                                                                    |
|  +------------------+    +-------------------------------------+  |
|  |   Agent Engine   |    |         MCP Client Manager          |  |
|  |  (claude_cli.py) |    |                                     |  |
|  |                  |    |  +----------+  +----------+         |  |
|  |  mcp_connect() --+--->|  | Session  |  | Session  |  ...    |  |
|  |  mcp_disconnect()-+-->|  | (stdio)  |  | (SSE)    |         |  |
|  |  mcp_status() ---+--->|  +----+-----+  +----+-----+         |  |
|  |                  |    |       |              |               |  |
|  |  mcp_* tools  <--+---+  tools discovered    |               |  |
|  +------------------+    +------+---------------+---------------+  |
|                                 |               |                  |
+-------------------------------------------------------------------+
                                  |               |
                    +-------------+    +----------+----------+
                    |                  |                      |
              +-----+------+    +-----+------+    +----------+---+
              |  Filesystem |    |   GitHub   |    |   Remote     |
              |  MCP Server |    |  MCP Server|    |  MCP Server  |
              |  (stdio)    |    |  (SSE)     |    |  (SSE)       |
              |  localhost   |    |  :3001     |    |  external    |
              +-------------+    +------------+    +--------------+
```

### Web UI: MCP Connections Panel

```text
+-- Agent Config Modal: MCP Tab ------------------------------------+
|                                                                    |
|  MCP Connections                                    [+ Connect]    |
|                                                                    |
|  +--------------------------------------------------------------+ |
|  | filesystem (stdio)                              [Connected]   | |
|  | npx @modelcontextprotocol/server-filesystem                   | |
|  | Tools: 5 | Latency: 12ms | Uptime: 1h         [Disconnect]   | |
|  | Persisted: yes                                                | |
|  +--------------------------------------------------------------+ |
|  | github (sse)                                    [Connected]   | |
|  | http://localhost:3001/mcp                                     | |
|  | Tools: 5 | Latency: 42ms | Uptime: 2m         [Disconnect]   | |
|  | Persisted: no (session only)                     [Save]       | |
|  +--------------------------------------------------------------+ |
|  | slack (sse)                                   [Disconnected]  | |
|  | http://localhost:3002/mcp                                     | |
|  | Last error: Connection refused                 [Reconnect]    | |
|  | Persisted: yes                                 [Remove]       | |
|  +--------------------------------------------------------------+ |
|                                                                    |
|  Discovered Tools (from all connections):                          |
|  +--------------------------------------------------------------+ |
|  | filesystem_read_file    | Read a file from the filesystem     | |
|  | filesystem_write_file   | Write content to a file             | |
|  | filesystem_list_dir     | List directory contents              | |
|  | filesystem_search       | Search files by pattern              | |
|  | filesystem_get_info     | Get file metadata                    | |
|  | github_list_repos       | List GitHub repositories             | |
|  | github_list_prs         | List pull requests                   | |
|  | github_create_issue     | Create a new issue                   | |
|  | github_read_file        | Read file from a repository          | |
|  | github_search_code      | Search code across repos             | |
|  +--------------------------------------------------------------+ |
|                                                                    |
+-------------------------------------------------------------------+
```

### Connect Dialog

```text
+-- Connect to MCP Server -----------------------------------------+
|                                                                    |
|  Transport:  (*) SSE    ( ) stdio                                 |
|                                                                    |
|  URL:        [http://localhost:3001/mcp                         ]  |
|                                                                    |
|  -- OR for stdio --                                               |
|  Command:    [npx                                               ]  |
|  Arguments:  [-y @modelcontextprotocol/server-github            ]  |
|                                                                    |
|  Name:       [github                ] (auto-detected if blank)    |
|                                                                    |
|  [x] Save to mcp.json (persist across restarts)                   |
|                                                                    |
|  [Cancel]                                          [Connect]      |
+-------------------------------------------------------------------+
```

### agent.json: Auto-Connect List

```json
{
  "description": "Research team lead",
  "display_name": "Research Lead",
  "model": "claude-sonnet-4-6",
  "mcp_auto_connect": [
    {
      "name": "github",
      "url": "http://localhost:3001/mcp",
      "transport": "sse",
      "required": false
    },
    {
      "name": "arxiv",
      "url": "http://localhost:3003/mcp",
      "transport": "sse",
      "required": true
    }
  ]
}
```

When `required: true`, the agent will not start a chat session until the MCP server
is reachable. When `required: false`, the agent starts normally and connects when
the server becomes available.

---

## Example Workflow

### Scenario: Agent Discovers and Uses GitHub MCP Server

```text
Step 1: User tells agent to connect
---------------------------------------------------------------------------
User: Connect to the GitHub MCP server at localhost:3001

main: [mcp_connect(url="http://localhost:3001/mcp", name="github")]

main: Connected to GitHub MCP server. Discovered 5 new tools:
      - github_list_repos: List repositories
      - github_list_prs: List pull requests
      - github_create_issue: Create an issue
      - github_read_file: Read file from repo
      - github_search_code: Search code in repos

      These tools are now available for this session. Would you like
      me to save this connection for future sessions?

Step 2: User asks agent to use the new tools
---------------------------------------------------------------------------
User: List my open PRs on brain-agent

main: [mcp_github_list_prs(repo="brain-agent", state="open")]

main: You have 3 open pull requests on brain-agent:

      #42 - Add MCP dynamic connections (draft)
            Created 2h ago, 3 files changed
      #41 - Fix QMD index path normalization
            Created 1d ago, ready for review
      #40 - Sidebar UI refactor
            Created 2d ago, 12 files changed

Step 3: User asks to persist the connection
---------------------------------------------------------------------------
User: Save this connection

main: [mcp_connect(url="http://localhost:3001/mcp", name="github",
       persist=true)]

main: GitHub MCP server connection saved to mcp.json. It will
      reconnect automatically on server restart.
```

### Scenario: Auto-Connect on Agent Startup

```text
Server starts:

  [MCP] Connecting to configured servers...
  [MCP] filesystem (stdio): connected, 5 tools
  [MCP] github (sse): connected, 5 tools
  [MCP] slack (sse): connection refused, will retry in 30s

  [MCP] Auto-connecting for agent "Research Lead"...
  [MCP] arxiv (sse, required): connected, 3 tools
  [MCP] github (sse): already connected, sharing session

  [MCP] All required connections established.
```

---

## Connection Lifecycle

```text
  mcp.json loaded          mcp_connect() called
  at startup               during conversation
       |                          |
       v                          v
  +-----------+            +-----------+
  | PERSISTED |            | TRANSIENT |
  | (in json) |            | (session) |
  +-----+-----+            +-----+-----+
        |                        |
        +--------+-------+-------+
                 |       |
                 v       v
           +-----+-------+-----+
           |    CONNECTING      |
           |  (handshake, tool  |
           |   discovery)       |
           +----------+---------+
                      |
              success | failure
             +--------+--------+
             |                 |
             v                 v
       +-----------+    +-----------+
       | CONNECTED |    |  FAILED   |
       | (active,  |    | (retry    |
       |  tools    |    |  in 30s)  |
       |  available)|    +-----------+
       +-----+-----+          |
             |                 | retry succeeds
             |                 +-------->--+
             |                             |
             v                             v
       mcp_disconnect()            CONNECTED
             |
             v
       +-----------+
       |DISCONNECTED|
       +-----------+
             |
             +-- if persisted: stays in mcp.json (reconnects on restart)
             +-- if transient: removed entirely
```

---

## Implementation Plan

### Phase 1: MCP Client Manager (Day 1-2)

1. Refactor `_mcp_manager` in `claude_cli.py` to support dynamic connections
2. Add `connect(url, transport, name)` method that performs handshake and tool
   discovery at runtime
3. Add `disconnect(name)` method that cleanly closes the MCP session
4. Add `status()` method returning connection health, tools, latency
5. Thread-safe: use `_qmd_session_lock` pattern for connection management

### Phase 2: New Tools (Day 2-3)

1. Implement `mcp_connect` tool with URL validation and timeout
2. Implement `mcp_disconnect` tool
3. Implement `mcp_status` tool
4. Register dynamically discovered tools in the tool registry so they appear
   as `mcp_<server>_<tool>` (same pattern as existing MCP tools)
5. Handle tool name collisions (prefix with server name)

### Phase 3: Persistence (Day 3)

1. Add `persist` parameter to `mcp_connect` that writes to `mcp.json`
2. Add `remove` parameter to `mcp_disconnect` that removes from `mcp.json`
3. Add `mcp_auto_connect` support in `agent.json`
4. Implement startup auto-connect with retry logic for unavailable servers

### Phase 4: Web UI (Day 4)

1. Add MCP connections panel to agent config modal (MCP tab)
2. Show real-time connection status (connected/disconnected/error)
3. Connect dialog with transport selection and URL input
4. Tool discovery list showing all tools from all connections
5. Save/remove buttons for persistence management

### Phase 5: Resilience (Day 5)

1. Auto-reconnect on connection drop (exponential backoff: 5s, 10s, 30s, 60s)
2. Health check ping every 30s for SSE connections
3. Graceful degradation: if an MCP server goes down, its tools become unavailable
   but the agent continues working with remaining tools
4. Connection timeout: 10s for initial connect, 30s for tool calls

---

## Security Considerations

### Trusting Remote MCP Servers

MCP servers can expose arbitrary tools. Connecting to an untrusted server is a
security risk because:

1. **Tool names can shadow built-in tools** -- a malicious server could register
   a tool named `read_file` that exfiltrates data. Mitigation: MCP tools are
   always prefixed with server name (`mcp_github_read_file`), never shadow
   built-in tools.

2. **Tool descriptions can mislead the agent** -- a tool described as "search files"
   could actually send data externally. Mitigation: tools.md can include warnings
   about untrusted MCP servers; user must explicitly connect.

3. **SSE connections to external hosts** -- data leaves the local network.
   Mitigation: log all external MCP connections; optionally restrict to
   localhost/LAN in config.

### Proposed Security Model

```text
config.json:
{
  "mcp": {
    "allow_remote": false,          // only localhost by default
    "trusted_hosts": [              // explicit allowlist for remote
      "mcp.example.com",
      "192.168.4.*"
    ],
    "max_connections": 10,          // prevent runaway connections
    "tool_call_timeout": 30000,     // 30s max per tool call
    "require_user_approval": true   // prompt before connecting
  }
}
```

When `require_user_approval` is true, the agent must ask the user before connecting
to a new MCP server. The user confirms in the chat, and the connection proceeds.

---

## Benefits

- **Dynamic capability expansion** -- agents gain new tools without server restart
- **Task-driven connections** -- connect to what you need, when you need it
- **Better visibility** -- real-time status of all MCP connections in Web UI
- **Session-scoped connections** -- temporary connections that clean up automatically
- **Composable agent capabilities** -- different agents connect to different servers
  based on their role (Researcher connects to arxiv, Coder connects to GitHub)
- **Future-proof** -- as the MCP ecosystem grows, agents can tap into it dynamically

## Trade-offs

- **Complexity** -- dynamic connections are harder to debug than static config.
  Mitigated by comprehensive status reporting and logging.
- **Security surface** -- more connection points means more attack surface.
  Mitigated by localhost-default, trusted hosts allowlist, user approval.
- **Resource usage** -- each MCP connection holds a session (stdio process or SSE
  stream). Mitigated by max_connections limit and auto-disconnect on idle.
- **Tool namespace pollution** -- many connections means many `mcp_*` tools in the
  agent's tool list. Mitigated by clear prefixing and the ability to disconnect
  when not needed.

## Dependencies

- Existing MCP infrastructure in `claude_cli.py` (stdio + SSE transports)
- No new libraries needed (uses existing `subprocess` for stdio, `urllib` for SSE)
- Web UI changes are self-contained in `web/index.html`

## Comparison with Current System

```text
                    Current (Static)         Proposed (Dynamic)
                    ----------------         ------------------
Configuration:      Edit mcp.json            mcp_connect() tool call
Activation:         Server restart           Immediate (runtime)
Visibility:         Config file only         Web UI panel + mcp_status
Lifecycle:          Always on                Connect/disconnect on demand
Persistence:        Always persisted         Optional (session or persisted)
Agent-specific:     Per mcp.json             Per agent + shared connections
Auto-discovery:     None                     Tool listing on connect
Error handling:     Startup failure          Retry with backoff, graceful degradation
```
