---
name: project_scratchpad_think_tools
description: "v9.295.0: Spickzettel-Werkzeuge (think + sequential_thinking) + per-Modell scratchpad_mode mit Auto-Wahl per Klassifikator; Eval-Ergebnisse + die Beschreibungs-Falle"
metadata: 
  node_type: memory
  type: project
  originSessionId: f9c51588-c7a1-499e-b1a1-afb7a2d6a090
---

v9.295.0 baute zwei No-op-Denk-Werkzeuge ("Spickzettel") + einen per-Modell-Modus, der schwache lokale Modelle (gemma-12B) dazu bringt, VOR der Antwort einen Gedanken abzulegen, der über Tool-Runden persistiert (anders als natives Reasoning, das pro Runde verworfen wird — belegt in engine/llm_loop.py: rr.reasoning geht NIE in loop_messages).

**Zwei Werkzeuge** (4-Site-registriert, engine/tools/misc_tools.py):
- `think` (Anzeige "Spickzettel") = minimales Anthropic-think-Tool, 1 Feld `thought`.
- `sequential_thinking` (Anzeige "Erweiterter Spickzettel") = voller Original-MCP-Mechanismus (4 Pflicht- + 5 opt. Felder, Buchführung). State PRO-REQUEST in RequestContext._dynamic["_seqthink_state"] statt prozess-global (das Original leakt zwischen Sessions — bewusst geändert für Multi-User).

**DIE BESCHREIBUNGS-FALLE (wichtigste Lektion):** sequential_thinking zerlegt Aufgaben NUR, wenn die Tool-Beschreibung die 11-Punkt-"You should:"-Liste des Originals wortgetreu enthält. Meine gekürzte Version (Prompt-Bloat-Instinkt) machte gemma das Tool 1× aufrufen (keine Zerlegung); die volle Original-Beschreibung → 2-3× (nummerierte Schritte). Die Doku-Behauptung "bricht komplexe Aufgaben runter" ist ein MODELL-Verhalten, kein Werkzeug-Feature — das Werkzeug ist ein No-op. NICHT kürzen trotz [[feedback_prompt_bloat_regression]].

**Floor:** beide Werkzeuge in `_TOOL_GATING_NEVER_STRIP_TOOLS` (brain.py) — sonst deferrt der Klassifikator die neue `thinking`-Gruppe raus (needed_groups kennt sie nie) und das Modell sieht das Tool nie in-prompt.

**Steuerung:** per-Modell `scratchpad_mode` ∈ off|simple|sequential|auto (Models-Tab-Dropdown, ersetzt kurzlebige Booleans force_think/force_sequential_thinking → mappen auf simple/sequential). Aufforderung (FORCE_THINK_PROMPT / FORCE_SEQUENTIAL_THINKING_PROMPT) wire-only auf letzter User-Nachricht via _append_to_wire_user — KV-stabil wie Caveman, geht NICHT in compute_prefix_id (nur User-Msg geändert). Bei `auto`: brain.resolve_scratchpad_choice(analysis) aus task_types+complexity: research/analysis/coding/math + medium/high → simple; high + ≥2 Reasoning-Typen → sequential; low/fast/reporting → off. Der Klassifikator gibt KEIN refusal-Signal → Refusal-Fragen nicht direkt targetbar.

**Eval (gemma-12B, 5 Reps × 3 Policy-Fragen, Mistral-Judge, Opus-Gold-Reuse):**
kein Spickzettel 0.524 · einfach 0.689 (+0.165) · erweitert 0.647 (+0.123). Der erweiterte ist NICHT besser im Mittel, nur gleichmäßiger (stdev 0.25 vs 0.34) bei ~2.8× Zeit. Multi-Doc-Synthese profitiert stark (0.24→0.90); Refusal-Fragen (F1) werden vom einfachen leicht GESCHÄDIGT. stdev ~0.34 → ≥5 Reps nötig ([[feedback_eval_single_run_noise]]), gemma hochstochastisch (dieselbe Frage 0.12–0.98).

**Kosten:** lokal nur Zeit (einfach 2×, erweitert 2.8×). Cloud + Token (~2× mit Prompt-Cache; Cache dämpft nur INPUT, Output-Aufschlag voll bepreist). → default `auto` für gemma, `off` für Cloud.

**Test-Infra-Lektionen:** (1) lange Evals via `nohup` fahren — Bash-Tool-Hintergrundtasks werden nach ~5 Min gekillt (Wrapper-Shell-Lebenszeit); nohup entkoppelt. (2) eval/config.json Judge war auf totes `CLIProxyAPI/mistral-medium-3.5` → gefixt auf `mistral-medium-3.5` (mistral-direct), s. [[project_cliproxyapi_removed_direct_providers]]. (3) brain_chat gibt Tool-Events in `res["_tool_events"]` (event=tool_result, data.name), NICHT res["tools"].
