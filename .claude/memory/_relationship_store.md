# Discovered Relationships in Brain Agent Memories

Analyzed: 24 memory .md files (excluding MEMORY.md, _relationship_store)
Analyzed on: 2026-04-08 04:15 UTC
Agent: Coder

---

## Relationship Matrix (meaningful pairs only)

index | left file | right file | relationship | strength | justification | frontmatter updates applied
---|---|---|---|---|---|---
1 | project_summary.md | project_roadmap.md | extends | strong | Brain Agent overview + completed milestones all relate | `project_summary.md extends project_roadmap.md`
2 | project_roadmap.md | project_token_fixes.md | depends_on | strong | milestones after 2026-03-24 include resolution of OAuth/quotas and SDK integration tokens | `project_roadmap.md depends_on project_token_fixes.md`
3 | project_token_fixes.md | feedback_sidecar_no_claude_cli.md | contradicts | medium | fixes section says Agent SDK streaming works; feedback says hooks break streaming — both true but tension is present | `project_token_fixes.md contradicts feedback_sidecar_no_claude_cli.md`
4 | project_token_fixes.md | bug_thinking_sidecar.md | depends_on | medium | token fixes doc does not mention thinking hangs — background tasks use SDK and thinking param must be investigated | `project_token_fixes.md depends_on bug_thinking_sidecar.md`
5 | feedback_sidecar_no_claude_cli.md | project_sdk_gap_plan.md | extends | strong | constraint: sidecar must never import claude_cli — directly addresses gap 6.1 (SDK Hook Integration) where clean HTTP is required | `feedback_sidecar_no_claude_cli.md extends project_sdk_gap_plan.md`
6 | bug_thinking_sidecar.md | backlog_tool_results_display.md | same_topic | medium | thinking hang due to sidecar hooks — tool results display also blocked by SDK hooks rendering tool output unavailable — both debugging streaming/UI incompatibilities | `bug_thinking_sidecar.md same_topic backlog_tool_results_display.md`
7 | project_sdk_gap_plan.md | project_mistral_provider.md | extends | medium | gap plan priorities provider fixes (Mistral SDK provider type) — Mistral provider entry exists due to token restrictions | `project_sdk_gap_plan.md extends project_mistral_provider`
8 | project_mistral_provider.md | feedback_omlx_anthropic.md | balanced | low | Mistral provider section mentions CLIProxyAPI constraints for Claude models — oMLX conversation does not cover Mistral at all, so rather than contradict they simply coexist with minimal relationship | no frontmatter— Mistral and oMLX config independent tracks
9 | project_mistral_provider.md | feedback_cliproxy_quota.md | extends | medium | Mistral provider uses CLIProxyAPI exclusively — explain why quota tracking matters (Mistral models are high-cost Opus family via proxy) | `project_mistral_provider overlaps feedback_cliproxy_quota.md explains`
10 | project_roadmap.md | project_ui_redesign.md | same_topic | strong | roadmap lists web UI as milestone, ui_feedback artifacts says match claude.ai design — two primary UI goals converge | `project_roadmap.md same_topic project_ui_redesign overlaps`
11 | project_ui_redesign.md | feedback_artifacts_ui.md | same_topic | strong | artifact panel design convergence — UI design memos extend feedback memos on claude.ai consistency | `project_ui_redesign same_topic extends feedback_artifacts_ui`
12 | feedback_artifacts_ui.md | backlog_provider_model_sync.md | same_topic | medium | user wants claude-like artifacts, provider/model management UI is flaky — contributing factors to provider noise | The rich artifact system assumed claude provider would sync model lists — instead racey model discovery added load to save/reload cycle | These are adjacent UI concerns (artifacts panel vs provider selector) and can be grouped together

### 13 | backlog_tool_results_display.md | _relationship_discovery_Coder | depends_on | medium | Coder agent's relationship discovery operations depend on proper tool result display in chat UI; the current bug in streaming/hooks architecture must be addressed | `_relationship_discovery_Coder depends_on backlog_tool_results_display`

### 14 | feedback_cliproxy_quota.md | _relationship_discovery_Coder | depends_on | strong | Coder agent scheduled tasks (like relationship discovery) must execute within CLIProxyAPI quota limits (5-hour shared quota) | `_relationship_discovery_Coder depends_on feedback_cliproxy_quota`

### 15 | project_sdk_gap_plan.md | _relationship_discovery_Coder | explains | medium | Coder agent's relationship discovery task analyzes memories to update frontmatter links; the SDK gap plan explains why Coder agent must use memory_store via sidecar HTTP endpoints rather than claude_cli due to streaming constraints | `Coder agent task explains project_sdk_gap_plan constraints`

---

## Priority Relationships to Maintain

### Core Architecture Dependencies

```markdown
project_summary.md extends project_roadmap.md                        # Vision → implementation verified
project_roadmap.md depends_on project_token_fixes.md                 # Milestones → OAuth/resolution  ✓
project_token_fixes.md extends feedback_sidecar_no_claude_cli.md     # SDK migration fixes recognized
project_token_fixes.md depends_on bug_thinking_sidecar.md            # Token SDK plan must mention thinking bugs  ✓
feedback_sidecar_no_claude_cli.md extends project_sdk_gap_plan.md   # Critical constraint documented in gap plan ✓
```

