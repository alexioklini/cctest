# E-Mail-Tools v2 — Provider-Konnektoren (IMAP, POP3, Exchange) + generische Tools — Umsetzungsplan

**Stand:** 2026-07-17, Basis v9.363.0+. Erarbeitet aus der Analyse der bestehenden
Gmail-Tools — dieser Plan ist für die Umsetzung in einer frischen Session geschrieben.

**Ziel:** Die fünf Gmail-Tools werden zu **provider-agnostischen E-Mail-Tools**
(`email_inbox`, `email_read`, `email_search`, `email_send`, `email_reply` + neu
`email_accounts`), die über **konfigurierbare Konnektoren** laufen. Konnektor-Typen:
**IMAP+SMTP** (deckt Gmail, GMX, iCloud, Outlook.com-IMAP, generische Mailserver),
**POP3+SMTP** (reduzierter Funktionsumfang) und **Exchange** (EWS). Mehrere Konten
parallel; jedes Konto hat Typ + Zugangsdaten; die Tools bekommen einen optionalen
`account`-Parameter (leer = Default-Konto). Gmail wird ein **Preset** des
IMAP-Konnektors, kein Sonderfall mehr.

**User-Entscheidungen (2026-07-17, verbindlich):**
1. Konnektoren für die unterschiedlichen Provider-Typen (POP3, IMAP, Exchange, …)
   + generelle, nicht Gmail-spezifische Tools, die die aktiven Konnektoren nutzen
   (abholen, lesen, erstellen etc.).
2. **Exchange-Anbindung über `exchangelib` für den Zugriff auf On-Prem-Exchange** —
   EWS mit Benutzername/Passwort ist damit gesetzt; Microsoft 365 / Graph
   ist NICHT der Anwendungsfall. Es existiert eine **funktionierende
   Beispiel-Anbindung** an den lokalen Exchange-Server (vom User geliefert,
   `email_service.py` aus dem Order-Buch-Projekt) — deren verifiziertes
   Verbindungsmuster ist in **Anhang A** destilliert und ist die Vorlage für
   den Konnektor.

---

## Verifizierte Ist-Fakten (nicht erneut prüfen, außer bei Zweifel)

| Fakt | Beleg |
|---|---|
| 5 Tools: `gmail_inbox/read/search/send/reply`, Impl in `engine/tools/gmail_tools.py` (377 LOC), IMAP/SMTP hardcoded auf `imap.gmail.com` / `smtp.gmail.com:465`, App-Passwort-Auth | `engine/tools/gmail_tools.py` |
| Suche nutzt Gmail-proprietäres `X-GM-RAW` (voller Gmail-Query-Syntax) — auf generischem IMAP nicht verfügbar | `gmail_tools.py:190` |
| Config: `tools_config.json → gmail {enabled, email, app_password}` (Integration-only Pseudo-Tool, KEIN TOOL_DISPATCH-Eintrag), Fallback `agents/main/gmail.json` | `brain.py:5433` (`_DEFAULT_TOOLS_CONFIG`), `_gmail_config()` in `gmail_tools.py:28` |
| Wiring in brain.py: `TOOL_GROUPS["email"]` (Z.1454), `TOOL_DISPATCH`, Re-Export-Import Z.4848, `READONLY_TOOLS` (Plan-Mode, Z.972: inbox/read/search), Tool-Icons Z.15648 + Labels Z.15676, Status-Branch `get_tool_status` Z.5713 | `brain.py` |
| **GDPR-Wiring**: Read-Tools haben Result-Seams via `_brain._gdpr_anon_tool_text(..., "gmail_*")` (M3/G9); `gmail_send`/`gmail_reply` sind in `_EGRESS_EXTRA_TOOLS` → `EGRESS_TOOLS` (M2/G7, Egress-Gate); `gmail_send` refused Anhänge FAIL-CLOSED bei aktivem Mapping (Datei auf Platte = Echtwerte, L6) | `gmail_tools.py:122/160/208/258`, `brain.py:1516` |
| Schemas: 5 Einträge in `TOOL_DEFINITIONS` | `engine/tool_schemas.py:457–525` |
| Pseudo-Tool-GUI: `settings_tools.js` Case `'gmail'` (Render Z.281, Save-Rec Z.555); Integration-only-Liste in `handlers/admin_config.py:102`; Status-Fallback-Merge Z.1306; Passwort-Redaction im Config-Export `handlers/admin_artifacts.py:939` | jeweilige Dateien |
| Frontend: deutsche Tool-Labels `chat_tools.js:160–164`; `WRITE_EXEC`-Set enthält `gmail_send/gmail_reply` (`settings_general_tabs.js:2329`) | `web/js/` |
| Tests, die Gmail-Namen referenzieren: `tests/test_gdpr_egress_gate.py`, `tests/test_helpdesk_tools.py` | grep |
| Skill-Doku erwähnt gmail: `brain-agent-guide/02-tools.md`, `04-recipes.md`, `05-internals.md`, `06-user-manual.md` | grep |
| Neues Tool = 4 Sites / 3 Dateien; DISPATCH-Wert = direkte Fn-Ref (kein Lambda); engine-Module dürfen brain nur lazy importieren | CLAUDE.md / engine/CLAUDE.md |
| Tool-Schemas sind Teil des Warmup-KV-Prefix → Schemas müssen **statisch** bleiben (keine Kontennamen dynamisch in Descriptions) | CLAUDE.md (KV-Prefix-Invariante) |
| `tool_settings` in config.json ist per Tool-NAME gekeyed (enabled/deferred/purposes/states) — Umbenennung braucht Key-Migration, sonst verlieren die Tools ihre Purpose-Matrix | brain.py `seed_tool_purpose_states` (v9.101.1) |

