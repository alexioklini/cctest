# Feature Proposal: Audit Trail

**Status:** Proposed
**Effort:** ~5 days
**Priority:** High
**Author:** Brain Agent Team
**Date:** 2026-03-20

---

## Problem Statement

There is no immutable record of what agents have done. When an agent:

- Sends an email to a client
- Deletes a file from the filesystem
- Runs a destructive shell command
- Stores or overwrites memory
- Delegates a task to another agent

...there is no structured log to review later. Chat history captures LLM
conversation flow, but not a searchable, filterable record of concrete actions.

This creates problems for:

- **Debugging**: "Why did the build break?" requires scrolling through chat logs
- **Accountability**: "Which agent sent that email?" is hard to answer
- **Compliance**: No exportable record of agent actions for review
- **Trust**: Users cannot verify what agents did while unattended (scheduled tasks)
- **Incident response**: "What did the agent touch?" after something goes wrong

---

## Proposed Solution

An append-only audit log in SQLite capturing every significant agent action
with full context. Searchable, filterable, and exportable via API and UI.

Key principles:
- **Append-only**: No UPDATE or DELETE operations on the audit table
- **Structured**: Every entry has consistent fields for filtering
- **Lightweight**: Minimal overhead on normal operations
- **Complete**: All tool executions logged, not just "dangerous" ones

---

## Audit Log Entry Structure

```json
{
  "id": 1042,
  "timestamp": "2026-03-20T14:32:15.123Z",
  "agent": "Researcher",
  "session_id": "sess_abc123",
  "action_type": "command_execute",
  "tool_name": "execute_command",
  "args_summary": "npm test --coverage",
  "args_full": "{\"command\": \"npm test --coverage\", \"timeout\": 120}",
  "result_summary": "exit_code=0, 15 tests passed",
  "result_status": "success",
  "duration_ms": 34200,
  "tokens_used": null,
  "source": "chat",
  "user": "alexander"
}
```

### Field Descriptions

```
+------------------+---------------------------------------------------+
| Field            | Description                                       |
+------------------+---------------------------------------------------+
| id               | Auto-increment primary key                        |
| timestamp        | ISO 8601 UTC timestamp                            |
| agent            | Agent name (main, Researcher, Coder, etc.)        |
| session_id       | Chat session or scheduled task ID                 |
| action_type      | Categorized action (see Action Types below)       |
| tool_name        | Exact tool name as called                         |
| args_summary     | Human-readable summary of arguments (< 200 chars) |
| args_full        | Full JSON arguments (for detail view)             |
| result_summary   | Human-readable outcome (< 200 chars)              |
| result_status    | success | error | timeout | blocked               |
| duration_ms      | Execution time in milliseconds                    |
| tokens_used      | LLM tokens for this action (null if not LLM call) |
| source           | chat | scheduled | delegation                     |
| user             | User who initiated (or "system" for scheduled)    |
+------------------+---------------------------------------------------+
```

---

## Action Types

```
+---------------------+----------------------------+--------------------+
| Action Type         | Tool(s)                    | What is logged     |
+---------------------+----------------------------+--------------------+
| file_read           | read_file                  | Path               |
| file_write          | write_file, edit_file      | Path, size         |
| file_delete         | execute_command (rm)       | Path               |
| command_execute     | execute_command            | Command, exit code |
| email_send          | gmail_send                 | To, subject        |
| email_reply         | gmail_reply                | Thread, subject    |
| email_read          | gmail_read, gmail_inbox    | Count, search      |
| web_fetch           | web_fetch                  | URL                |
| web_search          | exa_search                 | Query              |
| memory_store        | memory_store               | Key/title          |
| memory_delete       | memory_delete              | Key/title          |
| memory_recall       | memory_recall              | Query              |
| memory_shared       | memory_shared              | Scope, query       |
| delegation          | delegate_task              | Target agent, task |
| task_cancel         | task_cancel                | Task ID            |
| schedule_create     | (scheduler)                | Schedule spec      |
| schedule_delete     | (scheduler)                | Task ID            |
| mcp_tool_call       | mcp_*                      | Server, tool, args |
| skill_use           | use_skill                  | Skill name         |
| code_execute        | run_code (if sandbox added)| Language, snippet  |
+---------------------+----------------------------+--------------------+
```

