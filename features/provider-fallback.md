# Feature Proposal: Provider Fallback Chains with Retry Logic

**Status:** Proposed
**Priority:** High
**Effort:** Medium (3-5 days)
**Affects:** server.py, claude_cli.py, web/index.html, config.json

---

## Problem

Brain Agent supports multiple LLM providers (CLIProxyAPI, oMLX, MiniMax, direct Anthropic),
but when a request is sent to a provider and it fails — network timeout, rate limit, server
crash, OAuth token expiry — the request fails immediately and the user sees a cryptic error.

Current failure modes with no recovery:

- CLIProxyAPI OAuth token expires mid-session: all requests fail until manual refresh
- oMLX runs out of memory on large context: 500 error, no retry
- MiniMax rate-limits heavy usage: 429 error, user must manually switch model
- Network blip during long tool-use chains: entire multi-step workflow lost
- Scheduled tasks fail silently when provider is temporarily down

Users must manually switch providers/models in the Web UI or config when a provider
has issues, which breaks flow and is impossible for unattended scheduled tasks.

---

## Proposed Solution

Ordered fallback chains per model with configurable retry logic and exponential backoff.
When a request fails, the system automatically retries the same provider (for transient
errors) or falls back to the next provider in the chain (for persistent failures).

### Core Concepts

1. **Fallback chain**: An ordered list of provider+model pairs to try for a given model
2. **Retry policy**: Per-provider retry count and backoff configuration
3. **Health tracking**: Real-time provider health scores based on recent success/failure
4. **Transparent failover**: User sees which provider ultimately served the response

---

## Configuration

### Models Config with Fallback Chains

