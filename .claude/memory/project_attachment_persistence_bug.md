---
name: project_attachment_persistence_bug
description: BUG+FIX in Arbeit — Chat-Attachments in /tmp werden vom macOS-Cleanup nach 3 Tagen gelöscht; Migration auf persistenten Pfad
metadata: 
  node_type: memory
  type: project
  originSessionId: 2a5d4fdd-4b90-4a02-922f-e7cdc4d16758
  modified: 2026-07-22T05:54:02.376Z
---

**ENTDECKT 2026-07-22** bei der Vorbereitung der PII-Phase-1-Tests: von 9 Analyse-Chat-Kandidaten hatten nur ~2 ihre Input-Attachments noch.

**ROOT CAUSE:** Chat-Attachments werden unter `/tmp/brain-attachments/<session_id>/` gespeichert (handlers/chat.py, `brain_attachments_dir`). `/tmp` → `/private/tmp` (macOS-Symlink); der periodische macOS-Cleanup **löscht /tmp-Dateien nach 3 Tagen**. Nach ~3 Wochen kann der Agent die im Chat hochgeladenen Dateien NICHT mehr lesen (read_document schlägt fehl), obwohl der Chat-Verlauf + Artefakte in der DB bleiben.

**WIDERSPRUCH zur Design-Zusage:** v9.138.0-Changelog sagt explizit "the session attach dir persists ALL files for the whole chat (never cleared, accumulates)" — aber "never cleared" gilt nur BRAIN-seitig; das OS räumt /tmp trotzdem. Die Persistenz-Zusage ist faktisch gebrochen.

**FIX (User-Entscheidung 2026-07-22: Persistenz ZUERST, vor Phase 1):** Attachments von `/tmp/brain-attachments/<sid>/` auf einen persistenten Pfad umstellen — z.B. `agents/main/attachments/<sid>/` (überlebt wie die Artefakte unter agents/main/artifacts/). Betrifft viele Sites (Explore-Agent kartiert): dir-Helper, Upload/Scan-Write, read_document-Notice-Pfade, session_attachment_paths, `_validate_file_path`-Whitelist (handlers/admin.py) + Download/Preview/Stat/Open-External-Endpoints, Notice-Parser (client panels_*.js + `_split_attachment_notice`), Cleanup/`_purge_attachment_paths`, /private/tmp-realpath-Handling, Tests mit hartkodiertem /tmp/brain-attachments.

**MIGRATION alter Chats:** Bestehende Chats verweisen in ihren gespeicherten Notice-Pfaden auf /tmp — die Dateien sind eh weg. Kein Rück-Migrations-Zwang; nur künftige Uploads landen persistent. Notice-Parser müssen BEIDE Pfade tolerieren (alt /tmp lesbar-falls-noch-da, neu persistent).

Gehört zu [[project_pii_quality_test_plan]] (Reproduzierbarkeit der Tests hängt daran).
