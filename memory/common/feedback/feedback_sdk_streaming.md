---
id: feedback_sdk_streaming
parent: Feedback System
created: 2026-03-15T04:15:00Z
last_updated: 2026-04-08T04:15:00Z
created_by: user
updated_by: Reporter-Relationship-Discovery-2026-04-08
relationships:
  - type: contradicts
    target: memory:common/feedback/feedback_direct_execution.md
update_site: Reporter Relationship Discovery
---

# Feedback: SDK Streaming Issues

## Issue Description
The feedback_sdk_streaming.md memory documents known issues with SDK-side streaming that affect Reporter agent operations.

## Problem Statement
- SDK hooks cause streaming buffering that prevents immediate output
- Affects real-time report generation and delivery
- Creates latency in email delivery when streaming is involved

## Consequences
- Slows down report preparation times
- Affects email delivery performance
- Compromises real-time data flow from upstream agents

## Relationship to Other Feedback

### Contradicts
Directly contradicts the principles documented in:
- **`memory:common/feedback/feedback_direct_execution.md`**
  - feedback_direct_execution requires: "User-triggered actions must execute directly, not via scheduler indirection"
  - feedback_sdk_streaming causes: SDK-side streaming creates buffering that delays execution

### Implications
This contradiction requires architectural decisions:
1. Either accept streaming buffering delays
2. Implement server-side hooks to bypass SDK buffering
3. Use REST sidecar approaches for streaming operations

## Recommended Mitigation
- Implement REST sidecar + server-side hooks architecture
- Avoid importing claude_cli in SDK sidecar (as documented in feedback_sidecar_no_claude_cli.md)
- Consider feedback_cliproxy_quota.md restrictions on shared quota management

---
_Memory entry updated by Reporter Relationship Discovery task - 2026-04-08T04:15:00Z_