---
name: project_two_html_report_renderers
description: "Brain has TWO HTML-report renderers (fancy report_html vs plain doc-styles preset); write_document picks by style='report'"
metadata: 
  node_type: memory
  type: project
  originSessionId: c222787c-f141-4bdf-b86f-396ce1dfcc1f
---

Brain rendert HTML-Reports über **ZWEI verschiedene Wege** — Verwechslung kostet Zeit (2026-07-02 mehrfach falsche Diagnose):

1. **`engine/report_html.render_report_html(markdown, title, category='report', doc_dir=…)`** — der SCHÖNE, editoriale Magazin-Look: Hero, Aurora-Hintergrund (`aurora-drift`), Dark-Mode (`prefers-color-scheme`), sticky TOC-Sidebar + Scroll-Spy-Script, Drop-Cap, `::kpi`-Boxen, `.cite-chip`/`.citations-panel` (Inline-Citations), Toolbar. Genutzt von Deep Research, Studio (`output_gen`) UND `write_document` **nur wenn `style='report'`/`'editorial'`** (seit v9.249.0/9.250.0).
2. **`_render_markdown_html` in `engine/tools/file_tools.py`** — das SCHLICHTE doc-styles-Preset: Calibri, blaue Print-Tabellen, `@page`-CSS, Header/Footer/Logo. Der Fallback-Pfad für `.html` OHNE `style='report'`.

**Wie man den Weg erkennt**: `grep aurora-drift` → fancy; `@page { margin` + Calibri → schlicht.

**v9.260.0**: `style='report'` ist jetzt in der `write_document`-Tool-Beschreibung (`tool_schemas.py`, 2 Stellen) als **Default für JEDEN HTML-Report** dokumentiert — vorher an das Wort „schön/nice" gekoppelt, weshalb „erstelle einen html-report" (ohne „schön") im biederen Preset landete (Milo-Report chat 1a830369 vs Diana-Report chat cb3b3a81).

**UI-Gotcha**: Die Web-UI rendert HTML-Artefakte NICHT von der Platte, sondern aus `artifact_versions.content` (SQLite chats.db). Eine Datei auf Disk zu überschreiben ändert die Anzeige NICHT — man muss `brain._register_artifact_version(path, action='edited', agent_id='main')` unter `with request_context(current_session_id=…)` aufrufen (legt neue Version an, UI zeigt `ORDER BY version DESC LIMIT 1`). Siehe [[project_inprocess_openai_loop]].

Der 9.259.0-Konverter-Fix (`_render_markdown_html`: `---`/`>`/Listen/Frontmatter/Titel-`**`) war REAL, reparierte aber nur den SCHLICHTEN Pfad — nicht das eigentliche „sieht nicht wie Report aus"-Problem des Nutzers (der wollte den fancy Look).
