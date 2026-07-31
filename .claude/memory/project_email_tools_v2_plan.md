---
name: project_email_tools_v2_plan
description: "ERLEDIGT v9.365.0 — EMAIL_TOOLS_V2_PLAN.md komplett umgesetzt (gmail_* → email_* über IMAP/POP3/EWS-Konnektoren); offen nur O1: Live-Validierung POP3 + Exchange"
metadata: 
  node_type: memory
  type: project
  originSessionId: 0669a85e-2bb2-4a28-92d7-fc8afce772e8
---

**ERLEDIGT (v9.365.0, 2026-07-17):** `EMAIL_TOOLS_V2_PLAN.md` vollständig
umgesetzt und live am echten Gmail-Konto validiert (alle 5 Ops + `email_accounts`
+ `POST /v1/tools/email/test`; beide Boot-Migrationen griffen automatisch,
Purpose-Matrix blieb erhalten).

Nicht-offensichtliches Betriebswissen:
- `engine/email_connectors.py` = brain-freie Protokollschicht (imaplib/poplib/
  smtplib/exchangelib); GDPR-Seams + Konto-Auflösung + Anhang-fail-closed liegen
  ausschließlich in `engine/tools/email_tools.py` (E2, ein Fix-Punkt).
- `email_send` RFC-prüft jede Empfängeradresse VOR dem Konnektor — Pseudonym-
  Tokens scheitern deterministisch, nicht mehr zufällig (Anhang-A-Notiz 5).
- Konten in `tools_config.json → email.accounts[]`; `username` leer = email ist
  Login; Gmail-Preset (`preset:"gmail"`) schaltet X-GM-RAW frei; Nicht-ASCII-
  Suche auf generischem IMAP fällt auf Client-Filter mit `search_scope` zurück.
- exchangelib ist NICHT im Server-Python installiert (lazy import, fail-loud);
  `verify_ssl:false` setzt `BaseProtocol.HTTP_ADAPTER_CLS` PROZESS-GLOBAL.
- **OFFEN (O1, VERTAGT per User 2026-07-17):** POP3 + Exchange nur unit-getestet
  (gemockte libs). Die Exchange-Live-Validierung geht ERST, wenn der Brain-Server
  im Netz der Bank läuft — der On-Prem-Exchange ist von außen nicht erreichbar
  (Specimen-Zugang existiert im Order-Buch-Projekt). exchangelib 5.6.0 ist seit
  2026-07-17 im Server-Python installiert und offline verifiziert; beim Umzug ins
  Banknetz nur noch Konto `type:"exchange_ews"` anlegen + „Verbindung testen".
- `agents/main/gmail.json` liegt weiter auf Platte (eingesammelt, obsolet —
  bewusst nicht gelöscht).

Verwandt: [[project_data_sources_v2_plan]] (gleiches Plan-Doc-Muster).