---

## SQLite Schema

```sql
CREATE TABLE audit_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp     TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now')),
    agent         TEXT    NOT NULL,
    session_id    TEXT,
    action_type   TEXT    NOT NULL,
    tool_name     TEXT    NOT NULL,
    args_summary  TEXT,
    args_full     TEXT,
    result_summary TEXT,
    result_status TEXT    NOT NULL DEFAULT 'success',
    duration_ms   INTEGER,
    tokens_used   INTEGER,
    source        TEXT    NOT NULL DEFAULT 'chat',
    user_name     TEXT
);

-- Indexes for common query patterns
CREATE INDEX idx_audit_timestamp ON audit_log(timestamp);
CREATE INDEX idx_audit_agent     ON audit_log(agent);
CREATE INDEX idx_audit_action    ON audit_log(action_type);
CREATE INDEX idx_audit_session   ON audit_log(session_id);
CREATE INDEX idx_audit_source    ON audit_log(source);

-- Composite index for filtered time-range queries
CREATE INDEX idx_audit_agent_time ON audit_log(agent, timestamp);
```

The table is stored in `agents/main/audit.db` (separate from chats.db to
avoid bloating the chat database and to allow independent rotation).

**Immutability enforcement**: The application code never issues UPDATE or DELETE
against `audit_log`. No API endpoint exposes mutation. The only write operation
is INSERT.

---

## API

### Query Audit Log

```
GET /v1/audit?agent=Researcher&type=command_execute&from=2026-03-20T00:00:00Z&to=2026-03-20T23:59:59Z&limit=50&offset=0

Response:
{
  "entries": [
    {
      "id": 1042,
      "timestamp": "2026-03-20T14:32:15.123Z",
      "agent": "Researcher",
      "session_id": "sess_abc123",
      "action_type": "command_execute",
      "tool_name": "execute_command",
      "args_summary": "npm test --coverage",
      "result_summary": "exit_code=0, 15 tests passed",
      "result_status": "success",
      "duration_ms": 34200,
      "source": "chat"
    }
  ],
  "total": 128,
  "limit": 50,
  "offset": 0
}
```

Query parameters:
- `agent` — filter by agent name
- `type` — filter by action_type
- `source` — filter by source (chat, scheduled, delegation)
- `status` — filter by result_status
- `from`, `to` — time range (ISO 8601)
- `search` — full-text search in args_summary and result_summary
- `limit` — max results (default 50, max 500)
- `offset` — pagination offset

### Get Single Entry Detail

```
GET /v1/audit/1042

Response:
{
  "id": 1042,
  "timestamp": "2026-03-20T14:32:15.123Z",
  "agent": "Researcher",
  "session_id": "sess_abc123",
  "action_type": "command_execute",
  "tool_name": "execute_command",
  "args_summary": "npm test --coverage",
  "args_full": "{\"command\": \"npm test --coverage\", \"timeout\": 120}",
  "result_summary": "exit_code=0, 15 tests passed",
  "result_status": "success",
  "duration_ms": 34200,
  "tokens_used": null,
  "source": "chat",
  "user_name": "alexander"
}
```

### Export

```
GET /v1/audit/export?format=csv&from=2026-02-20&to=2026-03-20
GET /v1/audit/export?format=json&agent=Researcher
```

Returns CSV or JSON file download with all matching entries.

---

## Web UI Mockups

### Audit Log Tab (Settings)

```
+----------------------------------------------------------------------+
|  Settings > Audit Log                                                |
|                                                                      |
|  Filters:                                                            |
|  Agent: [All agents     v]  Type: [All actions    v]  Status: [All v]|
|  From:  [2026-03-20      ]  To:   [2026-03-20      ]  [Search...   ]|
|                                                    [Export CSV] [JSON]|
|                                                                      |
|  +------------------------------------------------------------------+|
|  | Time     | Agent      | Action          | Summary        | Status||
|  +----------+------------+-----------------+----------------+-------+|
|  | 14:32:15 | Researcher | command_execute | npm test       |  OK   ||
|  | 14:31:02 | Researcher | file_write      | src/api.ts     |  OK   ||
|  | 14:30:45 | Researcher | memory_recall   | "test patterns"|  OK   ||
|  | 14:28:11 | main       | delegation      | -> Researcher  |  OK   ||
|  | 13:15:00 | Reporter   | email_send      | To: team@co... |  OK   ||
|  | 13:14:30 | Reporter   | gmail_inbox     | 5 new messages |  OK   ||
|  | 12:00:01 | Coder      | command_execute | docker build   | ERROR ||
|  | 12:00:00 | Coder      | file_read       | Dockerfile     |  OK   ||
|  +----------+------------+-----------------+----------------+-------+|
|                                                                      |
|  Showing 1-50 of 1,247 entries           [< Prev]  Page 1  [Next >] |
|                                                                      |
+----------------------------------------------------------------------+
```

