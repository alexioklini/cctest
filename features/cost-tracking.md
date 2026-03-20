# Feature Proposal: Cost Tracking & Budget Management

**Status**: Proposed
**Effort**: ~6 days
**Priority**: High
**Author**: Brain Agent Team
**Date**: 2026-03-20

---

## Problem Statement

Brain Agent routes LLM requests across multiple providers — CLIProxyAPI (OAuth,
free), oMLX (local, free), MiniMax (paid per token), and potentially direct
Anthropic/OpenAI APIs. Today there is zero visibility into:

- How many tokens each agent consumes per day/week/month
- Which conversations or scheduled tasks are the most expensive
- Whether a runaway agent loop is burning through API credits
- What the actual dollar cost is across providers with different pricing

Without cost tracking, administrators discover overspend only when they check
provider dashboards manually — by which time the budget may already be blown.
Free providers (oMLX, CLIProxyAPI) still consume compute resources and have
rate limits, so tracking token volume matters even when dollar cost is zero.

---

## Proposed Solution

### Overview

Instrument every LLM call in `claude_cli.py` to log token counts, model,
provider, agent, session, and computed cost. Store in SQLite. Expose via API,
Web UI dashboard, and TUI command. Add per-agent budget limits with alerts.

---

## Data Model

### Cost Log Record (per LLM call)

```
+------------------------------------------------------------------+
| cost_log                                                          |
+------------------------------------------------------------------+
| id            INTEGER PRIMARY KEY AUTOINCREMENT                   |
| trace_id      TEXT        -- links to trace if observability on   |
| agent         TEXT        -- agent name (main, Researcher, etc)   |
| session_id    TEXT        -- chat session ID                      |
| task_id       TEXT        -- scheduler task ID (nullable)         |
| provider      TEXT        -- provider name from config.json       |
| model         TEXT        -- model identifier                     |
| tokens_in     INTEGER     -- input/prompt tokens                  |
| tokens_out    INTEGER     -- output/completion tokens             |
| tokens_cache  INTEGER     -- cached input tokens (if reported)    |
| cost_usd      REAL        -- computed cost in USD                 |
| latency_ms    INTEGER     -- response time in milliseconds        |
| status        TEXT        -- success / error / timeout            |
| created_at    TEXT        -- ISO 8601 timestamp                   |
+------------------------------------------------------------------+
```

### Budget Config (per agent, in agent.json)

```json
{
  "description": "Research specialist",
  "display_name": "Researcher",
  "model": "claude-sonnet-4-6",
  "budget": {
    "max_cost_per_day": 5.00,
    "max_cost_per_month": 100.00,
    "max_tokens_per_hour": 500000,
    "alert_threshold": 0.85,
    "on_exceed": "warn"
  }
}
```

`on_exceed` values: `"warn"` (log + banner, continue), `"block"` (reject new
requests until reset), `"downgrade"` (switch to cheaper model automatically).

### Provider Cost Rates (in config.json)

```json
{
  "providers": [
    {
      "name": "MiniMax",
      "base_url": "https://api.minimax.io/anthropic/v1",
      "cost_rates": {
        "MiniMax-M2.5": {
          "input_cost_per_1k": 0.0015,
          "output_cost_per_1k": 0.007,
          "cache_cost_per_1k": 0.00075
        },
        "MiniMax-M2.7": {
          "input_cost_per_1k": 0.003,
          "output_cost_per_1k": 0.015,
          "cache_cost_per_1k": 0.0015
        }
      }
    },
    {
      "name": "oMLX",
      "base_url": "http://127.0.0.1:8000/v1",
      "cost_rates": {
        "_default": {
          "input_cost_per_1k": 0,
          "output_cost_per_1k": 0
        }
      }
    }
  ]
}
```

Models without explicit rates inherit `_default` for their provider.
If no `_default` and no match, cost is recorded as 0 with a `cost_estimated: false` flag.

---

## SQLite Schema

