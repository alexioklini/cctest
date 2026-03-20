# Feature Proposal: Google Agent-to-Agent (A2A) Protocol Support

**Status:** Proposed
**Priority:** Low (strategic)
**Effort:** Significant (7-10 days)
**Affects:** `server.py`, `claude_cli.py`, `web/index.html`, `config.json`

---

## Problem

Brain Agent's multi-agent system is entirely self-contained. Agents communicate via
in-process `delegate_task` calls within the same server process. This means:

1. **No external interoperability** -- Brain Agent cannot talk to other agent systems.
   A second Brain Agent instance on another machine cannot delegate tasks to this one.

2. **No ecosystem participation** -- as agent platforms proliferate (OpenFang,
   LangGraph, CrewAI, AutoGen), there is no standard way for them to discover and
   use Brain Agent's capabilities, or vice versa.

3. **No distributed workloads** -- all agents run in one process on one machine.
   Cannot distribute compute-intensive tasks to agents running elsewhere.

4. **No enterprise integration** -- organizations deploying multiple agent systems
   cannot orchestrate them without custom glue code.

Google's Agent-to-Agent (A2A) protocol addresses this by defining a standard for
agent discovery, capability advertisement, task delegation, and result delivery
over HTTP.

---

## What A2A Actually Specifies

The A2A protocol (published by Google, April 2025) defines:

### Agent Card (Discovery)

Every A2A-compliant agent publishes a JSON document at `/.well-known/agent.json`
describing its capabilities:

```json
{
  "name": "Brain Agent - Researcher",
  "description": "Deep research and analysis agent with web search, memory, and delegation",
  "url": "https://brain.alexklinsky.dev/a2a",
  "version": "1.0",
  "capabilities": {
    "streaming": true,
    "pushNotifications": false,
    "stateTransitionHistory": true
  },
  "skills": [
    {
      "id": "web-research",
      "name": "Web Research",
      "description": "Search the web, fetch pages, synthesize findings",
      "tags": ["research", "search", "analysis"]
    },
    {
      "id": "document-analysis",
      "name": "Document Analysis",
      "description": "Read and analyze documents, extract key information",
      "tags": ["analysis", "documents", "summarization"]
    }
  ],
  "authentication": {
    "schemes": ["bearer"]
  }
}
```

### Task Lifecycle

A2A tasks follow a defined state machine:

```text
                    send/sendSubscribe
                          |
                          v
                    +-----------+
                    | SUBMITTED |
                    +-----+-----+
                          |
                          v
                    +-----------+
              +---->|  WORKING  |<----+
              |     +-----+-----+     |
              |           |           |
              |     +-----+-----+    |
              |     |           |    |
              v     v           v    |
        +---------+ +----------+ +--+------+
        |COMPLETED| | FAILED   | |INPUT    |
        +---------+ +----------+ |REQUIRED |
                                 +---------+
```

- **SUBMITTED** -- task received, queued for processing
- **WORKING** -- agent is actively processing the task
- **COMPLETED** -- task finished, result available
- **FAILED** -- task failed with error details
- **INPUT_REQUIRED** -- agent needs clarification from the caller

### Core Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/.well-known/agent.json` | Agent card (discovery) |
| POST | `/a2a/tasks/send` | Send task, wait for completion |
| POST | `/a2a/tasks/sendSubscribe` | Send task, receive SSE updates |
| GET | `/a2a/tasks/{id}` | Get task status and result |
| POST | `/a2a/tasks/{id}/cancel` | Cancel a running task |

### Message Format

```json
{
  "jsonrpc": "2.0",
  "method": "tasks/send",
  "params": {
    "id": "task-uuid-here",
    "message": {
      "role": "user",
      "parts": [
        {"type": "text", "text": "Research the latest developments in A2A protocol"}
      ]
    }
  }
}
```

---

## Proposed Solution

Implement A2A protocol support in Brain Agent as both a **server** (expose agents
to external systems) and a **client** (call external A2A agents).

### Architecture Overview

