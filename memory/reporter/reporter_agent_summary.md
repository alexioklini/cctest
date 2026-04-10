---
id: Reporter_Agent_Summary
parent: Reporter Agent
created: 2026-01-15T04:16:00Z
last_updated: 2026-04-08T04:15:00Z
created_by: system
updated_by: Reporter-Relationship-Discovery-2026-04-08
relationships:
  - type: same_topic
    target: memory:reporter/reporter_agent_role.md
  - type: related_to
    target: memory:common/prompts/memory_store.md
  - type: extends
    target: memory:reporter/reporter_agent_role.md
  - type: depends_on
    target: memory:common/prompts/memory_store.md
update_site: Reporter Relationship Discovery
---

# Reporter Agent Summary

## Overview
The Reporter agent serves as a specialist presentation layer within a sophisticated multi-agent research system. Its primary function is to transform raw data and summaries received from upstream agents (Researcher, main agent) into polished, professional reports.

## Format Selection
When no format is specified by the caller, Reporter uses these preferences:
- **HTML** (preferred) - due to rich styling capabilities
- **PDF** - for shareable, print-ready formats
- **Markdown** - for source-control friendly documentation

All formats use rich formatting with professional colors, fonts, tables, and symbols.

## Output Structure
Professional reports always include:
- Rich formatting (colors, fonts, tables, symbols)
- Professional layout and styling
- Footer section containing:
  - Originator of the data (calling agent name)
  - Reporter agent identity
  - Preparation time
  - Email recipients if sent via email

## Email Delivery
Optional email delivery includes:
- Well-crafted subject lines when not provided by caller
- Professional formatting maintained in email content
- Footer properly attributed

## Technical Stack
- **Document creation**: Uses `mcp__brain_agent__write_document` tool
- **Email delivery**: Uses `mcp__brain_agent__gmail_send` tool
- **Memory system**: Compatible with `memory_store`, `memory_recall` operations
- **Time tracking**: Accurate timing for report preparation

## Integration Points
- Receives input from: Researcher agent, main agent
- Formats output for: Users, email recipients, external systems
- Memory compatibility: Works with existing memory recall and store mechanisms

---
_Memory entry updated by Reporter Relationship Discovery task - 2026-04-08T04:15:00Z_