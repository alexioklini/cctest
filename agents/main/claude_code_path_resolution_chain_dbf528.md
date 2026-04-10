---
name: claude_code_path_resolution_chain
description: Dependency relationship between PATH discovery problem and its shell environment fix
type: depends_on
agent: main
related:
  - file: claude-code-version-path_2b8576.md
    type: same_topic
  - file: claude-code-version-path_b3cb28.md
    type: same_topic
  - file: openclaw-vs-claudecode-skills-comparison.md
    type: same_topic
  - file: openclaw-vs-claudecode-skills-comparison_d43e3a.md
    type: same_topic
  - file: researcher_agent_role_9f388c.md
    type: same_topic
  - file: memory_summary_aab3f9.md
    type: same_topic
  - file: skill_isolation_5f38ba.md
    type: same_topic
  - file: skill_manifests_9a5c5f.md
    type: same_topic
last_recalled: 2026-04-05
---

Claude Code binary location and PATH resolution forms a dependency chain:
- claude-code-version-path documents the original PATH discovery problem
- shell-env-fix-2025 provides the solution via login shell wrapper in execute_command
Both memories are required to understand the complete resolution path.
