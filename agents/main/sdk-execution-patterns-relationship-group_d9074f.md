---
name: "\"sdk-execution-patterns-relationship-group\""
description: Grouping memories about SDK/action execution patterns
type: feedback
agent: main
last_recalled: 2026-04-05
---

---
related:
  - name: "feedback_omlx_anthropic"
    relationship: "same_topic"
    detail: "Describes oMLX server using Anthropic API type, critical for SDK integration"
  - name: "feedback_sidecar_no_claude_cli"
    relationship: "references"
    detail: "SDK sidecar restriction (no claude_cli import) is required to prevent breaking anyio streaming"
  - name: "feedback_direct_execution"
    relationship: "depends_on"
    detail: "User-triggered actions must execute directly; SDK sidecar execution would break streaming"
  - name: "feedback_cliproxy_quota"
    relationship: "related"
    detail: "Shared quota between CLIProxyAPI and main Claude flow creates execution constraints"
---
The SDK execution pattern memories form a coherent group: oMLX's Anthropic API compatibility is foundational, the no-claude_cli-import constraint is a hard requirement for streaming preservation, and direct execution is necessary to avoid quota and buffering issues. These patterns define the execution contract for all SDK-integrated actions.
