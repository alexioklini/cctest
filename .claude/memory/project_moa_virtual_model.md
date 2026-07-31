---
name: project_moa_virtual_model
description: "v9.268.0 SHIPPED: 🧬 MoA (Smart) als klassifikationsgesteuertes virtuelles Modell — Revision der MoA-Ablehnung vom 2026-06-27"
metadata: 
  node_type: memory
  type: project
  originSessionId: c5dc3027-52b8-49cc-9e51-8a7dd527e89d
---

2026-07-02 (v9.268.0, commit ed52c1a8): **MoA als virtuelles Modell GEBAUT** —
die Wiederaufnahme von [[project_moa_rejected]] mit dem Fix für dessen
Kernproblem: die Prompt-Klassifikation GATED jetzt den Fan-out (Default-Gate
research/analysis/reporting/creative/orchestration; coding/math/fast/agentic
übersprungen, wo MoA im Eval verlor) und wählt Referenzen aus einem
konfigurierten Pool per `_bench_rank_key`; Aggregator = der Auto-Route-Pick.

Architektur-Entscheidungen (User-approved):
- `moa` = Pseudo-Direktive wie auto-cloud (KEIN models-Registry-Eintrag);
  `_parse_auto_directive` → (True, "cloud").
- Referenzen sehen die GANZE PII-bereinigte Wire-History (Hermes-Verhalten),
  tool-los, `background_call` parallel (contextvars-Pattern aus deep_research),
  `cost_purpose="moa_reference"`, Replies bewusst NICHT de-anonymisiert
  (Pseudonym-Raum-Regel: sonst Leak an Cloud-Aggregator).
- Drafts wire-only als Suffix an die letzte User-Message (`_append_to_wire_user`,
  `_build_moa_suffix` aus eval/moa_eval.py adaptiert); Goal-Iterationen 2+
  reusen den Fan-out (`_moa_cache`, `_goal_web_cache`-Muster).
- **Cache-Freeze BLEIBT** (wichtig, User-Einwand deckte meinen Denkfehler auf):
  Klassifikator läuft auf Folge-Turns via `resolve_task_analysis` NUR für
  Gate+Referenzwahl, Modell+Tools bleiben gepinnt → Cached-Token-Rabatt intakt
  (live verifiziert: cache_read>0 auf Runden 2+).
- Fortschritt = synthetische Tool-Cards `kind="moa_reference"`
  (`_emit_synthetic_tool_event`); Plan+Ground-Truth in `auto_route.moa`.
- Config `config.json → moa` (enabled/reference_pool/max_references/
  reference_max_tokens=600/reference_timeout_s=60/reference_input_max_chars/
  gate_task_types), Save-Branch validiert Gate STRIKT gegen `_TASK_TYPES`-Enum
  (Tippfehler = stilles Aus wäre unentdeckbar); `/v1/status → moa_enabled`
  gated den Dropdown-Eintrag. Scheduler lehnt moa ab (Fire-Time coerct → auto).

**v9.269.0** (00e9e2e9): GUI = **Matrix „Modell × Aufgabentyp"**
(`moa.task_pools {task_type: [models]}`) ersetzt funktional Gate+Pool in einer
Struktur — leere Spalte = kein Fan-out für den Typ; bei mehreren klassifizierten
task_types gewinnt der erste mit Pool. Legacy gate_task_types+reference_pool
bleiben Fallback; Settings-Matrix seedet beim ersten Öffnen aus Pool×Gate.
Live-Config: research/analysis/reporting/creative/orchestration je mit
mistral-medium-3.5/small/deepseeks bestückt, coding/math/fast/agentic leer.

**v9.269.1** (2a1b42fa): 🧬-Karten waren im Chat UNSICHTBAR — renderTurnBody
steckt ALLE synthetischen Rows in den Datenschutz-Collapsible, dessen
privacyBlockHtml bei 0 Anon/De-Anon-Zählern `return ''` macht → MoA-only-Turns
komplett verschluckt (+ Body hängt am default-aus showGdprDetails). Fix:
moa_reference → kind:'tool' im bodyItems-Build = inline wie echte Tool-Karten.
**GOTCHA für künftige synthetic-Kinds**: neuer `_emit_synthetic_tool_event`-kind
braucht IMMER einen eigenen Branch in chat_render.js renderTurnBody, sonst
landet er im zähler-gegateten Datenschutz-Block und ist unsichtbar.

**v9.270.0** (061f372f): Drafts SICHTBAR — result.draft auf der Done-Karte
persistiert (Pseudonym-Raum, nie session.messages); Chat-Karte = aufklappbares
<details> mit Entwurfstext; rechtes Panel Aktivität-Tab listet moa_reference-
Rows (GDPR-Kinds bleiben chat-only). Dabei 2 Vorbestandsbugs gefixt:
panels_background `_toolEntriesFromMetadata`-Guard zählte synthetische Rows
mit → Chats mit GDPR-/MoA-Karten verloren nach Reload die echte Tool-Liste im
Aktivität-Tab; `_collectActivityEntries` sync-ODER-metadata → jetzt concat.

