---
name: "memory-architecture"
description: "How the Brain Agent memory system works - main agent memory is shared memory"
type: project
agent: main
related:
  - file: memory_summary.md
    type: same_topic
  - file: dev-workflow-feedback_cffe4d.md
    type: same_topic
  - file: openclaw-vs-claudecode-skills-comparison.md
    type: same_topic
  - file: soul.md
    type: co_recalled
  - file: chats-indexed/chat-ac7a79c746f3-000.md
    type: same_topic
  - file: memory_summary_aab3f9.md
    type: same_topic
  - file: chats-indexed/chat-caba90d0429b-000.md
    type: same_topic
  - file: documents_folder_size_6dbcec.md
    type: same_topic
  - file: mbaim1-vpn-connection_248938.md
    type: same_topic
last_recalled: 2026-04-05
  - file: chats-indexed/chat-6185f1b8c894-000.md
    type: references
  - file: memory_health_report_2026-03-24_94a936.md
    type: same_topic
  - file: memory-architecture_fa7efd.md
    type: same_topic
  - file: claude-code-version-path_b3cb28.md
    type: same_topic
  - file: shell-env-fix-2025_59a0f4.md
    type: same_topic
  - file: shell-env-fix-2025_8c1d05.md
    type: same_topic
  - file: dev-workflow-feedback_af2401.md
    type: same_topic
  - file: tool-expansion-analysis-2026-03_7280e3.md
    type: co_recalled
  - file: tool-expansion-analysis-2026-03_b28222.md
    type: co_recalled
  - file: reporter_agent_summary_07ba84.md
    type: references
  - file: relationship_summary_researcher_agent_acea8d.md
    type: same_topic
  - file: infra_minimax_provider_983002.md
    type: same_topic
  - file: researcher_agent_role_9f388c.md
    type: same_topic
  - file: memory_summary_-_tool-expansion-analysis_b248dd.md
    type: same_topic
  - file: memory_summary_-_memory-architecture_0fc9d1.md
    type: same_topic
  - file: tool-expansion-analysis_-_memory-architecture_665052.md
    type: same_topic
  - file: dev-workflow-feedback_-_memory_summary_c9a09b.md
    type: same_topic
  - file: memory_summary_-_openclaw-vs-claudecode-skills-com_6b0901.md
    type: same_topic
  - file: claude-code-version-path_-_shell-env-fix-2025_224b40.md
    type: same_topic
  - file: reporter_agent_role_dependencies_ba9393.md
    type: same_topic
  - file: coder-agent-scheduled-tasks_23bd9e.md
    type: same_topic
  - file: reporter_agent_summary_2b0fba.md
    type: same_topic
  - file: openclaw-vs-claudecode-skills-comparison_d43e3a.md
    type: same_topic
  - file: memory_health_report_2026-03-25_0fdc66.md
    type: same_topic
  - file: memory_health_report_2026-03-26_3f4592.md
    type: same_topic
  - file: memory_health_reports_chain_0dfb71.md
    type: same_topic
  - file: memory_health_report_2026-03-25_a3253f.md
    type: co_recalled
  - file: researcher_tool_chain_6c2e11.md
    type: co_recalled
  - file: reporter-agent-summary_a4d7cf.md
    type: co_recalled
---

---
related:
  - name: "Memory Summary"
    relationship: "extends"
    detail: "Summary contains condensed version of this architecture decision"
  - name: "openclaw-vs-claudecode-skills-comparison"
    relationship: "same_topic"
    detail: "Both describe Brain Agent platform internals — memory system and skills system"
---
The main agent's private memory and shared memory are the SAME store. memory_recall and memory_shared(action="recall") return identical results. Architecture: Main agent memory = Shared memory (central hub). Other agents (Coder, Researcher, Reporter, etc.) have their own private memories plus can access main/shared memory via memory_shared. Hub-and-spoke model. Anything stored by the main agent is automatically visible to all other agents via shared memory. Other agents' private memories are NOT accessible to the main agent or other agents.
