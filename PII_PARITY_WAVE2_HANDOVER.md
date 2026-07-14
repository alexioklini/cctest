# PII-Parität — Welle 2 (M1–M11) · Handover

**Stand:** 2026-07-14 · Basis-VERSION `9.342.0` · **Status: M1/M2/M3/M11 GELIEFERT in v9.343.0** (die vier Leck-Stopps). **OFFEN: M4/M5, M6, M7, M8, M9, M10** (die Qualitäts-Bausteine).

**Vorgänger:** `PII_ANALYSIS_PARITY_HANDOVER.md` (Serie L1–L7, v9.334.0–9.342.0, KOMPLETT). Der dortige Katalog bleibt gültig — diese Welle *ergänzt* ihn, sie widerruft nichts.

---

## STATUS nach Session 1 der Welle 2 (v9.343.0)

### Geliefert — die vier Leck-Stopps

| # | Baustein | Schließt | Ergebnis |
|---|---|---|---|
| **M1** | Mapping in JEDEN Turn | **G1** | ✅ `gdpr_bind_mapping()` **mit Rehydration aus chats.db**; Sub-Turns erben das Parent-Mapping; Scheduler behält seine ID; `delegate_task`-Deanon-Leak entfernt |
| **M2** | Egress-Gate auf alle Egress-Tools | **G7** | ✅ `EGRESS_TOOLS` (Web ∪ gmail ∪ generate_image ∪ MCP); gmail-Anhänge fail-closed; `generate_image` mapping-UNABHÄNGIGER Cloud-Scan; Shell/Script **deny-by-default**; MCP-Result-Seam |
| **M3** | Seam-Lücken | **G8, G9, G10** | ✅ 12 Seams (translate, wiki_read/write, context_*, gmail_read/*, use_skill, transcribe, MCP, pinned, inject, ask_user) + 3 vergessene Gates (wiki_from_chat, audio_overview ×2) |
| **M11** | Neutrale Attachment-Dateinamen | **G14** | ✅ `att_01.pdf`; Originalname als **gescannter Inhalt**; Pfad bleibt echt → `read_document` unverändert |

**Tests:** `tests/test_gdpr_mapping_every_turn.py` (11, **mutationsgeprüft**), `tests/test_gdpr_egress_gate.py` (13, Kalibrierungs-Matrix in beide Richtungen), `tests/test_attachment_neutral_names.py` (9). 39 Test-Module grün. Live-E2E gegen die echten Module: Gate feuert im Background-Turn für alle 4 Egress-Tools; 13/13 Seams pseudonymisieren; Normalbetrieb unverändert.

### Drei Befunde, die die Analyse NICHT hatte (für die nächste Session relevant)

1. **Rehydration ist Pflicht, nicht Kür.** Decision 5 („Sub-Turns erben das Parent-Mapping") wäre auf langen Fan-outs still gescheitert: der interaktive Worker ruft `close_mapping()` in seinem `finally`, der Registry-Eintrag ist also weg, bevor ein 40-Minuten-Leaf sein erstes Dokument liest → `get_mapping()` → `None` → jeder Seam liest das als „keine Anonymisierung aktiv". Die verschlüsselte `pseudonym_maps`-Zeile überlebt; `gdpr_bind_mapping` lädt daraus nach.
2. **contextvars-Falle — NIE Request-State aus einer Funktion binden, die auf einem kopierten Context laufen kann.** Der naheliegende Fix (Mapping direkt im Gate `gdpr_pick_model_for_background` binden → alle 26 Aufrufer auf einen Schlag) ist FALSCH: `deep_research`/`kg_extract` dispatchen via `copy_context().run(...)`, und das kopiert die ContextVar-**Bindings**, nicht das `RequestContext`-**Objekt** dahinter. Eine Attribut-Mutation dort blutet in die Geschwister-Tasks UND zurück in den Parent (mit einem Scratch-Skript nachgewiesen). Der Gate MELDET die ID jetzt nur (`deanon_fn.mapping_id`).
3. **`apply_known_values` ist KATEGORIE-GEFILTERT** (default `name`/`email`/`passport`/`dob`). Ein Mapping-Eintrag unter einer anderen Kategorie (z. B. `contact`) wird vom Known-Values-Sweep **nicht** erfasst. Immer über `Mapping.record(orig, fake, rule_id)` registrieren, nie `.forward`/`.reverse` direkt beschreiben. (Kostete beim E2E eine halbe Stunde Fehlersuche — der Seam war korrekt, die Test-Fixture nicht.)

### Nicht wiederholen — zwei Sackgassen

- **Kein zweiter Seam auf bereits gefakten Text.** Fakes sind shape-preserving, real aussehende Namen: ein Re-Scan klassifiziert sie als frische PII und mintet **Fakes-von-Fakes** → der Reply-Deanonymisierer bricht, und die NUTZERIN sieht den Fake. Deshalb ist der BG-Task-Preamble bewusst **nicht** geseamt (M1 stellt ihn schon in die richtige Fake-Welt) und der Seam **nicht** in `_inject_web_preamble_into_wire` (den geteilten Choke-Point) gehoben — der Web-Preamble kommt bereits geseamt aus `tool_web_fetch`.
- **Token-Allowlisting für `python_exec` ist eine Kategorieverwechslung.** `df = …`, `for r in …`, `print(…)` sind keine Kommandos. Die erste Fassung des deny-by-default verweigerte damit JEDE pandas/openpyxl-Analyse — das hätte das Leck gegen eine stille **Qualitäts**-Regression getauscht. Richtig: `python_exec` an seinen **Imports** messen, `execute_command` an seinen **Kommando-Tokens**.

### Entscheidungen dieser Session (nicht neu aufrollen)

- **MCP:** pauschal konservativ (kein Args-Deanon, Gate JA, Result-Seam JA). Kein `local:true`-Flag.
- **`kyc`-Preset hebt `organisation` NICHT auf `anonymise`.** Stattdessen für Welle 3: **eigenes Preset `screening`** (Personen anonym, Orgs im Klartext + Web frei) — für `risikoanalysen`/`compliance-prüfung` der ehrlichere Modus, weil dort die FIRMA das Prüfsubjekt ist und ihr Name ohnehin an die Suchmaschine muss. `kyc` bleibt unverändert.
- **M10:** Variante **(b)** — Egress-Schutz an den Scanner koppeln (nicht ans Preset). Noch nicht gebaut.

### Dokumentierter Rest (bewusst offen, nicht vergessen)

- **BG-Task vor Anonymisierungs-Beginn gestartet:** lief ungemappt, sein Output kommt roh im Preamble an. Die Werte fängt der Ledger-Rewrite **einen Turn später**. Sauber wäre eine `gdpr_mapping_id`-Spalte auf `background_tasks` — bewusst vertagt statt mit einer Heuristik gefaked.
- Alles aus §3 („Was auch nach M1–M11 NICHT erreichbar ist") gilt unverändert.

**Nächster sinnvoller Schnitt:** **M4+M5 als Paket** (Org-Entitäten + Auto-Release) — der größte Qualitätshebel für den realen Mehrheits-Workload. `screening`-Preset dabei mitentscheiden. Danach M6 (Tabellen, mit Lasttest VOR dem Design) und M9 (Erkennungs-Netz).

---

---

## 0. Warum es eine zweite Welle gibt

L1–L7 wurde an **einem** Chat kalibriert: `58e3c521438a` (Projekt `ko-kunden`, KYC-Betrugsprüfung **einer Privatperson** mit Pass-Fotos). Der Chat war repräsentativ für *seine* Form — und nur für die.

Diese Analyse hat **alle Nicht-Code-Projekte und die projektlosen Analyse-Chats** gegen die fertige Pipeline simuliert (7 parallele Auditoren, ~90 real gelesene Sessions + ein Code-Audit der Nachbarkanäle; Belegstellen unten). Ergebnis:

> **Die Pipeline schützt und erhält die Analysequalität für den kalibrierten Fall — „eine Person, Dateien, Web, Chat-Turn". Sobald der Workload davon abweicht, bricht sie in drei Richtungen: (1) das Analyse-Subjekt ist eine ORGANISATION, (2) die Werte kommen als TABELLE/Massendaten statt als Prosa, (3) die Arbeit verlässt den interaktiven Chat-Turn (Background, Scheduler, Bild, Mail, MCP, Wiki, Audio).**

Und: **der reale Nutzungsschwerpunkt liegt außerhalb des kalibrierten Falls.** Von den ~90 Analyse-Sessions liefen die meisten **projektlos** (kein Preset, kein Turn-1-Auto-Anonymise) und **org-zentriert** (Firmenbewertung, KYC-Firmen, UBO, Adverse-Media, Sanktionslisten).

**Ziel dieser Welle (unverändert zur ersten):** Analysequalität mit aktivem Scanner ≈ Analysequalität ohne Scanner, ohne Klardaten-Egress. Neu ist nur, für *welche* Workloads das gelten muss.

---

## 0.1 Die drei Leitsätze dieser Welle

1. **Was für Personen gebaut wurde, muss für Organisationen existieren.** Die Entitäts-Schicht (L2) ist der Kern der Qualität — sie kennt nur `PER`. Firmen/Trusts/Fonds haben *nur* exakt-String-Fakes. Jeder org-zentrierte Workload (das ist die Mehrheit) erbt damit exakt die F1/F3-Failures, die L2 für Personen gelöst hat.
2. **Was für Prosa gebaut wurde, muss für Tabellen existieren.** NER ist auf Sätze trainiert. Der reale PII-Träger ist die Zelle: `KO TULLNERSAntonius`, `Pölzl Philipp`, `19470205`, `300622-800-1`. Dort ist der Recall nahe null — und ausgerechnet dort steht die *Masse* der Betroffenen.
3. **Was für den Chat-Turn gebaut wurde, gilt nur im Chat-Turn.** Der `_gdpr_mapping_id` lebt im `RequestContext` des interaktiven Workers. Jeder andere Turn (Scheduler, Background-Task, Delegation) läuft **ohne** — dort sind Result-Seams, Args-Deanon **und das Web-Gate** schlicht tot.

---

## 1. Der Failure-Katalog der zweiten Welle (G1–G12)

Nummerierung `G*` (Gap) statt `F*`, um Verwechslung mit dem L-Katalog zu vermeiden. „Beleg" = Datei:Zeile oder Session-ID. Severity: **leak** > **falsche-analyse** > **degradation** > **kosmetik**.

---

### G1 — Background-/Scheduler-Turns laufen komplett ohne Mapping · **leak** · *der schwerste Befund*

`_apply_bg_context` (`handlers/sidecar_proxy.py:145-180`) rekonstruiert ~20 Kontextfelder für Background-Turns — **`_gdpr_mapping_id` ist nicht dabei** (verifiziert; nur `run_turn` setzt es, `sidecar_proxy.py:284-285`). Der Scheduler baut seinen `tool_context` ebenfalls ohne (`engine/scheduler.py:1328-1332`).

Konsequenz, weil **alles** an dieser einen ID hängt (`brain._gdpr_anon_tool_text`, `brain.py:3157-3161`; Web-Gate, `brain.py:3586-3590`):

- Result-Anonymisierung: **tot** → `read_document` auf die Kundenakte liefert Klartext ans Cloud-Modell.
- Args-Deanon: **tot**.
- **Web-Egress-Gate: tot** → ein geplanter Lauf oder ein Fan-out-Leaf darf Klarnamen ungehindert googeln. Das ist exakt das Leck, das L4 im Chat geschlossen hat — eine Ebene tiefer.

Nur der *Einstiegs-Prompt* wird gegated (`gdpr_pick_model_for_background`). Danach ist der agentische Lauf blank.

**Trifft real:** Deep-Research-Fan-outs im Projekt `compliance-prüfung` (Adverse-Media über Personen; `502f561fd123`, `27ba68c6cc0e`, `7a53e1fc6701`), alle `run_background_task`-Sessions, jeden geplanten Task.

---

### G2 — Keine Entitäts-Schicht für ORGANISATIONEN · **falsche-analyse** · *der größte Qualitätshebel*

L2 gab Personen eine Entität mit Varianten (Case/Initialen/MRZ/Komma/ALLCAPS/Slug → **ein** Fake). Organisationen haben das nicht: jede Oberflächenform bekommt einen **eigenen** Fake.

**Empirisch am echten Scanner gemessen** (Offline-Lauf gegen `engine/pii_ner._pii_scan_text`, Session `bcad56fa99f8`): `Wiener Privatbank SE`, `Wiener Privatbank`, `Wiener Privatbank Immobilien GmbH`, `Matejka & Partner` (ohne „Asset Management GmbH"!), `Matejka`, `K5 Beteiligungs GmbH.` (mit Satzpunkt) = **je ein eigenes Finding** → je ein eigener Fake. **`WPB` wird gar nicht erkannt** → bleibt roh neben den Fakes stehen (= Split-Brain *und* ein Deanonymisierungs-Anker für den Cloud-Provider).

Was daran zerbricht — jeweils real belegt:

- **Konzern-/UBO-Struktur:** „Wiener Privatbank **Immobilien** GmbH ⊂ Wiener Privatbank" — die Mutter-Tochter-Beziehung steckt im *Namens-Enthaltensein*. Getrennte Fakes löschen sie. Ebenso `3SI Holding` / `3SI Partner` / `3SI Immo` (`748f92cfeacf`), `LEBC Star Trust` / `THE LEBC STAR TRUST` / `Intertrust … Ltd.` vs `… Limited` (`65b4aefeed11`).
- **Sanktions-/Registry-Membership-Check** (der Compliance-Kern): „Steht `ABACO OVERSEAS HOLDINGS INC.` in der Struck-off-Liste / auf der OFAC-SDN?" Listen führen ALLCAPS-/Alias-Formen. Anderer String → anderer Fake → **stiller False Negative in einem regulatorischen Bericht.** Belegt in `99a38595f0ca` (OFAC-SDN), `32e257377809`/`23a01f7651f2` (Bahamas-Registry), `6afd1872fe7e` (Companies House).
- **Retrieval:** das Modell mutiert Org-Namen selbst (lowercase, „INC." weggelassen — real beobachtet in `840e1858a899`) → L3a-Deanon matcht exakt-String nicht → `mempalace_kg_query` läuft mit mutiertem Fake → 0 Treffer.
- **Abgleich-Tasks** („stimmt Excel-Zeile == Firmenbuchauszug?") produzieren falsche „stimmt nicht überein"-Befunde (`4aad5750c260`, `7b3e9217c933`).

---

### G3 — Org-Recherche-Selbstblockade: die Komposition tötet den Use-Case · **degradation (total)**

Das L4-Gate lässt `organisation`/`business_id` **beim Frisch-Scan durch** (`_WEB_GATE_PASS_CATEGORIES`, `brain.py:3272`) — gut gemeint. Aber: sobald der Org-Name im Wire **gefakt** ist, kennt das Modell nur noch den Fake — und **Fakes werden in JEDEM Modus refused** (bewusste, richtige L4-Invariante).

→ Jede Einzelkomponente verhält sich as designed; **die Komposition macht Firmen-Recherche unmöglich.** Der Wert, den die Policy passieren ließe, kann das Gate nie erreichen.

Das ist keine Randnotiz: 15–30 Web-Calls **pro Session** in `risikoanalysen`, `compliance-prüfung`, `firmenbewertung` sind genau das. Adverse-Media-, Sanktions- und Registry-Screening *ist* der Projektzweck.

**Die saubere Lösung ist bereits gebaut** — sie wird nur nicht angewandt: die `release_web`-Hin-Übersetzung (Fake→Original nur im ausgehenden Request, `_web_release_translate_args`, v9.338.0). Für Kategorien, die die Policy **ohnehin passieren lässt**, muss sie automatisch laufen statt zu refusen.

---

### G4 — Tabellen/Massendaten: Erkennung nahe null, ausgerechnet dort, wo die Betroffenen sind · **leak**

Prosa-NER trifft die Zelle nicht. **Empirisch am echten Scanner (0 Findings)**:

| Form | Quelle | Ergebnis |
|---|---|---|
| `KO TULLNERSAntonius`, `KO DEMAIN Sanf.ESTAT`, `KO SELUCKY Mic or Ka`, `KO WHEELER o BEAUDET` | KO-Kunden-xlsx (Excel-Truncation) | nicht erkannt |
| `Pölzl Philipp` (Nachname-Vorname, kein Komma), Spalten `NAME`/`AUFTRAGGEBER_1..6` | Broker-Orders (`414d69fc53a0`) | Tabellen-NER unzuverlässig |
| `19470205` (DOB als YYYYMMDD, Header weit weg) | KO-Kunden-xlsx | nicht erkannt |
| Kd.Nr. `107625`, Depot `300622-800-1` | KO-Kunden-xlsx | nicht erkannt |
| `Tambas` in „Tambas-Notiz" (Kompositum) | Freitext-Spalte | nicht erkannt |
| `wer ist michael munterl` (lowercase, getippt) | `0a8f8a6273bf` | nicht erkannt |

**Das Excel wird in jeder ko-kunden-Session als erstes Tool-Result gelesen** → Dutzende Kundennamen + DOBs + Kontonummern gehen in Turn 1 roh an die Cloud, während *einzelne* erkannte Namen gefakt werden. Halb-Hybrid im Massenmaßstab.

**Zusatzschaden, empirisch:** die blinde `ca_sin`-Regel (kanadische Sozialversicherungsnr.) matcht `300622-800` als **Substring** von `300622-800-1` → die Zelle wird zu `<TOKEN>-1` → Konto-Joins und Summen-Checks brechen, und der Wert ist als *kanadische* ID falsch klassifiziert.

---

### G5 — Schreibpfade außerhalb des Artefakt-Baums: stille Fake-Dokumente · **falsche-analyse**

`make_gdpr_after_file_write_cb` bailt bei `not engine._is_artifact_path(path)` (`handlers/chat.py:2239`). Schreibt das Modell auf einen **absoluten Pfad** — was Nutzer real anweisen —, gibt es **keinen Reverse, keinen Lint, keine Warnung.**

**Real getroffen, 3×:** zwei HV-Sprechvorlagen (`.docx`, Session `6c8dc5937f2c`) und ein `konzernstruktur_neu.html` (`40efd852ca83`) ins Repo-Root. Ergebnis wäre: eine Sprechvorlage für eine **echte Hauptversammlung** mit **erfundenen Namen** von Aufsichtsrat, Vorstand, Prüfern und Notar — ohne jede Kennzeichnung. Das ist F6 („der Report lügt leise") in seiner reinsten Form, und L6 fängt es nicht, weil L6 den Artefakt-Baum voraussetzt.

---

### G6 — Bild-Artefakte: Fakes werden in Pixel gebrannt, ungelintet · **falsche-analyse**

`render_diagram` ∉ `GDPR_ARGS_DEANON_TOOLS` → Mermaid-Quelle trägt Fakes. PNG/SVG fallen aus dem File-Callback (`ext not in SUPPORTED_EXTS and ext not in _GDPR_LINT_ONLY_EXTS`, `handlers/chat.py:2241-2244`; `_GDPR_LINT_ONLY_EXTS = {".pdf"}`) → **kein Restore, kein Lint, keine Warnung, kein Degradations-Zähler.**

**Real und massiv:** 20+ `render_diagram`-Calls + 5 `generate_image`-Charts in `28e2f5cc1f4e`, 6 Diagramme in `d100c1dca495` (mit Firmenbuchnummern + Personen-Timeline), UBO-Diagramme in `65b4aefeed11`, 4 Diagramme in `04ac769e8e6a`. Sie werden per `![](…)` in die finale `.docx`/`.html` eingebettet, **deren Text rückübersetzt wird**.

→ **Ausgeliefertes Dokument: echter Text, falsche Grafiken.** In sich widersprüchlich, ohne Hinweis.

**Nebenbefund:** `.svg` ist Text-XML und **wäre trivial reversibel** — wird aber übersprungen.

---

### G7 — Egress-Kanäle außerhalb des Web-Gates · **leak**

Das Gate ist auf `WEB_SEARCH_TOOLS` gescopet (`brain.py:1470` + `:3583`). Alles andere, was die Maschine verlässt, ist ungegated:

- **`gmail_send`/`gmail_reply`** → direkt an `smtp.gmail.com:465` (`engine/tools/gmail_tools.py:283-285`). **Live-Specimen `30051b1f4439`:** das Modell rief `gmail_send(to="<EMAIL_1_a812>", body="Here is the requested IBAN: DE38…")` — der Send **ging real raus** und scheiterte nur daran, dass das opake Token zufällig keine RFC-gültige Adresse ist. Eine *shape-preserving* Fake-Adresse (die Pipeline erzeugt genau solche!) **wäre zugestellt worden** — an einen fremden Dritten, mit einer Fake-IBAN als echt präsentiert.
  Verschärfung: `attachments` liest Artefakt-Dateien von Platte — **die sind bereits deanonymisiert** (`chat.py:2348-2350`) → Mail mit Fake-Body + **Klartext-Anhang**. Und `gmail_read`/`gmail_search`-Results haben **keinen Seam** → fremde Mail-Inhalte roh ans Cloud-Modell.
- **`generate_image`** → immer `https://api.mistral.ai/v1/conversations` (`engine/tools/image_gen.py:35`, `:148-158`), unabhängig vom Session-Modell, **null GDPR-Logik im Modul**. Real genutzt mit Konzernstruktur + Kennzahlen im Prompt (`40efd852ca83`).
- **`execute_command`/`python_exec` per Mail-Seitentür:** `_DEANON_NETWORK_MARKER_RE` (`brain.py:3891`) kennt `curl|wget|nc|ssh|scp|telnet|ftp|nslookup|dig|ping|urllib|requests.|http.client|socket.|aiohttp|httpx` — **nicht `smtplib`, nicht `sendmail`, nicht `mail`, nicht `msmtp`, nicht `mailx`, nicht `osascript`** (steuert Apple Mail/Messages), nicht `gh`. **Live-Specimen `b4edbc9dc8e7`:** das Modell versuchte `mail -s "IBAN <fake>" <EMAIL_1_8d62>` und `sendmail … <<< …` — damals scheiterte es an zsh-Syntax. **Heute würde L3a diese Args deanonymisieren** → echte Mail mit echter IBAN, am Gate vorbei.
- **MCP-Tools:** der Fallback-Zweig (`engine/llm_loop.py:774-782`) hat **weder Args-Deanon noch Result-Seam noch Gate** → Fakes an einen potenziell **remote** MCP-Server (Egress) und dessen Antworten (CRM/Kalender/Mail) **roh** ans Cloud-Modell. Der einzige komplett seam-freie Tool-Pfad im Dispatcher.
- **Telegram-Frontend** (`frontends/telegram.py:90`, `:134`) sendet die **deanonymisierte** Antwort an `api.telegram.org` — by design, aber unter einem GDPR-Preset ein unbedachter Klartext-Egress an einen US-Dienst.

---

### G8 — `translate_text` restauriert Echtwerte **ins Tool-Result** · **leak** · *deterministisch*

`tool_translate_text` ist eine `background_call` und korrekt gegated. Aber `_anonymise_background_samples` **re-nutzt das Session-Mapping**, wenn `current_session_id` gesetzt ist (`brain.py:12147-12164`) — beim interaktiven Tool-Dispatch ist sie es immer. Also läuft `deanon_fn` über das Session-Mapping und `result = _xlate_deanon(reply)` (`engine/.../text.py:151`) **stellt die Echtwerte wieder her**. `tool_translate_text` gibt genau das zurück (`return _ok(result)`, `translate_tools.py:569`) — **ohne Re-Anonymisierung** (die Datei ruft `_gdpr_anon_tool_text` nirgends).

→ **Klartext im Cloud-Wire, jedes Mal, wenn gemappter Inhalt übersetzt wird.** Sauber im Code belegbar, nicht live reproduziert.

---

### G9 — Memory-/Wiki-/Audio-Verdichter schicken den Roh-Korpus in die Cloud · **leak**

Die **DB hält Klartext** (nur der Wire wird umgeschrieben — `handlers/chat.py:4090-4106`, Kommentar dort: *„the DB row holds the ORIGINAL"*). Jeder Konsument von ChatDB-Inhalt arbeitet also auf Roh-PII. Zwei tun das ungegated:

- **`wiki_from_chat`** (Auto-Memorize / `save_chat_to_memory`): schickt den **rohen ~24-KB-Gesprächskorpus** per `background_call` ans `wiki_model` (`wiki_store.py:846-850`) — **während der direkt daneben stehende `wiki_worth_saving` korrekt gegated ist** (`:752`). Das ist ein vergessener Call, kein Design.
- **Audio-Overview / Podcast:** `_corpus_to_audio` schickt den kompletten Korpus ungegated (`audio_overview.py:609-614`, ebenso `:478`); die TTS-Zeilen gehen zusätzlich an den Mistral-Provider (`:345-355`).

**Dazu die Wiki-Gegenrichtung:** `wiki_write`-Content sind **Modell-Fakes** (∉ Deanon-Whitelist) → **Fakes werden dauerhaft persistiert** und in die Wings gespiegelt (`mempalace_glue.py:1299-1302`). Das Mapping ist **per Session** → Session B liest die Fakes aus Session A als vermeintliche Echtdaten: weder deanonymisierbar noch konsistent zum eigenen Mapping. **Dauerhafte Gedächtnis-Vergiftung.** Und `wiki_read` hat **keinen Result-Seam** (`:1311ff`) → Wiki-Seiten mit Echtdaten (aus Nicht-Anon-Sessions) gehen roh ans Cloud-Modell.

---

### G10 — Ungescannte Wire-Injektionen · **leak**

Drei Wege, auf denen Roh-PII **am Scanner und am Ledger vorbei** in den Cloud-Wire gelangt (und die deshalb auch **kein Self-Heal** in Folge-Turns erzeugen, weil kein Ledger-Eintrag entsteht):

- **Pinned Sources:** `_build_pinned_sources` liest den vollen Dokumenttext und injiziert ihn (`handlers/chat.py:263-318`, Injektion `:4446-4454`) — **ohne Scan/Seam**. (Der Websuche-Prefetch ist *nicht* betroffen: der läuft als `tool_web_fetch`-Caller durch den L3b-Seam.)
- **BG-Task-Preamble:** `_build_background_task_preamble` (`chat.py:1467`) wird bei `:4461-4464` **NACH** dem Ledger-Rewrite injiziert. Das ist das F5 aus dem L-Katalog — **nicht geschlossen**.
- **`POST /v1/chat/inject`** (`chat.py:6433-6449`) → `drain_injections` (`llm_loop.py:962-971`) und **`ask_user`-Antworten** (`_ok({"answer": …})`, `ask_tools.py:578`) landen ungescannt im Loop.

---

### G11 — Citation-Validator läuft **vor** der Deanonymisierung · **falsche-analyse/degradation**

**Verifiziert:** `engine.validate_citations_in_response(reply, …)` bei `handlers/chat.py:5077`; `pseudonymizer.deanonymize_text(reply, …)` erst bei `:5210`.

Der Validator matcht also das **Fake**-Zitat byte-genau gegen die **echten** Quelldateien → **jedes wörtliche Zitat, das einen geschützten Wert enthält, wird „unverified".** In research_mode/Citation-Discipline-Projekten (KG-Real-Policies, compliance-prüfung, firmenbewertung, alle DD-Reports) hängt damit an praktisch jeder Antwort ein **falscher** Fidelity-Warnblock. Die Eval-Reihen würden systematisch schlechter scoren, **ohne dass die Antwortqualität real sinkt.**

---

### G12 — Erkennungs-Rest: Schriftbild, Sprache, Alphabet · **leak**

- **Sperrschrift** (notarielle Konvention): `Dr. Gottwald K R A N E B I T T E R`, `Herr Günter K E R B L E R` → **empirisch 0 Findings**, während die Normalform desselben Namens im selben Dokument gefakt wird. Der Cloud-Provider kann das Mapping damit **trivial invertieren** (die gesperrte Zeile nennt Funktion + Vorname). Belegt: `6c8dc5937f2c` (HV-Protokolle).
- **Kyrillisch / Transliteration:** der `compliance-prüfung`-Korpus enthält 162× „Рахметов" + russische Gerichtsdokumente; Queries laufen über „Юг-Авто"/„OOO Yug-Avto". Die Varianten-Schicht kennt **keine Transliteration**. Verschärfung: das Modell **generiert die kyrillische Variante selbst** (real: „Милко Борисов" aus dem Klarnamen, `1a830369e762`) — unter Anonymise transliteriert es dann den **Fake** → eine Form, die weder Original- noch Fake-Erkennung des Gates kennt.
- **Englisch:** deutsche NER auf englischem Content erkennt inkonsistent (empirisch: „Craig Federighi" ja, „Tim Cook"/„Joe Rossignol" nein; Span „By Kelly Woo" inkl. „By"). Der `ko-kunden`-Korpus ist >50 % englisch, `compliance-prüfung` teils russisch/französisch (Todesanzeige mit ~12 Angehörigen, `2c92112f2167`).
- **Verbalisierte PII** (Audio-Transkript der Video-Legitimation, ko-kunden): buchstabierter Name („B-O-N-N-E"), buchstabierte Mail („kbstart. P-A-C-B-E-L-L dot net"), diktierte Passnummer mit Bindestrichen. Keine der 71 Regeln greift.
- **Nicht-Prosa-Encodings:** `unzip -p sheet1.xml` → Namen in XML-Markup (`<t>Anna Becker</t>`), SharedStrings-Splits, base64 (`d1d4b4f6a2d8`). Vermutet, nicht gemessen.
- **Familien-Join:** `Rakhmetov` / `Rakhmetov**a**` (gendered) bekommen zwei unabhängige Fake-Identitäten → **das Verwandtschafts-Indiz, auf dem die DD-Schlussfolgerung steht, wird gelöscht.**
- **Kategorie-Flip:** ein „Firmen"-Kunde entpuppt sich mid-Session als verstorbene Privatperson (`2c92112f2167`: Bachmann Int = Leopold Lyko) → Org-Fake und Personen-Fake derselben realen Identität, ohne Verknüpfung.
- **Homonym-Verschmelzung:** drei reale „Atlantic Trading" (Kunde / Wiener GmbH / US-Community) kollabieren durch Exakt-String-Keying auf **einen** Fake → das Modell kann sie im anonymisierten Raum nicht mehr trennen (`9e4d3434bd59`). Umgekehrter F1: nicht Spaltung, sondern **Verschmelzung**.

---

### G13 — Der Schutz ist Projekt-Preset-zentriert, die Arbeit ist es nicht · **struktur/leak**

`gdpr_preset` hängt an `project.json`. Aber die **Mehrheit der realen KYC-/DD-/Compliance-Chats lief projektlos** (`587a737dc21d`, `1a830369e762`, `088683fc47bc`, `65b4aefeed11`, `4aad5750c260`, …): kein Preset → kein Turn-1-Auto-Anonymise → kein `web_egress`-Modus. Der Klarname bzw. die IBAN ging in **Turn 1** raus, bei mehreren sofort **ins Web**, bevor irgendein Schutz greifen konnte.

Der globale Scanner warnt nur; Auto-Anonymise braucht Modal-Accept oder Sticky — beides war nie gesetzt.

---

### G14 — Der Klarname im Attachment-Pfad · **leak** · *als „by design" fehlklassifiziert*

Der L-Katalog führt die Pfad-Exemption als **bewusste, unvermeidbare** Restlücke (known-open #6). **Diese Einordnung ist falsch — sie ist fixbar** (siehe M11). Sie hier zu belassen wäre der teuerste Fehler dieser Analyse, denn die Lücke trifft **jeden** Cluster maximal:

- `risikoanalysen`: der Dateiname nennt in **jeder** Session Subjekt *und* Prüfzweck — `Geldwäsche Risikoanalyse M&P AM_2025.xlsx`. Das Analyse-Subjekt ist gegenüber dem Cloud-Provider damit **de facto deanonymisiert**, egal wie gut der Content geschützt ist.
- `ko-kunden`: der gesamte Korpus ist über `CF_-_…_STARK_Bonnie_M_Mrs._107625_…` indexiert → jede `mempalace_query`-Antwort und jedes `list_directory` shippt effektiv die Kundenliste als Pfade.
- `compliance-prüfung`: `Reisepass_-_RAKHMETOVA_Diana`, russische INN/OGRN im Dateinamen.
- **Härtester Einzelfall:** ein Attachment namens `Alcuatmisi02026!.txt` (`4a6b889aee66`) — offensichtlich ein **Passwort**, das per Pfad-Exemption ungescannt in den Wire geht.

**Warum die Exemption existiert** (Docstring `_split_attachment_notice`, `handlers/chat.py:181-195`): (1) NER halluziniert auf Boilerplate (`"IMPORTANT"` → Organisation, Dateinamen → Adressen); (2) **ein pseudonymisierter Pfad bricht `read_document`** — das Modell bekommt einen Fake-Pfad und findet die Datei nicht.

Grund (2) ist der harte — **aber er unterstellt, dass der Pfad auf Platte den Klarnamen tragen MUSS.** Das stimmt nicht: Brain legt die Datei selbst an (`handlers/chat.py:7282-7284`), der Name ist frei wählbar:

```python
fname = f.get("name", "file")
safe_name = fname.replace("/", "_").replace("\\", "_")   # ← hier entsteht der Klarname im Pfad
fpath = os.path.join(attach_dir, safe_name)
```

---

## 2. Die Bausteine (M1–M11)

Reihenfolge = Empfehlung. Begründung darunter.

| # | Baustein | Schließt | Aufwand | Typ |
|---|---|---|---|---|
| **M1** | **Mapping in JEDEN Turn** (bg/scheduler/delegation) | **G1** | **S** | Leak-Stopp |
| **M2** | **Egress-Gate auf alle Egress-Tools** (gmail, generate_image, MCP, Shell-Mail-Marker) | **G7** | **S–M** | Leak-Stopp |
| **M3** | **Seam-Lücken schließen** (translate, wiki_read, context_*, gmail_read, MCP-Results, use_skill; Pinned/Inject/ask_user scannen) | **G8, G9, G10** | **M** | Leak-Stopp |
| **M4** | **Org-Entitäts-Schicht** | **G2** | **L** | Qualität |
| **M5** | **Auto-Release für Gate-passierende Kategorien** | **G3** | **S** | Qualität |
| **M6** | **Tabellen-/Massendaten-Erkennung** (Spalten-Heuristik, ID-Regeln, längste-zuerst) | **G4** | **M** | Leak-Stopp + Qualität |
| **M7** | **Artefakt-Vollständigkeit** (Nicht-Baum-Pfade, Bild-Artefakte, SVG-Reverse) | **G5, G6** | **M** | Fail-loud |
| **M8** | **Citation-Validator hinter den Reverse** | **G11** | **S** | Qualität |
| **M9** | **Erkennungs-Netz** (Sperrschrift, Transliteration/Kyrillisch, EN-NER, Verbalisierung, Familien-Stamm, Homonyme, same_as) | **G12** | **M–L** | Leak-Stopp + Qualität |
| **M10** | **Ad-hoc-Schutz ohne Projekt** (Preset entkoppeln) | **G13** | **S–M** | Struktur |
| **M11** | **Neutrale Attachment-Dateinamen** (Klarname als *Inhalt*, nicht als Pfad) | **G14** | **S** | Leak-Stopp |

**Warum diese Reihenfolge:** M1–M3 sind **Leck-Stopps mit kleinem Eingriff** — sie machen die Zusage „anonymisiert" erst wahr (heute ist sie an mehreren Stellen falsch, und *falsches Sicherheitsgefühl ist ein eigener Schaden*, F5 des L-Katalogs). **M11 gehört zu dieser Gruppe** (S, mechanisch, schließt ein Leck, das in JEDEM Cluster hart getroffen hat) — er ist nur deshalb hinten einsortiert, weil er als einziger die Attachment-Ergonomie sichtbar verändert. M4/M5 sind der **Qualitätshebel für den realen Mehrheits-Workload** und hängen zusammen: M5 ist wertlos ohne M4 (ohne Org-Varianten trifft der Auto-Release die falschen Formen). M6/M9 heben den Recall dort, wo die Masse der Betroffenen liegt. M7/M8/M10 sind Vertrauens- und Struktur-Schicht.

---

### M1 — Mapping in jeden Turn · **S** · schließt G1

**Der Fix ist klein und mechanisch** — das Feld existiert bereits im interaktiven Pfad, es wird nur nicht durchgereicht:

1. `brain.build_tool_context(...)` bekommt `gdpr_mapping_id` mitgegeben (heute Default `""`, `brain.py:8575-8600`) — Caller: `engine/scheduler.py:1328`, `engine/background_tasks.py`, `brain._run_delegate`-Nachfolger.
2. `handlers/sidecar_proxy._apply_bg_context` (`:145-180`) setzt `tl._gdpr_mapping_id = ctx.get("gdpr_mapping_id") or ""` — **eine Zeile**, analog zu den ~20 Nachbarfeldern.
3. Woher kommt die ID im Background? Zwei Fälle sauber trennen:
   - **Sub-Turn eines anonymisierenden Chats** (Fan-out, Delegation): das **Session-Mapping erben** — die Sub-Analyse muss dieselbe Fake-Welt sehen wie der Parent, sonst entsteht „fake²" (siehe unten).
   - **Scheduler-Lauf** (eigene `sched-*`-Session): **eigenes Mapping minten**, sobald der Task-Prompt oder ein Tool-Result Findings hat. Der Gate `gdpr_pick_model_for_background` weiß das bereits — er muss die Mapping-ID nur *behalten* statt sie wegzuwerfen.

**Zusätzlich die Ergebnis-Rückführung reparieren** (heute in beide Richtungen falsch):
- `run_background_task`: `_deanon` wird gebaut, aber **nie angewandt** (`background_tasks.py:404`) — der Output trägt Fakes eines **Einmal-Mappings** (`_sid=""` → one-shot, `brain.py:12167`) und wird nach dem Ledger-Rewrite injiziert → **unauflösbare Drittidentitäten** im Endtext.
- `delegate_task` macht das Gegenteil: `_del_deanon` **wird** angewandt (`brain.py:9065`) → `task_status` liefert **Echtwerte** als ungeseamtes Tool-Result in den Cloud-Parent = **Leak**.
- **Zielbild:** Sub-Turn erbt das Parent-Mapping → das Ergebnis ist **bereits in der richtigen Fake-Welt** → keine Deanon-Akrobatik nötig, nur der Result-Seam beim Einspeisen.

**Verifikation:** anonymisierende Session → `run_background_task` mit einem Prompt, der ein Kunden-Dokument lesen soll → im Sub-Turn-Trace darf **kein** Klarwert stehen, und ein `searxng_search` im Sub-Turn **muss** am Gate refusen. Heute tut es das nicht.

---

### M2 — Egress-Gate auf alle Egress-Tools · **S–M** · schließt G7

Das Gate ist gebaut und funktioniert — es ist nur zu schmal gescopet. **Nicht neu bauen, sondern anwenden.**

1. **Gate-Scope erweitern:** `_gdpr_guard_web_args` (`brain.py:3583`) prüft heute `tool_name in WEB_SEARCH_TOOLS`. Neu: eine `EGRESS_TOOLS`-Menge = `WEB_SEARCH_TOOLS ∪ {gmail_send, gmail_reply, generate_image} ∪ MCP-Tools`. Semantik bleibt identisch (Fakes → immer refuse; bekannte Originale → Policy; Frisch-Scan → Policy).
   **Konsequenz für gmail:** ein Fake-Empfänger wird refused statt zugestellt — genau das, was `30051b1f4439` gebraucht hätte. Und der `attachments`-Pfad (deanonymisierte Datei von Platte!) muss **fail-closed** sein: bei aktivem Mapping keine Artefakt-Anhänge ohne expliziten Consent.
2. **Shell-Mail-Seitentür:** `_DEANON_NETWORK_MARKER_RE` (`brain.py:3891`) ergänzen um `smtplib|sendmail|\bmail\b|\bmailx\b|msmtp|osascript|\bgh\b|\bopen\b\s+-a`.
   **Besser noch — Richtungswechsel:** heute ist es *allow mit Blocklist* (deanonymisiere, außer ein Marker taucht auf). Für `execute_command`/`python_exec` gehört es umgedreht: **deny by default**, deanonymisiere nur, wenn der String erkennbar rein lokal ist (Pfade, `grep`, `pandas`, `python -c` ohne Netz-Import). Eine Blocklist gegen einen kreativen Agenten ist strukturell verloren.
3. **MCP:** im Fallback-Zweig (`engine/llm_loop.py:774-782`) Args-Deanon **nicht** (Remote!) + Gate **ja** + Result-Seam **ja**. Falls per-Server-Differenzierung gewünscht (lokaler vs. remote MCP): ein `local: true`-Flag in `mcp.json`, das den Server in die Deanon-Whitelist hebt — sonst konservativ behandeln.
4. **`generate_image`:** zusätzlich zum Gate ein **harter Cloud-Egress-Check unabhängig vom Session-Modell** — `image_gen.py` postet *immer* an Mistral, auch aus einer **lokalen** Session, in der die Anonymisierung gar nicht läuft (`5175bf8fdf70`: Familien-Stammbaum inkl. Verstorbenen-Status als Bild-Prompt an die Cloud). Die Annahme „lokales Modell = bleibt lokal" bricht **am Tool, nicht am Chat-Modell**. Gleiches gilt für `gmail_send` und TTS aus Lokal-Sessions.

---

### M3 — Seam-Lücken schließen · **M** · schließt G8, G9, G10

Reines Nachziehen des etablierten Per-Tool-Musters (`_gdpr_anon_tool_text` am finalen `_ok(...)`):

| Ort | Fix |
|---|---|
| `translate_tools.py:569` | Result durch den Seam **oder** — sauberer — `_xlate_deanon` im Übersetzungs-Pfad **nicht** über das Session-Mapping laufen lassen (`brain.py:12147-12164`) |
| `mempalace_glue.py:1311ff` (`wiki_read`) | Result-Seam |
| `mempalace_glue.py:1299-1302` (`wiki_write`) | **Args-Deanon** (Wiki = lokaler Speicher!) → es landen **Echtwerte** auf Platte, keine Fakes. Damit ist die Gedächtnis-Vergiftung strukturell gelöst (kein Session-Mapping-Leak über Sessions). |
| `engine/tools/context_tools.py` | Result-Seam **+** Args-Deanon (read-only Retrieval, exakt das mempalace-Muster) — heute **null** GDPR-Referenz, obwohl die Tools den Lossless-Context-DAG mit Original-PII replayen |
| `gmail_tools.py` (`gmail_read`/`inbox`/`search`) | Result-Seam |
| `wiki_store.py:846-850` (`wiki_from_chat`) | `gdpr_pick_model_for_background` davor — das Muster steht **30 Zeilen höher** in `wiki_worth_saving` (`:752`) |
| `audio_overview.py:478`, `:609-614` | dito; TTS-Zeilen (`:345-355`) sind zusätzlich Cloud-Egress |
| `use_skill`-Result | Seam (billig; statische Repo-Skills unverändert — relevant seit „SKILL.md aus Chat generieren", v9.294) |
| `chat.py:263-318` / `:4446` (Pinned Sources) | **scannen** wie den getippten User-Text (nicht nur Seam) |
| `chat.py:1467` / `:4461` (BG-Preamble) | vor die Ledger-Rewrite-Stelle ziehen **oder** scannen |
| `llm_loop.py:962-971` (`/v1/chat/inject`), `ask_tools.py:578` (`ask_user`-Antwort) | scannen **und ledgern** (sonst kein Self-Heal) |

**Invariante beachten:** kein generischer Post-Hook — die 12 Tools, die `_gdpr_anon_tool_text` schon selbst rufen, würden doppelt anonymisieren (L3-Entscheidung 6, gilt weiter).

---

### M4 — Org-Entitäts-Schicht · **L** · schließt G2 · *der Qualitätshebel*

**Wiederverwenden, nicht neu bauen.** `engine/identity.py` (L1/L2) hat bereits: Normalisierung, Clustering, `entity_attach`, `render_variant`, `standard_variant_pairs`. Der Org-Fall braucht dieselbe Mechanik mit einer anderen Normalisierungs-Funktion.

**Org-Normalform** (Vorschlag, am realen Material kalibrieren):
- Rechtsform-Suffixe strippen/normalisieren: `SE`, `AG`, `GmbH`, `GmbH & Co KG`, `Ltd`/`Limited`, `Inc`/`Inc.`, `LLP`, `a.s.`, `m.b.H.`, `Holding(s)`
- Case-fold, Interpunktion (Satzpunkt! — real beobachtet: `K5 Beteiligungs GmbH.`), `&`/`und`/`and`
- **Akronym-Ableitung**: `Wiener Privatbank SE` → `WPB` (Großbuchstaben der Wortanfänge). Deckt die real beobachteten Kurzformen `WPB`, `M&P`, `AOHI`, `LLB`, `TÜV`.
- **Substring-Beziehung als Signal, nicht als Zufall:** `Wiener Privatbank Immobilien GmbH` teilt den Stamm mit `Wiener Privatbank` → **verwandte, aber DISTINKTE Entitäten**. Der Fake muss diese Beziehung **spiegeln**: Mutter `Nordstern Bank AG` → Tochter `Nordstern Immobilien GmbH`. Sonst ist die Konzernstruktur im Fake-Raum unsichtbar (das ist der Kern von G2).
- **Varianten als echte `forward`/`reverse`-Paare registrieren** — dieselbe Invariante wie bei L2a (Session-2-Entscheidung 9): nur dann werden L3a-Deanon und das Web-Gate automatisch org-fähig, ohne dort Code anzufassen.

**Zwei Härtungen aus dem Material:**
- **`same_as`-Verknüpfung** (Kategorie-Flip): wenn sich ein Org-Kunde als Person entpuppt (`Bachmann Int` = `Leopold Lyko`), müssen beide Ledger-Äste verknüpfbar sein.
- **Homonym-Trennung:** Org-Fake-Key um einen Jurisdiktions-/Kontext-Hint erweitern, sonst kollabieren drei reale „Atlantic Trading" auf einen Fake (G12) — das ist F1 *rückwärts* und erzeugt Gift-Evidenz.

**Achtung — Vorbedingung:** unter dem heutigen `kyc`-Preset ist `organisation` effektiv **`ignore`** (Kategorie `business_id`; das Preset hebt nur `name`). M4 ist also erst wirksam, wenn das Preset `organisation` auf `anonymise` hebt. **Das ist eine bewusste Entscheidung, die zusammen mit M5 fallen muss** — ohne M5 (Auto-Release) macht ein anonymisierter Org-Name jede Firmen-Recherche unmöglich (G3). **M4 und M5 sind ein Paket. Niemals M4 ohne M5 ausliefern.**

---

### M5 — Auto-Release für Gate-passierende Kategorien · **S** · schließt G3

Die Mechanik existiert vollständig: `_web_release_translate_args` (v9.338.0) übersetzt Fake→Original **nur im ausgehenden Request**, die Results kommen durch den L3b-Seam rück-anonymisiert zurück. Das Modell sieht das Original **nie**.

**Änderung:** Der Gate refust heute *jeden* Fake. Neu: Trägt der Fake einen Wert, dessen Kategorie die Policy **ohnehin passieren lässt** (`_WEB_GATE_PASS_CATEGORIES` = `business_id`, `network` — plus künftig `organisation`, sobald M4 sie anonymisiert), dann **automatisch hin-übersetzen statt refusen**.

→ Firmen-Recherche funktioniert wieder **vollständig**, ohne dass die Cloud den echten Namen sieht (er steht nur im Request an die Suchmaschine — die ihn ohnehin bekommen würde, denn die Policy lässt Orgs zu). Personen-Werte bleiben unberührt: refuse/ask wie bisher.

**Das ist die exakt gleiche Konstruktion wie L4-Phase-2 — nur mit „stehendem Consent per Policy" statt „Consent per Klick".** Der Effekt auf die Analysequalität ist der größte Einzelposten dieser Welle: alle Registry-/Sanktions-/Adverse-Media-Checks über Firmen kommen zurück.

**Audit-Pflicht:** jeder Auto-Release als `pii_web_egress match=policy_released` (kinds, nie Werte) + Degradations-Streifen bleibt ehrlich.

---

### M6 — Tabellen-/Massendaten · **M** · schließt G4

1. **Spalten-Heuristik statt Zell-NER:** Wird in einer Markdown-/CSV-Tabelle **eine** Zelle einer Spalte als `PER`/`ORG` erkannt (oder heißt der Header `Name`, `Kunde`, `Auftraggeber*`, `Inhaber`, `Erstellt von`), gelten **alle** Zellen der Spalte als Kandidaten derselben Kategorie. Das ist die einzige Methode, die gegen Excel-Truncation (`KO TULLNERSAntonius`) und invertierte Formen (`Pölzl Philipp`) robust ist.
2. **Header nie tokenisieren.** Ein Spaltenkopf, der zu `<BANK_ACCOUNT_CTX_1_d799>` wird, zerstört die Tabellensemantik (real beobachtet).
3. **Kontext-gegatete ID-Regeln:** Header `Kd.Nr.`/`Kto`/`Depot`/`Kundennummer` → die Zellen sind IDs, auch wenn sie formlos sind (`107625`, `330532`). Analog `19470205` (YYYYMMDD) unter Header `Geburtsdatum`.
4. **Längste-zuerst-Ersetzung + Zellgrenzen-Anker.** Zwei belegte Schäden: `ca_sin` matcht `300622-800` als Substring von `300622-800-1` (Zelle wird `<TOKEN>-1`); die Kd.Nr. `107625` ist Substring der Kontonummer `107625-801-6`. **Der Fake der Kontonummer muss den Fake der Kd.Nr. als Präfix wiederverwenden** — sonst zerbricht der Kunde↔Konto-Join (E1/E2).
5. **Skalierung messen** (offen, kein Beleg): ein Excel-Read seedet 200–300 Ledger-Einträge; gefetchte Registry-PDFs fügen tausende **irrelevante Dritt-Orgs** hinzu. Mapping-Größe, Ledger-Latenz und der Wire-Rewrite über 150 k Zeichen/Turn sind **ungetestet**. Vor M6-Abschluss einen Lasttest fahren (Material: die KO-Kunden-xlsx, ~80 Zeilen / ~40 Kunden).

---

### M7 — Artefakt-Vollständigkeit · **M** · schließt G5, G6

1. **Nicht-Baum-Pfade:** `make_gdpr_after_file_write_cb` (`chat.py:2239`) bailt bei `not _is_artifact_path`. Neu: **außerhalb des Baums mindestens Reverse + Lint + Fail-loud-Notice** (Muster: der PDF-Clamp aus L6). Ein `.docx` für eine echte HV mit erfundenen Organen darf nicht still entstehen. (Der ursprüngliche Grund für den Bail — nicht in fremde Dateien schreiben — bleibt für *fremde* Dateien richtig; für **vom Modell in diesem Turn geschriebene** Dateien ist er falsch.)
2. **Bild-Artefakte:** `render_diagram` **in die Args-Deanon-Whitelist** — der Renderer läuft **lokal** (`image_gen.py:260`, `:444`), es gibt kein Egress-Risiko, und dann trägt das Diagramm **Echtwerte**, genau wie die `.docx` daneben nach dem Reverse. Das ist der billigste und korrekteste Fix (kein Lint nötig, kein Warnstreifen — das Artefakt ist einfach richtig).
   `generate_image` kann das **nicht** (Cloud!) → dort bleibt es beim Gate (M2) + **Warnstreifen**: „Bild enthält Pseudonyme, nicht rückübersetzbar."
3. **`.svg` ist Text-XML** → in `SUPPORTED_EXTS` aufnehmen (trivial reversibel, wird heute grundlos übersprungen).
4. **`_GDPR_LINT_ONLY_EXTS`** um `.png`/`.jpg` erweitern, solange 2. nicht überall greift (Fail-loud statt stiller Lüge).

---

### M8 — Citation-Validator hinter den Reverse · **S** · schließt G11

`engine.validate_citations_in_response` (`chat.py:5077`) **hinter** `deanonymize_text` (`:5210`) ziehen — oder die Zitat-Spans vor dem Match durchs Mapping rückübersetzen. Heute scheitert jedes Zitat, das einen geschützten Wert enthält, systematisch.

**Vorsicht:** `_reround_uncited_only` ([[feedback_reround_uncited_only]]) hängt an diesem Ergebnis — der Re-Round darf nicht auf falschen „unverified" feuern. Nach dem Fix die Eval-Reihe (KG-Real-Policies) einmal gegenprüfen: die Scores müssen **unverändert** zum Scanner-aus-Lauf sein.

---

### M9 — Erkennungs-Netz · **M–L** · schließt G12

Nach Nutzen sortiert (jeder Punkt ist am realen Material belegt):

1. **Sperrschrift-Normalisierung vor dem Scan:** `([A-ZÄÖÜ]\s){3,}[A-ZÄÖÜ]` → kollabieren, scannen, und die Sperrschrift-Form als **Variante** der Entität registrieren (damit der Ledger-Rewrite sie rückwirkend fängt). Billig, schließt ein sauberes Leak.
2. **Verbalisierungs-Normalisierer** für Audio-Transkripte: `dot`→`.`, `at`→`@`, Buchstabier-Kollaps (`B-O-N-N-E`), Ziffern-mit-Füllwörtern. Nur auf Transkript-Quellen anwenden.
3. **EN-NER-Netz** (`en_core_web_*`) zusätzlich zu `de` — Sprachdetektion am Seam, Union der Findings (dasselbe Muster wie das `sm ∪ md`-Recall-Netz aus v9.342). Der Korpus ist mehrheitlich nicht-deutsch.
4. **Transliteration** (ICU/GOST) als Varianten-Generator: `Рахметов` ↔ `Rakhmetov`. Plus ein Kyrillisch-Regex-Netz. **Und:** das Egress-Gate zusätzlich gegen **Transliterations-/Fuzzy-Formen bekannter Fakes** prüfen (das Modell transliteriert den Fake selbst — real beobachtet).
5. **Familien-Stamm:** Personen mit gemeinsamem Nachnamensstamm (inkl. gendered `-ov`/`-ova`) bekommen einen **gemeinsamen Fake-Nachnamen-Stamm** mit passender Endung. Sonst löscht die Anonymisierung genau das Indiz, auf dem die DD-Schlussfolgerung steht.
6. **`same_as` + Homonym-Key** (siehe M4).
7. **Nicht-Prosa-Encodings** (XML-Markup, base64, SharedStrings) — *vermutet, nicht gemessen*: erst einen Repro bauen (`unzip -p … sheet1.xml` über eine PII-xlsx), dann entscheiden.

---

### M10 — Ad-hoc-Schutz ohne Projekt · **S–M** · schließt G13

Der Schutz darf nicht am `project.json` hängen, wenn die Arbeit es nicht tut. Zwei Optionen (eine wählen, nicht beide):

- **(a) Preset als Session-Eigenschaft** (nicht nur Projekt): ein `gdpr_preset`-Umschalter im Composer/Session-Menü, den man einer projektlosen Analyse-Session gibt. Ehrlichste Variante, ein Klick Overhead.
- **(b) Egress-Schutz an den Scanner koppeln, nicht ans Preset:** ist der globale Scanner `enabled` und findet der Turn Personen-Kategorien, gilt `web_egress` **auch ohne Preset** (Anonymise bleibt Modal-/Sticky-gesteuert wie heute). Kein Klick, aber weniger explizit.

**Empfehlung: (b) als Default + (a) als Opt-in.** Begründung: die belegten Turn-1-Leaks (`587a`, `1a83`, `088683`) waren allesamt **Web-Egress**, nicht Wire-Egress — (b) fängt genau die, ohne dass der Nutzer etwas tun muss.

---

### M11 — Neutrale Attachment-Dateinamen · **S** · schließt G14

**Die Leitidee: den Namen an der Quelle neutralisieren, statt ihn im Wire zu reparieren.**

Nicht der Klarname wird in den Wire geschrieben und dann per Exemption geschützt — er landet dort **gar nicht erst**. Bei aktivem Scanner legt Brain das Attachment unter einem **neutralen** Namen ab:

```
/tmp/brain-attachments/<sid>/CF_-_STARK_Bonnie_M_Mrs._107625_Scan.pdf   ← heute
/tmp/brain-attachments/<sid>/att_01.pdf                                  ← M11
```

Und der **Originalname wird zu gescanntem Inhalt** — als Zeile in der typed-Hälfte (z. B. im OCR-Block, der seit L5a ohnehin dort landet):

```
att_01.pdf = "CF_-_STARK_Bonnie_M_Mrs._107625_Scan.pdf"
```

**Warum das der saubere Schnitt ist — nicht ein Kompromiss:**

| | heute | mit M11 |
|---|---|---|
| Pfad im Wire | Klarname (exempt, ungescannt) | `att_01.pdf` — **PII-frei** |
| `read_document(pfad)` | funktioniert (echter Pfad) | funktioniert **identisch** (echter Pfad!) — **kein Deanon-Roundtrip, nichts kann brechen** |
| Originalname | Leak | **Inhalt** → Scan + Ledger → pseudonymisiert (`Sam Mitchell`, Kd.Nr.-Token) |
| Weiß das Modell, was die Datei ist? | ja (via Klarname) | ja (via pseudonymisierten Namen) |
| Exemption `_split_attachment_notice` | schützt Klarnamen | **unverändert** — schützt jetzt nur noch echtes Boilerplate |

**Der springende Punkt:** Grund (2) der Exemption (*„Fake-Pfad bricht read_document"*) **verschwindet**, statt umgangen zu werden. Es gibt keinen Fake-Pfad mehr — es gibt einen echten, neutralen.

**Bonus, den man sonst nie bekommt:** der Originalname ist eine der **8 F1-Oberflächenformen** (`STARK_Bonnie_M_Mrs.`). Statt ihn als Leak zu dulden, kann der L5-Entity-Seed ihn als **Ankerwert** verwenden — der Dateiname wird vom Leck zur Evidenz (dieselbe Denkfigur wie L5 beim OCR-Block).

**Umsetzung (4 Stellen, alle in `handlers/chat.py`):**

| Ort | Änderung |
|---|---|
| `:7282-7284` | `safe_name` → `att_<n><ext>`; `{neutral: original}`-Map für diesen Turn behalten |
| `:7325-7340` (Notice-Bau) | Pfadliste trägt die neutralen Namen — bleibt exempt, ist jetzt aber **PII-frei** |
| OCR-Block / typed half | Mapping-Zeilen `att_01.pdf = <Originalname>` anhängen → wird gescannt + geledgert |
| `_split_attachment_notice` (`:181`) | **nichts** — bewusst unverändert |

**Zwei ehrliche Kosten:**

1. **Henne-Ei beim Timing.** Der Dateiname wird beim Upload vergeben, das Mapping entsteht (ohne Preset) erst beim Scan. → **Immer neutral benennen, sobald `gdpr_scanner.enabled`** — nicht nur bei aktivem Mapping. Deterministisch, kostenlos, kein Timing-Problem. (Bei Scanner=aus bleibt alles wie heute.)
2. **Ergonomie.** Artefakt-Ordner und `list_directory` zeigen `att_01.pdf` statt sprechender Namen. Akzeptabel — und nur in Sessions mit aktivem Scanner.

**Was M11 NICHT löst (wichtig, nicht verwechseln):** Pfade in **`mempalace_query`-Results** und **`list_directory` über Projekt-Ordner** (`…/ingested/CF_-_STARK_Bonnie_M_107625.md`). Die liegen nicht in `/tmp`, Brain hat sie nicht angelegt, kann sie nicht umbenennen. Dort greift der bestehende Mechanismus: L3b pseudonymisiert den Pfad im Result, L3a übersetzt ihn beim Folge-`read_document` zurück. Der Roundtrip schließt — **über einen anderen Weg**. M11 ist der Upload-Kanal, nicht der Projekt-Korpus.

**Verifikation:** Attachment mit Klarnamen-Dateinamen in einer anonymisierenden Session hochladen → im Wire steht **kein** Klarname (auch nicht im Pfad); `read_document` auf den neutralen Pfad **funktioniert**; der Originalname erscheint im Ledger als `anonymise`-Zeile. Golden-Material: der 10-JPG-Satz aus `58e3c521438a` + `Alcuatmisi02026!.txt` (Passwort-im-Dateinamen).

---

## 3. Was auch nach M1–M11 NICHT erreichbar ist

Ehrlich benennen (CLAUDE.md Regel 12):

- **Cloud-Vision-Pixel** bleiben unpseudonymisierbar (unverändert aus dem L-Katalog). Verschärfung, neu: bei multimodalen Modellen liest das Modell die **echte** MRZ aus den Pixeln, während die Text-Kanäle die **Fake**-MRZ zeigen → das Modell sieht einen Widerspruch, den die Anonymisierung erzeugt hat (`bc73d55fe958`). Kandidat für einen Clamp-Satz („Werte aus Bildpixeln nicht mit Textwerten vergleichen") — oder für lokale Vision.
- **Offene Personensuche ohne Namens-Release** bleibt prinzipiell unmöglich. M5 löst das für **Organisationen** (weil die Policy sie ohnehin passieren lässt) — für Personen bleibt der Consent-Klick die ehrliche Antwort.
- **Quasi-Identifikatoren** (Bilanzkennzahlen, ISIN, FN, Beteiligungsquoten, Berufs-/Abteilungsangaben) sind keine PII-Kategorie und bleiben roh. Bei einer anonymisierten Firma ist die Re-Identifikation über die Kennzahlen trivial. **Das ist eine bewusste Grenze, kein Bug** — aber sie gehört in die Preset-Doku, damit niemand ein falsches Sicherheitsgefühl bekommt.
- **Pfade im PROJEKT-Korpus** (nicht im Upload-Kanal) tragen weiter Klarnamen: `…/ingested/CF_-_STARK_Bonnie_M_107625.md`, `…/projects/risikoanalysen/…/Geldwäsche Risikoanalyse M&P AM_2025.xlsx`. Die hat Brain nicht angelegt und kann sie nicht umbenennen. Der L3b/L3a-Roundtrip (Pfad im Result pseudonymisiert → beim `read_document` zurückübersetzt) hält das funktionsfähig, aber der Dateiname bleibt eine Namens-Oberflächenform, die die Erkennung treffen muss. **(Der UPLOAD-Kanal ist mit M11 gelöst — nicht verwechseln. Die frühere Einordnung „Pfad-Exemption ist unvermeidbar" war schlicht falsch, siehe G14.)**

**Erwartete Parität nach M1–M11:** Org-zentrierte Analyse (Konzern/UBO/Registry/Adverse-Media) auf **~90 %** — der Rest ist der Consent-Klick für Personen. Massendaten-Workloads: Leak geschlossen, Join-Qualität abhängig von M6.4 (ID-Präfix-Konsistenz). Background/Scheduler: von **„ungeschützt"** auf **„wie der Chat"**. Attachment-Upload: Klarname im Wire **vollständig weg** (M11), ohne dass ein einziger Pfad bricht.

---

## 4. Verifikation — an DIESEM Material, nicht an synthetischem

Regel aus dem L-Katalog, gilt weiter ([[feedback_depth_over_speed]]):

| Baustein | Golden-Material | Erfolgskriterium |
|---|---|---|
| M1 | anonymisierende Session → `run_background_task` „lies die Kundenakte und suche im Web" | Sub-Turn-Trace: **0 Klarwerte**; das `searxng_search` im Sub-Turn **refust** |
| M2 | `30051b1f4439` (gmail_send-Specimen), `b4edbc9dc8e7` (`mail`/`sendmail`-Specimen) | Send wird **refused**, nicht zugestellt; `mail -s … <fake>` wird **nicht** deanonymisiert |
| M3 | `translate_text` über einen gemappten Namen | Tool-Result trägt den **Fake**, nicht das Original |
| M4/M5 | `bcad56fa99f8` (WPB-Konzern), `32e257377809` (ABACO-Registry), `99a38595f0ca` (OFAC-SDN) | Alle Org-Oberflächenformen (Langform/Kurzform/`WPB`/Slug/ALLCAPS) → **ein** Fake; Registry-Suche **läuft** (auto-released) und findet dasselbe wie mit Scanner=aus |
| M6 | `Kopie von KO_Kunden_Stand 02.06.2026 SG.xlsx` (~40 Kunden) | **0 rohe** Kundennamen/DOBs/Kd.Nr. im Wire; `107625-801-6` behält den Kd.Nr.-Präfix im Fake; Lasttest: Ledger/Latenz |
| M7 | `6c8dc5937f2c` (HV-.docx auf absoluten Pfad), `28e2f5cc1f4e` (20 Diagramme) | Nicht-Baum-`.docx` wird **deanonymisiert oder fail-loud**; Diagramm-PNG trägt **Echtwerte** |
| M8 | KG-Real-Policies-Eval-Reihe | Citation-Scores mit Scanner=an **==** Scanner=aus |
| M9 | `6c8dc5937f2c` (Sperrschrift), `compliance-prüfung` (Kyrillisch), ko-kunden-Audio-Transkript | Sperrschrift-Namen erkannt; `Рахметов` ↔ `Rakhmetov` = **eine** Entität; buchstabierte Mail erkannt |
| M11 | 10-JPG-Satz aus `58e3c521438a`; `Alcuatmisi02026!.txt` (Passwort-Dateiname) | **Kein** Klarname im Wire — auch nicht im Pfad; `read_document(att_01.pdf)` funktioniert; Originalname erscheint als `anonymise`-Ledger-Zeile |

**Reproduktions-Werkzeug** (die Agenten dieser Analyse haben es benutzt — es funktioniert und berührt keinen Server): `engine/pii_ner._pii_scan_text` offline gegen den echten Text laufen lassen, mit `preset="kyc"`-Overlay auf der Live-Config. So sind die „0 Findings"-Aussagen oben entstanden — **nicht** durch Simulation.

**Wichtig:** `tests/test_pii_ner.py::test_action_resolves_from_contact_category` liest die **Live-Config** und schlägt fehl, solange `name` nicht `ignore` ist. Bei Live-Tests einplanen (bekannter Fallstrick aus Session 3 des L-Katalogs).

---

## 5. Pflichten vor Commit/Push (unverändert)

- **VERSION an ZWEI Stellen**: `brain.py:4` + `CHANGELOG` ([[feedback_version_two_places]]).
- **Kuratierter Changelog** (`engine/changelog_curated.py`): M2/M5/M7/M10 sind user-/admin-sichtbar → Eintrag **oben**, Deutsch, „Sie", **nutzenorientiert**. M1/M3 sind Leak-Fixes ohne sichtbare Funktion — aber der *Effekt* („Ihre Daten sind auch in geplanten Läufen und Hintergrund-Aufgaben geschützt") ist sichtbar → gebündelt eintragen.
- **`brain-agent-guide`-Skill im SELBEN Commit** ([[feedback_update_skill_before_push]]): `02-tools.md` (Gate-Scope!), `05-internals.md` (Seams), `06-user-manual.md` (Deutsch — Auto-Release, Degradations-Anzeige).
- **INVARIANTS.md § GDPR/PII**: Seam-Liste, Egress-Tool-Liste, Org-Entitäts-Schicht, **die neue Invariante „jeder Turn hat ein Mapping"**.
- `py_compile` nach jedem Edit + `/v1/status`-Version prüfen ([[feedback_compile_check_brain_py]]).
- Restart via `launchctl`, **nie SIGKILL** ([[feedback_never_sigkill_brain]]).
- **Direkt auf `main`** ([[feedback_commit_to_main]]).
- Test-Sessions nach Live-E2E **löschen** ([[feedback_cleanup_test_sessions]]) — und diesmal **auch die `pii_decisions`-Zeilen** (der v9.342-Purge deckt den Session-Delete-Pfad; bei manuellen API-Tests prüfen).

---

## 6. Offene Entscheidungen für die neue Session

**Getroffen (nicht neu aufrollen):**
1. M4 und M5 sind **ein Paket** — Org-Anonymisierung ohne Auto-Release macht Firmen-Recherche unmöglich.
2. `render_diagram` gehört in die **Args-Deanon-Whitelist** (lokaler Renderer, kein Egress) — nicht in einen Lint-Pfad.
3. `wiki_write` bekommt **Args-Deanon** (Echtwerte auf Platte), nicht einen Result-Seam. Das löst die Cross-Session-Vergiftung strukturell.
4. Für `execute_command`/`python_exec`: **deny-by-default** statt Marker-Blocklist (die Blocklist ist gegen einen kreativen Agenten strukturell verloren).
5. Sub-Turns **erben das Parent-Mapping** (statt eigener Einmal-Mappings) — damit entfällt die fehlerhafte Deanon-Akrobatik in beiden Richtungen.
6. **M11: neutraler Dateiname auf Platte + Originalname als gescannter Inhalt** — NICHT „Pfad pseudonymisieren" (bräche `read_document`) und NICHT „Warnstreifen bei Klarnamen im Basename" (kaschiert nur). Die Exemption in `_split_attachment_notice` bleibt **unangetastet**; sie schützt danach nur noch echtes Boilerplate. Neutral benannt wird, sobald `gdpr_scanner.enabled` — nicht erst bei aktivem Mapping (sonst Henne-Ei).

**Wirklich offen (Entscheidung nötig):**
- **M10 (a) oder (b)?** Empfehlung (b) als Default + (a) als Opt-in — aber das ist ein Produkt-Call, kein technischer.
- **Hebt das `kyc`-Preset `organisation` auf `anonymise`?** Konsequenz: alle Firmennamen werden gefakt. Nur sinnvoll **mit** M5. Alternative: ein eigenes Preset `screening` (Personen anonym, Orgs im Klartext + Web frei) — das wäre für `risikoanalysen`/`compliance-prüfung` möglicherweise der **ehrlichere** Modus, weil dort die Firma das *Prüfsubjekt* ist und ihr Name ohnehin an die Suchmaschine muss.
- **MCP:** pauschal konservativ (kein Deanon, Gate + Seam) oder per-Server-`local`-Flag?
- **Massendaten-Skalierung:** Lasttest vor M6-Design — falls das Ledger bei 300 Einträgen/Turn oder der Wire-Rewrite bei 150 k Zeichen bricht, ändert das die M6-Architektur (dann eher: Massen-Tabellen **gar nicht** in den Wire, sondern nur serverseitig auswerten — das L1-Muster „Modell liefert INTENT, Server rechnet").

---

## 7. Referenzen

- **Vorgänger-Handover:** `PII_ANALYSIS_PARITY_HANDOVER.md` (L1–L7, Failure-Katalog F1–F7, Code-Landkarte).
- **Kalibrierungs-Chat:** `58e3c521438a` (ko-kunden) — *ein* Workload von vielen.
- **Neu belegte Workload-Formen:** Org/UBO/Konzern (`bcad56fa99f8`, `65b4aefeed11`, `748f92cfeacf`, `6afd1872fe7e`) · Sanktions-/Adverse-Media-Screening (`99a38595f0ca`, `502f561fd123`, `088683fc47bc`) · Massen-Tabellen (`ca28e9255db3`, `414d69fc53a0`, `4aad5750c260`) · Bild-Report-Fabrik (`28e2f5cc1f4e`, `d100c1dca495`, `04ac769e8e6a`) · Notariat/Sperrschrift (`6c8dc5937f2c`) · E-Mail-Egress (`30051b1f4439`, `b4edbc9dc8e7`) · Kyrillisch/mehrsprachig (`compliance-prüfung`, `2c92112f2167`, `1a830369e762`) · Eval-Masse (`KG-Real-Policies`, 1238 Sessions).
- **Code-Landkarte dieser Welle:** `handlers/sidecar_proxy.py:145-180` (G1) · `brain.py:3272/3583/3874/3891` (Gate, Whitelist, Marker) · `handlers/chat.py:2239/2241/4446/4461/5077/5210` (Callback-Bail, Lint-Exts, Pinned, BG-Preamble, Validator-Reihenfolge) · **`handlers/chat.py:181-195` (`_split_attachment_notice` — Exemption + ihre Begründung) + `:7282-7284` (Dateiname-Vergabe) + `:7325-7340` (Notice-Bau) = die drei M11-Stellen** · `engine/tools/image_gen.py:35/148/260` · `engine/tools/gmail_tools.py:247/283` · `engine/mempalace_glue.py:1299/1311` · `wiki_store.py:752/846` · `audio_overview.py:345/478/609` · `engine/tools/context_tools.py` (seam-frei) · `engine/llm_loop.py:774-782` (MCP) · `engine/scheduler.py:1328`.

**Ehrlichkeit (Regel 12):** Alle Code-Zeilen oben sind statisch verifiziert, nicht live reproduziert. Die „0 Findings"-Aussagen (Sperrschrift, `WPB`, `19470205`, Excel-Namen, `ca_sin`-Partial-Match) stammen aus **echten Offline-Läufen** des Scanners. **Vermutet, nicht gemessen:** Nicht-Prosa-Encodings (XML/base64), Mapping-/Ledger-Skalierung bei Massendaten, spaCy-Verhalten auf Kyrillisch, ob Slug-Rückübersetzung `<img src>`-Links auf fake-benannte PNGs bricht.
