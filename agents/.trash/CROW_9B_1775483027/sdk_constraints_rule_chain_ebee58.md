---
name: sdk_constraints_rule_chain
description: Critical SDK migration constraints and their interconnections
type: feedback
agent: CROW_9B
related:
  - file: project_summary_arch_links_d8ce8a.md
    type: same_topic
  - file: tech_infrastructure_dependencies_cf424e.md
    type: same_topic
  - file: provider_management_ecosystem_4bd9c7.md
    type: same_topic
---

# Critical SDK Constraint Chain

The CROW_9B SDK migration enforces four non-negotiable constraints:

## Rule 1: API Type Decisions
- oMLX uses **anthropic** API type (not OpenAI) per `feedback_omlx_anthropic`
- Mistral uses **mistral SDK** provider type via Pro subscription key
- These decisions directly inform `project_token_fixes.md` implementation

## Rule 2: Sidecar Architecture  
- SDK sidecar **MUST NOT** import `claude_cli` → breaks anyio streaming per `feedback_sidecar_no_claude_cli`
- User-triggered actions must bypass client-side scheduler indirection per `feedback_direct_execution`

## Rule 3: Resource Management
- CLIProxyAPI shares Claude's 5-hour quota → runaway tool loops will exhaust quota (undaunted by `feedback_cliproxy_quota`)
- Local oMLX inferencer on port 8000 with Crow-4B model management documented

## Rule 4: Streaming Preservation
- REST sidecar pattern required to preserve SSE streaming (SSE streaming broken when thinking parameter used via SDK sidecar)
- Tool result display blocked by SDK hooks killing streaming → requires restructuring per `backlog_tool_results_display`

# Frontmatter Relationships
- `feedback_omlx_anthropic` → **contradicts_openai_assumption**
- `feedback_sidecar_no_claude` → **breaks_streaming_rule**
- `feedback_direct_execution` → **bypasses_scheduler_bug**
- `feedback_cliproxy_quota` → **shares_claude_quota Rīser**
- `project_token_fixes` → **implements_all_constraints**
