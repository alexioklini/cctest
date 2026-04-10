---
name: "\"shell-env-fix-2025\""
description: "\"Shell environment fix for execute_command - login shell wrapper for full PATH\""
type: project
agent: main
related:
  - file: memory-architecture.md
    type: same_topic
  - file: memory_health_report_2026-03-24_94a936.md
    type: same_topic
  - file: openclaw-vs-claudecode-skills-comparison_92bcff.md
    type: same_topic
  - file: reporter_agent_summary_07ba84.md
    type: same_topic
  - file: mbaim1-vpn-connection_248938.md
    type: same_topic
  - file: memory_summary.md
    type: same_topic
  - file: memory-architecture_fa7efd.md
    type: same_topic
  - file: openclaw-vs-claudecode-skills-comparison.md
    type: same_topic
  - file: shell-env-fix-2025_59a0f4.md
    type: same_topic
  - file: dev-workflow-feedback_cffe4d.md
    type: same_topic
  - file: tool-expansion-analysis-2026-03_b28222.md
    type: same_topic
  - file: dev-workflow-feedback_af2401.md
    type: same_topic
last_recalled: 2026-04-05
  - file: memory_summary_aab3f9.md
    type: co_recalled
  - file: tool-expansion-analysis-2026-03_7280e3.md
    type: co_recalled
  - file: memory_summary_references_openclaw_comparison_59db81.md
    type: same_topic
  - file: memory_summary_references_memory_architecture_855b1f.md
    type: same_topic
  - file: memory_summary_references_reporter_agent_9d1c1f.md
    type: same_topic
  - file: memory_summary_-_memory-architecture_0fc9d1.md
    type: same_topic
  - file: dev-workflow-feedback_-_memory_summary_c9a09b.md
    type: same_topic
  - file: memory_health_report_2026-03-26_3f4592.md
    type: co_recalled
  - file: memory_health_reports_chain_0dfb71.md
    type: co_recalled
  - file: memory_health_report_2026-03-25_a3253f.md
    type: co_recalled
  - file: memory_generation_pipeline_ac1bd2.md
    type: co_recalled
  - file: openclaw-vs-claudecode-skills-comparison-extended-_82c838.md
    type: co_recalled
  - file: memory_system_e5dba6.md
    type: co_recalled
  - file: memory-architecture_-_tool-expansion-analysis-2026_2bb117.md
    type: co_recalled
  - file: shell-env-fix-2025_-_claude-code-version-path_35b821.md
    type: co_recalled
  - file: scheduled_tasks_87aabf.md
    type: co_recalled
  - file: memory_summary_218799.md
    type: co_recalled
  - file: reporter_agent_role_747c13.md
    type: co_recalled
---

---
related:
  - name: "claude-code-version-path"
    relationship: "depends_on"
    detail: "The login shell fix directly resolves the PATH discovery problem documented in claude-code-version-path"
  - name: "Memory Summary"
    relationship: "extends"
    detail: "Summary references login shell fix; this memory is the authoritative technical detail"
  - name: "tool-expansion-analysis-2026-03"
    relationship: "references"
    detail: "Shell env fix is foundational infrastructure that tool expansion depends on — MCP stdio servers and CLI-based tools require proper PATH"
---
Fixed execute_command to use login shell for proper PATH inheritance. Added `_build_shell_command()` helper to claude_cli.py that wraps commands as `/bin/zsh -l -c "command"` to source ~/.zprofile and ~/.zshrc. Configurable via tools_config.json: `login_shell` (default: true), `shell_path` (default: auto-detect from $SHELL, fallback /bin/zsh). Both streaming and non-streaming code paths updated. Root cause: server process environment lacked PATH entries from shell profiles, so npm-installed tools like `claude` weren't found.