---

## Architektur-Entscheidungen (mit Begründung)

**E1 — Konnektor-Abstraktion in `engine/email_connectors.py` (brain-frei).**
Neue Basisklasse `EmailConnector` mit den fünf Operationen
`list_messages(folder, limit)` / `read_message(id, folder)` / `search(query, limit)` /
`send(to, cc, subject, body, attachments)` / `reply(id, body)` plus
`capabilities` (Flags: `folders`, `server_search`, `native_query_syntax`, `reply`).
Das Modul ist **reines Protokoll-Code** (imaplib/poplib/smtplib/exchangelib) ohne
`import brain` — damit testbar ohne Server-Runtime. Die MIME-Helfer
(`_decode_mime_header`, `_get_email_body`) ziehen aus `gmail_tools.py` mit um.

**E2 — GDPR-Seams bleiben an der TOOL-Schicht, nicht im Konnektor.**
`engine/tools/email_tools.py` (Nachfolger von `gmail_tools.py`) ruft den Konnektor
und legt DANACH den Result-Seam (`_gdpr_anon_tool_text`) bzw. vorher die
Anhang-fail-closed-Prüfung an. Ein Fix-Punkt für alle Konnektor-Typen
([[feedback_single_fix_point]]) — ein neuer Konnektor kann den Datenschutz
strukturell nicht vergessen. Der Anhang-Refuse und die `_norm`-Adress-Normalisierung
aus `tool_gmail_send` ziehen 1:1 in das generische `tool_email_send` um.

**E3 — Konten-Modell im Pseudo-Tool `email` (ersetzt `gmail`):**
```json
"email": {
  "enabled": true,
  "default_account": "gmail",
  "accounts": [
    {"name": "gmail",  "type": "imap", "preset": "gmail",
     "email": "…", "password": "…",
     "imap_host": "imap.gmail.com", "imap_port": 993,
     "smtp_host": "smtp.gmail.com", "smtp_port": 465, "smtp_security": "ssl"},
    {"name": "buero",  "type": "pop3",
     "email": "…", "username": "…", "password": "…",
     "pop3_host": "…", "pop3_port": 995,
     "smtp_host": "…", "smtp_port": 587, "smtp_security": "starttls"},
    {"name": "firma",  "type": "exchange_ews",
     "email": "…", "username": "DOMAIN\\user", "password": "…",
     "server": "mail.firma.tld", "autodiscover": false}
  ]
}
```
`username` leer = `email` wird als Login verwendet (Gmail-Fall). **Presets**
(gmail, outlook, gmx, icloud, web.de, custom) füllen nur Host/Port/Security vor —
zur Laufzeit zählen ausschließlich die gespeicherten Felder. Das Gmail-Preset
aktiviert zusätzlich `native_query_syntax` (X-GM-RAW).

