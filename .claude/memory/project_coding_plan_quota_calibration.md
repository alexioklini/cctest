---
name: project_coding_plan_quota_calibration
description: "Coding-Plan-Quota-Schätzung: Token- UND Request-Metrik sind über Tage instabil — Anbieter-Fensterphase/Dashboard-Rundung dominieren; Kalibrieren ist die Antwort, 5h-Fenster bleiben grob"
metadata: 
  node_type: memory
  type: project
  originSessionId: 30c08f30-07d0-4d63-afc4-72e84c4e5388
---

2026-07-05: Nach dem Eval-Tag (viele kleine Calls) wichen die Plan-Schätzungen
(9.283.x, gewichtete Token-Summen vs `limit_tokens`) stark von den echten
Dashboards ab — in BEIDE Richtungen (glm 5h: wir 18% vs echt 42%; kimi 5h:
wir 15% vs echt 5%; mistral Monat: wir 18% vs echt 8%).

**Getestet und VERWORFEN: Requests-Metrik statt Tokens.** Zwei-Tage-Gegenprobe
(gestrige Kalibrierfenster vs heutige Dashboard-Werte): KEINE Metrik ist
stabil — glm real stieg bei sinkendem Verkehr (Requests UND Tokens), kimi
real fiel bei steigendem. Auch Medium-only-Zählung (Mistral) widerlegt
(medium verdoppelt, Dashboard statisch 8%). Dominante Fehlerquellen sind
ANBIETER-seitig: 5h-Fenster-Phasenlage, Dashboard-Trägheit/Integer-Rundung.
Ein Metrik-Wechsel wäre Scheinpräzision — nicht bauen ohne neue Evidenz.

**Antwort = vorhandener Kalibrier-Mechanismus** (`POST /v1/plans/calibrate
{plan_id, window_kind, dashboard_pct}` → Limit-Refit aus Ist-Fensternutzung,
kein Restart nötig). Am 05.07. alle 5 Fenster nachgezogen (glm 5h-Limit
12,5M→6,0M; mistral Monat 7,7M→17,5M — Kapazität war stark unterschätzt).

Merkregeln:
- **5h-Fenster = grobe Schätzer**, gelegentlich nachkalibrieren (besonders
  nach untypischen Nutzungstagen); **weekly/monthly sind die verlässlichen
  Anker** (lagen nur 1-4pp daneben).
- Kalibrierung driftet mit dem Nutzungs-MIX (viele kleine Calls vs wenige
  große) — nach Eval-Tagen prüfen.
- Mistral: die Eval-Judge-Calls (eval/judge_mistral.py + eval/run.py
  provider='mistral') gehen DIREKT an api.mistral.ai, am Brain-Ledger
  VORBEI — Vibe-Quota wird real stärker belastet als cost_log weiß.
- BEIFANG-Bug gefixt (9.284.x): handlers/admin_costs._list_cost prüfte das
  rohe flat_plan-Feld statt brain.model_is_flat_plan → coding_plan-verlinkte
  Modelle (glm/kimi seit 9.283.0) zeigten API-Listenpreis $0 im Breakdown.

Siehe [[project_flat_plan_and_refusal_eval]] (flat_plan-Abrechnung) +
[[project_moa_plan_delegation]] (der Eval-Tag, der die Drift auslöste).
