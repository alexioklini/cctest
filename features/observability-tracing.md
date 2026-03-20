# Feature Proposal: Observability & Distributed Tracing

**Status**: Proposed
**Effort**: ~8 days
**Priority**: High
**Author**: Brain Agent Team
**Date**: 2026-03-20

---

## Problem Statement

Brain Agent has basic Python logging but no structured observability. When
something goes wrong — slow responses, failed tool calls, agent delegation
timeouts — debugging requires reading raw log files and guessing at causality.

Currently unanswerable questions:

- "Why did this response take 45 seconds?" (was it the LLM, a tool, or network?)
- "How many tool calls did that scheduled task make before timing out?"
- "What is the average latency per provider this week?"
- "Which tool fails most often?"
- "How deep did the agent delegation chain go?"
- "What happened between the user's message and the final response?"

The multi-agent architecture with delegations, tool calls, and scheduled tasks
creates complex execution paths that are invisible without tracing.

---

## Proposed Solution

### Overview

Add structured trace logging using a span-based model (inspired by
OpenTelemetry but lightweight and self-contained). Every LLM call, tool
execution, and agent delegation becomes a span within a trace. Store in
SQLite. Expose via API, Web UI dashboard, and TUI command. Support export
to JSON and OpenTelemetry format.

---

## Data Model

### Trace Hierarchy

```
Trace (one per user message or scheduled task)
  +-- Span: llm_call (user message -> first LLM response)
  |     +-- Span: tool_call (execute_command)
  |     +-- Span: tool_call (memory_recall)
  +-- Span: llm_call (tool results -> second LLM response)
  |     +-- Span: delegation (delegate_task to Researcher)
  |     |     +-- Span: llm_call (Researcher first turn)
  |     |     |     +-- Span: tool_call (web_fetch)
  |     |     |     +-- Span: tool_call (exa_search)
  |     |     +-- Span: llm_call (Researcher second turn)
  |     |           +-- Span: tool_call (memory_store)
  +-- Span: llm_call (delegation result -> final response)
```

### Span Record

```
+------------------------------------------------------------------+
| traces                                                            |
+------------------------------------------------------------------+
| id            TEXT PRIMARY KEY     -- span ID (uuid4)             |
| trace_id      TEXT NOT NULL        -- root trace ID               |
| parent_id     TEXT                 -- parent span ID (nullable)   |
| agent         TEXT NOT NULL        -- agent name                  |
| session_id    TEXT                 -- chat session                 |
| task_id       TEXT                 -- scheduled task (nullable)    |
| type          TEXT NOT NULL        -- see span types below        |
| name          TEXT NOT NULL        -- human-readable label        |
| status        TEXT NOT NULL        -- ok / error / timeout        |
| started_at    TEXT NOT NULL        -- ISO 8601 with ms            |
| ended_at      TEXT                 -- ISO 8601 with ms            |
| duration_ms   INTEGER             -- computed on end              |
| metadata      TEXT                 -- JSON blob (see below)       |
+------------------------------------------------------------------+
```

### Span Types

| Type         | Name Example                  | Metadata Fields                          |
|-------------|-------------------------------|------------------------------------------|
| `request`   | "user message"                | message_preview, model                   |
| `llm_call`  | "claude-sonnet-4 call"        | model, provider, tokens_in, tokens_out, stop_reason |
| `tool_call` | "execute_command"             | tool_name, args_preview, result_preview, exit_code |
| `delegation`| "delegate to Researcher"      | target_agent, task_preview, rounds       |
| `mcp_call`  | "qmd/query"                  | server, method, args_preview             |
| `memory_op` | "memory_recall"              | query, results_count, source (qmd/fallback) |
| `schedule`  | "daily-digest execution"      | task_name, cron, timeout                 |

### Metadata JSON Examples

```json
// llm_call metadata
{
  "model": "claude-sonnet-4-6",
  "provider": "CLIProxyAPI",
  "tokens_in": 4250,
  "tokens_out": 890,
  "stop_reason": "end_turn",
  "temperature": 1.0
}

// tool_call metadata
{
  "tool_name": "execute_command",
  "args_preview": "git status",
  "result_preview": "On branch main\nnothing to commit...",
  "exit_code": 0
}

// delegation metadata
{
  "target_agent": "Researcher",
  "task_preview": "Find recent papers on transformer architectures",
  "rounds": 4,
  "final_status": "completed"
}
```

