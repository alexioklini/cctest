---
name: "\"dev-workflow-feedback\""
description: "\"Don't stall during development tasks - recover from errors immediately\""
type: feedback
agent: main
related:
  - file: memory_health_report_2026-03-24_94a936.md
    type: same_topic
  - file: memory-architecture.md
    type: same_topic
  - file: openclaw-vs-claudecode-skills-comparison_92bcff.md
    type: same_topic
  - file: reporter_agent_summary_07ba84.md
    type: same_topic
  - file: mbaim1-vpn-connection_248938.md
    type: same_topic
  - file: memory_summary.md
    type: same_topic
  - file: memory_health_report_2026-03-25_a3253f.md
    type: same_topic
  - file: shell-env-fix-2025_8c1d05.md
    type: same_topic
  - file: memory-architecture_fa7efd.md
    type: same_topic
  - file: openclaw-vs-claudecode-skills-comparison.md
    type: same_topic
last_recalled: 2026-04-05
  - file: dev-workflow-feedback_cffe4d.md
    type: co_recalled
  - file: memory_summary_aab3f9.md
    type: co_recalled
  - file: tool-expansion-analysis-2026-03_7280e3.md
    type: co_recalled
  - file: tool-expansion-analysis-2026-03_b28222.md
    type: co_recalled
  - file: memory_health_report_2026-03-26_3f4592.md
    type: same_topic
  - file: memory_summary_references_openclaw_comparison_59db81.md
    type: same_topic
  - file: memory_summary_references_memory_architecture_855b1f.md
    type: same_topic
  - file: memory_summary_references_reporter_agent_9d1c1f.md
    type: same_topic
  - file: memory_health_report_2026-03-24_2f06ed.md
    type: same_topic
  - file: memory_health_report_2026-03-25_0fdc66.md
    type: same_topic
  - file: memory_health_report_2026-04-04_97d30f.md
    type: same_topic
  - file: memory_health_reports_chain_0dfb71.md
    type: co_recalled
  - file: dev-workflow-feedback_-_memory_summary_c9a09b.md
    type: co_recalled
  - file: memory_health_report_2026-03-26_179e66.md
    type: same_topic
  - file: memory_health_report_2026-04-05_919dd1.md
    type: same_topic
  - file: reporter_agent_summary_8cef33.md
    type: same_topic
  - file: memory_health_report_2026-04-06_7591b1.md
    type: same_topic
---

---
related:
  - name: "Memory Summary"
    relationship: "extends"
    detail: "Summary captures this feedback as a key decision; this memory is the authoritative source"
  - name: "Memory Health Report — 2026-03-25"
    relationship: "references"
    detail: "Health report automation is an example of a process that should follow this feedback — self-recover from merge errors instead of stalling"
---
When performing development tasks: Don't stop and wait for the user when hitting recoverable errors (like edit_file match failures). Re-read the file, fix the match, and continue immediately. Don't break flow by asking unnecessary questions mid-task. Keep momentum — the user expects continuous execution until the task is done.
