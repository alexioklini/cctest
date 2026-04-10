---
name: "claude-code-version-path"
description: CLI tool version path resolution was dependent on the shell environment fix
type: project
agent: main
related:
  - file: reporter_agent_role_dependencies_ba9393.md
    type: same_topic
  - file: shell-env-fix-2025_59a0f4.md
    type: same_topic
  - file: memory_summary_-_memory-architecture_0fc9d1.md
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
  - file: tool-expansion-analysis-2026-03_7280e3.md
    type: same_topic
  - file: memory_summary_aab3f9.md
    type: same_topic
---

---
related:
  - name: "shell-env-fix-2025"
    relationship: "resolves"
    detail: "The PATH discovery problem for versioned CLI tools (like claude npm global) was resolved by implementing login shell wrapper in execute_command that sources shell profile files"
  - name: "Researcher_Tool_Chain"
    relationship: "enables"
    detail: "Proper version path resolution is foundational for Researcher's tool execution environment, enabling Glob/Grep/Read and external CLI tools like npm packages"
  - name: "Memory Summary"
    relationship: "context_detail"
    detail: "Version path discovery issue is referenced as a resolved infrastructure concern in the platform overview"
---
The 'claude-code-version-path' memory documents the investigation and resolution of tool discovery failures within the `execute_command` infrastructure. The root cause was identified as: the server process environment lacked PATH entries because `execute_command` did not source shell profile files (.zprofile/.zshrc). The resolution implemented a login shell wrapper via `_build_shell_command()`, changing execution from `command` to `/bin/zsh -l -c "command"`, configurable in tools_config.json. This fix was critical for discovering and executing versioned CLI tools like npm-installed `claude` globally.
