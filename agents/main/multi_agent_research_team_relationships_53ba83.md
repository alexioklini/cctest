---
name: multi_agent_research_team_relationships
description: Relationships between core agents in research team and their roles
type: project
agent: main
related:
  - file: crow_9b_relationships_part_1_b35ee3.md
    type: same_topic
  - file: infrastructure_provider_relationships_25e122.md
    type: same_topic
last_recalled: 2026-04-08
---

# Multi-Agent Research Team — Core Relationships

## Agent Roster & Roles
- **main_agent** — [agents/main/soul.md](agents/main/soul.md)
  - role: orchestrator/controller
  - provides: CLIProxyAPI integration; task scheduling; agent lifecycle
- **CROW_9B** — [agents/CROW_9B/soul.md](agents/CROW_9B/soul.md)
  - role: reasoning/coding/execution agent (Qwen 3.5 distilled from Opus 4.6 and heretic)
  - specializes: precise reasoning, writing, long-form dialogue, code analysis
- **Reporter** — [agents/Reporter/soul.md](agents/Reporter/soul.md)
  - role: presentation layer specialist
  - transforms raw summaries from calling agents into professional reports (HTML/PDF/DOCX)
  - outputs: richly formatted artifacts; integrates with gmail_send for email delivery
- **Researcher** — [agents/Researcher/soul.md](agents/Researcher/soul.md)
  - role: research and data gathering agent
  - queries: web search, document reading, memory recall for topic context

## Workflow Dependencies
- **scheduled_tasks_depend_on**: [main_agent] — tasks are enqueued via main agent's scheduler
- **reporting_pipeline**: [Researcher]->[Reporter] — Researcher gathers findings, Reporter compiles report
- **CROW_9B_signal_success_health_report**: [memory/health_report_chain] — CROW_9B triggers memory updates on successful runs; health report chain records outcomes over time

## Cross-Agent Dependencies
- **CROW_9B depends_on**: memory_system — uses structured .md files in memory index
- **Reporter depends_on**: CROW_9B and Researcher for content feed; uses memory/document tools
- **Researcher depends_on**: memory_recall, web tools, document readers

## Exceptions & Constraints
- **main_agent_quota_sharing**: CLIProxyAPI (main’s provider) shares Claude’s 5-hour quota — affects all downstream agents using CLIProxyAPI indirectly
- **direct_execution_recommended**: user-triggered tasks should bypass scheduler to avoid runaway loop exhaustion

