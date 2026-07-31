---
name: project_eval_snippet_rule_gemma_vs_mistral
description: "2026-05-26 Eval nach Snippet-Regel (v9.34): gemma-4-26B ≈ mistral-medium auf Policies; Restlücke = Retrieval/Citation, nicht Modell"
metadata: 
  node_type: memory
  type: project
  originSessionId: 78498eb4-5ff3-4a7e-ae09-234032c1a087
---

2026-05-26: Eval-Suite (eval/run.py, 15Q Policy-Canary, Gold-Reuse aus 20260523T175237_disc-none_E4-postextract, Mistral-Self-Judge mistral-medium-3.5, KG-Real-Policies) nach der strukturellen Snippet-Regel (v9.34: mempalace_query liefert kein Snippet bei File-Drawern → read_document erzwungen) + Domänen-Konsolidierung (v9.33).

**Ergebnis (identisches Setup, gleiches Gold, gleicher Judge):**
- mistral-medium-3.5 (cloud): brain mean **0.729**, Δ−0.20 vs Gold
- gemma-4-26B-A4B-it-MLX-4bit (lokal/oMLX): brain mean **0.717**, Δ−0.21
- Δ(gemma−mistral) = **−0.011** → praktisch gleichauf (innerhalb Mistral-Judge-Varianz ±0.09, [[project_eval_citation_validator_phase1]]). Lokales 26B hält mit Cloud-mistral-medium mit.

**Die Snippet-Regel brachte KEINEN messbaren Eval-Sprung** (0.729 liegt im Rauschen der alten Baseline ~0.66–0.73, [[project_eval_baseline_medium35]]) — aber auch keinen Schaden (alle 15 Antworten ok, kein read_document-Ausfall, kein Einbruch). Strukturelle Gewinne (Chat=Task-Konsistenz, keine Historie, kein Snippet-Schummeln) sind real, bewegen aber den Score nicht.

**Restlücke ist MODELL-UNABHÄNGIG** (bei beiden gleich) = Retrieval + Citation, NICHT „aus Snippet halluziniert" (meine Hypothese war falsch):
- P2_archivierung (0.35/0.45): FALSCHES Dokument gewählt
- C2_passwort_zitat (0.38/0.45): kein wörtliches Zitat
- C3_isms (0.5/0.45): off-target docs
- gemma besser nur bei F1_geldwaesche (+0.15 Refusal), R3 (+0.15); sonst alle Δ klein.
- M3: Gold selbst versagte (0.0) → beide Brains gewinnen, verzerrt die Means leicht nach unten beim Gold.

**KG-span-Fix (v9.36.0) + Retest:** Forensik ergab, dass P2/C2 NUR mempalace_kg_search nutzten, NIE read_document — weil (a) jedes KG-Tripel ein `span` (≤200-Zeichen-Zitat) im Ergebnis trug und (b) die config-Prosa explizit sagte „if span present, cite directly, no read_document needed". Das Modell antwortete aus dem span des FALSCHEN Dokuments. Fix: alle 3 KG-Tools liefern kein span mehr + read_document-Pflicht-Hint; config-Prosa angepasst. SAUBERER Retest (mistral-MEDIUM-3.5 vor UND nach, gleicher Judge/Gold, --only P2,C2,C3): ALLE drei rufen jetzt read_document (P2 4×, C2 7×, C3 3×) — Tool-Verhalten gefixt. ABER Score-Δ = +0.003 (Rauschen): P2 0.45→0.38, C2 0.38→0.33, C3 0.50→0.63. P2+C2 sogar minimal SCHLECHTER trotz read_document. → Hypothese „span-Antworten war die Score-Ursache" WIDERLEGT. Der span-Fix ist Korrektheit (keine span-Halluzination mehr möglich), KEIN Score-Hebel.

**ZENTRALE ERKENNTNIS:** „Liest nicht" war Symptom, nicht die ganze Ursache. Die Tool-Disziplin-Schicht (Snippet/span → read_document) ist jetzt strukturell sauber gelöst (v9.34 + v9.36). Die VERBLEIBENDE Eval-Lücke (−0.20) ist Retrieval-PRÄZISION: selbst MIT read_document (C2: 7× gelesen → trotzdem 0.33) findet/zitiert das Modell nicht das Richtige. Das ist Modell- + Retrieval-Ranking-Schwäche, KEIN Pipeline-Bug mehr.

**Nächster Schritt (offen):** Retrieval-Ranking / Dokumentwahl verbessern (welcher Drawer/welches Dokument rankt oben für eine Frage) + wörtliche Zitier-Treue — NICHT mehr Tool-Disziplin. Evtl. cross-encoder-rerank-Tuning, query-Formulierung, oder ein präziseres Modell. [[project_chroma_direct_search_fix]] + [[project_eval_baseline_medium35]] sind verwandt.

Befehl für Reproduktion: `BRAIN_USER=admin BRAIN_PASS=admin python3 eval/run.py --skip-gold --reuse-results <golddir> [--brain-model <id>] --label <x>` — Judge-Provider=mistral steht in eval/config.json (kein --judge-model setzen, das kippt auf claude_code). brain.project=KG-Real-Policies.