```text
+---------------------+          A2A           +---------------------+
|   Brain Agent A     |<---------------------->|   Brain Agent B     |
|   (your machine)    |       HTTP/JSON        |   (remote machine)  |
|                     |                        |                     |
| main                |                        | main                |
| Researcher ---------|--- tasks/send -------->| Coder               |
| Reporter            |<-- result -------------|                     |
| crow                |                        |                     |
+---------------------+                        +---------------------+
         |                                              |
         |  A2A                                         |  A2A
         v                                              v
+---------------------+                        +---------------------+
|   OpenFang Agent    |                        |  Enterprise System  |
|   (third party)     |                        |  (corporate)        |
+---------------------+                        +---------------------+
```

### A2A Agent Card Published by Brain Agent

```text
GET https://brain.alexklinsky.dev/.well-known/agent.json

Response:
```

```json
{
  "name": "Brain Agent",
  "description": "Multi-agent AI platform with research, coding, reporting, and orchestration capabilities",
  "url": "https://brain.alexklinsky.dev/a2a",
  "version": "1.0",
  "capabilities": {
    "streaming": true,
    "pushNotifications": false,
    "stateTransitionHistory": true
  },
  "defaultInputModes": ["text"],
  "defaultOutputModes": ["text"],
  "skills": [
    {
      "id": "research",
      "name": "Research",
      "description": "Deep web research with Exa search, page fetching, and memory-backed synthesis",
      "tags": ["research", "search", "analysis", "web"],
      "examples": [
        "Research the current state of A2A protocol adoption",
        "Find and compare local LLM inference frameworks"
      ]
    },
    {
      "id": "coding",
      "name": "Coding",
      "description": "Read, write, and edit code files. Run shell commands. Search codebases.",
      "tags": ["code", "programming", "files", "shell"],
      "examples": [
        "Fix the bug in server.py line 42",
        "Add a new API endpoint for user profiles"
      ]
    },
    {
      "id": "reporting",
      "name": "Reporting",
      "description": "Generate reports, summaries, and documents from research and data",
      "tags": ["reports", "writing", "documents", "summary"],
      "examples": [
        "Write a report comparing MLX and llama.cpp performance",
        "Summarize this week's email threads"
      ]
    },
    {
      "id": "orchestration",
      "name": "Orchestration",
      "description": "Coordinate multiple agents, delegate tasks, manage workflows",
      "tags": ["orchestration", "delegation", "workflow", "management"],
      "examples": [
        "Research topic X, then have the reporter write it up",
        "Schedule a daily research task on AI news"
      ]
    }
  ],
  "authentication": {
    "schemes": ["bearer"],
    "credentials": "Contact admin for API key"
  }
}
```

### Sequence Diagram: External Agent Sends Task

```text
External Agent                Brain Agent Server              Internal Agent
     |                              |                              |
     |  POST /a2a/tasks/send        |                              |
     |  {task: "Research A2A"}      |                              |
     |----------------------------->|                              |
     |                              |  Match skill "research"      |
     |                              |  -> Researcher agent         |
     |                              |                              |
     |  200 {status: "submitted"}   |                              |
     |<-----------------------------|                              |
     |                              |  delegate_task(              |
     |                              |    agent="Researcher",       |
     |                              |    task="Research A2A")      |
     |                              |----------------------------->|
     |                              |                              |
     |                              |     [exa_search, web_fetch,  |
     |                              |      memory_store, ...]      |
     |                              |                              |
     |                              |  result: {research findings} |
     |                              |<-----------------------------|
     |                              |                              |
     |  GET /a2a/tasks/{id}         |                              |
     |----------------------------->|                              |
     |                              |                              |
     |  200 {status: "completed",   |                              |
     |       result: {findings}}    |                              |
     |<-----------------------------|                              |
     |                              |                              |
```

### Sequence Diagram: Brain Agent Calls External A2A Agent