**E4 — Sauberer Rename `gmail_*` → `email_*`, KEINE Alias-Tools.**
Alias-Einträge in `TOOL_DISPATCH` verletzen die Dispatch-Identity-Regel und
verdoppeln die Schema-Fläche (Prompt-Bloat, [[feedback_prompt_bloat_regression]]).
Stattdessen einmalige **Boot-Migration**: (a) `tools_config.json`: `gmail`-Record →
`email`-Record mit einem Konto `name:"gmail", type:"imap", preset:"gmail"`
(der `gmail.json`-Fallback wird dabei mit eingesammelt, dann obsolet);
(b) `config.json → tool_settings`: Keys `gmail_*` → `email_*` umbenennen
(enabled/deferred/purposes/states/Prosa bleiben erhalten). Alte Chats referenzieren
Tool-Namen nur in der Historie — die braucht keine lebenden Definitionen.

**E5 — `account`-Parameter + neues Read-only-Tool `email_accounts`, Schemas statisch.**
Alle fünf Tools bekommen optional `account` (string; leer = `default_account`).
Kontennamen dürfen NICHT dynamisch in die Schema-Descriptions (KV-Prefix-Invariante) —
deshalb ein sechstes, winziges Tool `email_accounts` (listet Name, Typ, E-Mail-Adresse,
Capabilities je Konto; keine Secrets). Unbekannter `account` → Fehlermeldung, die die
verfügbaren Kontennamen nennt (self-healing für das Modell). `email_accounts` und die
drei Read-Tools kommen in `READONLY_TOOLS` (Plan-Mode).

**E6 — Suche capability-basiert statt Gmail-Syntax.**
`email_search` übersetzt eine einfache Query in Standard-`IMAP SEARCH`-Kriterien
(Heuristik: `from:x` → `FROM x`, `subject:x` → `SUBJECT x`, Rest → `TEXT`), außer das
Konto hat `native_query_syntax` (Gmail-Preset → weiterhin `X-GM-RAW`, voller Umfang).
POP3 hat KEINE Server-Suche: Fallback = Header der letzten N Mails clientseitig
filtern, mit klarem Hinweis im Result (`"search_scope": "last_200_headers"`).
Die Tool-Description beschreibt die einfache Syntax; dass Gmail mehr kann, steht
im Result, nicht im Schema (statisch!).

**E7 — POP3 = bewusst reduzierter Konnektor.**
`poplib` (POP3_SSL): `list_messages` = letzte N via TOP (nur Header), `read_message`
= RETR per Message-Nummer, keine Folder (Parameter wird ignoriert + im Result
vermerkt), Suche nur clientseitig (E6), `send`/`reply` laufen über die
SMTP-Konfiguration des Kontos (Reply ohne IMAP-Zugriff auf die Original-Mail:
POP3-RETR der Mail liefert Message-ID/Reply-To — Threading funktioniert).
Capabilities-Flags machen die Einschränkungen dem Modell im `email_accounts`-Output
sichtbar statt sie stillschweigend zu brechen ([[Regel 12 — fail loud]]).

