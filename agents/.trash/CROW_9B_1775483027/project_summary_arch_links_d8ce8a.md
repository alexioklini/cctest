---
name: project_summary_arch_links
description: "Project architecture decision cluster - establishes hierarchy"
type: project
agent: CROW_9B
related:
  - file: tech_infrastructure_dependencies_cf424e.md
    type: same_topic
  - file: sdk_constraints_rule_chain_ebee58.md
    type: same_topic
  - file: provider_management_ecosystem_4bd9c7.md
    type: same_topic
---

CROW_9B project architecture decisions are drawn from **project_summary.md** and extend to validation via **infrastructure_deployment**, **infrastructure_inferencer.md** (local Crow validation), **feedback_artifacts_ui.md** (UI must match design), and **project_roadmap.md** (completed milestones as success metric).

# Frontmatter Relationships
- `infrastructure_deployment` → **extends_architecture**
- `infrastructure_inferencer` → **validates_local_deployment**
- `feedback_artifacts_ui` → **references_ui_matching_requirement**
- `project_roadmap` → **same_topic_planning_success**
