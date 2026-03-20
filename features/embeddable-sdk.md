# Feature: Embeddable Python SDK

**Status**: Proposal
**Effort**: ~15 days
**Priority**: Medium
**Affects**: claude_cli.py (major refactor), new brain_agent/ package

---

## Problem

Brain Agent's core engine (tools, agents, memory, MCP) is tightly coupled to the
HTTP server in `server.py` and `claude_cli.py`. Developers who want to use agent
capabilities in their own Python scripts, CI/CD pipelines, or applications must:

1. Run the full server daemon
2. Make HTTP requests to localhost:8420
3. Parse SSE streams
4. Manage sessions externally

This is heavyweight for simple use cases like "run an agent against this codebase
and return a summary" or "auto-review this PR in CI."

---

## Proposed Solution

Extract the core engine from `claude_cli.py` into a standalone Python package
`brain-agent` that can be imported and used directly, with no server required.

### Architecture Comparison

**Current: Everything goes through the server**

```
┌──────────────┐     HTTP/SSE      ┌──────────────┐
│  Your App    │ ──────────────────>│  server.py   │
│  (external)  │ <──────────────────│  port 8420   │
└──────────────┘                    │  ┌────────┐  │
                                    │  │ engine │  │
                                    │  │(claude │  │
                                    │  │_cli.py)│  │
                                    │  └────────┘  │
                                    └──────────────┘
```

**Proposed: Direct in-process usage**

```
┌──────────────────────────────────┐
│  Your App                        │
│                                  │
│  from brain_agent import Agent   │
│                                  │
│  ┌────────────────────────────┐  │
│  │  brain_agent (SDK)         │  │
│  │  ┌──────┐ ┌──────┐        │  │
│  │  │tools │ │memory│        │  │
│  │  └──────┘ └──────┘        │  │
│  │  ┌──────┐ ┌──────┐        │  │
│  │  │ mcp  │ │agents│        │  │
│  │  └──────┘ └──────┘        │  │
│  └────────────────────────────┘  │
└──────────────────────────────────┘
   No server needed. Runs in-process.
```

**Both paths coexist: server uses the SDK internally**

```
┌────────────┐  HTTP   ┌────────────┐
│  Web UI    │────────>│ server.py  │──┐
│  Telegram  │────────>│            │  │
│  TUI       │────────>│            │  │  uses
└────────────┘         └────────────┘  │
                                       ▼
┌────────────┐              ┌──────────────────┐
│  Your App  │─────────────>│  brain_agent SDK │
│  CI script │  direct      │  (Python package)│
│  Notebook  │  import      └──────────────────┘
└────────────┘
```

---

### Package Structure

```
brain_agent/
├── __init__.py              # Public API: Agent, Tool, Config
├── agent.py                 # Agent class: chat(), delegate(), configure()
├── tools/
│   ├── __init__.py          # Tool registry, base class
│   ├── file_ops.py          # read_file, write_file, edit_file, list_directory
│   ├── shell.py             # execute_command
│   ├── search.py            # search_files, web_fetch, exa_search
│   ├── memory.py            # memory_store, memory_recall, memory_shared
│   ├── gmail.py             # gmail_inbox, gmail_read, gmail_send, etc.
│   └── delegation.py        # delegate_task, task_status, task_cancel
├── memory/
│   ├── __init__.py          # MemoryStore interface
│   ├── qmd.py               # QMD-backed memory (hybrid search)
│   └── file_scan.py         # Fallback: file-scan substring matching
├── mcp/
│   ├── __init__.py          # MCP manager
│   ├── client.py            # MCP client (stdio + SSE)
│   └── registry.py          # Dynamic tool registration from MCP servers
├── providers/
│   ├── __init__.py          # Provider resolver
│   ├── anthropic.py         # Native Anthropic API
│   └── openai.py            # OpenAI-compatible API
├── config.py                # Config loading, provider routing
├── context.py               # System prompt assembly (soul.md, tools.md)
├── streaming.py             # Streaming response handler
└── py.typed                 # PEP 561 marker
```

### pyproject.toml

```toml
[project]
name = "brain-agent"
version = "0.1.0"
description = "Embeddable multi-agent AI toolkit with tools, memory, and MCP"
requires-python = ">=3.11"
dependencies = [
    "httpx>=0.27",
    "pyyaml>=6.0",
]

[project.optional-dependencies]
qmd = ["qmd-client>=0.1"]
gmail = ["google-auth-oauthlib>=1.0", "google-api-python-client>=2.0"]
mcp = ["mcp>=1.0"]
all = ["brain-agent[qmd,gmail,mcp]"]

[project.scripts]
brain-agent = "brain_agent.cli:main"
```

---

## Usage Examples

### Basic: Chat with an Agent

