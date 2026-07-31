---
name: SSD stream hangs when thinking param via SDK sidecar
description: SSE stream hangs when thinking parameter is sent via SDK sidecar causing server-side bug
type: backlog
related_to: [project_sdk_gap_plan, feedback_sdk_streaming, infra_deployment, feedback_sidecar_no_claude_cli]
---

SSE stream hangs when `thinking: true` parameter is sent via the SDK sidecar due to a server-side bug in the thinking feature.

**Impact:** Disrupts real-time streaming for interactive sessions when thinking mode is active.

**Root cause:** Thinking feature implementation conflicts with SSE streaming architecture in the SDK sidecar path.

**Technical details:**
- Without thinking parameter, SSE streaming works normally
- With `"thinking":"low"` parameter, stream hangs with only keepalive comments
- Confirmed via curl tests (POST /v1/chat comparison)
- No text_delta or done events are delivered when thinking is enabled

**Relation to other memories:**
- Requires investigation into SDK sidecar architecture (feedback_sdk_streaming, feedback_sidecar_no_claude_cli)
- Affects full deployment infrastructure (infra_deployment) connectivity
- Relevant to SDK migration gaps (project_sdk_gap_plan) that have been completed but reveal edge cases

**Investigation path:** Investigate `server.py` chat handler → parameter passing to sidecar → `sdk_sidecar.py` query handling. The `inference_params` with thinking budget may not be forwarded correctly to `claude_agent_sdk.query()`.

**Status:** Open bug requiring server-side fix to thinking feature streaming compatibility.
