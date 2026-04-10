---
name: Reporter_Reporting_Tooling
description: Tooling layer that the Reporter agent depends upon for document generation and email distribution
type: reference
agent: main
related:
  - file: reporter-agent-summary_a4d7cf.md
    type: same_topic
  - file: reporter_agent_role_747c13.md
    type: same_topic
  - file: multi_agent_content_pipeline_94fcc2.md
    type: same_topic
last_recalled: 2026-04-08
---

The Reporter agent relies on the write_document tool for creating rich documents in DOCX, XLSX, PPTX, and PDF formats. It also uses gmail_send for delivering reports via email with professional formatting and subject lines. These tools form the core execution layer for generating and distributing the agent's polished outputs to recipients.