Color coding:
- OK = green text
- ERROR = red text
- TIMEOUT = orange text
- BLOCKED = gray text

### Audit Entry Detail (Click to Expand)

```
+----------------------------------------------------------------------+
|  Audit Entry #1042                                                   |
|                                                                      |
|  Timestamp:    2026-03-20 14:32:15.123 UTC                           |
|  Agent:        Researcher                                            |
|  Session:      sess_abc123                                           |
|  Source:       chat (user: alexander)                                 |
|  Duration:     34.2 seconds                                          |
|                                                                      |
|  Action:       command_execute                                       |
|  Tool:         execute_command                                       |
|                                                                      |
|  Arguments:                                                          |
|  +----------------------------------------------------------------+ |
|  | {                                                               | |
|  |   "command": "npm test --coverage",                             | |
|  |   "timeout": 120                                                | |
|  | }                                                               | |
|  +----------------------------------------------------------------+ |
|                                                                      |
|  Result:  (success)                                                  |
|  +----------------------------------------------------------------+ |
|  | exit_code=0                                                     | |
|  | 15 tests passed, 0 failed                                      | |
|  | Coverage: 87.3% statements                                     | |
|  +----------------------------------------------------------------+ |
|                                                                      |
|                                                          [Close]     |
+----------------------------------------------------------------------+
```

### Email Send Audit Detail

```
+----------------------------------------------------------------------+
|  Audit Entry #1089                                                   |
|                                                                      |
|  Timestamp:    2026-03-20 13:15:00.456 UTC                           |
|  Agent:        Reporter                                              |
|  Source:       scheduled (task: daily_report)                         |
|                                                                      |
|  Action:       email_send                                            |
|  Tool:         gmail_send                                            |
|                                                                      |
|  Arguments:                                                          |
|  +----------------------------------------------------------------+ |
|  | {                                                               | |
|  |   "to": "team@company.com",                                    | |
|  |   "subject": "Daily Status Report - March 20",                 | |
|  |   "body": "[truncated, 2.1KB]"                                 | |
|  | }                                                               | |
|  +----------------------------------------------------------------+ |
|                                                                      |
|  Result:  (success)                                                  |
|  +----------------------------------------------------------------+ |
|  | Message sent. ID: msg_17ab3c...                                 | |
|  +----------------------------------------------------------------+ |
|                                                                      |
+----------------------------------------------------------------------+
```

---

## TUI Mockup

### /audit Command

```
  > /audit

  Recent Audit Log (last 20 entries):
  +----------+------------+-----------------+----------------------+--------+
  | Time     | Agent      | Action          | Summary              | Status |
  +----------+------------+-----------------+----------------------+--------+
  | 14:32:15 | Researcher | command_execute | npm test --coverage  | OK     |
  | 14:31:02 | Researcher | file_write      | src/api.ts (2.1KB)   | OK     |
  | 14:30:45 | Researcher | memory_recall   | "test patterns"      | OK     |
  | 14:28:11 | main       | delegation      | -> Researcher: "run  | OK     |
  |          |            |                 |   tests and fix..."  |        |
  | 13:15:00 | Reporter   | email_send      | To: team@company.com | OK     |
  +----------+------------+-----------------+----------------------+--------+

  > /audit --agent Researcher --type command_execute --last 1h

  Researcher commands (last hour):
  +----------+-----------------+-------------------------+--------+
  | Time     | Action          | Summary                 | Status |
  +----------+-----------------+-------------------------+--------+
  | 14:32:15 | command_execute | npm test --coverage     | OK     |
  | 14:25:03 | command_execute | git diff HEAD~1         | OK     |
  | 14:20:11 | command_execute | npm run build           | ERROR  |
  +----------+-----------------+-------------------------+--------+

  > /audit 1042

  (shows full detail for entry #1042)
```