```text
User                    Brain Agent (main)           External A2A Agent
  |                           |                              |
  | "Ask the design agent     |                              |
  |  to review this UI"       |                              |
  |-------------------------->|                              |
  |                           |  GET /.well-known/agent.json |
  |                           |----------------------------->|
  |                           |  {skills: ["ui-review",...]} |
  |                           |<-----------------------------|
  |                           |                              |
  |                           |  POST /a2a/tasks/send        |
  |                           |  {task: "Review this UI..."}  |
  |                           |----------------------------->|
  |                           |                              |
  |                           |  (external agent processes)  |
  |                           |                              |
  |                           |  200 {status: "completed",   |
  |                           |   result: "UI feedback..."}  |
  |                           |<-----------------------------|
  |                           |                              |
  | "The design agent says:   |                              |
  |  [feedback details]"      |                              |
  |<--------------------------|                              |
```

### Web UI: External Agents Panel

```text
+-- Settings: External Agents Tab ----------------------------------+
|                                                                    |
|  A2A Configuration                                                 |
|                                                                    |
|  Expose agents via A2A:  [x] Enabled                              |
|  A2A endpoint:           https://brain.alexklinsky.dev/a2a         |
|  Authentication:         Bearer token                              |
|                                                                    |
|  Exposed Agents:                                                   |
|  +--------------------------------------------------------------+ |
|  | [x] main          Orchestration, general tasks                | |
|  | [x] Researcher    Web research and analysis                   | |
|  | [ ] crow          (disabled - local only)                     | |
|  | [x] Reporter      Report generation                          | |
|  +--------------------------------------------------------------+ |
|                                                                    |
|  Trusted Peers:                                    [+ Add Peer]   |
|  +--------------------------------------------------------------+ |
|  | brain-lab.internal:8420          [Connected]                  | |
|  | Agents: Coder, Tester, DevOps   Last seen: 2m ago            | |
|  |                                  [Refresh] [Remove]           | |
|  +--------------------------------------------------------------+ |
|  | design-agent.example.com        [Connected]                  | |
|  | Agents: UI Reviewer, Copywriter  Last seen: 5m ago           | |
|  |                                  [Refresh] [Remove]           | |
|  +--------------------------------------------------------------+ |
|  | enterprise.corp.net:9000        [Unreachable]                | |
|  | Agents: unknown                  Last seen: 2d ago           | |
|  |                                  [Retry] [Remove]            | |
|  +--------------------------------------------------------------+ |
|                                                                    |
|  Discovered External Agents:                                       |
|  +--------------------------------------------------------------+ |
|  | Coder (brain-lab.internal)                                    | |
|  |   Skills: code-review, bug-fix, refactor                     | |
|  |   [Delegate Task]                                             | |
|  +--------------------------------------------------------------+ |
|  | UI Reviewer (design-agent.example.com)                        | |
|  |   Skills: ui-review, accessibility-audit, design-system       | |
|  |   [Delegate Task]                                             | |
|  +--------------------------------------------------------------+ |
|                                                                    |
+-------------------------------------------------------------------+
```

### Config: A2A Section

```json
{
  "a2a": {
    "enabled": true,
    "endpoint_prefix": "/a2a",
    "authentication": {
      "scheme": "bearer",
      "tokens": {
        "lab-instance": "secret-token-for-lab",
        "design-team": "secret-token-for-design"
      }
    },
    "exposed_agents": ["main", "Researcher", "Reporter"],
    "trusted_peers": [
      {
        "name": "Lab Instance",
        "url": "http://brain-lab.internal:8420",
        "token": "their-token-for-us"
      },
      {
        "name": "Design Team",
        "url": "https://design-agent.example.com",
        "token": "their-token-for-us"
      }
    ],
    "rate_limit": {
      "max_concurrent_tasks": 5,
      "max_tasks_per_minute": 20
    }
  }
}
```

---

## MCP vs A2A: Complementary, Not Competing

A common question: why do we need A2A when we already have MCP? They serve
fundamentally different purposes:

