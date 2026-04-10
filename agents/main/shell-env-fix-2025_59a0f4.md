---
name: "shell-env-fix-2025"
description: Shell environment fix for PATH inheritance in execute_command
type: project
agent: main
related:
  - file: researcher_tool_chain_6c2e11.md
    type: same_topic
  - file: reporter_agent_role_dependencies_ba9393.md
    type: same_topic
  - file: memory_summary_-_memory-architecture_0fc9d1.md
    type: same_topic
  - file: tool-expansion-analysis-2026-03_-_reporter_agent_s_2cf7e8.md
    type: same_topic
  - file: memory_summary_references_openclaw_comparison_59db81.md
    type: same_topic
  - file: researcher_agent_role_9f388c.md
    type: same_topic
  - file: shell-env-fix-2025_-_claude-code-version-path_35b821.md
    type: same_topic
  - file: relationship_summary_researcher_agent_acea8d.md
    type: same_topic
  - file: coder-agent-scheduled-tasks_-_memory_health_report_ae6d8d.md
    type: same_topic
  - file: openclaw-vs-claudecode-skills-comparison.md
    type: same_topic
  - file: claude-code-version-path_b3cb28.md
    type: same_topic
  - file: memory_health_report_2026-03-25_a3253f.md
    type: same_topic
  - file: memory_summary_218799.md
    type: same_topic
  - file: memory_generation_pipeline_ac1bd2.md
    type: same_topic
  - file: tool-expansion-analysis-2026-03_7280e3.md
    type: same_topic
  - file: reporter_agent_role_747c13.md
    type: same_topic
  - file: _relationship_discovery_crow4b_-_memory_system_f15e25.md
    type: same_topic
  - file: crow4b_-_memory_summary_6f4362.md
    type: same_topic
last_recalled: 2026-04-05
  - file: reporter_agent_summary_07ba84.md
    type: same_topic
  - file: memory_summary_aab3f9.md
    type: same_topic
  - file: reporter_tool_chain_92a110.md
    type: same_topic
---

---
related:
  - name: "claude-code-version-path"
    relationship: "resolves"
    detail: "The login shell fix directly resolves the PATH discovery problem documented in claude-code-version-path"
  - name: "tool-expansion-analysis-2026-03"
    relationship: "enables"
    detail: "Shell environment fix is foundational infrastructure that tool expansion depends on — MCP stdio servers and CLI-based tools require proper PATH discovery"
  - name: "Memory Summary"
    relationship: "extends"
    detail: "Memory Summary references login shell fix as infrastructure that enables MCP tool expansion"
  - name: "Researcher_Tool_Chain"
    relationship: "depends_on"
    detail: "Researcher agent's tool execution requires proper PATH resolution via login shell wrapper"
  - name: "Reporter Agent Summary"
    relationship: "depends_on"
    detail: "Reporter relies on tool infrastructure (including login shell PATH resolution) for report generation"
---
Fixed execute_command to use login shell for proper PATH inheritance. Added `_build_shell_command()` helper to claude_cli.py that wraps commands as `/bin/zsh -l -c "command"` to source ~/.zprofile and ~/.zshrc. Configurable via tools_config.json: `login_shell` (default: true), `shell_path` (default: auto-detect from $SHELL, fallback /bin/zsh). Both streaming and non-streaming code paths updated. Root cause: server process environment lacked PATH entries from shell profiles, so npm-installed tools like `claude` weren't found.
