---
name: scheduler_constraints
description: Scheduling and infrastructure constraints for reliable task execution
type: feedback
agent: main
last_recalled: 2026-04-05
---

Constraints on scheduled tasks: Tasks scheduled via CronCreate must avoid :00 and :30 minutes to prevent global API bursts. Recurring jobs auto-expire after 7 days. CLIProxyAPI SDK sidecar has streaming constraint: must never import claude_cli. Related to: scheduled_tasks, scheduled_task_flags_cli
