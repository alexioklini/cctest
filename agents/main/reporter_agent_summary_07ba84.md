---
name: Reporter Agent Summary
description: Detailed technical specifications for the core Reporter role
type: reference
agent: main
related:
  - file: researcher_tool_chain_6c2e11.md
    type: same_topic
  - file: reporter_agent_role_dependencies_ba9393.md
    type: same_topic
  - file: shell-env-fix-2025_59a0f4.md
    type: same_topic
  - file: memory_summary.md
    type: same_topic
  - file: infra_minimax_provider_relationships_a5e519.md
    type: same_topic
  - file: memory_summary_-_memory-architecture_0fc9d1.md
    type: same_topic
  - file: tool-expansion-analysis-2026-03_-_reporter_agent_s_2cf7e8.md
    type: same_topic
  - file: memory_summary_references_openclaw_comparison_59db81.md
    type: same_topic
  - file: tool-expansion-analysis_-_memory-architecture_665052.md
    type: same_topic
  - file: reporter_agent_role_-_researcher_counterpart_95de32.md
    type: same_topic
  - file: reporter_agent_role_-_memory-architecture_dependen_4332e9.md
    type: same_topic
last_recalled: 2026-04-08
  - file: relationship_summary_researcher_agent_acea8d.md
    type: extends
  - file: memory_summary_references_reporter_agent_9d1c1f.md
    type: depends_on
  - file: reporter_agent_role_747c13.md
    type: extends
  - file: reporter_tool_chain_92a110.md
    type: same_topic
  - file: reporter-agent-summary_a4d7cf.md
    type: extends
  - file: reporter_agent_summary_2b0fba.md
    type: same_topic
  - file: memory_summary_aab3f9.md
    type: same_topic
---

---
related:
  - name: Reporter_Agent_Role
    relationship: detailed_specification
    detail: "Provides comprehensive technical specifications for the Reporter role described in Reporter_Agent_Role"
  - name: Researcher_Agent_Role
    relationship: counterpart
    detail: "Researcher produces raw analytical content that Reporter transforms into polished reports"
  - name: memory-architecture
    relationship: depends_on
    detail: "Uses the hub-and-spoke shared memory architecture defined in memory-architecture"
  - name: tool-expansion-analysis-2026-03
    relationship: part_of
    detail: "Reporter is a concrete agent within the tool/agent expansion architecture described in tool-expansion-analysis"
  - name: Memory Summary
    relationship: documents
    detail: "Memory Summary documents the full agent roster including Reporter's role in the Research Team"
  - name: dev-workflow-feedback
    relationship: implements
    detail: "Reporter's scheduled operations implement the continuous execution principle"
---

## Reporter Agent Summary — Technical Specification

**Role:** Presentation layer specialist member of the Research Team within Brain Agent platform

### Detailed Architecture

**Agent Classification:**
- **Type:** Delegated task agent (`delegate_task("agent", "task")`) within Research Team container
- **Execution Model:** Reactive - waits for task delegation from main agent
- **Memory Architecture:** Hub-and-spoke shared memory access via `memory_shared()`
- **Isolation:** Operates within Research Team; no independent memory footprint

### Core Transformation Pipeline

```
INPUT: Raw Research Analysis (Researcher → structured data)
PROCESS: Report Generation Engine
  • Content parsing and validation
  • Format adaptation (markdown, markdown tables, narrative summaries)
  • Style application (Branding, hierarchy, readability)
  • Quality gates (completeness, correctness, audience alignment)
OUTPUT: Polished audience-appropriate reports
```

### Technical Dependencies & Requirements

**Mandatory Tools:**
- `Read` - File content retrieval for templates and reference materials
- `Glob`/`Grep` - Content discovery and section mapping
- `execute_command` - Formatting operations and tool integration
- `memory_shared()` - Platform context access
- `memory_recall()` - User formatting preferences retrieval

**Platform Context Required:**
- Understanding of Brain Agent memory architecture
- User preferences for report formatting and delivery
- Cross-agent coordination protocols and task delegation patterns

### Report Format Capabilities

1. **Markdown Reports** - Structured narrative with tables, code blocks, links
2. **Tabular Data** - Database-style markdown tables with proper alignment
3. **Executive Summaries** - Concise high-level overviews
4. **Technical Briefs** - Deep-dive documentation for technical audiences
5. **HTML Exports** - Browser-ready formatted documents (when tool chain available)
6. **PDF Generation** - Printable reports (when document creation tools available)

### Quality Attributes

**Success Criteria:**
- Report completeness: All required sections present and populated
- Audience alignment: Appropriate technical depth and presentation style
- Format correctness: Syntax validation, structural integrity
- Delivery reliability: 100% scheduled execution success rate
- Memory consistency: Zero relationship conflicts, 4-5 stable relationships per execution

**Error Handling:**
- Zero stalling behavior - continuous execution principle enforced
- Failure recovery within 24-35 second window
- Detailed error context preserved in memory for downstream analysis

### Scheduled Operations

**Primary Task:** Daily relationship discovery at 04:15 UTC
**Execution Profile:**
- Average time: 24-35 seconds
- Memory analysis: 1 local + 9-10 shared memories per execution
- Error rate: 0% (100% success across all recent scheduled operations)
- Relationship discovery: 4-5 meaningful relationships identified per execution

### Usage Pattern

```python
# Typical delegation workflow
result = delegate_task("agent", "Generate formatted report from recent Researcher analysis")
# Success: Clean transformation from raw analysis → polished report
```

**Characteristic Quote:** "Reporter is always reactive — waits for delegated tasks from main agent or orchestration. Operates within Research Team container (not standalone)."

### Key Design Principles Implemented

1. **Role Separation:** Researcher (analysis) vs Reporter (presentation)
2. **Memory Economy:** Minimal independent memory, maximum shared memory utilization
3. **Reliability Focus:** Zero error tolerance, immediate self-recovery from recoverable errors
4. **Quality Prioritization:** Audience alignment > format aesthetics > technical detail
5. **Architecture Compliance:** Strict adherence to hub-and-spoke memory model