```sql
CREATE TABLE IF NOT EXISTS cost_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    trace_id      TEXT,
    agent         TEXT NOT NULL,
    session_id    TEXT,
    task_id       TEXT,
    provider      TEXT NOT NULL,
    model         TEXT NOT NULL,
    tokens_in     INTEGER NOT NULL DEFAULT 0,
    tokens_out    INTEGER NOT NULL DEFAULT 0,
    tokens_cache  INTEGER NOT NULL DEFAULT 0,
    cost_usd      REAL NOT NULL DEFAULT 0.0,
    latency_ms    INTEGER,
    status        TEXT NOT NULL DEFAULT 'success',
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_cost_agent     ON cost_log(agent);
CREATE INDEX idx_cost_session   ON cost_log(session_id);
CREATE INDEX idx_cost_created   ON cost_log(created_at);
CREATE INDEX idx_cost_model     ON cost_log(model);
CREATE INDEX idx_cost_task      ON cost_log(task_id);

-- Materialized daily summaries for fast dashboard queries
CREATE TABLE IF NOT EXISTS cost_daily (
    date          TEXT NOT NULL,          -- YYYY-MM-DD
    agent         TEXT NOT NULL,
    model         TEXT NOT NULL,
    total_calls   INTEGER NOT NULL DEFAULT 0,
    total_in      INTEGER NOT NULL DEFAULT 0,
    total_out     INTEGER NOT NULL DEFAULT 0,
    total_cost    REAL NOT NULL DEFAULT 0.0,
    PRIMARY KEY (date, agent, model)
);
```

The `cost_daily` table is updated by an aggregation step that runs every 5
minutes (piggybacking on the existing `_qmd_index_keeper` background loop)
or on demand when the dashboard is opened.

---

## Web UI: Cost Dashboard Tab

```
+-----------------------------------------------------------------------+
| Brain Agent                                    [main v] [Settings]    |
+-----------------------------------------------------------------------+
| Chats | Agents | Schedule | Skills | Memory | [* Cost] | Settings    |
+-----------------------------------------------------------------------+
|                                                                       |
|  Cost Dashboard                            Period: [This Week v]      |
|                                                                       |
|  +--- Daily Spend (USD) -------------------------------------------+ |
|  |                                                                   | |
|  |  $8 |                                          ##                 | |
|  |  $6 |                              ##          ##                 | |
|  |  $4 |          ##      ##          ##    ##    ##                 | |
|  |  $2 |    ##    ##      ##    ##    ##    ##    ##                 | |
|  |  $0 +----+-----+-------+-----+-----+-----+-----+--              | |
|  |       Mon   Tue    Wed   Thu   Fri   Sat   Sun                   | |
|  +-------------------------------------------------------------------+ |
|                                                                       |
|  Total This Week: $28.40          Avg/Day: $4.06                     |
|                                                                       |
|  +--- Spend by Agent --------+  +--- Spend by Model ---------------+ |
|  |                            |  |                                   | |
|  |  main        $12.50  44%  |  |  claude-opus-4    $15.20  53%    | |
|  |  Researcher   $9.80  34%  |  |  claude-sonnet-4   $8.10  29%   | |
|  |  Reporter     $4.10  14%  |  |  MiniMax-M2.7      $3.80  13%   | |
|  |  crow         $2.00   7%  |  |  oMLX (local)      $0.00   0%   | |
|  |                            |  |  CLIProxy (free)   $1.30   5%   | |
|  +----------------------------+  +-----------------------------------+ |
|                                                                       |
|  +--- Top Sessions by Cost ----------------------------------------+ |
|  | Session                    Agent       Calls  Tokens    Cost     | |
|  | "Debug memory leak"        main          34   125K    $3.20     | |
|  | "Write quarterly report"   Reporter      28    98K    $2.80     | |
|  | "Research competitors"     Researcher    45   210K    $2.50     | |
|  | [scheduled] daily-digest   main          12    45K    $1.10     | |
|  +------------------------------------------------------------------+ |
+-----------------------------------------------------------------------+
```

### Per-Agent Budget Config in Agent Settings

