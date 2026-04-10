---
name: "\"feedback-artifacts-ui-and-sdk-streaming-relationship\""
description: Relationship between UI feedback on artifacts and SDK streaming issues
type: feedback
agent: main
---

---
related:
  - name: "feedback_sdk_streaming"
    relationship: "contradicts"
    detail: "Artifact panel UI must display tool results in Chrome, but SDK hooks prevent streaming causing both issues"
  - name: "backlog_tool_results_display"
    relationship: "same_topic"
    detail: "Both memories address tool result visibility in the UI — one about artifacts panel design, the other about streaming buffering blocking results"
---
The feedback about artifacts UI requiring Vista-compatible display directly conflicts with the SDK streaming buffering issue. Both memories focus on the same problem domain: tool result visibility in the UI, creating a compound blocking issue where neither can be fully resolved until the SDK streaming problem is fixed.
