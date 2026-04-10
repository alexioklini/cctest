---
name: "\"claude-code-version-path\""
description: Claude Code install location and PATH issues on Mac Studio M2 Max
type: project
agent: main
related:
  - file: openclaw-vs-claudecode-skills-comparison_92bcff.md
    type: same_topic
  - file: memory_summary.md
    type: same_topic
  - file: openclaw-vs-claudecode-skills-comparison.md
    type: same_topic
  - file: tool-expansion-analysis-2026-03_b28222.md
    type: same_topic
  - file: reporter_agent_summary_07ba84.md
    type: same_topic
  - file: memory_summary_aab3f9.md
    type: same_topic
last_recalled: 2026-04-05
  - file: claude-code-version-path_b3cb28.md
    type: same_topic
  - file: shell-env-fix-2025_59a0f4.md
    type: extends
  - file: claude_code_path_resolution_chain_dbf528.md
    type: same_topic
  - file: openclaw-vs-claudecode-skills-comparison_d43e3a.md
    type: same_topic
  - file: scheduled_tasks_87aabf.md
    type: same_topic
  - file: scheduled_task_flags_cli_ab8813.md
    type: same_topic
  - file: skills_system_1b7cd0.md
    type: same_topic
  - file: memory_system_e5dba6.md
    type: same_topic
  - file: skill_manifests_vs_discovery_f8ada1.md
    type: same_topic
  - file: skill_execution_comparison_e9c7e3.md
    type: same_topic
  - file: opus_fallback_corruption_sse_overload_errors_cause_09c3a0.md
    type: same_topic
---

---
related:
  - name: "shell-env-fix-2025"
    relationship: "depends_on"
    detail: "The PATH issue documented here was resolved by the login shell fix in shell-env-fix-2025"
---
Claude Code v2.1.83 is installed on the Mac Studio M2 Max. The `claude` binary is NOT on PATH in non-interactive shells (Brain Agent's execute_command). To find it, either: 1) source ~/.zshrc first, 2) check npm global bin: $(npm -g bin)/claude, 3) check ~/.npm-global/bin/claude, or 4) find via: find / -name claude -type f 2>/dev/null. The Claude desktop app bundles older versions at ~/Library/Application Support/Claude/claude-code/. The actual latest is installed via npm globally.