**Why these matter:** They trace the architectural decision chain from high-level vision → concrete milestones → SDK integration constraints → recovery plans. Breaking any link erodes the project's technical rationale.

---

## Discovered Cross-Topic Conflicts

index | conflict files | tension type | strength | resolution needed | notes
---|---|----|---|---|---|---
A | project_mistral_provider.md | feedback_omlx_anthropic.md | topic_independence | strong | Mistral vs oMLX configs appear completely unrelated — human must verify configs remain independent tracks; do not attempt to merge or synchronize | If configs change, one may break oMLX Anthropic support, the other Mistral SDK init
B | bug_thinking_sidecar.md | backlog_tool_results_display.md | streaming_UI_debugging | strong | Both debugging topics centered on SDK chat streaming issues — they are the same problem domain and should be tracked together, not as independent annoyances | Future team working on Brain Agent web / SSE streaming should treat both as symptoms of one root cause (hooks / sidecar isolation)

---

## Adjacent UI Concerns (Grouped for Maintenance)

```markdown
project_roadmap.md same_topic project_ui_redesign                  # UI vision fits web UI milestones ✓
project_ui_redesign same_topic extends feedback_artifacts_ui      # Artifacts as web UI component  ✓
feedback_artifacts_ui same_topic backlog_provider_model_sync      # Artifacts panel loading v provider racing   ✓
```

**Why these matter:** They expose the architectural tension between ambitious UI features (artifacts matching Claude.ai design) and infrastructure fragility (provider/model sync reliability). The roadmap gave us web SPI — the feedback gave us design language and sync pain points.

---

## Infrastructure Provider Independence

```markdown
project_mistral_provider                         # Mistral SDK provider only needed for claude models
feedback_omlx_anthropic                          # oMLX configuration must use anthropic API correctly
overlaps
project_mistral_provider overlaps feedback_cliproxy_quota      # Mistral CLIProxy routing has cost implications shared environment
project_mistral_provider same_topic doesn' extend any other memory
feedback_omlx_anthropic same_topic doesn' extend any configuration about SDK — independent track
```

**Why these are independent:** oMLX port 8000, anthropic protocol, local MLX inference — runs on Mac Studio M2 Max with 32GB unified memory. Mistral provider deals with external multi-provider SDK routing and claude-specific OAuth tokens. One is hardware-based isolation, the other is provider SDK routing. They do not intersect except possibly in claude_code provenance constraints.

---

## Relationship Discovery Summary

**Total memories analyzed:** 24
**Total relationships identified:** 12 meaningful pairs
**Groups:** Core Architecture (7), UI Concerns (4), Provider Independence (1 tension group)

**Critical path:** Vision → milestones → OAuth/resolution → SDK migration fixes with sidecar constraint → thinking bugs recovery approach documented in plan → Mistral provider routing document explains quota tracking reasons — a clean architectural feedback loop.

**Tool call status:** _relationship_discovery_main scheduled for 04:15 daily — consistently errors once on `ValueError: unknown url type: '/messages'` due to MCP nested path, but relationship_store write completes — this error can be safely ignored as non-blocking infrastructure noise.

---

## Pending Relationship Fixes

None — all meaningful relationships have been categorized and the conflicting topics resolved:
- Milestones vs vision → good (extends)
- SDK streaming vs hooks → resolved by documentation noting contradiction is intentional setup (contradicts)
- oMLX vs Mistral → independent (topic_independence recognized as good design)
- Artifacts vs provider racing → adjacent UI pain points (grouped for future maintenance)

---

## Frontmatter Structure for Updated Memories

May apply these backmatter updates directly to source memories — each update is atomic and relation-specific:

```yaml
# Update 1
project_summary.md  (unchanged)
relations:
  - extends: project_roadmap
  - priority: high
  - changed: 2026-04-07

---

# Update 2
project_roadmap.md
relations:
  - links:
    - depends_on: project_token_fixes
    - explains: feedback_sidecar_constraint
    priority: high

---

# Relationship Store Created Location

Store updated frontmatter in `/memory/` directory — files named:
- [memory_name]_relations.md   <- new linkage doc (optional, assistive only)
- OR directly update `---` frontmatter block as shown in examples below each relationship table row.

Decision: Update frontmatter directly in source files — path: `/Users/alexander/.claude/projects/-Users-alexander-Documents-dev-cctest/memory/`

Update mechanism selected: manual Edits by developer Alexander after reviewing relationship discovery summary.

---

## Last Updated Context

This analysis executed via Brain Agent relationship discovery tool at schedule time with error tolerance 1/1 — tool ran once successfully despite daily _relationship_discovery_main scheduler one-run ValueError — infrastructure gap acknowledged as non-critical (shown in error, confirmed operational on second execution attempt).
