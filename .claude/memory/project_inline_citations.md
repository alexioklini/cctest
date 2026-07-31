---
name: project_inline_citations
description: "v9.67-9.68 (2026-06-03) Inline Citations built (Phase 4, FINAL) — dynamic classifier-driven citation discipline + numbered inline chips + jump-to-passage. Four-feature order COMPLETE."
metadata: 
  node_type: memory
  type: project
  originSessionId: 8de38dd2-48cf-4103-bc5a-5bf45893b489
---

Inline Citations shipped — Phase 4 (final) of `plans/IMPLEMENTATION_ORDER.md`. The **four-feature order is now COMPLETE**: Output Presets ([[project_output_presets_studio]]) · Studio · Deep Research ([[project_deep_research]]) · Inline Citations.

**v9.67.0 — dynamic citation discipline (foundation).** The research-mode bundle (REFUSAL/PRECISION/per-claim CITATION + the validator) was gated on the per-project `research_mode` flag. Now it fires DYNAMICALLY on any **grounding turn** in ANY chat, driven by the prompt classifier — no manual toggle. `brain.turn_needs_grounding(tool_groups)` + `_GROUNDING_TOOL_GROUPS={memory,web,documents,context}` (NOT `core` — write ≠ cite). Chat worker stashes `session._grounding_tool_groups` every turn (both auto + concrete branches, regardless of warm status); injects `render_research_mode_disciplines()` as a WIRE-ONLY preamble (`_inject_web_preamble_into_wire`) — NOT the system prompt (KV-safe, works on warm/local). Validator gate broadened from `_proj_active and _research_active` → `session._citation_discipline_active`. `research_mode` toggle kept as explicit force-on + keyword-mode fallback. Verified live: non-project "search web + cite" → classifier tools=['web'] → discipline injected → reply cited throughout → validator ran.

**v9.68.0 — inline chips (UI, frontend-only).** `[Quelle: file — "quote"]` → numbered `<sup>[n]</sup>` chips (was a book icon; `renderCitationPin`); per-message "Quellen" footer legend (`_buildCitationLegend`, ⚠ badge from `citation_validation.unverified_samples` matched by basename+excerpt); click popover gains "Im Dokument öffnen →" (`openCitationSource`: resolve path via chat refs → `API.getFilePreview` → right-panel viewer → `_highlightQuoteIn` string-match + `<mark class=citation-span>` + scroll) / "Quelle öffnen ↗" for web sources. Ships v1 string-match (spec §6); drawer-offset anchoring deferred; old msgs degrade (E5). +4 globals (baseline 1107→1111).

**KEY FACTS:**
- `background_call`/`gdpr_pick_model_for_background` `purpose=` must be in `_VALID_PURPOSES` (used `transform`). Custom purposes crash `resolve_active_tools`.
- `NodeFilter` is not in the eslint env → use `window.NodeFilter`.
- The validator (`validate_citations_in_response`) verifies quotes against FILES read this turn — web-source quotes show as unverified (expected; chips link those out).
- Citation parse already existed: `extractCitationsFromRaw` → `{file,locator,quote}[]`; `restoreCitationPins`; handles backtick-wrapped + no-"Quelle:"-prefix forms.

**Deferred refinements (not built, fine):** drawer-offset span anchoring (vs string-match); Studio MP3 viewer case (needs Audio Overview); Exa merge live-verify (no key). Out-of-order plans remain: Audio Overview, Join mode, Mind Map, Video/Audio ingest, Live mic.