---

## SQLite Schema

```sql
CREATE TABLE IF NOT EXISTS traces (
    id          TEXT PRIMARY KEY,
    trace_id    TEXT NOT NULL,
    parent_id   TEXT,
    agent       TEXT NOT NULL,
    session_id  TEXT,
    task_id     TEXT,
    type        TEXT NOT NULL,
    name        TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'ok',
    started_at  TEXT NOT NULL,
    ended_at    TEXT,
    duration_ms INTEGER,
    metadata    TEXT  -- JSON
);

CREATE INDEX idx_traces_trace    ON traces(trace_id);
CREATE INDEX idx_traces_parent   ON traces(parent_id);
CREATE INDEX idx_traces_agent    ON traces(agent);
CREATE INDEX idx_traces_type     ON traces(type);
CREATE INDEX idx_traces_started  ON traces(started_at);
CREATE INDEX idx_traces_session  ON traces(session_id);
CREATE INDEX idx_traces_status   ON traces(status);

-- Cleanup: auto-delete traces older than 30 days (configurable)
-- Triggered by background cleanup in _qmd_index_keeper loop
```

Storage is managed in a separate `traces.db` file in the main agent directory
to avoid bloating `chats.db`. At estimated 500 bytes per span and 20 spans per
conversation turn, a busy day (100 conversations) generates ~1MB. Monthly
retention at moderate usage: 10-30MB.

---

## Web UI: Traces Tab

### Trace List View

```
+-----------------------------------------------------------------------+
| Brain Agent                                    [main v] [Settings]    |
+-----------------------------------------------------------------------+
| Chats | Agents | Schedule | Skills | Memory | Cost | [* Traces]      |
+-----------------------------------------------------------------------+
|                                                                       |
|  Traces                  Agent: [All     v]  Status: [All v]          |
|                          Period: [Today   v]  Model:  [All v]         |
|                                                                       |
|  +--- Recent Traces ------------------------------------------+      |
|  | Time     Agent      Type       Name              Dur   Sts |      |
|  |----------------------------------------------------------- |      |
|  | 14:32  main       request   "Debug memory leak"  12.4s  ok |      |
|  |   |-- llm_call   claude-opus-4                    2.1s  ok |      |
|  |   |-- tool_call  execute_command (git log)        0.3s  ok |      |
|  |   |-- tool_call  read_file (server.py)            0.1s  ok |      |
|  |   '-- llm_call   claude-opus-4                    1.8s  ok |      |
|  |                                                             |      |
|  | 14:28  Researcher request   "Find AI papers"     34.2s  ok |      |
|  |   |-- llm_call   claude-sonnet-4                  1.2s  ok |      |
|  |   |-- tool_call  exa_search                       2.8s  ok |      |
|  |   |-- tool_call  web_fetch (3 URLs)               8.4s  ok |      |
|  |   |-- tool_call  memory_store                     0.2s  ok |      |
|  |   '-- llm_call   claude-sonnet-4                  0.9s  ok |      |
|  |                                                             |      |
|  | 14:15  main       schedule  "daily-digest"       45.0s  err|      |
|  |   |-- llm_call   claude-sonnet-4                  1.5s  ok |      |
|  |   |-- delegation Researcher                      30.0s  err|      |
|  |   '-- llm_call   claude-sonnet-4                  0.8s  ok |      |
|  +-------------------------------------------------------------+     |
+-----------------------------------------------------------------------+
```

### Trace Detail View (Waterfall Diagram)

Clicking on a trace expands it into a full waterfall view:

```
+-----------------------------------------------------------------------+
| Trace: "Debug memory leak"   Agent: main   Duration: 12.4s           |
| Session: abc123   Started: 2026-03-20 14:32:05                       |
+-----------------------------------------------------------------------+
|                                                                       |
|  Timeline (0s ---- 4s ---- 8s ---- 12s)                              |
|                                                                       |
|  request  [================================================] 12.4s   |
|                                                                       |
|  llm_call [==========]                                        2.1s   |
|           claude-opus-4  4.2K tok in  890 tok out                    |
|                                                                       |
|  tool     .          [==]                                     0.3s   |
|           execute_command: git log --oneline -20                     |
|           exit_code: 0                                               |
|                                                                       |
|  tool     .             [=]                                   0.1s   |
|           read_file: server.py (lines 1-50)                          |
|                                                                       |
|  llm_call .              [========]                           1.8s   |
|           claude-opus-4  6.1K tok in  1.2K tok out                   |
|                                                                       |
|  tool     .                        [=================]       5.2s   |
|           execute_command: python3 -c "import server; ..."           |
|           exit_code: 1  (ERROR)                                      |
|                                                                       |
|  llm_call .                                          [====]  1.4s   |
|           claude-opus-4  7.8K tok in  650 tok out                    |
|                                                                       |
|  Totals: 3 LLM calls (5.3s), 3 tool calls (5.6s), overhead 1.5s    |
|  Tokens: 18.1K in, 2.7K out   Est. cost: $0.52                      |
+-----------------------------------------------------------------------+
```

