---
name: Brain Agent Project Context
description: High-level project goal and non-obvious context — detailed architecture lives in CLAUDE.md
type: project
related_to: [project_sdk_gap_plan, infra_deployment, feedback_direct_execution, infra_inferencer]
relations:
  - extends: project_roadmap
  - depends_on: infra_deployment
  - overlaps: project_token_fixes
  - priority: high
---

Brain Agent — a complete multi-agent AI platform built from scratch, comparable to OpenClaw.

**Why:** User's goal is a fully autonomous multi-agent platform with local-first inference, multiple LLM providers, and complete tool coverage. Not just a chat wrapper — a persistent daemon with scheduler, memory, teams, delegation, and multi-frontend access (web, terminal, Telegram).

**How to apply:** CLAUDE.md is the authoritative source for architecture, tools (30+), patterns, and API surface. Don't duplicate that here. This memory captures the "why" and overall direction. The project is actively developed with rapid iteration — always check current code state rather than relying on summaries.
