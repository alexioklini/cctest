---
id: feedback_direct_execution
parent: Feedback System
created: 2026-03-15T04:16:00Z
last_updated: 2026-04-08T04:15:00Z
created_by: user
updated_by: Reporter-Relationship-Discovery-2026-04-08
relationships:
  - type: contradicts
    target: memory:common/feedback/feedback_sdk_streaming.md
update_site: Reporter Relationship Discovery
---

# Feedback: Direct Execution Requirement

## Issue Description
The feedback_direct_execution.md memory documents a critical requirement for the system architecture.

## Requirement
"User-triggered actions must execute directly, not via scheduler indirection"

## Problem Statement
- Scheduler indirection creates unnecessary delays
- Buffering in SDK streaming prevents immediate results
- Affects user experience and real-time operations

## System Impact
- Applies to Reporter agent operations that depend on upstream agents
- Interacts with feedback_sdk_streaming.md which causes the buffering issue
- Requires architectural solutions at the infrastructure level

## Resolution Path
Two options to satisfy this requirement:
1. **REST sidecar pattern**: Create a separate service that handles streaming without SDK buffering
2. **Server-side hooks**: Implement direct execution pathways in the underlying server infrastructure

## Critical Constraint
- SDK sidecar must never import claude_cli (as documented in feedback_sidecar_no_claude_cli.md)
- Sharing quota restrictions through CLIProxyAPI must be managed (feedback_cliproxy_quota.md)

## Recommendation
Implement REST sidecar architecture to bypass both streaming buffering and ensure direct execution.

---
_Memory entry updated by Reporter Relationship Discovery task - 2026-04-08T04:15:00Z_