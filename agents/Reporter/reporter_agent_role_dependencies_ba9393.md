---
name: "Reporter Agent Role & Dependencies"
description: Detailed breakdown of Reporter agent's functional dependencies and reporting responsibilities
type: project
agent: Reporter
related:
  - file: memory_summary_aab3f9.md
    type: same_topic
  - file: _relationship_discovery_reporter_2026-03-25_update_88ba31.md
    type: same_topic
  - file: reporter_agent_role_dependencies_39f405.md
    type: same_topic
  - file: _relationship_discovery_reporter_2026-03-26_758f0c.md
    type: same_topic
  - file: _relationship_discovery_reporter_2026-03-25_538622.md
    type: same_topic
  - file: memory_health_report_2026-03-25_0fdc66.md
    type: same_topic
  - file: memory_summary.md
    type: same_topic
  - file: _relationship_discovery_reporter_2026-03-25_update_49c520.md
    type: same_topic
  - file: memory_health_report_2026-03-26_3f4592.md
    type: same_topic
---

---
related:
  - name: "Memory Summary"
    relationship: "extends"
    detail: "Provides expanded functional detail on Reporter's role; both describe the same agent but at different levels of abstraction"
  - name: "Researcher (Team Head)"
    relationship: "depends_on"
    detail: "Reporter receives raw analysis output from Researcher; cannot function independently without Researcher input"
  - name: "memory-architecture"
    relationship: "references"
    detail: "Reporter's team context (shared memory, delegation model) is defined by the hub-and-spoke architecture"
  - name: "dev-workflow-feedback"
    relationship: "same_topic"
    detail: "Both address execution principles; Reporter applies 'no stalling' feedback when formatting reports"
---

## Reporter Agent: Functional Dependencies & Responsibilities

### Primary Role
The Reporter is the **presentation layer** of the Research Team. It receives raw analysis findings from the Researcher and transforms them into well-structured, user-ready reports. Key responsibilities:

1. **Report Formatting** — Markdown, tables, hierarchical structure, visual clarity
2. **Data Synthesis** — Condensing raw findings into coherent narrative
3. **Output Polishing** — Grammar, consistency, professional presentation
4. **Audience Adaptation** — Tailoring report depth/style to context

### Functional Dependencies
- **Hard Dependency:** Researcher (team head) — provides raw analysis output
- **Infrastructure Dependency:** Shared memory (memory-architecture hub-and-spoke model)
- **Design Dependency:** Presentation standards (Web UI Tool Toggle patterns show Reporter's domain)
- **Execution Dependency:** No stalling principle (dev-workflow-feedback) — maintain report generation momentum

### Why Reporter is a Separate Agent
Rather than having Researcher produce polished reports directly, the Reporter is separate because:
- **Specialization:** Report formatting is distinct skill from research/analysis
- **Scalability:** Multiple Researchers can feed one Reporter, or one Reporter can reuse analysis across contexts
- **Reusability:** Raw findings can be formatted multiple ways (PDF, HTML, email, dashboard)
- **Quality:** Dedicated presentation agent ensures consistent visual/structural standards

### Team Integration Pattern
1. **Main Agent** delegates: "Research X and report on it"
2. **Main Agent** → **Researcher Team** (delegation)
3. **Researcher** performs analysis, returns raw findings
4. **Reporter** transforms findings into final report
5. **Reporter** returns formatted output to Main Agent
6. **Main Agent** returns report to user

### Current Constraints
- Reporter is **purely reactive** — no independent initiative or research
- Reporter depends on clear, well-structured input from Researcher for best output quality
- Reporter has minimal private memory footprint (design choice — reduces overhead)