### Delegation Trace (Nested Waterfall)

```
+-----------------------------------------------------------------------+
| Trace: "Research competitors"   Agent: main   Duration: 34.2s        |
+-----------------------------------------------------------------------+
|                                                                       |
|  request  [================================================] 34.2s   |
|                                                                       |
|  llm_call [===]                                               1.2s   |
|           claude-opus-4: "I'll delegate this to Researcher"          |
|                                                                       |
|  delegate .   [========================================]     28.5s   |
|  Researcher                                                          |
|  |                                                                   |
|  |  llm_call  [====]                                          1.8s   |
|  |            claude-sonnet-4                                        |
|  |                                                                   |
|  |  tool      .    [========]                                 3.2s   |
|  |            exa_search: "competitor analysis AI agents 2026"       |
|  |                                                                   |
|  |  tool      .             [=============]                   5.1s   |
|  |            web_fetch: https://example.com/report                  |
|  |                                                                   |
|  |  llm_call  .                           [====]              1.5s   |
|  |            claude-sonnet-4                                        |
|  |                                                                   |
|  |  tool      .                                [==]           0.8s   |
|  |            memory_store: "competitor_analysis.md"                  |
|  |                                                                   |
|  |  llm_call  .                                   [===]       1.2s   |
|  |            claude-sonnet-4 (final summary)                        |
|  |                                                                   |
|                                                                       |
|  llm_call .                                          [====]   1.5s   |
|           claude-opus-4: formats final response to user              |
+-----------------------------------------------------------------------+
```

### Performance Dashboard

```
+-----------------------------------------------------------------------+
| Performance Dashboard                          Period: [This Week v]  |
+-----------------------------------------------------------------------+
|                                                                       |
|  +--- Avg Latency by Model ----+  +--- Tool Call Frequency ---------+|
|  |                              |  |                                  ||
|  | claude-opus-4     2.4s avg  |  | execute_command    234  ======== ||
|  | claude-sonnet-4   1.1s avg  |  | memory_recall      189  ======  ||
|  | MiniMax-M2.7      3.8s avg  |  | web_fetch          145  =====  ||
|  | oMLX Crow-4B      0.4s avg  |  | read_file          112  ====   ||
|  |                              |  | exa_search          67  ==    ||
|  +------------------------------+  | memory_store        45  ==    ||
|                                    | delegate_task       23  =     ||
|  +--- Error Rates by Provider --+  +--------------------------------+|
|  |                              |                                     |
|  | CLIProxyAPI    2.1%  =       |  +--- Requests / Hour ------------+|
|  | MiniMax       12.4%  ====    |  |  40|     **                     ||
|  | oMLX           0.5%          |  |  30|   **  **        **         ||
|  | Anthropic      1.2%  =       |  |  20| **      **    **  **       ||
|  |                              |  |  10|**          ****      **    ||
|  +------------------------------+  |   0+-+--+--+--+--+--+--+--+   ||
|                                    |     8  10 12 14 16 18 20 22    ||
|  +--- Tokens per Day ----------+  +--------------------------------+|
|  |                              |                                     |
|  |  Mon   245K  ========        |  Summary:                          |
|  |  Tue   312K  ==========      |    Total traces: 1,247             |
|  |  Wed   198K  =======         |    Avg spans/trace: 6.2            |
|  |  Thu   289K  =========       |    P50 latency: 4.2s              |
|  |  Fri   356K  ===========     |    P95 latency: 28.1s             |
|  |  Sat   145K  =====           |    Error rate: 3.8%               |
|  |  Sun   178K  ======          |    Top error: MiniMax timeout     |
|  +------------------------------+                                     |
+-----------------------------------------------------------------------+
```

---

## TUI: /traces Command

