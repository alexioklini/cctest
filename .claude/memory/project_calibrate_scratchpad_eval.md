---
name: project_calibrate_scratchpad_eval
description: "v9.298.0: dritter Spickzettel `calibrate` (getrimmtes metacognitiveMonitoring aus waldzellai/model-enhancement-servers) — Eval: Refusal-Bucket 0.61→0.83, Refusal-Achse 0.07→0.67; ABER M2-Synthese-Regression + Timeouts → Refusal-Spezialist, kein Allround-Default"
metadata: 
  node_type: memory
  type: project
  originSessionId: 4659f1cf-369a-4265-a633-3bd519cca8da
---

v9.298.0 (2026-07-09/10): drittes Scratchpad-Werkzeug `calibrate` — Idee aus der Analyse von waldzellai/model-enhancement-servers (metacognitiveMonitoring, stark getrimmt). Statt "denk erst nach" ([[project_scratchpad_think_tools]]) erzwingt es "prüfe, ob du es überhaupt weißt": facts (gelesen, mit Quelle) / inferences / speculation / gaps / confidence / recommendation ∈ answer|answer_with_caveats|refuse; die Antwort muss der eigenen recommendation folgen. Schema BEWUSST flach (String-Arrays, 5 Pflichtfelder — gemma-tauglich, kein Nested wie das Original); volle prozedurale "You should:"-Liste in der Beschreibung (Beschreibungs-Falle gilt). Einzige echte Logik: deterministischer Konsistenz-Check im Code (answer bei leeren facts → zurückgespiegelt).

**Sichtbarkeit opt-in-only (das Integrations-Muster):** statisch deferred via config.json tool_settings (interactive=deferred) → in KEINEM bestehenden Modus in-prompt, alle Wire-Shapes/KV-Prefixe byte-identisch (Warmup unberührt; Eval-Baselines poolbar). Nur scratchpad_mode="calibrate" hebt es per ctx.undefer_tools in-prompt (undefer schlägt classifier-defer_extra), excludet think+sequential_thinking, injiziert FORCE_CALIBRATE_PROMPT wire-only. NICHT in _TOOL_GATING_NEVER_STRIP_TOOLS (undefer reicht). auto wählt calibrate NICHT (Klassifikator hat kein Refusal-Signal).

**Eval (gemma-12B, 5 Reps, Mistral-Judge, Opus-Gold-Reuse; Arme off/simple/calibrate, off+simple für M1/M2/F1 aus den ft-* Läufen v9.295.0 gepoolt — poolbar WEIL Wire byte-identisch blieb):**
- **Refusal-Bucket (F1/F2/F3): calibrate 0.827 vs off 0.610 vs simple 0.583; Refusal-Achse 0.67 vs 0.07/0.10.** F2 0.73→0.94, F3 0.63→0.96 (Refusal-Achse 0.8-0.9!), F1 0.47→0.58 (schwächer, weil Doku AML tangential erwähnt → gemma findet "facts" und cavated statt zu verweigern).
- **Multi-Doc: M1 ok (0.86 vs simple 0.90), M2 REGRESSION**: 2/5 Timeouts (480s-Stall, gemma hängt in der calibrate-Disziplin bei breiter Synthese) + Restwerte 0.75/0.98/0.12 (⌀0.62 vs simple 0.93).
- **Fazit: calibrate ist der Refusal-SPEZIALIST, kein Allround-Ersatz für simple.** simple bleibt die auto-Wahl für Synthese.

**Smoke-Beleg des Zielverhaltens:** gemma füllt alle Felder korrekt, Antwort BEGINNT mit der Lücke statt zu raten; speculation-Einträge tauchen nicht als Fakt in der Antwort auf.

**GESHIPPT** (User-Entscheidung, Commit 04dcc6c9): UI-Dropdown 'Kalibriert (immer)' im Models-Tab, Skill 02-tools + SKILL.md 1.166.0, kuratierter Eintrag (admin). auto bleibt ohne calibrate. Der pre-commit-Hook staged config.example.json mit → der deferred-Default reist für Fresh-Installs mit (config.json selbst ist gitignored; Boot-Seed füllt nur purposes, keine states).

**Phase B — classifier-getriebenes Routing GEPRÜFT und VERWORFEN (v9.299.0, Commit 82745cbd):** User-Frage "Kalibrierung über Prompt-Klassifikation steuern?". (1) Bestands-Vokabular trennt nicht (F-Fragen klassifizieren wie R/P/C). (2) Prototyp-Feld `answer_scope ∈ specific|broad` trennt in der GEFAHR-Richtung perfekt (3/3 Synthese=broad; Fehlschüsse F2/C2 kosten nur Upside). (3) ABER Scope-Eval (R1/P2/C3 × off/simple/calibrate × 5): calibrate VERLIERT auf beantwortbaren specific-Fragen gegen simple (0.768 vs 0.849 gepoolt; R1 0.71 vs 0.89, Precision 0.56 vs 0.86 — Kalibrier-Disziplin macht gemma auf Beantwortbares ZÖGERLICH; keine Timeouts) → specific→calibrate verschlechtert die Mehrheit für die Refusal-Minderheit. ENTSCHEID: auto unverändert, calibrate bleibt Fest-Modus; answer_scope REVERTIERT (unkonsumiertes Feld auf decode-bound Klassifikator = Latenz; reasoning-Feld-Lektion; Re-Add-Kommentar in classify_task_structured). BLEIBT: POST /v1/admin/classify (admin-Probe des Produktions-Klassifikators, Batch bis 50 — Diskriminations-Messung VOR jeder Routing-Verdrahtung). OFFEN (nur auf User-Wunsch): per-PROJEKT-Strict-Refusal-Knopf (Compliance-Projekte: falsch-selbstbewusste Antwort teurer als −0.08 auf Beantwortbares) — answer_scope-Re-Add wäre 3 kleine Edits (Schema+Prompt+Parse).

Eval-Infra: Arm-Toggle live via POST /v1/models/config (Save speichert verbatim, keine Feld-Whitelist serverseitig — Mapping der Legacy-Booleans passiert clientseitig). Timeout-Samples (brain.json error) als gedroppte Proben behandeln, nie als 0 werten. Treiber: scratchpad run_calibrate_eval.sh (nohup), Ergebnisse eval/results/*cal-{off,simple,calib}-r*.
