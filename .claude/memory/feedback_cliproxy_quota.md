---
name: CLIProxyAPI shares Claude usage quota
description: Brain Agent calls via CLIProxyAPI count against the same 5-hour Claude usage window as Claude Code — runaway tool loops can exhaust the quota
type: feedback
---

CLIProxyAPI-proxied Opus calls consume the same Claude OAuth quota (5-hour sliding window) as direct Claude Code usage. Brain Agent background activity can silently exhaust the window.

**Why:** On 2026-03-26, two accidental "Hi" sessions triggered a tool loop: 78 Opus calls, ~9.7M input tokens, ~$148 estimated cost — all in ~10 minutes. Each call sent 320K–336K tokens (full bloated context). The user's 5-hour window was completely consumed by this, with no obvious user-initiated activity to explain it.

**How to apply:** When debugging "quota exhausted" issues, check `costs.db` for recent Brain Agent activity first (`cost_log` table, `created_at` column). Watch for patterns of many calls with large `tokens_in` and tiny `tokens_out` — classic tool-loop signature. Consider adding rate limits or max-context guards to prevent runaway loops from draining the shared quota.
