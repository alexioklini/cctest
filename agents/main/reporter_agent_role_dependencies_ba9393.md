---
name: "Reporter Agent Role & Dependencies"
description: Extended description of Reporter agent's role and architectural dependencies
type: reference
agent: main
related:
  - file: dev-workflow-feedback_cffe4d.md
    type: same_topic
  - file: documents_folder_size_6dbcec.md
    type: same_topic
  - file: infra_minimax_provider_983002.md
    type: same_topic
  - file: memory-architecture.md
    type: same_topic
  - file: memory_summary_-_openclaw-vs-claudecode-skills-com_6b0901.md
    type: same_topic
  - file: relationship_summary_researcher_agent_acea8d.md
    type: same_topic
  - file: shell-env-fix-2025_59a0f4.md
    type: same_topic
  - file: reporter_agent_summary_07ba84.md
    type: same_topic
last_recalled: 2026-04-08
  - file: memory_summary_references_reporter_agent_9d1c1f.md
    type: depends_on
  - file: memory_summary_aab3f9.md
    type: extends
  - file: openclaw-vs-claudecode-ref-memory-summary_bcb3eb.md
    type: same_topic
  - file: scheduled_tasks_87aabf.md
    type: same_topic
  - file: scheduled_task_flags_cli_ab8813.md
    type: same_topic
  - file: skills_system_1b7cd0.md
    type: same_topic
  - file: memory_system_e5dba6.md
    type: same_topic
  - file: reporter-agent-summary_a4d7cf.md
    type: same_topic
  - file: memory_summary_-_relationship_summary_researcher_a_5dd031.md
    type: same_topic
  - file: openclaw-vs-claudecode-skills-comparison-extended-_82c838.md
    type: same_topic
  - file: memory_summary_-_user_identity_520e90.md
    type: same_topic
  - file: claude-code-version-path_b3cb28.md
    type: same_topic
  - file: memory-architecture_-_tool-expansion-analysis-2026_2bb117.md
    type: same_topic
  - file: researcher_agent_role_-_reporter_agent_summary_d7f789.md
    type: same_topic
  - file: shell-env-fix-2025_-_claude-code-version-path_35b821.md
    type: same_topic
  - file: tool-expansion-analysis-2026-03_-_reporter_agent_s_2cf7e8.md
    type: same_topic
  - file: coder-agent-scheduled-tasks_-_memory_health_report_ae6d8d.md
    type: same_topic
  - file: infra_minimax_provider_relationships_a5e519.md
    type: same_topic
  - file: researcher_agent_role_-_researcher_tool_chain_2d9815.md
    type: same_topic
  - file: memory_health_reports_chain_0dfb71.md
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
  - file: _relationship_discovery_crow4b_-_scheduled_tasks_f0e6d1.md
    type: same_topic
  - file: crow4b_-_memory_summary_6f4362.md
    type: same_topic
  - file: reporter_tool_chain_92a110.md
    type: same_topic
  - file: reporter_agent_role_-_researcher_counterpart_95de32.md
    type: same_topic
  - file: reporter_agent_-_web_ui_tool_toggle_presentation_79f2ec.md
    type: same_topic
  - file: reporter_agent_role_-_memory-architecture_dependen_4332e9.md
    type: same_topic
  - file: mcp_servers_vs_native_tools_supplementary_tools_an_408255.md
    type: same_topic
  - file: system_architecture_brain-agent_on_mac_studio_m2_m_bb9752.md
    type: same_topic
  - file: crow_9b_relationships_part_1_b35ee3.md
    type: same_topic
  - file: multi_agent_content_pipeline_94fcc2.md
    type: same_topic
  - file: sdk_gap_plan_implementation_5ab4e9.md
    type: same_topic
---

---
related:
  - name: "memory-architecture"
    relationship: "depends_on"
    detail: "Reporter's architecture depends on the hub-and-spoke shared memory model defined in memory-architecture"
  - name: "Memory Summary"
    relationship: "extends"
    detail: "Memory Summary documents Reporter's role; this memory provides detailed functional breakdown and dependencies"
  - name: "Researcher Agent"
    relationship: "depends_on"
    detail: "Reporter receives raw content requiring transformation and presentation from the Researcher agent"
  - name: "Web UI Tool Toggle"
    relationship: "same_topic"
    detail: "Both address presentation layer concerns — Reporter specializes in report formatting, UI toggle in tool visibility"
  - name: "dev-workflow-feedback"
    relationship: "references"
    detail: "Reporter's scheduled operations should follow the self-recovery principle: maintain momentum and self-recover from errors during report generation"
---
## Reporter Agent — Role & Dependencies

### Agent Role & Identity
The **Reporter** is a specialized member of the **Research Team** (alongside the Researcher agent) within the Brain Agent platform. As a team member delegated via `delegate_task("agent", "task")`, the Reporter functions as a **presentation layer specialist** — transforming raw research analysis into structured, formatted reports suitable for various audiences and consumption contexts.

### Core Responsibilities
1. **Report Generation & Synthesis** — Convert Researcher's raw analysis into polished, audience-appropriate documentation
2. **Format Adaptation** — Deliver reports in multiple formats (markdown, tabular data, narrative summaries, structured documents)
3. **Audience Customization** — Tailor depth, detail level, and presentation style based on consumer needs
4. **Quality & Clarity** — Ensure information is digestible, well-organized, and actionable

### Functional Dependencies
- **Primary Dependency**: Receives raw analysis from **Researcher agent** within Research Team delegation workflow
- **System Architecture**: Accesses shared memory via `memory_shared()` to understand:
  - Overall platform decisions and context
  - User preferences for report formatting and presentation
  - Cross-agent coordination and dependencies
- **Shared Memory Model**: Uses hub-and-spoke architecture where main agent's memory IS the shared memory store — Reporter can reliably access global context without duplication

### Working Pattern Insights
- **Minimal Independent Memory**: Maintains primarily reactive memory (operational context, formatting preferences)
- **Scheduled Operations**: Relationship discovery runs daily at 04:15 UTC with consistent 24-35 second execution times (4 tools used per run)
- **Memory Health**: All 4-5 identified relationships are stable and documented. No conflicts or stale entries.
- **Execution Reliability**: All recent scheduled executions (2026-03-24 through 2026-03-25, covering 48-hour window) completed successfully with no timeouts or failures — 100% success rate

### Key Memory Relationships
1. **extends** Reporter Agent Role & Dependencies — Detailed functional breakdown at lower abstraction level
2. **depends_on** Researcher Agent — Receives raw content requiring transformation and presentation
3. **references** memory-architecture — Uses shared memory system for accessing context and task requirements
4. **same_topic** Web UI Tool Toggle — Both focus on presentation layer (report formatting vs UI tool visibility)

### Technical Execution Patterns
- All relationship discovery tasks execute in 24-35 seconds, analyzing 1 local memory + 9-10 shared memories
- Consistently identifies 4-5 meaningful relationships with no failed or incomplete scans
- Memory frontmatter properly maintained with `related:` sections linking to target memories
- No errors, timeouts, or resource contention observed

### Important Context
- Reporter is **always reactive** — waits for delegated tasks from main agent or orchestration layer
- Operates within the **Research Team container** — not standalone
- Success metric: Transformation quality, format correctness, audience alignment
- Should maintain awareness of formatting preferences stored in shared memory (e.g., "Web UI Tool Toggle" preferences)