```
+-----------------------------------------------------------------------+
| Agent Settings: Researcher                                            |
+-----------------------------------------------------------------------+
|                                                                       |
|  Display Name:  [Researcher          ]                                |
|  Model:         [claude-sonnet-4-6   v]                               |
|  Avatar:        [magnifying glass    v]                                |
|                                                                       |
|  --- Budget & Limits ---                                              |
|                                                                       |
|  Max cost/day:       [$5.00        ]                                  |
|  Max cost/month:     [$100.00      ]                                  |
|  Max tokens/hour:    [500000       ]                                  |
|  Alert at:           [85  ]%                                          |
|  When exceeded:      [warn         v]  (warn / block / downgrade)     |
|                                                                       |
|  --- Current Usage ---                                                |
|  Today:   $3.40 / $5.00   [=======>          ] 68%                   |
|  Month:   $42.50 / $100   [=====>            ] 42%                   |
|  Tokens/hr: 125K / 500K   [==>               ] 25%                   |
|                                                                       |
|  [Save]  [Cancel]                                                     |
+-----------------------------------------------------------------------+
```

### Budget Alert Banner

When an agent crosses the `alert_threshold`, a banner appears in the Web UI
header and is included in Telegram/TUI output:

```
+-----------------------------------------------------------------------+
| [!] Budget Alert: Researcher at 85% of daily budget ($4.25 / $5.00)  |
|     7 calls remaining at current avg cost. Consider switching model.  |
+-----------------------------------------------------------------------+
```

When `on_exceed: "block"` and limit is reached:

```
+-----------------------------------------------------------------------+
| [X] Budget Exceeded: Researcher hit daily limit ($5.00 / $5.00)      |
|     Requests blocked until tomorrow. Override in Agent Settings.      |
+-----------------------------------------------------------------------+
```

### Cost Per Session in Session List

```
+-----------------------------------------------------------------------+
| Sessions                                          [New Chat]          |
+-----------------------------------------------------------------------+
| > Debug memory leak             main     34 msgs    $3.20   2h ago   |
|   Write quarterly report        Reporter 28 msgs    $2.80   5h ago   |
|   Research competitors          Researcher 45 msgs  $2.50   1d ago   |
|   Fix CSS alignment             main     8 msgs     $0.40   1d ago   |
|   Daily digest (scheduled)      main     12 msgs    $1.10   2d ago   |
+-----------------------------------------------------------------------+
```

---

## TUI: /costs Command

```
$ /costs

Cost Summary (This Week)
+-------------+--------+--------+---------+--------+
| Agent       | Calls  | Tok In | Tok Out | Cost   |
+-------------+--------+--------+---------+--------+
| main        |    142 |  450K  |   120K  | $12.50 |
| Researcher  |     98 |  310K  |    85K  |  $9.80 |
| Reporter    |     56 |  180K  |    52K  |  $4.10 |
| crow        |     34 |   80K  |    30K  |  $2.00 |
+-------------+--------+--------+---------+--------+
| TOTAL       |    330 | 1.02M  |  287K   | $28.40 |
+-------------+--------+--------+---------+--------+

Top Models: claude-opus-4 ($15.20), claude-sonnet-4 ($8.10), MiniMax-M2.7 ($3.80)

$ /costs Researcher --period today

Researcher - Today
  Calls: 23   Tokens: 78K in / 21K out   Cost: $3.40
  Budget: $3.40 / $5.00 (68%)
  Top session: "Research competitors" — 15 calls, $2.10
```

---

## Workflows

### 1. Admin Configures Cost Rates

1. Open Settings tab in Web UI (or edit `config.json` directly)
2. For each provider, set `cost_rates` per model
3. Set `_default` rate for providers where all models share pricing
4. For free providers (oMLX, CLIProxyAPI), set rates to 0
5. Server reloads config on save — new rates apply immediately

### 2. Agent Chat Generates Cost Data

1. User sends message in Web UI to Researcher agent
2. `claude_cli.py` sends LLM request to provider
3. Response includes `usage: {input_tokens, output_tokens}` in API response
4. Engine looks up cost rate for the model from `_models_config`
5. Computes: `cost = (tokens_in / 1000 * input_rate) + (tokens_out / 1000 * output_rate)`
6. Inserts row into `cost_log` table
7. If agent has budget config, checks thresholds
8. If threshold crossed, emits budget alert event via SSE

### 3. Admin Reviews Spending

1. Opens Cost tab in Web UI
2. Dashboard queries `cost_daily` for chart data (fast, pre-aggregated)
3. Sees Researcher spent $9.80 this week, mostly on Claude Opus
4. Drills into top sessions — finds "Research competitors" used 45 calls
5. Decides to set Researcher's default model to Sonnet to reduce costs

