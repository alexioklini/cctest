---
name: project_chat_title_multimodal
description: "v9.290.4 — Chat-Titel bei Bild-Anhängen zeigte rohe Multimodal-Liste; _derive_session_title akzeptiert jetzt Liste + extrahiert text-Parts"
metadata:
  node_type: memory
  type: project
  originSessionId: 01778bad-d744-4aff-8af1-720179c3af78
---
v9.290.4: Chats mit Bild-Anhang bekamen als Titel die rohe Multimodal-Content-Liste — `[{'type': 'image_url', 'image_url': {'url':` (in der Sidebar sichtbar, z.B. ausweispruefung-Chats). Ursache: `_derive_session_title` (server.py) bekam `str(content)` einer Liste `[{type:image_url,...},{type:text,text:...}]`.

FIX: `_derive_session_title(text)` akzeptiert jetzt str ODER Liste. Bei einer Liste: nur die `type=="text"`-Parts extrahieren + verketten (Bild-Parts ignorieren); reiner-Bild-Fall → 'Anhang'. ZWEI Aufrufer durchgereicht (beide machten vorher `str(content)`): `Session.add_message` (server.py:~420) + der GDPR-anonymise-Persistenz-Pfad (handlers/chat.py:~3570). 

Nur NEUE Chats profitieren — 5 bestehende Alt-Titel in chats.db blieben (Backfill wäre ein separater Migration-Call → [[feedback_defer_to_users_migration_calls]], User fragen). GOTCHA: chats.db NICHT per frischer sqlite3-Connection proben ([[feedback_never_probe_server_config_via_import]]) — die leere ~/.brain-agent/chats.db ist nicht die aktive; über die laufende API (`/v1/sessions`) prüfen. Unit-getestet (multimodal→text-Part, image-only→'Anhang', plain unverändert, attach-notice gestrippt).
