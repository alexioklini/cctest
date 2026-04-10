---
name: "\"infra-deployment-and-inferencer-relationship\""
description: Relationship between deployment infrastructure and local inferencing
type: project
agent: main
last_recalled: 2026-04-05
---

---
related:
  - name: "infra_deployment"
    relationship: "extends"
    detail: "infra_inferencer builds upon the deployment architecture described in infra_deployment by adding local model serving"
  - name: "infra_inferencer"
    relationship: "same_topic"
    detail: "Both describe infrastructure components for enabling the system, one cloud-based deployment, the other local model serving"
  - name: "project_sdk_gap_plan"
    relationship: "depends_on"
    detail: "Local inferencing (oMLX server) is a requirement for closing SDK migration gaps via MCP servers"
---
The local oMLX inferencer (port 8000 with Crow-4B model) extends the cloud deployment architecture described in infra_deployment.md by providing on-premise model serving capabilities. This dependency enables the full SDK gap plan, particularly for MCP server integration and improving inference performance.