```text
┌─────────────────────────────────────────────────────────────────────┐
│  models_config.json (via GET/POST /v1/models/config)                │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  {                                                                  │
│    "models": {                                                      │
│      "claude-opus-4-6": {                                           │
│        "purpose": "coding",                                         │
│        "fallback_chain": [                                          │
│          {                                                          │
│            "provider": "cliproxy",                                  │
│            "model": "claude-opus-4-6",                              │
│            "retry": { "max_attempts": 2, "backoff_base": 1.0 }     │
│          },                                                         │
│          {                                                          │
│            "provider": "anthropic",                                 │
│            "model": "claude-opus-4-6",                              │
│            "retry": { "max_attempts": 1, "backoff_base": 2.0 }     │
│          },                                                         │
│          {                                                          │
│            "provider": "minimax",                                   │
│            "model": "MiniMax-M1-80k",                               │
│            "retry": { "max_attempts": 1, "backoff_base": 1.0 }     │
│          }                                                          │
│        ]                                                            │
│      },                                                             │
│      "claude-sonnet-4-6": {                                         │
│        "purpose": "general",                                        │
│        "fallback_chain": [                                          │
│          { "provider": "cliproxy", "model": "claude-sonnet-4-6" }, │
│          { "provider": "anthropic", "model": "claude-sonnet-4-6" } │
│        ]                                                            │
│      }                                                              │
│    },                                                               │
│    "default_retry": {                                               │
│      "max_attempts": 2,                                             │
│      "backoff_base": 1.0,                                           │
│      "backoff_max": 30.0,                                           │
│      "retryable_status_codes": [429, 500, 502, 503, 504]           │
│    }                                                                │
│  }                                                                  │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

### Retry Policy Details

| Parameter          | Default | Description                                     |
|--------------------|--------|-------------------------------------------------|
| `max_attempts`     | 2      | Retries per provider before moving to next       |
| `backoff_base`     | 1.0    | Initial delay in seconds (doubles each retry)    |
| `backoff_max`      | 30.0   | Maximum delay cap                                |
| `retryable_codes`  | 429,5xx| HTTP codes that trigger retry vs. immediate fail |
| `timeout`          | 60     | Per-request timeout in seconds                   |

Backoff formula: `min(backoff_base * 2^attempt, backoff_max) + jitter(0, 0.5s)`

---

## Web UI Mockups

### Models Tab: Fallback Chain Visualization

```text
┌──────────────────────────────────────────────────────────────────────┐
│  Settings > Models                                                    │
├──────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  claude-opus-4-6                                          [Edit]     │
│  Purpose: coding                                                     │
│                                                                      │
│  Fallback Chain:                                                     │
│  ┌──────────────┐     ┌──────────────┐     ┌──────────────┐         │
│  │  CLIProxyAPI  │────▶│  Anthropic   │────▶│   MiniMax    │         │
│  │  claude-opus  │     │  claude-opus │     │  M1-80k      │         │
│  │  retry: 2x    │     │  retry: 1x   │     │  retry: 1x   │         │
│  │  ● healthy    │     │  ● healthy   │     │  ● healthy   │         │
│  └──────────────┘     └──────────────┘     └──────────────┘         │
│                                                                      │
│  claude-sonnet-4-6                                        [Edit]     │
│  Purpose: general                                                    │
│                                                                      │
│  Fallback Chain:                                                     │
│  ┌──────────────┐     ┌──────────────┐                              │
│  │  CLIProxyAPI  │────▶│  Anthropic   │                              │
│  │  claude-son.  │     │  claude-son. │                              │
│  │  retry: 2x    │     │  retry: 2x   │                              │
│  │  ● healthy    │     │  ○ untested  │                              │
│  └──────────────┘     └──────────────┘                              │
│                                                                      │
│  Crow-4B-Opus-4.6-Distill                                 [Edit]     │
│  Purpose: quick                                                      │
│                                                                      │
│  Fallback Chain:                                                     │
│  ┌──────────────┐                                                    │
│  │    oMLX       │    (no fallback — local only)                     │
│  │  Crow-4B      │                                                   │
│  │  retry: 3x    │                                                   │
│  │  ● healthy    │                                                   │
│  └──────────────┘                                                    │
│                                                                      │
│                                              [+ Add Model]           │
└──────────────────────────────────────────────────────────────────────┘
```

### Chat: Real-Time Fallback Status

```text
┌──────────────────────────────────────────────────────────────────────┐
│  Chat with main (claude-opus-4-6)                                    │
├──────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  You: Analyze the codebase and suggest improvements                  │
│                                                                      │
│  ┌─────────────────────────────────────────────────────────────────┐ │
│  │  ⚠ CLIProxyAPI returned 503 (Service Unavailable)               │ │
│  │  Retrying in 1s... (attempt 1/2)                                │ │
│  └─────────────────────────────────────────────────────────────────┘ │
│                                                                      │
│  ┌─────────────────────────────────────────────────────────────────┐ │
│  │  ⚠ CLIProxyAPI failed after 2 attempts                          │ │
│  │  Falling back to Anthropic (claude-opus-4-6)...                 │ │
│  └─────────────────────────────────────────────────────────────────┘ │
│                                                                      │
│  main: Based on my analysis of the codebase...                      │
│                                                                      │
│  ┌─────────────────────────────────────────────────────────────────┐ │
│  │  ℹ Responded via Anthropic (primary CLIProxyAPI unavailable)    │ │
│  └─────────────────────────────────────────────────────────────────┘ │
│                                                                      │
└──────────────────────────────────────────────────────────────────────┘
```

### Provider Health Dashboard

```text
┌──────────────────────────────────────────────────────────────────────┐
│  Settings > Providers > Health                                       │
├──────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  Provider          Status    Uptime (24h)   Avg Latency   Last Error │
│  ─────────────────────────────────────────────────────────────────── │
│  CLIProxyAPI       ● UP      98.2%          340ms         503 @ 14:22│
│  oMLX              ● UP      100%           120ms         —          │
│  MiniMax           ● UP      99.5%          890ms         429 @ 11:05│
│  Anthropic         ○ IDLE    —              —             never used │
│                                                                      │
│  ──── Recent Events ────────────────────────────────────────────────│
│  14:23  CLIProxyAPI recovered (was down 47s)                         │
│  14:22  CLIProxyAPI → 503: upstream connection refused               │
│  14:22  Fallback: claude-opus → Anthropic (success)                  │
│  11:05  MiniMax → 429: rate limit exceeded                           │
│  11:05  Retry MiniMax after 2s backoff (success)                     │
│                                                                      │
│  ──── Health History (24h) ─────────────────────────────────────────│
│  CLIProxyAPI  ████████████████████░████████████████████████  98.2%   │
│  oMLX         ████████████████████████████████████████████  100%     │
│  MiniMax      ███████████████████████████████████░████████  99.5%   │
│               00:00        06:00        12:00        18:00  now      │
│                                                                      │
│  ░ = degraded/down    █ = healthy                                    │
└──────────────────────────────────────────────────────────────────────┘
```

---

## Sequence Diagram

```text
  User          Server         CLIProxyAPI     Anthropic       MiniMax
   │               │               │               │               │
   │  POST /chat   │               │               │               │
   │──────────────▶│               │               │               │
   │               │               │               │               │
   │               │  POST /messages               │               │
   │               │──────────────▶│               │               │
   │               │      503      │               │               │
   │               │◀──────────────│               │               │
   │               │               │               │               │
   │  SSE: retry   │  (backoff 1s) │               │               │
   │◀──────────────│               │               │               │
   │               │  POST /messages (retry 2/2)   │               │
   │               │──────────────▶│               │               │
   │               │    timeout     │               │               │
   │               │◀──────────────│               │               │
   │               │               │               │               │
   │  SSE: fallback│               │               │               │
   │◀──────────────│               │               │               │
   │               │  POST /messages               │               │
   │               │──────────────────────────────▶│               │
   │               │      429 rate limited          │               │
   │               │◀──────────────────────────────│               │
   │               │               │               │               │
   │  SSE: retry   │  (backoff 2s) │               │               │
   │◀──────────────│               │               │               │
   │               │               │  POST /messages               │
   │               │──────────────────────────────────────────────▶│
   │               │               │     200 OK + streaming        │
   │               │◀──────────────────────────────────────────────│
   │               │               │               │               │
   │  SSE: stream  │               │               │               │
   │◀──────────────│               │               │               │
   │               │               │               │               │
   │  SSE: meta    │  {"fallback": "minimax", "original": "cliproxy"}
   │◀──────────────│               │               │               │
   │               │               │               │               │