```text
+-------------------+---------------------------+---------------------------+
|                   | MCP                       | A2A                       |
+-------------------+---------------------------+---------------------------+
| Unit of work      | Tool call (function)      | Task (goal/objective)     |
| Granularity       | Fine (read_file, search)  | Coarse (research topic X) |
| Intelligence      | Server is dumb (no LLM)   | Agent is smart (has LLM)  |
| State             | Stateless tool calls      | Stateful task lifecycle   |
| Discovery         | Tool list on connect      | Agent card at well-known  |
| Communication     | Request/response          | Async with status updates |
| Audience          | Agent-to-tool             | Agent-to-agent            |
| Example           | "search for 'A2A'"        | "Research A2A adoption"   |
+-------------------+---------------------------+---------------------------+

MCP: "Here are functions you can call"     (tools for agents)
A2A: "Here are tasks I can accomplish"     (agents for agents)
```

Brain Agent uses both:
- **MCP** for connecting to tool servers (filesystem, GitHub, databases)
- **A2A** for connecting to other agent systems (other Brain Agents, external agents)

They are complementary layers. An A2A agent might internally use MCP tools to
accomplish the tasks it receives.

---

## Implementation Plan

### Phase 1: A2A Server -- Agent Card (Day 1)

1. Add `GET /.well-known/agent.json` endpoint to `server.py`
2. Auto-generate agent card from registered agents and their `soul.md`/`agent.json`
3. Map agent descriptions to A2A skills
4. Include authentication requirements
5. Respect `exposed_agents` config (not all agents need to be public)

### Phase 2: A2A Server -- Task Handling (Day 2-3)

1. Add `POST /a2a/tasks/send` endpoint
2. Parse incoming task, match skill to internal agent
3. Create internal `delegate_task` call to the matched agent
4. Track task state (submitted -> working -> completed/failed)
5. Add `GET /a2a/tasks/{id}` for status polling
6. Add `POST /a2a/tasks/{id}/cancel` for cancellation

### Phase 3: A2A Server -- Streaming (Day 3-4)

1. Add `POST /a2a/tasks/sendSubscribe` endpoint
2. Return SSE stream with task status updates
3. Map internal agent streaming to A2A SSE format
4. Include state transition history

### Phase 4: A2A Client -- Discovery (Day 4-5)

1. Add `a2a_discover(url)` function that fetches agent cards
2. Cache discovered agent capabilities
3. Add `trusted_peers` config section
4. Background refresh of peer agent cards (hourly)

### Phase 5: A2A Client -- Task Delegation (Day 5-7)

1. Add `delegate_external(peer, skill, task)` tool
2. Send tasks to external A2A agents
3. Poll or subscribe for results
4. Translate A2A responses back to internal format
5. Handle errors, timeouts, and retries

### Phase 6: Web UI (Day 7-8)

1. Add "External Agents" tab to settings
2. Show exposed agents with toggle
3. Trusted peers list with status
4. Discovered external agents with skills
5. "Delegate Task" button that opens a task dialog

### Phase 7: Security and Hardening (Day 8-10)

1. Bearer token authentication for incoming tasks
2. Rate limiting (concurrent tasks, tasks per minute)
3. Input validation and sanitization
4. Audit logging for all A2A interactions
5. TLS requirement for non-localhost peers
6. Task timeout enforcement

---

## Security Model

### Authentication

```text
Incoming A2A request:

  External Agent                          Brain Agent
       |                                       |
       |  POST /a2a/tasks/send                 |
       |  Authorization: Bearer <token>        |
       |-------------------------------------->|
       |                                       |
       |                           Validate token against
       |                           config.a2a.authentication.tokens
       |                                       |
       |                           Token valid? -> process task
       |                           Token invalid? -> 401 Unauthorized
       |                           No token? -> 401 Unauthorized
```

### Trust Levels

```text
Level 0: Unauthenticated
  - Can fetch /.well-known/agent.json (discovery only)
  - Cannot send tasks

Level 1: Authenticated
  - Can send tasks to exposed agents
  - Subject to rate limiting
  - All actions logged

Level 2: Trusted Peer
  - Can send tasks with elevated priority
  - Can request streaming updates
  - Can access task history
  - Bidirectional trust (we also call them)
```

