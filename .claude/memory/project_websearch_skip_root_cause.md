---
name: ""
metadata: 
  node_type: memory
  originSessionId: 42768d20-64f3-45a4-8df7-f92e2a00969e
---

2026-06-10: Long debugging saga (chats 766e3575, 38647ef5, 79dfacfb, 6f25aa84, 5e1c36a2, b6502d44, db6e95a3). Symptom: ask "was ist das Qwopus model" → model (mistral-small AND medium-3.5) searched only mempalace, found nothing, GAVE UP with "not in knowledge base — add a document" instead of escalating to the web tools that WERE in the toolset.

**WRONG turns we took (v9.99.4–9.99.9, ALL reverted to v9.99.3):** chased it through tool-gating — DEFERRED/MCP prompt trimming, documents↔memory classifier coupling, the never-strip floor ({core,workflows}→{}→{core,memory,workflows}). NONE of these were the cause. PROOF it wasn't gating: mistral-MEDIUM with LLM classification OFF (full static toolset, web tools present, zero gating) STILL only ran mempalace ×4 and quit.

**ACTUAL root cause = two things in `config.json` (gitignored, per-machine):**
1. `tool_settings.read_document.description` had "**Step 2 of the project memory flow** (see mempalace_query...)" + heavy drawer/read_path prose → anchored the model onto a memory-first flow even on pure web turns. read_document is ALWAYS present, so this anchor fired every turn.
2. `research_mode_disciplines.refusal` was memory-specific: "if mempalace_query returns 0 drawers → the project does NOT contain it → refuse... 2-3 rephrasings before giving up". Injected DYNAMICALLY on every grounding turn (v9.67.0), incl. web-only → told the model to give up after empty memory instead of trying the web.

**FIX (config.json edits, no code, version stayed 9.99.10):**
- Made all three research disciplines (refusal/precision/citation) TOOL-AGNOSTIC: "the retrieved source" not "read_document"; refusal = "don't assert unproven facts; if not available after a genuine attempt, say so" — no mention of memory/web/any tool, no "give up".
- Stripped mempalace/drawer/read_path refs out of read_document description (it's always-present, must not reference deferred mempalace). Moved that flow text INTO mempalace_query's description (only renders when mempalace loaded).
- Principle enforced + audited (0 violations): a tool description may only reference tools that are ALWAYS co-present with it (same group if deferred, or globally-always-on). read_file/read_document ok (both always on); a deferred tool may reference its group-mates + always-on tools.

RESULT (user-confirmed): works for small AND medium, mem-deferred→web directly, mem-present→mem then web, with LLM tool-opt→mem then web, NO tool_search loops in any variant.

LESSON: when "web search isn't used / model won't escalate", look at the TOOL DESCRIPTIONS and the injected research disciplines FIRST (they steer behaviour), not the tool-gating/floor (that's just availability). See also [[feedback_eval_single_run_noise]] (mistral-small variance fooled us repeatedly here too).
