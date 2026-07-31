---
name: project_moa_feedback_loops
description: "v9.286.0 MoA-Delegation zwei Rückkopplungsschleifen — Proposer-Refinement (Planner nennt schwache Ansätze→gezielt 1× nachbessern) + Executor-Post-Verify (Planner auditet Antwort, interaktiv Re-Round bis 2×, Goal-continue-Maschinerie wiederverwendet)"
metadata: 
  node_type: memory
  type: project
  originSessionId: feb3943e-f758-4620-aaae-45e2881eaad5
---

**v9.286.0** — zwei Feedback-Loops für die Plan-Delegation (`delegate`-Modus des Experten-Gremiums), auf User-Wunsch. Baut auf [[project_moa_plan_delegation]] + [[project_moa_virtual_model]].

**(A) Proposer-Refinement** (automatisch, Planner↔Proposer):
- `_MOA_PLANNER_SYSTEM` verlangt bei insufficient jetzt `PLAN_VERDICT: insufficient [A, C] — <umsetzbare Begründung>`; `_run_moa_planner` parst `weak_letters`+`verdict_reason` (Regex, fail-open ready).
- `_refine_moa_drafts` befragt GENAU die benannten Proposer 1× erneut (positional A=drafts[0]; `_MOA_REF_REFINE_SYSTEM` = alter Entwurf + Grund → besserer Ansatz), dann EIN Re-Planning. Leere `[]` = Anfrage selbst blockt → kein Refine. Gescheiterte Nachbesserung behält Original-Draft. Genau 1 Runde (Kosten-Cap).
- Karte: `moa_reference` mit `refine`-Flag ("· Nachbesserung").

**(B) Executor-Post-Verifikation** (NUR interaktiv, Planner auditet Executor):
- `_run_executor_verify` = 1 bg-call an Planner VOR `add_message` (`_MOA_VERIFY_SYSTEM`, `VERIFY_VERDICT: ok|insufficient — <fix>`, fail-open ok). Verdikt in `msg_metadata.auto_route.moa.verify` gefaltet → persistiert (kein separater DB-Update nötig, weil vor add_message).
- Re-Round: selber Executor erneut mit Fix als styled continuation-User-Msg, **Goal-continue-Maschinerie wiederverwendet** (`_partial_*.clear()` + `continue`). `_moa_delegate_state` reused → Plan+Pin unverändert, kein Re-Fan-out/Re-Plan (beide unter `if _moa_delegate_state is None`).
- Cap `moa.executor_verify_max_rounds` (Default 2, 0=nur Audit). Bei round==max kein weiterer Audit (Cap stoppt Audit UND Re-Round).
- Läuft NICHT wenn `_goal_active` (zwei Judge-Loops würden doppelt zählen), nie bei `_se`, nie nicht-interaktiv.
- Karte `moa_verify`; SSE `moa_verify_continue` schließt Antwort-Blase (chat_send.js + panels_termchat.js).

**INVARIANT (User-Spec):** User-Plan-Review geht IMMER an den Planner, NIE an die Proposer ("die haben ihre Arbeit erledigt"). Refinement ist der EINZIGE Planner→Proposer-Kanal, und rein automatisch.

**LIVE E2E verifiziert** (mehrteilige Web-Recherche, deepseek-v4-pro Executor): 2 Re-Rounds bis Cap 2/2, Verdikte + Instruktionen korrekt, Zwischen-Assistant-Msgs tragen verify-Metadata (assistant#15 round0, #19 round1). Einfache Recherche: verdict=ok, kein Re-Round.

**Config-Save:** partieller `{moa:{executor_verify_max_rounds:9}}`-POST clampt auf 5, wipet NICHT die restliche MoA-Config (merge known keys). Settings-Feld "Ergebnis-Prüfung: max. Nachbesserungen".

Gotcha: MoA-Gating ist stochastisch (Klassifikator) — dieselbe Museums-Query mal `research`→delegate, mal `fast`/`analysis`→kein MoA. Für E2E eine eindeutig research-geformte, mehrteilige, web-fordernde Query nehmen.