```python
from brain_agent import Agent

# Create agent with default config
agent = Agent("main", config_path="./config.json")

# Simple synchronous chat
response = agent.chat("What files are in the current directory?")
print(response.text)
# Lists the files using the list_directory tool internally

# Streaming chat
for chunk in agent.stream("Analyze this codebase and summarize the architecture"):
    print(chunk.text, end="", flush=True)
    if chunk.tool_call:
        print(f"\n  [tool: {chunk.tool_call.name}]")
```

### CI/CD: Auto-Review Pull Requests

```python
#!/usr/bin/env python3
"""PR review bot -- runs in GitHub Actions."""

import subprocess
import sys
from brain_agent import Agent

agent = Agent("Reviewer", config={
    "provider": {
        "api_type": "anthropic",
        "api_key": os.environ["ANTHROPIC_API_KEY"],
        "model": "claude-sonnet-4-6",
    },
    "tools": ["read_file", "search_files", "execute_command"],
})

# Get the diff
diff = subprocess.check_output(
    ["git", "diff", "origin/main...HEAD"], text=True
)

response = agent.chat(f"""
Review this pull request diff. Focus on:
- Security issues
- Performance problems
- Logic errors
- Missing error handling

Diff:
{diff}
""")

# Post review comment
print(response.text)
if response.metadata.get("severity") == "critical":
    sys.exit(1)
```

### Embedding in a FastAPI App

```python
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from brain_agent import Agent

app = FastAPI()

# Shared agent instance (thread-safe)
support_agent = Agent("Support", config={
    "provider": {
        "api_type": "openai",
        "base_url": "http://127.0.0.1:8000/v1",
        "model": "Crow-4B-Opus-4.6-Distill",
    },
    "soul": "You are a helpful customer support agent for Acme Corp.",
    "tools": ["web_fetch", "memory_recall"],
})

@app.post("/api/support")
async def support_chat(message: str, session_id: str):
    async def generate():
        async for chunk in support_agent.astream(
            message, session_id=session_id
        ):
            yield f"data: {chunk.json()}\n\n"
    return StreamingResponse(generate(), media_type="text/event-stream")
```

### Jupyter Notebook

```python
# In a notebook cell:
from brain_agent import Agent

agent = Agent("Analyst", config={
    "provider": {"api_type": "anthropic", "model": "claude-sonnet-4-6"},
    "tools": ["read_file", "execute_command", "memory_store"],
})

# Analyze a dataset
result = agent.chat("""
Read data/sales_2025.csv and:
1. Compute monthly revenue trends
2. Find the top 5 products by revenue
3. Store the key findings in memory
""")

# The agent used execute_command to run pandas code,
# read_file to inspect the CSV, and memory_store to save findings
print(result.text)
```

```
# Next cell -- recall what the agent learned:
findings = agent.chat("What did you find about the sales data?")
print(findings.text)
# Agent uses memory_recall to retrieve stored findings
```

---

## API Design

### Agent Class

```
┌─────────────────────────────────────────────────────────────┐
│  class Agent                                                │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  __init__(name, config_path=None, config=None)              │
│      Create an agent. Config from file or dict.             │
│                                                             │
│  chat(message, session_id=None) -> Response                 │
│      Send message, get complete response. Blocks until      │
│      all tool calls resolve.                                │
│                                                             │
│  stream(message, session_id=None) -> Iterator[Chunk]        │
│      Send message, get streaming chunks. Yields text        │
│      tokens and tool call notifications.                    │
│                                                             │
│  astream(message, session_id=None) -> AsyncIterator[Chunk]  │
│      Async version of stream().                             │
│                                                             │
│  delegate(agent_name, task) -> TaskHandle                   │
│      Delegate task to another agent. Returns handle         │
│      for polling status.                                    │
│                                                             │
│  configure(tools=None, soul=None, model=None)               │
│      Reconfigure agent at runtime.                          │
│                                                             │
│  memory_store(key, content) -> None                         │
│  memory_recall(query) -> list[MemoryResult]                 │
│      Direct memory access (bypasses chat).                  │
│                                                             │
│  close()                                                    │
│      Cleanup: close MCP connections, flush memory.          │
│                                                             │
├─────────────────────────────────────────────────────────────┤
│  Properties                                                 │
│                                                             │
│  .name: str                                                 │
│  .model: str                                                │
│  .tools: list[str]      # enabled tool names                │
│  .sessions: dict        # active sessions                   │
│  .token_usage: Usage    # cumulative token counts           │
└─────────────────────────────────────────────────────────────┘
```

### Response / Chunk Objects

