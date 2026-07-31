---
name: citation-re-round-replaced-by-reload-stable-warning-nudge-ui-surfaced
description: "2026-05-16 (v8.40.0) — removed citation re-round, replaced with persistent warning block appended to reply; surfaced sidecar empty-round nudges as live spinner label + persisted reply tail hint."
metadata: 
  node_type: memory
  type: project
  originSessionId: 2a9491cc-644e-4089-8fa5-2ddd12d061b1
---

**Shipped 2026-05-16** in `handlers/chat.py` + `web/js/chat.js`. Two related fixes after the v8.39.0 sidecar nudge fix exposed a separate citation-reround regression.

**The trigger**: chat `aa7c90d7` (CLIProxyAPI/mistral-small-latest, F1_geldwaesche). Model produced a clean ~700c refusal (`Nicht spezifiziert`, `keine spezifische Richtlinie`). Citation validator counted 2 uncited claims out of 2 → `citation_reround_needed` returned true → synchronous re-round fired → model rewrote the refusal into a 165c reply with one fabricated citation pointing at an irrelevant `Löschkonzept` PDF whose only "Geldwäsche" mention was a buried list item. The reround **made the answer worse, not better**.

**Why re-round can't be fixed**:
- The validator can't distinguish 'this is a refusal because the corpus has no info' from 'this answer has unsourced claims that should be cited' — both look like 'high uncited ratio'.
- The reround prompt has an escape hatch ('lösche die Behauptung'), but in practice the model produces a plausible-looking fake citation instead — the surrounding incentive structure (model thinks it's being graded on having citations) overrides the hatch.
- Hard-coding refusal-phrase heuristics would be brittle across languages and project contexts.

**The replacement** (single decision: remove reround, never call it again):
1. `engine.citation_reround_needed(_val)` is still imported (same threshold: >30% uncited claims OR ≥2 unverified quotes) — used only to decide whether to append a warning.
2. When triggered, a markdown block is appended to the reply text itself:
   ```
   ---

   > ⚠️ **Hinweis zur Quellentreue**: N von M Behauptungen ohne Quellenangabe. Diese Antwort wurde vom Citation-Validator markiert — bitte einzelne Aussagen vor Weiterverwendung gegenprüfen.
   ```
3. Reload-stable because it lives in the assistant message `content`, not in metadata. `msg_metadata.citation_validation.warning_appended = true` for the inspector.
4. `engine.run_citation_reround` + `engine.build_citation_reround_feedback` stay defined in brain.py (zero callers). Frontend `citation_reround_start` / `citation_reround_done` SSE handlers stay too — no-ops because the events are never emitted. Cleanup deferred — surgical change.

**Nudge surfacing** (companion fix in same release):
The sidecar's `empty_round_nudge` SSE event (added v8.39.0) was forwarded to the client but unhandled — invisible during the turn, invisible after reload. Now:
- `build_chat_event_callback` carries a `nudge_count: [0]` (one-element list = mutable in closure) in its state dict.
- The `empty_round_nudge` branch reads `data.attempt` (1..3) and takes max — dedupes if the same event arrives twice.
- The worker writes `msg_metadata.nudge_count = N` when N > 0.
- The reply gets a reload-stable hint appended (`> ℹ️ **Hinweis**: Das Modell hat N Mal neu angesetzt, bevor eine Antwort kam.`) — skipped when the reply is the give-up text since it already says it.
- `web/js/chat.js` gets an `empty_round_nudge` SSE handler that swaps the spinner label to `Modell wird neu angestoßen (N/3)…` and forces `spinnerBar.active` on.
- The existing `text_delta` handler clears that label as soon as real text starts streaming (string-prefix match — surgical, no new state).

**How to apply:**
- Per-claim citation discipline is now a *signal*, not an enforced rewrite. The model produces its answer once, the user sees both the answer and a quality marker.
- If a future feature needs to enforce citations, do it at retrieval time (refuse to surface uncited drawers) rather than at output time (refuse to surface uncited answers).
- See [[project_eval_citation_reround_phase2]] (the original reround design — now historical) and [[feedback_reround_uncited_only.md]] (already deprecated the unverified-quote trigger; this release deprecates the rest).
- See [[project_sidecar_eos_token_strip]] for the upstream nudge mechanism in the sidecar.