---

## Workflows

### 1. Email Accountability

```
Question: "Who sent that email to the client?"

Admin: /audit --type email_send --last 7d

  Results:
  | 2026-03-18 09:00 | Reporter | email_send | To: client@acme.com, |
  |                  |          |            | Subject: "Weekly..." |

Admin clicks entry -> sees full args including recipient, subject,
and truncated body summary.

Answer: Reporter agent sent it during a scheduled daily_report task.
```

### 2. Debugging a Failure

```
Question: "Why did the deployment fail last night?"

Admin: /audit --agent Coder --type command_execute --from 2026-03-19T22:00

  Results:
  | 22:01:00 | command_execute | git pull origin main    | OK    |
  | 22:01:15 | command_execute | npm install             | OK    |
  | 22:01:45 | command_execute | npm run build           | ERROR |
  | 22:02:00 | command_execute | npm run build --verbose | ERROR |

Click entry for "npm run build" -> sees result_summary:
  "exit_code=1, Error: Cannot find module 'lodash'"

Root cause: missing dependency after git pull.
```

### 3. Compliance Export

```
Compliance officer needs all agent actions for March 2026.

GET /v1/audit/export?format=csv&from=2026-03-01&to=2026-03-31

Downloads: audit_2026-03-01_2026-03-31.csv (15,247 entries)

Columns: timestamp, agent, action_type, tool_name, args_summary,
         result_summary, result_status, duration_ms, source, user_name

CSV can be imported into spreadsheet or compliance tool for review.
```

### 4. Incident Response

```
Alert: "Production database has unexpected records"

Admin: /audit --type command_execute --search "psql\|sqlite3\|database"

  Results:
  | 2026-03-20 03:00 | Coder | command_execute | psql -c "INSERT..." | OK |

Click entry -> sees full command:
  psql production_db -c "INSERT INTO users (name) VALUES ('test')"

Source: scheduled task "nightly_data_sync"
Agent: Coder

Root cause found. Disable the scheduled task, fix the query.
```

---

## What NOT to Log

To avoid storage bloat and privacy issues:

| Excluded | Reason |
|----------|--------|
| Full LLM response text | Too large (KBs per response), stored in chat history |
| Full LLM prompts | Contains system prompt, very large |
| File contents (read) | Could be huge; just log path and size |
| Email body (full) | Privacy; log subject and recipient only |
| Memory content (full) | Could be large; log title and size |
| Internal tool routing | Implementation detail, not user-facing |
| SSE keepalive events | Noise |

The `args_full` field stores full tool arguments for detail view, but
`result_summary` is always truncated to 200 characters. For email_send,
the body is replaced with `[body: N bytes]` in `args_full`.

---

## Storage and Rotation

### Growth Estimation

```
Average entry size: ~500 bytes
Typical daily volume: 200-500 entries
Daily storage: ~250KB
Monthly storage: ~7.5MB
Yearly storage: ~90MB
```

This is modest. SQLite handles millions of rows efficiently.

### Rotation Policy

```json
{
  "audit": {
    "retention_days": 90,
    "archive_before_delete": true,
    "archive_path": "agents/main/audit-archive/"
  }
}
```

- **Default retention**: 90 days in active database
- **Archive**: Before deletion, export to compressed JSON file
- **Archive files**: `audit-archive/audit-2026-Q1.json.gz`
- **Rotation job**: Runs daily at midnight (part of scheduler)
- **Manual override**: `GET /v1/audit/export` to export any range at any time

---

## Privacy Considerations

- **Email content**: Only subject and recipients logged, not body
- **File content**: Only path and size logged, not content
- **Memory content**: Only title/key logged, not full text
- **Command output**: Only exit code and summary logged, not full stdout
- **User identity**: Logged as username for accountability
- **No PII scrubbing**: Admin is responsible for what agents process
- **Access control**: Audit endpoint should require admin authentication
  (currently no auth on local server; relevant when exposed via tunnel)

---

## Implementation Details

### Audit Logger (claude_cli.py)

