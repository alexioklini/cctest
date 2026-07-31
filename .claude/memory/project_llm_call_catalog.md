---
name: llm-call-catalog
description: "Full catalog of every LLM use case in Brain Agent, the config setting that picks the model, and the fallback. As of 2026-05-17 after unified background-model policy (configured → server default_model, no haiku/cheapest heuristics)."
metadata: 
  node_type: memory
  type: project
  originSessionId: 60a6bac4-188b-4b35-b05f-befa1c3d976b
---

Complete catalog of LLM invocation sites in Brain Agent, mapping each use case to its model-resolution chain.

**Why:** Hard to keep mental model of where every background LLM call gets its model from. Used as the source-of-truth when changing model defaults or adding new admin settings. Compiled 2026-05-17 after the unification pass that removed all "cheapest haiku → cheapest enabled" auto-pick scans.

**How to apply:** Before adding a new background LLM caller, consult this table to see which config knob you should reuse vs. invent. Before changing a fallback default, check which use cases inherit from it.

## Use case → model resolution

| Use case | Model picked from setting | Fallback |
|---|---|---|
| Interactive chat (user turn) | `session.model` — user-picked from composer dropdown | None (errors if invalid) |
| Per-chat summary (sidebar synopsis) | `config.json → chat_summary_model` | Server `default_model` (via `_background_model_default()`) |
| User-profile daemon rebuild | `config.json → chat_summary_model` (shared) → else `tools_config.refinement.model` | Server `default_model` |
| Next-prompt suggestion | `session.model` (same as the chat) | Server `default_model` |
| Auto-memory extraction | `agent.json → token_config.auto_memory.model` → `…model_fallback` | Server `default_model` |
| Tool-result summarisation (worker subagent) | Inherits the delegating call's model | Server `default_model` |
| Chat-memory classifier (file/skip gate) | Inherits the chat's model | Server `default_model` |
| Code-graph node summaries | Server `default_model` directly | None (skipped if unset) |
| MemPalace KG triple extraction | `config.json → kg_extraction.model` | Server `default_model` |
| MemPalace closet regen | Same as KG extraction (`kg_extraction.model`) | Server `default_model` |
| Auto-dream | `agent autodream cfg → model` → `…model_fallback` | Server `default_model` |
| Relationship discovery (live + scheduled) | `agent cfg → relationship_discovery.model` → `…model_fallback` | Server `default_model` |
| LCM context summarisation | `mempalace.lcm.summary_model` → `…summary_model_fallback` | Server `default_model` |
| `/refine` endpoint | `tools_config.json → refinement.model` | Server `default_model` |
| `/chat-prompt` endpoint | `tools_config.json → refinement.model` | Server `default_model` |
| `ask_llm` tool | Caller-supplied `model` arg | None (must be explicit) |
| `delegate_task` tool / agent-to-agent | Target agent's `model` field | Server `default_model` |
| Scheduled task execution | `schedules.model` column (per-task) | Owning agent's model → server `default_model` |
| Workflow step | Workflow step `model` field | Inherits parent → server `default_model` |
| Text translation | `tools_config.translation.default_model` → `…detection_fallback_model` → `tools_config.refinement.model` | Server `default_model` |
| Language detection | `tools_config.translation.detection_fallback_model` | Server `default_model` |
| Document translation (docx/pptx/pdf) | Same chain as text translation | Server `default_model` |
| Audio transcription | `tools_config.transcribe_audio.default_model` | `tools_config.transcribe_audio.fallback_model` (no server-default rung — audio model needed) |
| TTS | `tools_config.text_to_speech.default_model` | None (errors if unset) |
| Image-describe (vision fallback for non-vision chat models) | `attachments.image_model` (thread-local mirror) | First enabled image-capable model — **not** unified with `_background_model_default()` |
| Warmup (pool primer) | The model warming itself (`models.<id>` config) | None |
| Embeddings | Fixed (QMD subprocess, no `model` arg) | N/A |

## Global modifier — GDPR auto-swap

Every **non-interactive** call passes through `engine.gdpr_pick_model_for_background(model, texts, purpose)`:
- PII in payload + `default_local_fallback_model` configured → silently swap to the local model.
- PII + `server_block=true` + no local route → `GDPRBlockedError` (caller returns nothing).
- Audit rows: `pii_detected`, `pii_auto_fallback`, `pii_blocked`.

Interactive chat is gated client-side instead (composer auto-swaps the dropdown to a local model when PII is in the draft or history).

## Sites that still do ad-hoc picking (deviate from unified policy)

1. **Image-describe vision fallback** (`brain.py` attachment routing) — picks "first enabled image-capable model" via its own scan instead of routing through `_background_model_default()`. If you want strict policy uniformity, this needs the same patch applied elsewhere.
2. **Transcription fallback** uses the tool-config's own `fallback_model` (default `whisper-base`) and never falls through to `server.default_model`. Intentional — transcription needs an audio-capable model, server default is a chat model — but the chain stops there.
3. **TTS** has no fallback (no audio-out among chat models).

## Unified policy (as of 2026-05-17)

All background LLM auto-picks now route through helpers in `brain.py`:
- `_is_model_available(mid)` — configured + enabled check.
- `_background_model_default()` — returns `_delegate_fallback_model` (= server's `default_model`) when available, else `""`.
- `_resolve_model_with_fallback(primary, fallback, hardcoded_default=None)` — primary → fallback → server default → empty.

No more "cheapest haiku → cheapest enabled" scans. No more hardcoded `claude-haiku-4-5-20251001` fallbacks. Removed from: `_generate_chat_summary`, `_profile_pick_model`, `_auto_memory_extract`, code-graph node summaries, `_find_cheapest_model`, `_resolve_autodream_model`, `_resolve_summary_model` (LCM), relationship discovery, `/refine`, `/chat-prompt`.

Related: [[chat-summary-model-setting]] for the admin UI surface, [[unified-background-model-policy]] for the policy change.
