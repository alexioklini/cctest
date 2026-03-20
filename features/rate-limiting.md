# Feature Proposal: Rate Limiting

**Status:** Proposed
**Effort:** ~4 days
**Priority:** High
**Author:** Brain Agent Team
**Date:** 2026-03-20

---

## Problem Statement

There is currently no mechanism to throttle agent API usage. This creates
several real problems:

- **Runaway costs**: An agent stuck in a tool loop can make hundreds of LLM calls,
  burning through API credits. With Claude Opus at ~$15/MTok output, a loop of
  50 calls could cost $5+ before anyone notices.
- **Scheduled task storms**: A misconfigured recurring task (e.g., every minute
  instead of every hour) fires repeatedly, each invocation making multiple LLM calls.
- **No per-agent budgets**: All agents share the same API keys with no individual caps.
  The Researcher agent doing a deep dive can starve other agents of budget.
- **No visibility**: No way to see which agent is consuming how much until the
  provider invoice arrives.

The existing `MAX_TOOL_ROUNDS` limit (per-conversation) prevents infinite tool loops
within a single chat turn, but does not limit total API calls across turns, sessions,
or scheduled tasks.

---

## Proposed Solution

Configurable rate limits per agent across multiple dimensions:

- **Requests per minute** — prevents burst abuse and tight loops
- **Tokens per hour** — controls sustained usage
- **Cost per day** — hard budget cap

Each limit has two thresholds:
- **Soft limit (warn)**: at 80% of limit, log warning and notify UI
- **Hard limit (block)**: at 100%, reject the request with a clear message

---

## Configuration

### Per-Agent (agent.json)

```json
{
  "description": "Research assistant",
  "display_name": "Researcher",
  "model": "claude-sonnet-4-6",
  "rate_limits": {
    "max_requests_per_minute": 10,
    "max_tokens_per_hour": 100000,
    "max_cost_per_day": 5.00
  }
}
```

### Global Defaults (config.json)

```json
{
  "server": {
    "port": 8420,
    "rate_limits": {
      "default_per_agent": {
        "max_requests_per_minute": 20,
        "max_tokens_per_hour": 500000,
        "max_cost_per_day": 25.00
      },
      "global": {
        "max_requests_per_minute": 60,
        "max_tokens_per_hour": 2000000,
        "max_cost_per_day": 100.00
      }
    }
  }
}
```

Priority: agent-specific overrides > global defaults.
Global limits apply across all agents combined.

### Limit Dimensions

```
+---------------------------+------------------------------------------+
| Dimension                 | What it tracks                           |
+---------------------------+------------------------------------------+
| requests_per_minute       | LLM API calls (chat completions)         |
| tokens_per_hour           | Total tokens (input + output)            |
| cost_per_day              | Estimated cost in USD                    |
+---------------------------+------------------------------------------+
```

Token counts come from the API response `usage` field. Cost is estimated
using per-model pricing tables configured in `config.json`.

---

## Sliding Window Algorithm

Use a **sliding window** counter (not fixed window) to avoid the "boundary burst"
problem where a user could make 2x requests at a window boundary.

```
Sliding Window (requests/minute):

  Time:    |----60s----|
  Events:  * * * *   * * * * * *
                      ^^^^^^^^^^ = 10 events in last 60s

  Implementation:
  - Store timestamps of each request in a deque
  - On new request: remove entries older than window
  - Count remaining: if >= limit, reject
  - O(1) amortized, O(n) worst case where n = window size
```

### Data Structure

```python
class RateLimiter:
    def __init__(self):
        self.requests = {}    # agent -> deque of timestamps
        self.tokens = {}      # agent -> deque of (timestamp, count)
        self.cost = {}        # agent -> deque of (timestamp, amount)
        self.lock = threading.Lock()

    def check(self, agent, dimension) -> (bool, float):
        """Returns (allowed, retry_after_seconds)"""

    def record(self, agent, tokens_used, estimated_cost):
        """Record a completed request"""
```

Stored in memory only (not SQLite). Resets on server restart, which is
acceptable since rate limits are about preventing runaway usage, not
long-term accounting.

---

## API Changes

### Rate Limited Response

When a request is blocked by rate limits, the server returns HTTP 429:

```
HTTP/1.1 429 Too Many Requests
Retry-After: 45
Content-Type: application/json

{
  "error": {
    "type": "rate_limit_exceeded",
    "message": "Agent 'Researcher' exceeded max_requests_per_minute (10). Retry in 45 seconds.",
    "agent": "Researcher",
    "dimension": "max_requests_per_minute",
    "limit": 10,
    "current": 10,
    "retry_after": 45
  }
}
```