### 4. Budget Alert Fires

1. Researcher agent processes a scheduled task
2. After the LLM call, cost tracker checks: daily spend = $4.30, limit = $5.00
3. $4.30 / $5.00 = 86% > alert_threshold (85%)
4. Server logs warning, stores alert in memory
5. Next Web UI page load shows banner: "Researcher at 86% of daily budget"
6. If `on_exceed: "block"` and spend hits $5.00, next request returns error:
   `{"error": "budget_exceeded", "agent": "Researcher", "limit": 5.00}`
7. Admin can override in settings or wait for daily reset (midnight UTC)

---

## Handling Free Providers

| Provider     | Cost Model | Tracking Approach                         |
|-------------|------------|-------------------------------------------|
| oMLX        | Free local | Track tokens only. Cost = $0.00.          |
| CLIProxyAPI | Free OAuth | Track tokens only. Cost = $0.00.          |
| MiniMax     | Paid API   | Full cost tracking with configured rates. |
| Anthropic   | Paid API   | Full cost tracking with configured rates. |

Even for free providers, token tracking is valuable:
- oMLX has throughput limits (tokens/sec based on hardware)
- CLIProxyAPI has OAuth rate limits
- Token counts help estimate what it **would** cost on paid providers
- Budget `max_tokens_per_hour` applies regardless of cost

---

## Cost Estimation Accuracy

LLM APIs report actual token usage in response headers/body. Brain Agent
should use these actual values, not estimates.

| Source             | Accuracy | Notes                                    |
|-------------------|----------|------------------------------------------|
| Anthropic API     | Exact    | `usage.input_tokens`, `output_tokens`    |
| OpenAI-compatible | Exact    | `usage.prompt_tokens`, `completion_tokens`|
| oMLX              | Exact    | Reports usage in OpenAI format           |
| CLIProxyAPI       | Exact    | Proxies Anthropic response with usage    |
| MiniMax           | Exact    | Anthropic-compatible response format     |

Dollar cost accuracy depends on configured rates matching actual provider
pricing. Server logs a warning if a model has no configured rate.

---

## API Endpoints

```
GET  /v1/costs/summary?period=week&agent=Researcher
     Returns: {total_cost, total_calls, total_tokens_in, total_tokens_out,
               by_agent: [...], by_model: [...], by_day: [...]}

GET  /v1/costs/sessions?agent=Researcher&limit=20
     Returns: [{session_id, title, agent, calls, tokens, cost, last_active}]

GET  /v1/costs/alerts
     Returns: [{agent, type, message, threshold, current, limit, created_at}]

POST /v1/costs/export
     Body: {period: "month", format: "csv"}
     Returns: CSV download of cost_log rows
```

---

## Implementation Plan

| Day | Task                                                        |
|-----|-------------------------------------------------------------|
| 1   | SQLite schema, cost_log insert in claude_cli.py LLM calls  |
| 2   | Cost rate config in config.json, rate lookup, computation   |
| 3   | Budget config in agent.json, threshold checking, alerts     |
| 4   | API endpoints for summary, sessions, alerts, export         |
| 5   | Web UI: Cost Dashboard tab, charts, session cost display    |
| 6   | TUI /costs command, Telegram alerts, testing, edge cases    |

---

## Benefits

- **Visibility**: Know exactly what each agent costs per day, session, task
- **Control**: Set budgets to prevent runaway costs from agent loops or heavy tasks
- **Optimization**: Identify expensive patterns and switch to cheaper models
- **Accountability**: Per-agent and per-session attribution for team environments
- **Planning**: Historical data enables forecasting and capacity planning
- **Safety**: Block mode prevents accidental overspend on paid providers

---

## Open Questions

1. Should cost data live in `chats.db` or a separate `costs.db`?
   Recommendation: separate DB to avoid bloating chat history.

2. Should budget resets be midnight UTC or configurable timezone?
   Recommendation: UTC for simplicity, configurable later.

3. Should `downgrade` mode have a configurable fallback model chain?
   E.g., Opus -> Sonnet -> Haiku -> oMLX local.
   Recommendation: yes, as an ordered list in budget config.

4. Should cost tracking be opt-in or always-on?
   Recommendation: always-on for token counting, opt-in for dollar cost
   display (requires configuring rates).
