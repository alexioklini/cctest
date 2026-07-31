---
name: project_doc_refinement_eval
description: "Messung 2026-05-27 — LLM-Veredelung von Companion-Markdowns vor Mining; gemischtes Ergebnis, naiver Ansatz verworfen"
metadata: 
  node_type: memory
  type: project
  originSessionId: 9ca0dd2a-02c9-494f-99df-841efee16f3b
---

2026-05-27: Messung zur Hypothese aus [[project_doc_refinement_before_mining]] (Veredelung roher markitdown-Companions → besseres Retrieval). 5 Lücken-Dokumente (P2/C2/C3/M3) per Brain-Hintergrundmodell (mistral-medium-3.5) **chunked** veredelt (Form-/Seiten-Lärm raus, Sektionen als Überschriften+Bullets), Companions in-place ersetzt (Frontmatter intakt), re-gemined, Subset-Eval **je 3× vor/nach** (Brain-only, --skip-gold, Gold wiederverwendet aus 20260527T083345). Tool: `eval/refine_companions.py` (one-shot, NICHT Produktcode; POST /v1/chat, throwaway-session; `--restore` setzt aus `.md.orig` zurück).

**Ergebnis (Mittel über 3 Läufe):**
| Frage | Baseline | Post | Δ |
|---|---|---|---|
| P2 Archivierung (precision) | 0.70 | 0.39 | **−0.31** ⚠️ |
| C2 Arbeitsplatz (citation) | 0.73 | 0.77 | +0.04 |
| C3 ISMS-Ziele (citation) | 0.62 | 0.92 | **+0.30** ✓ |
| M3 Cloud/Lieferanten (multi_doc) | 0.98 | 0.71 | **−0.27** ⚠️ |

**Fazit: naive Veredelung lohnt sich NICHT — gemischt, netto negativ.** C3 (das Dok mit dem schlimmsten Form-Lärm) bestätigt die Hypothese (+0.30), aber P2 und M3 regredieren stark (>3× über Baseline-Rauschen ≤0.08).

**Counterintuitive Ursache (verifiziert, widerlegt meine erste Vermutung):** Die P2-Regression ist NICHT verlorener Inhalt. Geprüft: die veredelte Archivierungs-Datei BEHIELT alle Fristen und strukturierte sie sogar SAUBERER als das Original (Tages/Wochen/Monats/Jahressicherung: 2 Wochen / 5 Wochen / 12 Monate / 7 Jahre — alle da). Trotzdem fiel P2 0.70→0.39. ⇒ Die Regression ist ein **Retrieval-/Chunking-Effekt**: veränderte Chunk-Grenzen verschieben, welcher Drawer vom Reranker für die Query getroffen wird — die Veredelung kann ein vorher gut passendes Chunk in ein schlechter passendes umbrechen. Deckt sich mit [[project_kg_vs_vanilla_mempalace_regression]] (LLM-Schicht über Retrieval = unvorhersehbare Schäden).

**Wichtige Methodik-Erkenntnisse:**
1. **Single-shot truncation:** erster Versuch (kein Chunking) schnitt das größte Dok (Lieferanten 28KB) mitten im Dokument ab → 2637c statt vollständig, Inhalt verloren. Output-Cap (16K tok) war NICHT der Grund — mistral stoppt selbst früh bei sehr langem Single-Shot-Rewrite. Fix: section-weises Chunking (~7KB) + Stitch + Truncation-Guard (ratio<0.15 oder Zeile endet auf unvollständiger Tabellenzeile). Eine produktive Endausbaustufe MÜSSTE chunken.
2. **Varianz:** C3 schwankte im Baseline-Rauschen selbst um 0.80 (0.20/1.00/0.65) — Einzellauf beweist nichts, 3× Pflicht.
3. **Re-Mine-Trigger:** Companion-Content-Änderung (Frontmatter intakt) → sync-daemon re-mined via content-hash; `last_files_filed:0` ist irreführend (zählt nur NEU gefilte Sources, nicht delete+refile), aber `~/.mempalace/brain/chroma.sqlite3`-mtime bestätigte den Write.

**Konsequenz für [[project_doc_refinement_before_mining]]:** Die simple "veredle-dann-mine"-Option NICHT bauen — sie verbessert manche Docs, verschlechtert aber Präzisions-/Multi-Doc-Retrieval unvorhersehbar durch Chunk-Grenzen-Verschiebung. Falls überhaupt weiterverfolgt: müsste chunk-grenzen-STABIL sein (Veredelung darf bestehende gute Chunks nicht umbrechen) ODER nur als ergänzende Schicht neben dem rohen Text, nicht als Ersatz. Vorerst: Hypothese getestet, abgelehnt.

---

**FOLLOW-UP 2026-05-27: chunk-bewusste Veredelung (Idee: LLM erzeugt Sektionen passend zur Chunk-Größe) — ebenfalls ABGELEHNT.**

Miner-Mechanik verifiziert (`~/.mempalace/venv/.../mempalace/miner.py` `chunk_text`): **fenster-basiert**, nicht block-treu. `CHUNK_SIZE=800, OVERLAP=100`. Gleitendes 800-Zeichen-Fenster, bricht an `\n\n` NUR wenn die Grenze in der 2. Fensterhälfte (>400) liegt; sonst harter Schnitt bei 800. ⇒ Ein vorhergehender langer Block schiebt den nächsten aus dem sauberen Fenster; das LLM kann die 700-Zeichen-Grenze ohnehin nicht zuverlässig einhalten.

Verifizierter Schadensfall (roh): P2-Fristenliste (Tages/Wochen/Monats/Jahressicherung = 2 Wo/5 Wo/12 Mon/7 Jahre) wird im Rohdokument zwischen chunk 6↔7 zerschnitten — kein Chunk hat die vollständige Liste. Chunk-aware veredelt: WIEDER zerschnitten (chunk 3↔4, sogar mitten im Wort) trotz expliziter <700-Zeichen-Anweisung.

Eval chunk-aware (P2+C3, 3×, vs Baseline roh):
| Frage | roh | naiv veredelt | chunk-aware |
|---|---|---|---|
| P2 (atomare Fristenliste) | 0.70 | 0.39 | **0.37** (bleibt kaputt) |
| C3 (beschreibende Ziele) | 0.62 | 0.92 | **0.91** (bleibt verbessert) |

**Verallgemeinerbare Erkenntnis (der eigentliche Wert):** Ob Veredelung hilft, hängt am INHALTSTYP, nicht an der Veredelungsqualität:
- **Beschreibender Inhalt** (C3: Ziele über mehrere Bullets, jeder Chunk trägt Teil-Antwort) → Veredelung verdichtet Signal, HILFT (+0.30).
- **Atomare Faktenliste** (P2: 4 exakte Fristen als Einheit gebraucht) → wenn länger als das 800-Zeichen-Fenster, zwingt KEINE Veredelung sie in einen Chunk; jeder Schnitt macht jeden Chunk unvollständig → SCHADET (−0.31).

**Endgültig:** Veredelung per Prompt kann den fenster-basierten 800-Zeichen-Chunker nicht zuverlässig bedienen. Der echte Hebel läge tiefer: größeres CHUNK_SIZE im Miner, ODER (wie Brainy) ganze Datei lesen statt chunk-retrieven für kleine kuratierte Korpora — beides außerhalb von "Veredelung". Thema geschlossen.