```

---

## Workflow Example

### Scenario: CLIProxyAPI Down During Chat

1. **User sends message** in Web UI, model is `claude-opus-4-6`
2. `server.py` resolves primary provider: CLIProxyAPI on port 8317
3. Request to CLIProxyAPI fails with `503 Service Unavailable`
4. SSE event sent to client: `{"type": "fallback_status", "msg": "CLIProxyAPI returned 503, retrying in 1s..."}`
5. After 1s backoff, retry CLIProxyAPI — timeout after 10s
6. SSE event: `{"type": "fallback_status", "msg": "CLIProxyAPI failed after 2 attempts. Falling back to Anthropic..."}`
7. Request to Anthropic direct API succeeds
8. Response streams normally to user
9. Final SSE metadata: `{"provider_used": "anthropic", "fallback_reason": "cliproxy unavailable"}`
10. Web UI shows subtle banner: "Responded via Anthropic (primary unavailable)"

### Scenario: Scheduled Task with Provider Down

1. Scheduler fires daily 09:00 task for Researcher agent (model: `claude-sonnet-4-6`)
2. CLIProxyAPI is down (overnight OAuth token expired)
3. Fallback chain tries Anthropic — succeeds
4. Task completes, history records: `provider_used: anthropic, fallback: true`
5. When CLIProxyAPI recovers, subsequent tasks use it again automatically

---

## Implementation Plan

### Phase 1: Core Retry Logic (server.py, claude_cli.py)

- Add `_send_with_fallback()` wrapper around LLM API calls
- Parse fallback chain from models config
- Implement exponential backoff with jitter
- Track retryable vs. non-retryable errors (429/5xx = retry, 401/403 = skip provider)
- Thread-safe health counters per provider

### Phase 2: SSE Fallback Events

- New SSE event types: `fallback_retry`, `fallback_switch`, `fallback_meta`
- Web UI renders retry/fallback status inline in chat
- TUI and Telegram adapters show fallback info in response

### Phase 3: Health Dashboard

- `/v1/providers/health` endpoint returning per-provider stats
- Rolling window (1h, 24h) of success/failure/latency
- Web UI health visualization in Settings > Providers
- Provider auto-disable after N consecutive failures (with auto-re-enable probe)

### Phase 4: Configuration UI

- Fallback chain editor in Settings > Models tab
- Drag-and-drop reorder of fallback providers
- Per-provider retry config with sensible defaults

---

## Error Classification

| HTTP Code | Category      | Action                              |
|-----------|--------------|--------------------------------------|
| 401       | Auth failure  | Skip provider, try next in chain     |
| 403       | Forbidden     | Skip provider, try next in chain     |
| 429       | Rate limit    | Retry same provider with backoff     |
| 500       | Server error  | Retry same provider (1x), then skip  |
| 502       | Bad gateway   | Retry same provider (1x), then skip  |
| 503       | Unavailable   | Skip provider immediately            |
| 504       | Timeout       | Skip provider immediately            |
| Conn. refused | Down      | Skip provider immediately            |
| Timeout   | Slow          | Skip provider immediately            |

---

## When ALL Providers Fail

If every provider in the fallback chain is exhausted:

1. Return clear error to user: "All providers failed. Tried: CLIProxyAPI (503), Anthropic (429), MiniMax (timeout)."
2. Include per-provider error details in the response
3. Suggest actions: "Check provider status in Settings > Providers > Health"
4. For scheduled tasks: record failure in history with full error chain, retry on next schedule
5. Do NOT silently drop the request

---

## Benefits

- **Zero-downtime experience**: Provider outages become invisible to users
- **Scheduled task reliability**: Tasks succeed even when primary provider is temporarily down
- **Cost optimization**: Use free CLIProxyAPI first, fall back to paid Anthropic only when needed
- **Observability**: Health dashboard shows provider reliability trends over time
- **Graceful degradation**: System remains functional even with partial provider failures

## Trade-offs

- **Increased latency on failure**: Retries + backoff add seconds to failed requests
- **Configuration complexity**: Fallback chains need thoughtful setup per model
- **Cost surprise**: Automatic fallback to paid providers could incur unexpected costs
  - Mitigation: optional `cost_limit` per provider, or `fallback_approval: true` to ask user first
- **Model behavior differences**: Falling back to a different model (e.g., Opus to MiniMax M1) may produce different quality responses
  - Mitigation: warn user when fallback uses a different model family

## Effort Estimate

| Component               | Effort  |
|------------------------|---------|
| Core retry logic        | 1 day   |
| SSE fallback events     | 0.5 day |
| Health tracking         | 1 day   |
| Web UI health dashboard | 1 day   |
| Web UI chain editor     | 1 day   |
| Testing + edge cases    | 0.5 day |
| **Total**              | **5 days** |
