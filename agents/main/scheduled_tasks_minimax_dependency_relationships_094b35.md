---
name: scheduled_tasks_minimax_dependency_relationships
description: "Infrastructure-level dependency relationship showing how the scheduler system runs the MiniMax-integrated agents reliably"
type: general
agent: main
last_recalled: 2026-04-05
related:
  - file: memory_summary_aab3f9.md
    type: co_recalled
  - file: memory_summary_-_user_identity_520e90.md
    type: co_recalled
---

The scheduled tasks infrastructure explicitly manages MiniMax provider as part of its daily 04:15 execution cycle (42s runtime). Successful executions create relationship chains between infrastructure (scheduler-dependency), user context (user-profile-and-workstyle), and the MiniMax provider configuration itself, ensuring operational continuity across the memory network.
operational continuity across the memory network.
