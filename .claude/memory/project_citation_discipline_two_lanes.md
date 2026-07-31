---
name: project_citation_discipline_two_lanes
description: "v9.272.0: Zitat-Disziplin repariert — Zwei-Bahnen-REFUSAL (source-bound vs Allgemeinwissen) + Anti-Fabrikations-CITATION + deterministischer Klammer-Strip bei Zero-Retrieval"
metadata: 
  node_type: memory
  type: project
  originSessionId: c5dc3027-52b8-49cc-9e51-8a7dd527e89d
---

2026-07-03 (v9.272.0, c9c50313): Die zwei dominanten Fehlermuster aus dem
MoA-Produktions-Eval ([[project_moa_virtual_model]]) — REFUSAL-Krankheit
(„keine Quellen abrufbar → verweigere", selbst bei Lehrbuchfragen) und
FABRIZIERTE `[Quelle: …]`-Klammern (erfundene Dateien/RFC-Verbatims/
Statistiken) — behoben. WURZEL: die GROUNDED-ANSWER-Disziplin
(refusal/precision/citation, brain.py:862ff + config.json
research_mode_disciplines, dort lagen BYTE-IDENTISCHE Kopien der Defaults →
bei Default-Änderungen IMMER beide anheben!) feuert seit dem dynamischen
Trigger auf fast jedem Turn, und ihr REFUSAL-Text verbot Trainingswissen
kategorisch — geschrieben für Compliance, angewendet auf „Erkläre TLS 1.3".

FIX Ebene 1 (Prompt): REFUSAL = zwei Bahnen — (a) source-bound streng
evidenzpflichtig, (b) Allgemeinwissen normal beantworten OHNE Klammern +
einmaliger Hinweis „beruht auf allgemeinem Fachwissen"; nie die Aufgabe
verweigern, wenn (b) sie beantworten kann. CITATION beginnt mit
Anti-Fabrikations-Hardline (nichts abgerufen → keine Klammern; erfundene
Quelle schlimmer als unzitierter Satz; nie Drafts/Konversation zitieren).

FIX Ebene 2 (deterministisch): `brain.strip_fabricated_citations` —
Worker strippt alle Klammern, wenn Turn nachweislich nichts abrief
(kein Retrieval-Tool-Call + keine web_sources + verified==0 + brackets>0);
ehrliche Notiz angehängt, metadata.citation_validation.fabricated_stripped.
KEIN LLM/Re-Round — der v8.40.0-Re-Round wurde einst entfernt, WEIL er
Refusals in Fake-Zitate umschrieb; Strip ist reine String-Chirurgie.

REGRESSION (eval/moa_prod_eval.py --only FERMI2,FACT1,FACT3,SYNTH2,WRITE1,
30 Turns): Verweigerungen 7→0, Klammern im Endtext →0 (Strip griff 7×,
20/30 Antworten mit Bahn-B-Hinweis), Stichproben inhaltlich sauber.

**GUARD-NACHMESSUNG (9.272.2, F1-F3+R1-R3 je 3 Reps, e021128a)**: Bahn-(a)-
Hard-Guard + Gründlichkeits-Klausel gebaut. Ergebnis ERNÜCHTERND für auto
(mistral-small): refusal 0.52→nur 0.55, retrieval 0.82→0.70 (Give-up-Flakes,
darum die Gründlichkeits-Klausel nachgezogen). DURCHBRUCH mit **medium+Guard**:
retrieval 0.957±0.025 (bester Wert je; Juni 0.67), F3 3×1.00, refusal 0.68.
KERNAUSSAGE: der wirksame Hebel für Grounding-Fragen ist das MODELL
(mistral-medium), nicht weitere Prompt-Chirurgie — bestätigt den Juni-Befund
('route grounding Qs to mistral-medium'). Juni-Trade-off-Bilanz: Juni
(refusal 0.91/retrieval 0.67) vs medium+Guard (0.68/0.96) ≈ netto gleich bis
besser, aber andere Verteilung. F1/F2 bleiben bimodal (0.25 vs 1.00 je Rep:
mal saubere Verweigerung, mal substanzieller Antwortbau um tangentiale
Fragmente).

**ROUTING GESHIPPT (9.272.3, cb8c2264)**: research-Fragen routen auf
mistral-medium via Benchmark-Override (models[small].benchmark.research.
override={capability:45} — synthetischer Bench 97=97 überschätzt small;
real 0.70-0.82 vs 0.957) + Klassifikator-Beispiel 'interne Dokument-Lookups
= research, nie fast'. Verifiziert auto×3: **retrieval 0.963±0.00** (bester
Wert je, Juni 0.67). REFUSAL-Bucket bleibt der offene Trade-off: alle
Juli-Arme 0.40-0.68 (bimodal 0.17-1.00 je Rep, n=3 trennt nicht) vs Juni
0.91 — die Zwei-Bahnen-Welt kauft +0.29 retrieval für ca. -0.3 refusal auf
expected-refuse-Fragen. Entscheidung akzeptieren oder gezielten F-Bucket-Fix
(z.B. Projekt-Instructions für KG-Real-Policies) separat angehen.

**REGRESSION ENTDECKT (Policies-Eval 2026-07-03, 3 Reps/Arm)**: Die
Zwei-Bahnen-Disziplin hat den REFUSAL-Bucket des KG-Real-Policies-Evals
einbrechen lassen — expected-refuse-Fragen (F1-F3 'steht nicht in unseren
Richtlinien') fielen von 0.913 (Juni-Baseline) auf 0.523 (auto Juli):
das Modell beantwortet jetzt substantiell ('Unsere Richtlinie verweist auf
FM-GwG…') statt sauber 'nicht gefunden' zu sagen — Bahn-(b)-Erlaubnis
schwächt den Refusal-Druck auch für Bahn-(a)-Fragen. GEGENLÄUFIG POSITIV:
retrieval-Bucket 0.672→0.821 und citation leicht besser (Antworten
vollständiger, wo Quellen existieren). OFFENER FIX: Bahn-(a)-Guard schärfen
('Fragen nach dem Inhalt UNSERER Dokumente ohne Fundstelle → Kernantwort
bleibt nicht-gefunden; Allgemeinwissen nur klar getrennt darunter, nie den
Dokumenten zugeschrieben') + Refusal-Bucket-Regression nachmessen.

EBENE 3 GESTRICHEN (Entscheidung 2026-07-03, mit User): Trigger-Kopplung ans
Klassifikator-Intent bringt nach Ebene 1+2 nur noch ~1k Tokens/Turn (nie
gecacht, Centbeträge) + weniger „Allgemeinwissen"-Boilerplate — dagegen echtes
Regressionsrisiko im WPB-Compliance-Kern (Intent-Trigger wurde 9.67→9.101
BEWUSST aufgegeben: Klassifikator kann Turns falsch einschätzen, die doch
retrieven). Wiedervorlage nur bei: (a) Hinweis-Boilerplate nervt (dann
Mini-Textfix: Hinweis ÜBER Deliverables statt darin), (b) Kosten-Telemetrie
zeigt Präambel als Posten, (c) lokales Modell zeigt Prompt-Längen-Probleme.
