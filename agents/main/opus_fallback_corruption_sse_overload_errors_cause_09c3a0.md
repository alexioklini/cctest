---
name: "Opus fallback corruption: SSE overload errors cause message history corruption"
description: "Bug report and root cause analysis: Opus overload/rate-limit errors during tool loops cause message history corruption. Fallback to Gemini Flash fails to reset session state. Requires server restart to recover."
type: chat_transcript
agent: main
related:
  - file: skill_manifests_vs_discovery_f8ada1.md
    type: same_topic
  - file: researcher_agent_role_9f388c.md
    type: same_topic
  - file: openclaw-vs-claudecode-skills-comparison.md
    type: same_topic
  - file: skill_execution_comparison_e9c7e3.md
    type: same_topic
  - file: claude-code-version-path_2b8576.md
    type: same_topic
  - file: skill_isolation_5f38ba.md
    type: same_topic
  - file: skill_manifests_9a5c5f.md
    type: same_topic
last_recalled: 2026-04-05
---

## Final Status: Both fixes ✅ Complete

### Issue Report

**User Report**: In the last chat in main, in the middle, Opus had an error (probably overload or rate limiting) - it failed back to Gemini Flash. No change on the model helped until the user restarted the server, then Opus was working again. After checking in Claude Code, Opus was working correctly. The pattern: after a rate limit or overload error and a fallback, the chat session is corrupted; changing models does not help.

---

## Root Cause Analysis

The bug is a **message history corruption during mid-tool-loop fallback**.

### What Happens Step by Step

1. **Turn starts** — `_msg_count_before = len(session.messages)` is snapshot ✅
2. **Tool loop begins** — `send_message()` is called
3. **SSE error occurs** with `overloaded_error` or `rate_limit_error`
4. Error is marked as `permanent: True` in thread-local storage
5. Fallback mechanism triggers, but session state is left corrupted

### The Problem Path

1. SSE `error` event with `overloaded_error` → sets `permanent: True`
2. `_classify_error_transient()` sees `permanent: True` → returns `False`
3. `_retry_with_backoff()` line 13243: `if not _classify_error_transient(error_info): return None, error_info` → **no retries, immediate fallback**

The issue: Overload and rate-limit errors from streaming are being marked as permanent when they should be classified as transient.

### Current Broken Code

```python
_thread_local._last_send_error = {"code": 0, "message": err_msg, "permanent": True}  # Streaming error: always permanent
```

### The Fix Needed (line 12932)

Overload/rate-limit errors from streaming are transient, not permanent:

```python
# Overload/rate-limit errors from streaming are transient, not permanent
is_permanent = err_type not in ("overloaded_error", "rate_limit_error", "api_error")
_thread_local._last_send_error = {"code": 0, "message": err_msg, "permanent": is_permanent}
```

### Impact

Without the fix: Overload errors trigger fallback without retry, leaving message history corrupted. User must restart the server to recover.

With the fix: Overload errors are retried, session state remains consistent, and fallback only occurs after exhausting retries.