### What Gets Exposed (and What Does Not)

```text
Exposed via A2A:
  - Agent names and descriptions (from agent.json)
  - Skill summaries (derived from soul.md)
  - Task results (text responses)

NOT exposed:
  - Internal tool calls or tool names
  - Memory contents
  - Config details (API keys, tokens, hosts)
  - Chat history
  - Other agents not in exposed_agents list
  - File system access
  - Internal delegation chains
```

---

## Workflow: Complete Example

### Setup

```text
1. Enable A2A in config.json:
   "a2a": { "enabled": true, "exposed_agents": ["Researcher"] }

2. Restart server:
   python3 brain.py restart

3. Verify agent card:
   curl https://brain.alexklinsky.dev/.well-known/agent.json
   -> Returns agent card with Researcher skills

4. Add trusted peer:
   Edit config.json, add peer URL and token
   Or use Web UI: Settings -> External Agents -> Add Peer
```

### Runtime

```text
5. External system discovers Brain Agent:
   GET /.well-known/agent.json
   -> Sees "research" skill

6. External system sends task:
   POST /a2a/tasks/send
   {"task": "Research current state of WebTransport adoption"}

7. Brain Agent routes to Researcher agent:
   delegate_task(agent="Researcher", task="Research WebTransport...")

8. Researcher uses its tools:
   exa_search("WebTransport adoption 2026")
   web_fetch("https://...")
   memory_store(...)

9. Result returned via A2A:
   {"status": "completed", "result": {"text": "WebTransport has seen..."}}

10. Meanwhile, Brain Agent discovers peer's agents:
    GET http://peer/.well-known/agent.json
    -> Sees "code-review" skill on peer's Coder agent

11. User asks Brain Agent to use external agent:
    "Ask the lab's Coder to review my PR"
    -> Brain Agent sends task to peer via A2A
    -> Receives code review feedback
    -> Presents to user
```

---

## Benefits

- **Ecosystem participation** -- Brain Agent becomes part of the growing A2A
  ecosystem instead of being an isolated system
- **Distributed architecture** -- run specialized agents on different machines
  and have them collaborate over A2A
- **Enterprise integration** -- organizations can connect Brain Agent to their
  existing agent infrastructure
- **Multi-instance coordination** -- run a Brain Agent at home and at work,
  have them collaborate on tasks
- **Standard protocol** -- A2A is backed by Google and adopted by multiple
  platforms. Not a custom integration.
- **Future-proof** -- as A2A adoption grows, Brain Agent is ready to interoperate

## Trade-offs

- **Significant effort** -- 7-10 days is the largest feature in this batch.
  The protocol has many details (streaming, state machine, error handling).
- **Security surface** -- exposing agents over HTTP requires careful auth,
  rate limiting, and input validation. A misconfiguration could allow
  unauthorized access to agent capabilities.
- **Latency** -- external tasks go over HTTP instead of in-process calls.
  Network latency + serialization overhead. Acceptable for coarse-grained
  tasks, not for fine-grained tool calls.
- **Dependency on protocol stability** -- A2A is relatively new (April 2025).
  The protocol may evolve, requiring updates.
- **Complexity** -- adds a new subsystem (A2A server + client) to an already
  feature-rich platform. Must be well-isolated to avoid impacting core
  stability.

## Dependencies

- Cloudflare tunnel (for public A2A endpoint)
- TLS (required for non-localhost peers)
- No new Python libraries (uses stdlib `http.server`, `json`, `urllib`)
- Agent registry and `delegate_task` infrastructure (already exists)

## Future Extensions

- **A2A marketplace** -- discover agents from a public registry
- **Multi-hop delegation** -- Agent A -> Agent B -> Agent C (via A2A at each hop)
- **Shared memory across instances** -- A2A + memory sync for distributed knowledge
- **Agent migration** -- move an agent from one Brain Agent instance to another
- **A2A over WebSocket** -- lower latency for high-frequency task exchange
- **Push notifications** -- A2A spec supports push; implement for long-running tasks
