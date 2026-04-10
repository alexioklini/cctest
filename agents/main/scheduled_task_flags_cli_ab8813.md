---
name: scheduled_task_flags_cli
description: Critical SDK dependency constraint for streaming functionality
type: feedback
agent: main
related:
  - file: reporter_agent_role_dependencies_ba9393.md
    type: same_topic
  - file: memory_summary.md
    type: same_topic
  - file: tool-expansion-analysis_-_memory-architecture_665052.md
    type: same_topic
  - file: researcher_agent_role_9f388c.md
    type: same_topic
  - file: relationship_summary_researcher_agent_acea8d.md
    type: same_topic
  - file: openclaw-vs-claudecode-skills-comparison.md
    type: same_topic
  - file: claude-code-version-path_2b8576.md
    type: same_topic
last_recalled: 2026-04-08
---

SDK sidecar constraint: CLIProxyAPI running under Brain Agent SDK must never import claude_cli or any Claude Code CLI component, as this breaks anyio streaming with 'RuntimeError: cannot schedule new futures after interpreter shutdown'. This is a critical dependency constraint. Related to: feedback_sidecar_no_claude_cli