**E8 — Exchange via EWS (`exchangelib`) für On-Prem — per User-Entscheid Nr. 2 gesetzt,
Verbindungsmuster durch das gelieferte Specimen VERIFIZIERT (Anhang A).**
`exchangelib` deckt On-Prem-Exchange mit Benutzername/Passwort ab und mappt sauber
auf das Konnektor-Interface (inkl. Server-Suche via QuerySet-`filter()`).
Microsoft 365 (Graph API + OAuth2) ist explizit NICHT der Anwendungsfall und bleibt
außerhalb des Scopes. `exchangelib` ist eine neue Dependency → Verfügbarkeit im
Server-Python prüfen, lazy importieren (fail-loud „Exchange-Konnektor: exchangelib
nicht installiert" statt Boot-Bruch). Konto-Felder (aus dem Specimen abgeleitet):
`server` (EWS-Host), `username`, `password`, `email` (primary SMTP address),
`autodiscover` (bool, Default false — das Specimen verbindet direkt),
`verify_ssl` (bool, Default true — siehe Anhang A zur Self-Signed-Cert-Falle).
Kein `auth`-Feld nötig: exchangelib verhandelt den Auth-Typ selbst; das Specimen
läuft mit schlichtem `Credentials(username, password)`.

**E9 — Egress- und Plan-Mode-Wiring folgt dem Rename.**
`_EGRESS_EXTRA_TOOLS`: `gmail_send/gmail_reply` → `email_send/email_reply`.
`READONLY_TOOLS`: drei Read-Tools + `email_accounts`. Der Egress-Gate-Mechanismus
selbst ist tool-namensbasiert und braucht keine Logik-Änderung — nur die Namen.
`tests/test_gdpr_egress_gate.py` mit umbenennen (der Test IST die Kalibrier-Matrix,
er muss den neuen Namen kennen, sonst ist das Gate für E-Mail-Egress grün-blind).

---

## Phasen (Kern vor UI, Kern-Mechanik validieren vor Ausbau — [[feedback_phase_a_then_validate]])

### Phase 1 — Konnektor-Kern + IMAP/SMTP + Rename + Migration (das Fundament)
1. `engine/email_connectors.py`: `EmailConnector`-Basis + `ImapSmtpConnector`
   (Logik aus `gmail_tools.py` generalisiert: Host/Port/Security aus Konto-Config
   statt Konstanten; X-GM-RAW nur bei `native_query_syntax`). MIME-Helfer mit umziehen.
2. `engine/tools/email_tools.py`: sechs `tool_email_*`-Fns (Konto auflösen →
   Konnektor bauen → Operation → GDPR-Seam). `_email_config()` ersetzt
   `_gmail_config()` (Konten-Modell E3). `gmail_tools.py` löschen (kein Drift-Zwilling —
   das war die Lektion von v8.32.0).
3. Schemas in `TOOL_DEFINITIONS` umbenennen + `account`-Param + `email_accounts`-Schema.
4. brain.py-Wiring: TOOL_GROUPS, TOOL_DISPATCH (direkte Fn-Refs), Re-Export-Import,
   READONLY_TOOLS, `_EGRESS_EXTRA_TOOLS`, Icons/Labels, `_DEFAULT_TOOLS_CONFIG`
   (`gmail`-Block → `email`-Block), `get_tool_status`-Branch.
5. Boot-Migration (E4) — idempotent, nur wenn `email`-Record noch fehlt.
6. Handler-Anpassungen: `admin_config.py` (Integration-only-Liste, Status-Merge),
   `admin_artifacts.py` (Redaction: alle `password`-Felder in `email.accounts[]`).
7. Tests: Unit-Tests für `ImapSmtpConnector` (imaplib/smtplib gemockt),
   Konto-Auflösung (default/benannt/unbekannt), Migration; `test_gdpr_egress_gate.py`
   + `test_helpdesk_tools.py` auf neue Namen.
8. **Validierung am echten Gmail-Konto** (Migration greift automatisch): inbox,
   read, search (X-GM-RAW), send an eigene Adresse, reply — erst wenn das grün ist,
   weiter. py_compile + `/v1/status` ([[feedback_compile_check_brain_py]]).
9. Skill-Doku (`02-tools.md`, `06-user-manual.md` deutsch) + kuratierter
   Changelog-Eintrag (user+admin: „E-Mail funktioniert jetzt mit jedem Anbieter…")
   + VERSION — **im selben Commit** (Standing Rules).

### Phase 2 — POP3-Konnektor
`Pop3Connector` (E7) + Capability-Durchreichung in den Tools (Folder-Ignore-Hinweis,
Client-Suche-Kennzeichnung). Unit-Tests mit gemocktem poplib. Test gegen einen echten
POP3-Zugang (GMX/web.de bieten POP3; ansonsten lokaler Dovecot im Test vertagt —
dann ehrlich als „nur unit-getestet" ausweisen).

### Phase 3 — Exchange-Konnektor (EWS, On-Prem — User-Entscheid Nr. 2, Vorlage Anhang A)
`exchangelib` ins Server-Python; `ExchangeEwsConnector` (E8) exakt nach dem
verifizierten Specimen-Muster: `Credentials(username, password)` +
`Configuration(server=…, credentials=…)` → `Account(primary_smtp_address=email,
config=…, access_type=DELEGATE, autodiscover=False)`. Lesen: `account.inbox`
(`.all().order_by('-datetime_received')[:limit]`), Suche via QuerySet-Filter
(`subject__contains`, `sender`, …), Senden via `Message(...).send()` mit
`Mailbox(email_address=…)`-Empfängern + `FileAttachment`, Reply via `.reply()`
(Threading nativ). `verify_ssl:false` → `NoVerifyHTTPAdapter` (Anhang A — Achtung:
prozess-global). Lazy import + fail-loud. **Live-Validierung gegen den lokalen
Exchange-Server** (der Specimen-Zugang existiert — Server/Credentials beim User
erfragen und als Konto anlegen); Unit-Tests gegen gemocktes exchangelib zusätzlich.

### Phase 4 — Settings-UI: Konten-Editor
`settings_tools.js` Case `'gmail'` → `'email'`: Kontenliste (Karten je Konto:
Name, Typ-Dropdown, Preset-Dropdown mit Host/Port-Autofill, Felder je Typ,
Passwort maskiert wie bisher), Konto hinzufügen/entfernen, Default-Konto-Auswahl.
`buildToolIntegrationRec` entsprechend. Optional (empfohlen, klein):
`POST /v1/tools/email/test {account}` — Verbindungstest (IMAP-Login bzw. POP3-Login
bzw. EWS-Bind, KEIN Senden) mit Button je Konto. `chat_tools.js`-Labels +
`WRITE_EXEC`-Set (`settings_general_tabs.js`) auf neue Namen. `js_gate.sh` grün
(Rename = Globals-Count konstant halten). Skill `06-user-manual.md` (deutsch)
+ `04-recipes.md` (Konto-einrichten-Rezept) aktualisieren.

### Phase 5 — Feinschliff + Restdoku
`05-internals.md`, CLAUDE.md-Erwähnungen (`gmail_tools` → `email_tools`,
EGRESS-Kommentare), Sweep: `grep -rn gmail` muss danach nur noch Historie
(CHANGELOG, alte Memory-/Plan-Docs) treffen. Alle Test-Module grün.

---

## Explizit NICHT im Scope

- **Microsoft 365 / Graph API** — per User-Entscheid Nr. 2 ist On-Prem-EWS der
  Anwendungsfall; Graph (OAuth2, App-Registrierung, Token-Refresh) kommt nicht rein.
- **OAuth2/XOAUTH2** (Gmail-OAuth statt App-Passwort) — O2.
- **HTML-Mails senden** — Tools bleiben plain text (Empfang: HTML→Text-Strip bleibt).
- **Anhänge herunterladen** beim Lesen — Parität zu heute (nur Namensliste). O3.
- **Push/IDLE, Ordner-Management, Löschen/Verschieben/Markieren** — Read+Send-Parität
  zu heute, keine Mailbox-Verwaltung.
- **Scheduler-/Daemon-Polling** („bei neuer Mail X tun") — eigenes Feature, nicht hier.

## Offene Punkte (vor/während Umsetzung klären)

- **O1 — Exchange-Testzugang:** Gibt es einen erreichbaren EWS-Server + Credentials
  für die Live-Validierung von Phase 3? Ohne ihn Phase 3 nur unit-getestet ausliefern
  oder vertagen — User-Call ([[feedback_defer_to_users_migration_calls]]).
- **O2 — OAuth2:** Gmail-App-Passwörter setzen 2FA voraus, M365 verlangt Graph —
  wenn ein M365-Konto der eigentliche Anwendungsfall ist, Phase 3 zugunsten einer
  Graph-Variante neu bewerten, BEVOR exchangelib eingebaut wird.
- **O3 — Anhang-Download beim Lesen** (`email_read` → Datei in den Artifact-Ordner):
  klein und nützlich, aber neue GDPR-Fläche (Datei auf Platte = Klartext) — nur mit
  bewusster Seam-Entscheidung nachziehen.
- **O4 — `email_accounts` weglassen?** Falls Minimalismus gewünscht: der
  Unbekannt-Konto-Fehler nennt die Kontennamen bereits; das Tool ist trotzdem
  empfohlen (Modell kann proaktiv „beide Postfächer prüfen").
- **O5 — Per-Konto-Freigabe** (welcher User/Agent darf welches Konto): heute ist
  Gmail global für den Agenten konfiguriert; Mehrkonten machen eine Policy-Frage
  auf (analog `data_sources_access`). Default: wie heute global — Policy erst bei
  Bedarf.

---

## Anhang A — Verifiziertes EWS-Verbindungsmuster (aus der gelieferten `email_service.py`)

Vom User bereitgestellte, **produktiv funktionierende** Anbindung an den lokalen
On-Prem-Exchange (Order-Buch-Projekt; Datei nicht ins Repo übernehmen — sie enthält
projektfremden Flask/DB-Code, nur das Verbindungsmuster zählt). Destillat:

```python
from exchangelib import Credentials, Configuration, Account, DELEGATE
from exchangelib import Message, HTMLBody, FileAttachment, Mailbox
from exchangelib.protocol import BaseProtocol, NoVerifyHTTPAdapter

# Self-signed Cert auf dem Exchange-Server → TLS-Verify aus:
BaseProtocol.HTTP_ADAPTER_CLS = NoVerifyHTTPAdapter   # ACHTUNG: prozess-global!

credentials = Credentials(username=cfg.username, password=cfg.password)
ews_config  = Configuration(server=cfg.server, credentials=credentials)
account = Account(
    primary_smtp_address=cfg.email,
    config=ews_config,
    credentials=credentials,
    access_type=DELEGATE,
    autodiscover=False,        # direkte Server-Angabe, kein Autodiscover
)

msg = Message(
    account=account, subject=subject,
    body=body,                                        # oder HTMLBody(html)
    to_recipients=[Mailbox(email_address=r) for r in recipients],
)
msg.attach(FileAttachment(name=fn, content=data, content_type=ct))
msg.send()
```

**Übernahme-Notizen für den Konnektor:**
1. **Auth**: schlichtes `Credentials(username, password)` reicht — exchangelib
   verhandelt den Auth-Typ mit dem Server selbst. Kein NTLM-Feld, kein OAuth.
2. **`autodiscover=False` + `server`-Host** ist das funktionierende Muster —
   Autodiscover nur als optionales Konto-Flag anbieten, Default aus.
3. **Self-Signed-Cert-Falle**: `BaseProtocol.HTTP_ADAPTER_CLS = NoVerifyHTTPAdapter`
   ist eine **prozess-globale Klassenvariable**, kein Per-Connection-Knob. Ein
   Konto mit `verify_ssl:false` schaltet TLS-Verify damit für ALLE
   exchangelib-Verbindungen im Brain-Prozess ab. Akzeptabel (typisch existiert genau
   ein On-Prem-Exchange), aber im Code kommentieren + im Admin-GUI als Warnhinweis
   am Feld ausweisen — nicht stillschweigend.
4. **Senden as-authenticated-mailbox**: EWS sendet immer als das angemeldete
   Postfach (`sender_email`-Parameter im Specimen wird bewusst ignoriert) — passt
   exakt zur bestehenden Tool-Semantik (From = Konto-Adresse).
5. **Empfänger-Validierung**: das Specimen filtert Adressen per Regex vor dem Send.
   Für die Brain-Tools ist das doppelt nützlich: der GDPR-Egress-Gate-Vorfall
   (v9.343.0, brain.py:1501 ff.) zeigte, dass opake Pseudonym-Tokens nur „zufällig"
   am Adressformat scheiterten — eine explizite RFC-Format-Prüfung in
   `tool_email_send` (VOR dem Konnektor, für alle Typen) macht das deterministisch.
6. **Lesen fehlt im Specimen** (reiner Send-Service) — `account.inbox` +
   QuerySet-Filter sind Standard-exchangelib und werden in Phase 3 live gegen den
   lokalen Server validiert.
7. **Verbindungstest**: das Specimen testet per echtem Test-Mail-Versand. Für den
   Admin-GUI-Test (Phase 4) reicht der `Account`-Bind + ein `inbox.total_count`-Read
   — KEIN Send (side-effect-frei).
