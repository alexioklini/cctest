---
name: Direct execution over scheduler for user-triggered actions
description: User-initiated actions must execute immediately, not be queued via the scheduler

type: feedback
related_to: [project_sdk_gap_plan, project_summary, feedback_cliproxy_quota]
---

Direct user actions should execute immediately in a background thread, not be routed through the scheduler indirection (set next_run → wait for poll loop).

**Why:** The scheduler loop polls every 30s, can be blocked by other tasks, or may not be running at all. This caused a "Refresh Now" click to silently do nothing — no execution was recorded. Only the overnight scheduled run actually wrote the file.

**How to apply:** When implementing any user-triggered action (button click, API call), execute it directly (e.g., spawn a background thread). Only use the scheduler for genuinely time-based/recurring tasks. The scheduler is for automation, not for proxying user intent.