### Rate Limit Status Endpoint

```
GET /v1/rate-limits
GET /v1/rate-limits?agent=Researcher

Response:
{
  "agents": {
    "Researcher": {
      "requests_per_minute": {"current": 8, "limit": 10, "remaining": 2},
      "tokens_per_hour":     {"current": 45000, "limit": 100000, "remaining": 55000},
      "cost_per_day":        {"current": 2.35, "limit": 5.00, "remaining": 2.65}
    },
    "Coder": {
      "requests_per_minute": {"current": 1, "limit": 20, "remaining": 19},
      "tokens_per_hour":     {"current": 12000, "limit": 500000, "remaining": 488000},
      "cost_per_day":        {"current": 0.80, "limit": 25.00, "remaining": 24.20}
    }
  },
  "global": {
    "requests_per_minute": {"current": 9, "limit": 60, "remaining": 51},
    "tokens_per_hour":     {"current": 57000, "limit": 2000000, "remaining": 1943000},
    "cost_per_day":        {"current": 3.15, "limit": 100.00, "remaining": 96.85}
  }
}
```

---

## Web UI Mockups

### Rate Limits in Agent Config Modal

```
+----------------------------------------------------------------------+
|  Edit Agent: Researcher                                              |
|                                                                      |
|  Description:  [Research assistant                              ]    |
|  Model:        [claude-sonnet-4-6                          v]        |
|  Avatar:       [magnifying glass icon]                               |
|                                                                      |
|  --- Rate Limits ---                                                 |
|                                                                      |
|  Requests/minute:    [    10 ] |========          | 8/10             |
|  Tokens/hour:        [100000 ] |====              | 45K/100K         |
|  Cost/day (USD):     [  5.00 ] |=====             | $2.35/$5.00      |
|                                                                      |
|  [ ] Use global defaults instead                                     |
|                                                                      |
|                                              [Cancel]  [Save]        |
+----------------------------------------------------------------------+
```

The progress bars show current usage vs limit in real-time. Colors:
- Green: < 50% of limit
- Yellow: 50-80% of limit
- Orange: 80-100% (soft limit zone, warning shown)
- Red: at limit (blocked)

### Rate Limit Warning in Chat

```
+----------------------------------------------------------------------+
| Researcher                                                           |
|                                                                      |
|  Let me search for more information on that topic...                 |
|                                                                      |
|  +----------------------------------------------------------------+ |
|  | [!] Rate limit warning: 8/10 requests this minute.             | |
|  |     Agent will be throttled if usage continues at this rate.    | |
|  +----------------------------------------------------------------+ |
|                                                                      |
+----------------------------------------------------------------------+
```

Warning appears as a yellow banner within the chat stream. Shown when
the agent crosses the 80% soft limit threshold.

### Rate Limit Block in Chat

```
+----------------------------------------------------------------------+
| Researcher                                                           |
|                                                                      |
|  +----------------------------------------------------------------+ |
|  | [X] Rate limited: max_requests_per_minute (10) exceeded.       | |
|  |     Resumes in 45 seconds.                     [====      ] 45s| |
|  +----------------------------------------------------------------+ |
|                                                                      |
+----------------------------------------------------------------------+
```

Block message appears as a red banner with a countdown timer. The progress
bar fills as the wait time elapses.

### Rate Limits Dashboard (Settings Tab)

```
+----------------------------------------------------------------------+
|  Settings > Rate Limits                                              |
|                                                                      |
|  Global Limits:                                                      |
|    Requests/min:  [   60 ]  Tokens/hr:  [2000000]  Cost/day: [100] |
|                                                                      |
|  Per-Agent Usage (live):                                             |
|  +--------------------+--------+-----------+---------+---------+    |
|  | Agent              | Req/m  | Tok/hr    | $/day   | Status  |    |
|  +--------------------+--------+-----------+---------+---------+    |
|  | main               |  2/20  |  12K/500K | $0.80   | OK      |    |
|  | Researcher         |  8/10  |  45K/100K | $2.35   | WARN    |    |
|  | Coder              |  1/20  |   8K/500K | $0.40   | OK      |    |
|  | Reporter           |  0/20  |   0K/500K | $0.00   | IDLE    |    |
|  +--------------------+--------+-----------+---------+---------+    |
|  | GLOBAL TOTAL       |  11/60 | 65K/2.0M  | $3.55   | OK      |    |
|  +--------------------+--------+-----------+---------+---------+    |
|                                                                      |
|  [Reset All Counters]                                                |
|                                                                      |
+----------------------------------------------------------------------+
```

