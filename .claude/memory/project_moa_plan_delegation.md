---
name: project_moa_plan_delegation
description: "v9.284.0 SHIPPED: Plan-Delegation (Planner/Executor-Split) im Experten-Gremium — Qualitäts-Parität bei −69% Listenpreis; Eval + Gotchas"
metadata: 
  node_type: memory
  type: project
  originSessionId: 30c08f30-07d0-4d63-afc4-72e84c4e5388
---

2026-07-04/05 (v9.284.0, Commits 8206a06d + 0964505e): **Plan-Delegation GEBAUT
und eval-belegt** — dritter Beitrags-Modus `delegate` in der Gremium-Matrix
(User-Idee: "glm 5.2 macht den plan, umsetzen aber ein billigeres modell").
Erweiterung von [[project_moa_virtual_model]].

Mechanik: Referenzen liefern Ansätze → PLANNER (task_aggregators-Eintrag /
Auto-Pick, live glm-5.2) konsolidiert per EINEM kurzen `background_call`
(`_run_moa_planner`, cost_purpose=`moa_planner`, Karte kind=`moa_planner`) →
Plan wird KLASSIFIZIERT (`resolve_moa_executor` — Plan-Klassifikation verrät
die wahre Aufgabenform besser als der Prompt) → Turn läuft auf dem günstigsten
fähigen Executor (Band-Ranking, Planner ausgeschlossen). Executor wird auf der
Session GEPINNT (`_moa_executor_model`/`_moa_planner_model`, Send-Handler
honoriert in Frozen- UND Fresh-Branch); Cache-Freeze WANDERT auf cache-priced
Executor. Delegate-Pläne geben `plan.aggregator=None` zurück (der fixe
Orchestrator ist dort der Planner, nie das Session-Modell).

Nicht-offensichtliche Gotchas:
- **Mid-Worker-Modellwechsel** braucht Rebuild des modellabhängigen Turn-States:
  dafür wurden inf_params- und Prefix-Build in Closures gehoben
  (`_inf_params_for`/`_build_prefix_for` in handlers/chat.py) — byte-identische
  Logik, wiederverwendbar nach dem Switch. `_current_model` im Request-Context
  mitziehen, `auto_route` re-emitten (Spinner).
- **Executor-Exclude NUR Planner-Identitäten**, NICHT das aktuelle
  Session-Modell: mit fixem Orchestrator sitzt die Session auf dem Auto-Pick
  (oft schon der billigste fähige Executor) — ihn auszuschließen erzwang im
  ersten Live-Test einen sinnlosen Switch flash→pro. Pick==Session-Modell =
  Pin ohne Switch.
- Fail-safe-Kette: Planner-Fehler → Drafts als plan-Suffix aufs aktuelle
  Modell; Klassifikations-Fehler → gate_hit/complexity des Prompts; leerer
  Pool → Modell bleibt. Nie ein Turn-Fehler.

**EVAL 2026-07-05** (eval/moa_prod_eval.py, neu: `--suffix` pro Server-Config +
`cost_list`-Erfassung — flat_plan-Modelle billen $0, Listenpreis ist die
ehrliche Kosten-Achse; 18 Fragen × 3 Reps je Arm, Opus-Blind-Judge via 4
parallele Subagents in den geteilten Score-Cache): **Qualitäts-PARITÄT bei
−69% Kosten.** Overall delegate 0.911±0.055 vs planmode 0.936±0.026 (Δ im
Rauschen). Auf den 4 delegate-gefeuerten Fragen: alle Deltas im Spread; der
einzige Ausreißer (WEB2 rep1, 0.19) war ein **DeepSeek-HTTP-520
Provider-Artefakt** mid-turn ('no done event'), kein Delegations-Versagen.
Kosten auf den gefeuerten Fragen: $0.031 vs $0.100 Listenpreis/Turn (WEB3 5×
billiger), Latenz +18% (Planner-Hop). Executor-Picks: deepseek-v4-pro/flash;
Planner immer glm-5.2. Live-Config: research/orchestration/agentic=delegate.

