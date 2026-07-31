---
name: project_doc_refinement_before_mining
description: "Geplante Per-Ordner-Option \"Veredelung vor Mining\" — Roh-Markdown vs. LLM-veredeltes Markdown beim Projekt-Dokument-Mining"
metadata: 
  node_type: memory
  type: project
  originSessionId: 9ca0dd2a-02c9-494f-99df-841efee16f3b
---

2026-05-27: Hypothese (vom User bestätigt durch Brainy-vs-Projekt-Chat-Beobachtung): Projekt-Chats brauchen mehr Babysitting als Brainy, **weil Brainys Quelle (brain-agent-guide-Skill) handgepflegtes, für LLMs gut strukturiertes Markdown ist**, während Projekt-Chats über rohe markitdown-Konversionen von PDFs/Policies retrieven. Der Formular-/Seiten-Lärm in rohen Companions (Antragstabellen, "Seite X von Y", Versionshistorie, Dokumentenklassifizierung) verwässert die ~800-Zeichen-Drawer-Embeddings → falsche Dokumentwahl (P2/C2/C3-Gap, modellunabhängig, ~−0.20). Beleg gesehen an `20_2_1_3_ARL_Ziele der Informationssicherheit.pdf.md`: ~25% Verwaltungs-Lärm, echter Inhalt nur in einer Sektion.

**Endausbaustufe (vom User definiert):** Option **pro Input-Ordner** in den Projekt-Einstellungen beim Hinzufügen von Dokumenten/Ordnern: **roh** (markitdown→minen, heutiges Verhalten) ODER **veredelt** (markitdown→LLM-Veredelung→minen). Passt als `input_folders[].refine: true` neben das bestehende `auto_sync`-Flag (Struktur: `{path, recursive, auto_sync, added_at}`).

**Invarianten-Constraints für den Bau** (siehe [[project_unified_extraction_pipeline]], engine/CLAUDE.md):
- Veredelung MUSS einmalig beim Mining erzeugt + als Companion gecacht werden (hash-gegatet wie heute), NICHT bei jedem Sync neu — sonst bricht die Byte-Stabilitäts-Invariante (re-embed jeden Zyklus).
- Companion-Frontmatter (`brain-source-mtime/size`) muss intakt bleiben, sonst überschreibt `convert_folder` die veredelte Fassung aus dem PDF.
- Citation-Konsequenz: der `read_document`-Pfad muss DIESELBE veredelte Companion lesen, gegen die gemined+zitiert wird — sonst matchen Zitate nicht. Veredelung als Schicht ÜBER `_do_extract`, damit alle 4 Konsumenten (chat-read, mining, PII-scan, ARL-classification) dieselbe Fassung sehen.
- Naiver Pipeline-Schritt verboten: wiederholt das bereits gescheiterte LLM-über-Retrieval-Muster ([[project_kg_vs_vanilla_mempalace_regression]]).

**Status: GETESTET + ABGELEHNT (2026-05-27).** Messung in [[project_doc_refinement_eval]] zeigt gemischtes, netto negatives Ergebnis: C3 +0.30 ✓ aber P2 −0.31 und M3 −0.27 ⚠️. Die Regression ist KEIN Inhaltsverlust (verifiziert: veredelte Fassung behielt alle Fristen, sauberer strukturiert) sondern ein Chunk-Grenzen-Verschiebungs-Effekt im Retrieval. Die simple "veredle-dann-mine"-Per-Ordner-Option NICHT bauen. Falls je weiterverfolgt: müsste chunk-grenzen-stabil sein oder ergänzende Schicht neben dem Rohtext (nicht Ersatz). Companions nach dem Test auf `.orig` zurückgesetzt + re-gemined.