Status column color-coded: green=OK, yellow=WARN (>80%), red=BLOCKED, gray=IDLE.

---

## TUI Mockup

### /limits Command

```
  > /limits

  Rate Limits:
  +------------------+----------+------------+-----------+----------+
  | Agent            | Req/min  | Tokens/hr  | Cost/day  | Status   |
  +------------------+----------+------------+-----------+----------+
  | main             |    2/20  |   12K/500K |  $0.80/25 | OK       |
  | Researcher       |    8/10  |  45K/100K  | $2.35/5   | WARN     |
  | Coder            |    1/20  |    8K/500K |  $0.40/25 | OK       |
  +------------------+----------+------------+-----------+----------+
  | GLOBAL           |   11/60  |  65K/2.0M  | $3.55/100 | OK       |
  +------------------+----------+------------+-----------+----------+

  > /limits Researcher

  Researcher Rate Limits:
    Requests/minute:  8/10  [========--] (WARN: approaching limit)
    Tokens/hour:      45K/100K [====------]
    Cost/day:         $2.35/$5.00 [=====-----]

    Last request: 12 seconds ago
    Next reset:   requests in 48s, tokens in 22min, cost at midnight
```

---

## Workflows

### 1. Normal Operation Within Limits

```
Admin configures Researcher: max 50 requests/hour

Researcher agent works normally:
  - Makes 3-5 requests per user question
  - Processes 8 questions in an hour = ~35 requests
  - Well within limit, no warnings
  - Usage visible in /limits dashboard
```

### 2. Agent Loop Caught by Rate Limit

```
Agent enters a tool loop:
  request 1 → tool call → request 2 → tool call → ...

  After 10 requests in 1 minute:
    Hard limit hit → 429 response
    Chat shows: "Rate limited. Resumes in 45 seconds."
    Loop is broken because the LLM call is rejected

  Without rate limits:
    Loop continues for MAX_TOOL_ROUNDS (could be 20+ requests)
    Much higher cost before MAX_TOOL_ROUNDS kicks in

  Rate limits complement MAX_TOOL_ROUNDS:
    - MAX_TOOL_ROUNDS: per-turn limit (prevents single-turn loops)
    - Rate limits: per-time limit (prevents multi-turn abuse)
```

### 3. Scheduled Task Storm

```
Misconfigured task: runs every 1 minute instead of every 1 hour

  Minute 1: task runs, 3 LLM calls
  Minute 2: task runs, 3 LLM calls
  ...
  Minute 7: rate limit hit (21 calls, limit 20/min)
    Task fails with rate_limit_exceeded
    Warning logged: "Agent 'Reporter' rate limited during scheduled task"
    Next task execution also blocked until window clears

  Admin sees warnings in scheduler history, fixes the config.
```

### 4. Cost Cap Protects Budget

```
Admin sets global cost cap: $100/day

  Morning: agents collectively spend $40 on various tasks
  Afternoon: large research task consumes $55
  Evening: new request → cost would be $96 → allowed (under $100)
  Next request: cost would be $102 → BLOCKED

  Message: "Daily cost limit ($100) reached. Resumes after midnight."
  Admin can raise limit or wait.
```

---

## Interaction with Existing Systems

### MAX_TOOL_ROUNDS

`MAX_TOOL_ROUNDS` limits tool calls within a single conversation turn.
Rate limits operate across turns and sessions. They are complementary:

```
MAX_TOOL_ROUNDS = 15     (per-turn, prevents single-turn loops)
requests_per_minute = 10  (per-time, prevents rapid multi-turn abuse)
```

A single turn with 15 tool rounds makes 1 LLM request per round = 15 requests.
If the rate limit is 10/min, the turn will be paused at round 10 for ~60 seconds
before continuing. This is the desired behavior — it slows down runaway turns.

### Tool Call Dedup

The dedup tracker catches identical consecutive tool calls (2 = hard abort).
Rate limits catch high-volume non-identical calls. No conflict.

### Scheduled Tasks

Scheduled tasks run as the agent. Rate limits apply identically. A scheduled
task that hits a rate limit fails with an error logged to scheduler history.
The scheduler does not retry rate-limited tasks (they will run at next
scheduled time).

### Delegated Tasks

When agent A delegates to agent B, the LLM calls count against agent B's
limits. The delegation itself (delegate_task tool call) does not count as
an LLM request for agent A.

---

## Implementation Details

### Rate Limiter Module (claude_cli.py)