**POLICIES-EVAL 2026-07-05 → 9.284.1 GATE-FIX** (Commit 8b2d6ee6): Auf
grounded Policy-QA (KG-Real-Policies, 15 Fragen × 3 Reps, Mistral-Judge,
Opus-Gold v9981-rep3 reused) VERLIERT Delegation klar: delegate 0.774±0.025
vs planmode 0.854±0.007. Treiber: refusal −0.231 (F1 konstant 0.17-0.25 —
der deepseek-flash-Executor ECHOT die Plan-Struktur in die Antwort:
'## Phase 1: Dokumentensuche' + 'Lassen Sie mich…'-Runden-Narration → die
9.281.0-Not-found-first-Form ist strukturell verletzt, Inhalt war ehrlich),
multi_doc −0.120, precision −0.104 (13-15/15 Fragen liefen auf flash statt
glm — Executor-Downgrade auf Synthese). FIX: `resolve_moa_plan` downgraded
delegate→plan wenn Klassifikator-tools ⊆ {memory} (der Konsequenz-Kandidat
aus dem 9.272-Policies-Eval, jetzt eval-belegt für die Delegations-Stufe).
Live verifiziert: [memory]→plan+glm; [web,memory]→delegate+flash. LESSON:
Delegation zahlt sich NUR aus, wo Tool-Runden die Kosten dominieren UND der
Plan die Intelligenz trägt (Web/Multi-Source); bei memory-grounded Synthese
ist das ANTWORT-Schreiben das Produkt → Executor-Downgrade schadet.

**RE-EVAL 2026-07-05 nach 9.284.2** (Web-Gate als Knob `delegate_requires_web`
default true, verallgemeinert auf 'kein web in tools'; + Anti-Plan-Echo-Zeile
im Delegate-Suffix; nur die 9 Regressions-Fragen refusal/multi_doc/precision
× 3 Reps): **0.833 ±0.030 vs planmode-Baseline 0.813 — Regression komplett
weg** (delegate-alt war 0.661 auf denselben Fragen). refusal 0.643 (Basis
0.558, alt 0.327), multi_doc 0.948≈0.962, precision 0.908≈0.920. Downgrade
live belegt: [memory]-Fragen laufen auf glm im plan-Modus, [web,*] delegiert
weiter.

**v9.285.0 PLAN-REVIEW** (a1932798): Interaktive Turns pausieren zwischen
Plan-Synthese und Executor-Lauf — SSE `moa_plan_review` + POST
/v1/chat/plan-review (approve mit editiertem Plan/Executor-Override |
clarify → Planner-Revision → neue Runde, max 5, Timeout 900s Auto-Freigabe).
Gate = body.interactive (Web+Terminal-Chat senden es SCHON IMMER, Server las
es nie — Evals/TUI/Scheduler nicht → nie Review). Nicht-interaktiv:
PLAN_VERDICT-Schlusszeile (ready|insufficient) = Planner-Selbstentscheid;
insufficient → plan-Suffix-Fallback. UI ohne neue Globals (addEventListener).
E2E live: clarify→Revision (3233→1293c)→approve mit Override.

**v9.289.2 INPUT-MIME-GATE** (Chat 17165661: Bild-Anhang → Executor deepseek
gewählt → deepseek nimmt keine Bildeingabe → Wire-Build scheitert): der
Delegate-Executor-Pick wählte REIN nach task_type/complexity, ohne die
MIME-Fähigkeit des Modells gegen die Anhänge zu prüfen. FIX: neue
`brain.model_supports_mimes(model, mimes)` matcht `raw_formats` (Modell-Config)
gegen die MIMEs, die der Turn als native Multimodal-Blöcke sendet;
`resolve_moa_executor` + `_run_plan_review_loop` filtern den Pool via
`require_mimes` auf Modelle, die ALLE akzeptieren. Kein Match → None → Turn
bleibt auf dem aktuellen Modell (hält die Blöcke ohnehin). MIMEs aus den
image_url-data:-Blöcken extrahiert (NICHT auf 'image' hartkodiert — User-Wunsch:
generisch aus modelconfig gegen Input-Files matchen); Disk-geroutete Anhänge
ausgenommen (Executor liest via read_document). Gleicher `_mime_matches`-
Mechanismus wie die Multimodal-Routing-Entscheidung im Send-Handler
(chat.py:6441).

Offen/Watch: (a) Referenz-Drafts von deepseek geben in plan-Prompts gern
"Ich kann keine Web-Recherche durchführen"-Kurzverweigerungen (kein Stub,
zählt als Draft) — Draft-Qualitätsfilter wäre der nächste Hebel. (b) Der
Plan-Echo-Fehlermodus (Executor übernimmt Plan-Überschriften in die Antwort)
könnte auch Web-Delegation leicht drücken — im Prod-Eval nicht sichtbar,
aber ein Suffix-Zusatz 'do not mirror the plan's structure or narrate your
steps' wäre ein billiger Kandidat für den nächsten Eval-Zyklus.