```
┌──────────────────────────────────┐
│  class Response                  │
├──────────────────────────────────┤
│  .text: str                      │
│  .tool_calls: list[ToolResult]   │
│  .token_usage: Usage             │
│  .session_id: str                │
│  .metadata: dict                 │
└──────────────────────────────────┘

┌──────────────────────────────────┐
│  class Chunk                     │
├──────────────────────────────────┤
│  .text: str | None               │
│  .tool_call: ToolResult | None   │
│  .done: bool                     │
│  .json() -> str                  │
└──────────────────────────────────┘

┌──────────────────────────────────┐
│  class ToolResult                │
├──────────────────────────────────┤
│  .name: str                      │
│  .input: dict                    │
│  .output: str                    │
│  .duration_ms: int               │
│  .error: str | None              │
└──────────────────────────────────┘
```

---

## Refactoring Plan

The core challenge is extracting the engine from `claude_cli.py` (currently ~5000+
lines of tightly coupled code) into a clean, importable package.

### Phase 1: Extract Tools (3 days)

Move each tool function into its own module under `brain_agent/tools/`. Replace
global state with a `ToolContext` object passed to each tool:

```
Before:  def _tool_read_file(path):  # uses global _thread_local
After:   def read_file(ctx: ToolContext, path: str) -> str:
```

### Phase 2: Extract Memory (2 days)

Move QMD integration and file-scan fallback into `brain_agent/memory/`. Define
a `MemoryStore` interface so implementations are swappable.

### Phase 3: Extract Providers (2 days)

Move provider routing, model resolution, and API clients into
`brain_agent/providers/`. Support both Anthropic and OpenAI-compatible APIs.

### Phase 4: Extract MCP (2 days)

Move MCP client and dynamic tool registration into `brain_agent/mcp/`. Handle
session lifecycle (connect, disconnect, reconnect).

### Phase 5: Agent Class (3 days)

Build the `Agent` class as the public API surface. Wire together tools, memory,
providers, and MCP. Implement `chat()`, `stream()`, `delegate()`.

### Phase 6: Server Integration (2 days)

Rewrite `server.py` to use the SDK internally instead of calling `claude_cli.py`
functions directly. This validates the API and ensures both paths work.

### Phase 7: Packaging + Docs (1 day)

Create `pyproject.toml`, write docstrings, add type hints, publish to PyPI.

---

## Benefits

- **Embeddable**: Use agent capabilities in any Python application without running
  a server
- **Testable**: Unit test tools and agent logic directly, no HTTP mocking needed
- **Scriptable**: Write quick scripts that leverage the full tool suite
- **Composable**: Build custom workflows by combining agents programmatically
- **CI/CD friendly**: Run agent tasks in ephemeral environments (GitHub Actions,
  Docker containers) with no daemon setup
- **Type-safe**: Full type hints and PEP 561 compliance for IDE support

## Trade-offs

- **Major refactor**: Extracting the engine is a significant effort. The current
  `claude_cli.py` uses globals, thread-locals, and implicit state extensively.
  This will take careful work to untangle.
- **Two entry points**: The server and the SDK must both work. Changes to tools
  must be tested via both paths. Risk of drift if not well-structured.
- **Config complexity**: SDK users need to provide provider config that the server
  normally loads from `config.json`. Need sensible defaults and clear error messages.
- **State management**: The server manages sessions in SQLite. The SDK needs its own
  lightweight session management (in-memory or optional SQLite).

## What Stays Server-Only

Not everything moves to the SDK. These features depend on the always-running daemon:

| Feature | Reason |
|---------|--------|
| Scheduler | Needs persistent daemon for timed/recurring tasks |
| Telegram bot | Long-running process with webhook/polling |
| Web UI | Served by the HTTP server |
| Chat history DB | Shared across frontends via server |
| SSE streaming API | HTTP-specific transport |
| Agent activity tracking | Server-wide state |
| QMD index keeper | Background maintenance thread |

The SDK provides the building blocks. The server adds orchestration, persistence,
and multi-frontend support on top.

## Effort Estimate

| Phase | Days |
|-------|------|
| Phase 1: Extract Tools | 3 |
| Phase 2: Extract Memory | 2 |
| Phase 3: Extract Providers | 2 |
| Phase 4: Extract MCP | 2 |
| Phase 5: Agent Class | 3 |
| Phase 6: Server Integration | 2 |
| Phase 7: Packaging + Docs | 1 |
| **Total** | **15** |

## Open Questions

1. Should the SDK support async-first (asyncio) or sync-first with async wrappers?
   The server is sync (Flask + threads). Modern Python leans async.
2. How to handle tools that need filesystem access in sandboxed environments
   (Docker, serverless)? Configurable tool permissions?
3. Should the SDK bundle a lightweight REPL for interactive use, or leave that
   to the TUI?
4. Package name: `brain-agent` on PyPI? Check availability.
5. Minimum Python version: 3.11 (for ExceptionGroup, tomllib) or 3.10?
