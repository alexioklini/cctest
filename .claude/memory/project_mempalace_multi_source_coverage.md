---
name: project_mempalace_multi_source_coverage
description: Scheduled/Chat-Task nutzt oft nur EINE der mehreren mempalace-Quellen — Fix muss bei hunderten Docs skalieren (keine Injektion/lies-jede-Quelle)
metadata: 
  node_type: memory
  type: project
  originSessionId: 78498eb4-5ff3-4a7e-ae09-234032c1a087
---

2026-05-26: Wiederkehrendes Problem im Projekt „webnews" (macrumors + caschys/stadt-bremerhaven URLs): ein Lauf (Chat ODER Scheduled Task) liefert manchmal eine Antwort, die nur auf EINER der hinterlegten Quellen basiert, obwohl `mempalace_query` Treffer aus beiden zurückgibt.

**Ursachen-Kette (alle einzeln gefixt, an echten Läufen belegt):**
- Wing-Bug: include_chat_history=true → leeres `project_chat__`-Wing → behoben (9.31.2/3, sucht jetzt project__ + chat).
- disable_web_search-Lockout + Shell-Hintertür-Revert (9.31-9.32).
- Domänen-Logik konsolidiert: Chat + Scheduler teilen `apply_domain_context` + `build_tool_context` (9.33.0) — Scheduler hat keine eigene Domänen-Logik mehr, ist jetzt team-aware.
- Artifact-Preamble auch im Task (9.32.5) → Query-Wortlaut näher am Chat.
- **Letzter Befund (sched-888)**: gleiches Modell (mistral-small), mempalace fand bei Chat UND Task beide Quellen — aber Chat rief read_document 2×, Task nur 1× → Report aus einer Quelle. Modell-Jitter auf der read_document-Ebene.

**KRITISCHE LEITPLANKE (User, 2026-05-26):** Die Lösung MUSS bei Projekten mit HUNDERTEN Dokumenten funktionieren. Quellen vor-injizieren ist RAUS (skaliert nicht). Genau dafür gibt es mempalace (filtert auf relevante Top-N Treffer).

**WICHTIG — die dokumentierte Disziplin (config.json tool_settings.mempalace_query):** Es gibt einen MANDATORY 3-STEP FLOW: (1) mempalace_query → ~800-Zeichen-Snippets, die ausdrücklich NIE zum Antworten ausreichen (nur Pointer/Ranking), (2) read_document auf JEDEN relevanten Drawer → volles Dokument, (3) NUR aus Step 2 antworten. „Answering from drawer text alone" ist als Hauptursache für Halluzination dokumentiert. → Snippet ist BEWUSST kein Antwortmaterial; „aus Snippets antworten" wäre FALSCH (verletzt die Disziplin). Skaliert trotzdem: read_document gilt nur für die wenigen mempalace-TREFFER (Top-N, z.B. 5), nicht für alle 200 Docs.

**Das eigentliche Problem (sched-888):** Das Modell verletzt Step 2 — es liest nur EINEN von mehreren relevanten Treffern → Antwort aus einer Quelle. Genau der dokumentierte Fehler. Kein neues Problem, sondern unvollständige Befolgung der 3-Schritt-Regel.

**LÖSUNG (v9.34.0, strukturell statt Hint):** mempalace_query liefert pro Drawer KEIN Snippet mehr, wenn ein lesbares Original existiert (`text` leer + `content_via:"read_document"`) → das Modell MUSS read_document aufrufen, „answering from partial snippet" strukturell unmöglich. Gilt für Projekt-Docs, brain_code UND Artefakte (synthetischer Marker `session/<sid>#artifact/<name>` → `agents/<agent>/artifacts/<date>_<sid>/<name>` aufgelöst). Drawer OHNE Datei (Chat-Turn/Summary/Profil-Sektion) → `text` VOLLSTÄNDIG (alte [:2000]-Kappung entfernt, sonst Inhaltsverlust ohne read_document-Ausweg) + `content_via:"snippet"`. Eine Regel, jeder Aufrufer, Datenlage entscheidet (`os.path.isfile`). read_hint listet bei >1 Dok alle. config-Prosa angepasst. — Klärung: „verbatim copy" in MemPalace lebt IM Drawer (chroma:document), NICHT zwingend als externe Datei; nur Projekt-Docs/brain_code/Artefakte haben ein File. User-Profil = Drawer ist eine ##-Sektion (präziser als das ganze .md) → Snippet korrekt. Multi-Quellen-read_document-Abdeckung bei mehreren Docs ist jetzt erzwungen (text leer → read nötig); ob das Modell ALLE liest bleibt zu messen, aber es kann nicht mehr aus einem Teil-Snippet antworten.