```python
class AuditLogger:
    def __init__(self, db_path="agents/main/audit.db"):
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._lock = threading.Lock()
        self._create_table()

    def log(self, agent, action_type, tool_name,
            args_summary="", args_full="",
            result_summary="", result_status="success",
            duration_ms=None, tokens_used=None,
            session_id=None, source="chat", user=None):
        with self._lock:
            self._conn.execute(
                "INSERT INTO audit_log (...) VALUES (...)",
                (agent, action_type, tool_name, ...)
            )
            self._conn.commit()
```

### Integration Points

Every tool execution in `claude_cli.py` gets an `audit_logger.log()` call:

```python
def _handle_tool_call(self, tool_name, tool_args, ...):
    start = time.time()
    try:
        result = self._execute_tool(tool_name, tool_args)
        status = "success"
    except TimeoutError:
        status = "timeout"
    except Exception as e:
        status = "error"
    finally:
        elapsed = int((time.time() - start) * 1000)
        audit_logger.log(
            agent=self.agent_name,
            action_type=ACTION_TYPE_MAP.get(tool_name, "unknown"),
            tool_name=tool_name,
            args_summary=summarize_args(tool_name, tool_args),
            args_full=json.dumps(tool_args),
            result_summary=summarize_result(tool_name, result),
            result_status=status,
            duration_ms=elapsed,
            session_id=self.session_id,
            source=self.source,
        )
    return result
```

### Summarization Functions

```python
def summarize_args(tool_name, args):
    """Generate human-readable summary, max 200 chars."""
    if tool_name == "execute_command":
        return args.get("command", "")[:200]
    elif tool_name == "gmail_send":
        return f"To: {args.get('to', '?')}, Subject: {args.get('subject', '?')}"[:200]
    elif tool_name == "write_file":
        return f"{args.get('path', '?')} ({len(args.get('content', ''))} bytes)"[:200]
    elif tool_name == "delegate_task":
        return f"-> {args.get('agent', '?')}: {args.get('task', '')[:100]}"[:200]
    # ... etc
```

### Thread Safety

The audit logger uses a threading.Lock for writes. Since audit writes are
small INSERTs (< 1ms), lock contention is negligible even with concurrent
agent threads.

Uses the same `@_db_safe` pattern as ChatDB to prevent SQLite errors from
crashing the server. A failed audit write logs a warning but does not
block the tool execution.

---

## Benefits

- **Accountability**: Clear record of every agent action
- **Debugging**: Filter by agent + time to trace what happened
- **Compliance**: Exportable records for audits and reviews
- **Trust**: Users can verify scheduled task behavior
- **Incident response**: Quickly find what agent touched what

## Risks and Mitigations

| Risk | Mitigation |
|------|-----------|
| Storage growth | 90-day retention + archival; ~90MB/year is manageable |
| Performance overhead | Single INSERT per tool call, < 1ms, negligible |
| Sensitive data in args | Truncate email bodies, file contents; log metadata only |
| Audit DB corruption | Separate from chats.db; @_db_safe wrapping; WAL mode |
| False sense of security | Audit is for review, not prevention; pair with rate limits |

## Effort Breakdown

| Task | Days |
|------|------|
| SQLite schema + AuditLogger class | 0.5 |
| Integration in _handle_tool_call | 1 |
| Summarization functions per tool | 0.5 |
| API endpoints (query, detail, export) | 1 |
| Web UI: audit tab with filters + detail | 1.5 |
| TUI: /audit command | 0.5 |
| Rotation/archival job | 0.5 |
| **Total** | **5.5** |

---

## Open Questions

1. Should the audit log be readable by agents themselves? (e.g., an agent
   could check its own recent actions to avoid repeating work)
2. Should there be an alert/webhook when certain action types occur?
   (e.g., notify admin on every email_send)
3. Should audit entries link to specific chat messages for context?
4. Should the audit DB be encrypted at rest for sensitive deployments?

---

## Related

- Chat history: `chats.db`, stores full conversation but not structured action log
- Scheduler history: `schedule_history` tool, limited to scheduled tasks
- Tool call dedup: `claude_cli.py`, tracks recent calls (not persistent)
- `@_db_safe` decorator: `claude_cli.py`, prevents SQLite errors from crashing
- Agent activity: `/v1/agents/activity`, real-time but not historical