```
$ /traces

Recent Traces (last 24h)
+----------+-------------+------------------------------+--------+------+
| Time     | Agent       | Name                         | Dur    | Sts  |
+----------+-------------+------------------------------+--------+------+
| 14:32    | main        | Debug memory leak            | 12.4s  | ok   |
| 14:28    | Researcher  | Find AI papers               | 34.2s  | ok   |
| 14:15    | main        | daily-digest (scheduled)     | 45.0s  | err  |
| 13:50    | Reporter    | Write status update          |  8.7s  | ok   |
| 13:22    | main        | Fix CSS alignment            |  5.1s  | ok   |
+----------+-------------+------------------------------+--------+------+

$ /traces 14:15 --detail

Trace: daily-digest (scheduled)   Duration: 45.0s   Status: error
  [0.0s ] llm_call  claude-sonnet-4             1.5s  ok
  [1.5s ] delegation Researcher                30.0s  timeout
  [1.5s ]   llm_call  claude-sonnet-4           1.2s  ok
  [2.7s ]   tool_call  exa_search               4.5s  ok
  [7.2s ]   tool_call  web_fetch               25.0s  timeout  <-- ROOT CAUSE
  [31.5s] llm_call  claude-sonnet-4             0.8s  ok
           (returned partial results due to delegation timeout)

Error: web_fetch to https://slow-api.example.com timed out after 25s

$ /traces --stats

This Week:
  Total traces: 1,247   Avg duration: 8.3s   Error rate: 3.8%
  Slowest: "Research competitors" (34.2s)
  Most errors: MiniMax provider (12.4% error rate)
  Most called tool: execute_command (234 calls)
```

---

## Workflows

### 1. User Notices Slow Response

1. User sends message, response takes 45 seconds
2. Opens Traces tab, finds the trace at the top of the list
3. Clicks to expand waterfall view
4. Sees: `execute_command: python3 heavy_script.py` took 30 seconds
5. Realizes the script was doing unnecessary work
6. Optimizes the script, next interaction completes in 5 seconds

### 2. Admin Investigates Provider Reliability

1. Opens Performance Dashboard, selects "This Month"
2. Error Rates chart shows MiniMax at 12.4% (others under 3%)
3. Filters traces by provider: MiniMax, status: error
4. Sees pattern: most errors are timeouts during peak hours (14:00-16:00)
5. Adjusts model routing to prefer CLIProxyAPI during peak hours
6. Sets up scheduled task to check MiniMax health hourly

### 3. Scheduled Task Failure Investigation

1. Schedule tab shows "daily-digest" task failed
2. Clicks through to trace view
3. Sees delegation to Researcher which itself delegated to web_fetch
4. web_fetch timed out after 25 seconds fetching a slow external URL
5. Admin adds timeout configuration for that specific URL
6. Adds fallback logic: if web_fetch fails, use cached version

---

## Sampling for High Volume

At high usage, every span generates storage. Sampling strategies:

| Volume Level    | Spans/Day | Strategy                              |
|----------------|-----------|---------------------------------------|
| Low (<1000)     | <5K       | Log everything (no sampling)          |
| Medium (1K-10K) | 5K-50K    | Log all errors, sample 25% of success |
| High (>10K)     | >50K      | Log all errors, sample 10% of success |

Sampling is configured in `config.json`:

```json
{
  "tracing": {
    "enabled": true,
    "retention_days": 30,
    "sampling": {
      "success_rate": 1.0,
      "error_rate": 1.0
    },
    "export": {
      "format": "otlp_json",
      "endpoint": null
    }
  }
}
```

Errors and slow requests (>P95 duration) are always captured regardless
of sampling rate.

---

## Integration with External Tools

### JSON Export

```
GET /v1/traces/{trace_id}/export?format=json

{
  "trace_id": "abc-123",
  "spans": [
    {
      "span_id": "span-1",
      "parent_id": null,
      "type": "request",
      "name": "user message",
      "started_at": "2026-03-20T14:32:05.123Z",
      "ended_at": "2026-03-20T14:32:17.523Z",
      "duration_ms": 12400,
      "status": "ok",
      "metadata": {...}
    },
    ...
  ]
}
```

### OpenTelemetry Export

Traces can be exported in OTLP JSON format for ingestion by Jaeger, Grafana
Tempo, Honeycomb, or any OTLP-compatible backend. The export maps:

