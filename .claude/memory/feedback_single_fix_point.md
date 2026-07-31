---
name: Fix at the single choke point, not per-caller
description: User corrected duplicate/layered fix pattern — always find the one place that handles all cases before writing code
type: feedback
originSessionId: 8b40445b-8569-4925-8a47-aca7f4c1ebe6
---
Before implementing a fix, ask: "what is the single place that sees ALL cases of this problem?" and fix only there.

**Why:** When fixing the stale-MemPalace-drawer problem, implemented per-handler purge logic in projects.py (covers API path only), then had to add a second mechanism in the daemon (covers manual edits too), then removed the first one. Result: two overlapping systems, extra code churn, wasted time.

**How to apply:** Before writing any fix, enumerate all the ways the problem can occur. If there's a single choke point that handles all of them (e.g. a daemon that runs every cycle and reads fresh state), fix it there only. Do not patch individual callers first and then discover the gaps.