**v9.271.0** (07d32248): (A) HYBRID-BEITRAGS-MODUS `moa.task_modes
{task_type: answer|plan}` (User-Idee) — Default research/orchestration/agentic
= 'plan': Referenzen liefern NUR die Herangehensweise (Antworten im
Systemprompt verboten → keine Fakten-Injektion), Aggregator kombiniert+führt
mit Tools aus; Synthese/Judgment behalten answer (eval-belegt). Plan-Suffix
'pick the best combination … EXECUTE it yourself'. (B) User-facing UMBENANNT
in **'Experten-Gremium'** (Composer/Karten 'Experte'+GREMIUM-Badge/Settings);
interne IDs (moa, moa_reference, moa_enabled) bewusst stabil. (C) STUB-FILTER:
CLIProxyAPI-Fehlertext 'No response was returned…' (deepseek-v4-pro liefert
den öfter als 73-Zeichen-Reply!) zählt als failed-Referenz, nicht als Draft.
Settings-Matrix hat 2. Kopfzeile 'Beitrags-Modus' (Dropdown je Spalte).

**PRODUKTIONS-EVAL 2026-07-03** (eval/moa_prod_eval.py, 15 Fragen × auto/
gremium × 3 Reps über die Live-API, 66 Blind-Scores in moa_judge_scores.json):
gremium 0.822 vs auto 0.629 overall (+0.193 — formal NOISE, auto-Spread ±0.31);
auf den 7 echten Fan-out-Fragen +0.234 mit **7/7 Frage-Siegen** (Sign-Test
p≈0.008) und **Varianz-KollAPS** (Spread 0.099 vs 0.310; Katastrophen-Antworten
<0.3: 2 vs 12 — mistral-small allein refused/verrechnet sich oft, die Drafts
von mistral-medium stabilisieren). Kosten 2,1×, Latenz GLEICH (12.6 vs 14.7s —
Drafts reduzieren Tool-Flailing des Aggregators). Kategorien: synthesis
0.60→0.79, judgment 0.70→0.79, gated-in-Checkables (coding/logic→analysis
klassifiziert!) 0.44→0.97 bzw. 0.67→1.00. Gate arbeitete: 24 Fan-outs/21 out;
Plan-Modus feuerte NIE (FERMI→math; kein Live-Web-Research im Set — ungetestet).
Aggregator war in allen 45 Turns mistral-small (Benchmark-Ranking). Kernaussage:
Gremium = Stabilisator gegen mistral-smalls Ausreißer, kein Peak-Lift-Beweis.

**EVAL V2 2026-07-03** (9.271.1, e99793d4; 18 Fragen inkl. 3 Web-Recherchen,
108 Turns): Fermi-Fix (Klassifikator-Steering: Schätzen=analysis, nicht math)
belegt — reasoning_open 0.37→0.66 (auto refused FERMI2 3/3!). PLAN-MODUS
erstmals live belegt: web_research 0.71→0.86; WEB2-Preisrecherche 0.45→0.88±0.00
(Referenz-PLAN führte Aggregator zur richtigen Preisseite — auto fand sie 2/3
nicht). Fan-out-Fragen +0.135 (7/11 Siege); gated-out-Pfade beider Arme gleich
(0.68/0.65 → v1-Gated-out-Gap war Varianz). Gesamt +0.071 (formal Noise, Spread
±0.18). WICHTIGSTER QUER-BEFUND für nächsten Schritt: mistral-smalls
REFUSAL-Krankheit ('keine Quellen → verweigere') + FABRIZIERTE Zitate
([Quelle: erfundene.md — "…"]) dominieren die Fehler BEIDER Arme →
Zitat-Disziplin-Fix ist der größte Hebel (User will das als nächstes angehen).

**POLICIES-EVAL 2026-07-03** (KG-Real-Policies, eval/run.py, Opus-Gold
wiederverwendet, mistral-Judge, moa×3 vs auto×3 Reps): **MoA hilft bei
grounded Policy-QA NICHT** — moa 0.687±0.010 vs auto 0.733±0.045 (−0.046 ≈
Spread → LOSS-Grenze). Refusal-Bucket sogar klar schlechter (0.381 vs 0.523):
tool-lose Referenzen füttern Allgemeinwissen, das bei expected-refuse-Fragen
genau falsch ist. Nur retrieval-Bucket leicht besser (0.856 vs 0.821).
KONSEQUENZ-KANDIDAT: Gate-Verfeinerung — kein Fan-out, wenn classifier
tools ⊆ {memory} (interne Dokument-Fragen), Gremium nur für Web-/
Multi-Source-Synthese. BEIFANG 1: Klassifikator-Bug 9.272.1 (Tool-Label-Echo
task_types=['memory'] → Analyse leer, s. CHANGELOG). BEIFANG 2:
9.272.0-Disziplin-Regression im Refusal-Bucket (s.
[[project_citation_discipline_two_lanes]]). Harness-Patch: eval/run.py
sendet Modell jetzt pro Chat-Call (Direktiven!); Hintergrund-Bash hat
~600s-Cap → lange Eval-Serien als EIN Lauf pro Background-Task ketten.

Bewusste v1-Grenzen: Referenzen sehen nur Text (Bilder aggregator-only);
Quota-Pre-flight nur für Aggregator; erster Token wartet auf langsamste
Referenz. Erwartung: Qualitäts-Lift erst bei diverserem Pool (Eval-Befund
bleibt: mistral-Flotte allein bringt keinen messbaren Lift — Feature ist
Infrastruktur + Second-Opinion-UX).
