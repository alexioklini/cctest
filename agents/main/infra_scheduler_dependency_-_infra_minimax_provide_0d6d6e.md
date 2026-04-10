---
name: "infra_scheduler_dependency <-> infra_minimax_provider (execution_reliability)"
description: Scheduler execution stability depends on reliable MiniMax provider configuration and operation
type: project
agent: main
last_recalled: 2026-04-08
---

The scheduled task system runs daily at 04:15 and relies on the MiniMax provider configuration. Successful executions with 24-35s run times are recorded in execution history, indicating stable dependency between the scheduler infrastructure and the MiniMax provider availability.