- `trace_id` -> OTel trace ID
- `span.id` -> OTel span ID
- `span.parent_id` -> OTel parent span ID
- `span.type` -> OTel span kind + attributes
- `span.metadata` -> OTel span attributes

Batch export runs on a configurable interval (default: disabled, manual export
only). When `export.endpoint` is set, spans are forwarded in real-time.

---

## API Endpoints

```
GET  /v1/traces?agent=main&status=error&period=today&limit=50
     Returns: [{trace_id, agent, name, duration_ms, status, span_count, started_at}]

GET  /v1/traces/{trace_id}
     Returns: {trace_id, spans: [...], total_duration, total_tokens, error_summary}

GET  /v1/traces/{trace_id}/export?format=json|otlp
     Returns: full trace in requested format

GET  /v1/traces/stats?period=week&agent=all
     Returns: {total_traces, avg_duration, p50, p95, p99, error_rate,
               by_model: [...], by_tool: [...], by_agent: [...]}

DELETE /v1/traces?before=2026-03-01
     Cleanup old traces
```

---

## Implementation in claude_cli.py

Tracing hooks are added as a context manager around existing operations:

```python
# Pseudocode for instrumentation
class Tracer:
    _thread_local = threading.local()

    @contextmanager
    def span(self, type, name, metadata=None):
        span = Span(
            id=uuid4(),
            trace_id=self._thread_local.trace_id,
            parent_id=self._thread_local.current_span_id,
            type=type, name=name,
            started_at=datetime.utcnow()
        )
        self._thread_local.current_span_id = span.id
        try:
            yield span
            span.status = "ok"
        except Exception as e:
            span.status = "error"
            span.metadata["error"] = str(e)
            raise
        finally:
            span.ended_at = datetime.utcnow()
            span.duration_ms = (span.ended_at - span.started_at).total_seconds() * 1000
            self._thread_local.current_span_id = span.parent_id
            self._store(span)
```

This is thread-safe via `threading.local()`, matching the existing pattern
used for `_thread_local.current_agent` and `_thread_local.mcp_manager`.

---

## Implementation Plan

| Day | Task                                                         |
|-----|--------------------------------------------------------------|
| 1   | SQLite schema, Tracer class, thread-local span management    |
| 2   | Instrument LLM calls in claude_cli.py with spans             |
| 3   | Instrument tool calls, delegation, MCP calls                 |
| 4   | API endpoints: list, detail, stats, export                   |
| 5   | Web UI: Traces tab, trace list with expandable spans         |
| 6   | Web UI: Waterfall detail view, performance dashboard         |
| 7   | TUI /traces command, sampling, retention cleanup             |
| 8   | OpenTelemetry export, testing, edge cases, documentation     |

---

## Benefits

- **Debug slow responses**: Waterfall view instantly shows which span is the bottleneck
- **Provider reliability**: Error rate tracking per provider enables informed routing
- **Agent optimization**: See how many rounds and tool calls each agent uses
- **Scheduled task forensics**: Understand why background tasks fail or timeout
- **Performance baselines**: P50/P95/P99 latency tracking over time
- **Capacity planning**: Tokens/day and requests/hour trends
- **Export flexibility**: JSON for custom analysis, OTLP for existing infra

---

## Storage Considerations

| Scenario          | Spans/Day | DB Size/Day | Monthly |
|-------------------|-----------|-------------|---------|
| Light use         | 500       | 250 KB      | 7.5 MB  |
| Moderate use      | 5,000     | 2.5 MB      | 75 MB   |
| Heavy use         | 50,000    | 25 MB       | 750 MB  |
| Heavy + sampling  | 10,000    | 5 MB        | 150 MB  |

Auto-cleanup runs daily, deleting traces older than `retention_days`.
The cleanup query uses the `idx_traces_started` index for efficiency.

---

## Open Questions

1. Should traces be stored in `chats.db`, `traces.db`, or in-memory only?
   Recommendation: separate `traces.db` in main agent directory.

2. Should the waterfall view be rendered server-side (HTML) or client-side (JS)?
   Recommendation: client-side with canvas/SVG for smooth interaction.

3. Should trace IDs be exposed in SSE events so the Web UI can link
   a chat response directly to its trace?
   Recommendation: yes, include `trace_id` in the SSE done event.

4. Should span metadata be size-limited to prevent bloat from large
   tool outputs?
   Recommendation: yes, truncate `result_preview` to 500 chars,
   `args_preview` to 200 chars.