```python
import collections
import threading
import time

class AgentRateLimiter:
    def __init__(self):
        self._lock = threading.Lock()
        self._requests = collections.defaultdict(collections.deque)
        self._tokens = collections.defaultdict(collections.deque)
        self._cost = collections.defaultdict(collections.deque)

    def check_and_record(self, agent: str, limits: dict) -> tuple[bool, str, float]:
        """
        Check if request is allowed. If allowed, record it.
        Returns: (allowed, reason, retry_after_seconds)
        """
        now = time.time()
        with self._lock:
            # Check requests/minute
            req_deque = self._requests[agent]
            self._prune(req_deque, now - 60)
            rpm = limits.get("max_requests_per_minute", float("inf"))
            if len(req_deque) >= rpm:
                oldest = req_deque[0]
                retry = 60 - (now - oldest)
                return False, "max_requests_per_minute", retry

            # Record
            req_deque.append(now)
            return True, "", 0

    def record_usage(self, agent: str, tokens: int, cost: float):
        """Record token and cost usage after response."""
        now = time.time()
        with self._lock:
            self._tokens[agent].append((now, tokens))
            self._cost[agent].append((now, cost))

    def get_status(self, agent: str, limits: dict) -> dict:
        """Current usage vs limits for display."""
        ...

    def _prune(self, dq, cutoff):
        while dq and (dq[0] if isinstance(dq[0], float) else dq[0][0]) < cutoff:
            dq.popleft()
```

### Integration Point (server.py)

Rate limit check happens in the `/v1/chat` endpoint before calling the LLM:

```python
@app.route('/v1/chat', methods=['POST'])
def chat():
    agent = request.json.get('agent', 'main')
    limits = get_agent_limits(agent)

    allowed, reason, retry_after = rate_limiter.check_and_record(agent, limits)
    if not allowed:
        return jsonify({
            "error": {"type": "rate_limit_exceeded", ...}
        }), 429, {"Retry-After": str(int(retry_after))}

    # ... proceed with LLM call
    # After response:
    rate_limiter.record_usage(agent, usage['total_tokens'], estimated_cost)
```

### Cost Estimation

```python
MODEL_PRICING = {
    "claude-opus-4-6":   {"input": 15.0, "output": 75.0},   # per MTok
    "claude-sonnet-4-6": {"input": 3.0,  "output": 15.0},
    "claude-haiku-3.5":  {"input": 0.80, "output": 4.0},
}

def estimate_cost(model, input_tokens, output_tokens):
    pricing = MODEL_PRICING.get(model, {"input": 3.0, "output": 15.0})
    return (input_tokens * pricing["input"] + output_tokens * pricing["output"]) / 1_000_000
```

---

## Benefits

- **Cost protection**: Hard caps prevent runaway spending
- **Loop breaking**: Rate limits catch agent loops that MAX_TOOL_ROUNDS misses
- **Per-agent budgets**: Different agents get different resource allocations
- **Visibility**: Dashboard shows real-time usage across all agents
- **Scheduled task safety**: Prevents misconfigured tasks from burning budget

## Risks and Mitigations

| Risk | Mitigation |
|------|-----------|
| Legitimate long tasks get throttled | Set appropriate limits; warn at 80% so admin can adjust |
| Rate limit resets on restart | Acceptable — rate limits prevent runaway, not accounting |
| Cost estimation inaccurate | Use actual token counts from API; pricing is approximate |
| Global limit blocks all agents | Per-agent limits should catch issues before global kicks in |

## Effort Breakdown

| Task | Days |
|------|------|
| RateLimiter class with sliding window | 0.5 |
| Integration in server.py /v1/chat | 0.5 |
| Config loading (agent.json + config.json) | 0.5 |
| API endpoint /v1/rate-limits | 0.5 |
| Web UI: config modal + dashboard + chat banners | 1 |
| TUI: /limits command | 0.5 |
| Scheduled task + delegation integration | 0.5 |
| **Total** | **4** |

---

## Open Questions

1. Should rate limits persist across server restarts? (SQLite storage adds
   complexity; in-memory is simpler and sufficient for runaway prevention.)
2. Should there be a "burst" allowance (e.g., 10/min sustained but allow
   a burst of 20 in any single minute)?
3. Should rate-limited scheduled tasks be automatically rescheduled, or just
   fail and wait for next scheduled time?
4. Should the cost tracker integrate with provider billing APIs for actual
   costs instead of estimates?

---

## Related

- MAX_TOOL_ROUNDS: `claude_cli.py`, per-turn tool call limit
- Tool call dedup: `claude_cli.py`, 2 identical calls = hard abort
- Scheduled tasks: `claude_cli.py`, configurable timeout (default 5 min)
- Agent activity: `/v1/agents/activity`, shows active tasks/chats
- Provider routing: `server.py`, auto-routes to correct provider per model
