# PII-Analyse-Parität — Handover (L1–L7)

**Stand:** 2026-07-14 · Basis-VERSION: `9.333.0` · **Status: NICHTS IMPLEMENTIERT — reines Design/Handover.**

**Ziel in einem Satz:** Ein KYC-/Betrugs-Analyse-Chat soll mit **aktiviertem** PII-Scanner + Auto-Anonymisierung/Deanonymisierung **nahezu dieselbe Analysequalität** liefern wie mit deaktiviertem Scanner — ohne dass Klardaten in die Cloud gehen.

**Anlass:** Chat `58e3c521438a` (Projekt `ko-kunden`, Modell `glm-5.2` = Cloud, **`gdpr_scanner.enabled = false`**). Betrugsprüfung „Stark Bonnie M". Die Frage war: was bricht, wenn man den Scanner einschaltet?
Antwort: **sehr viel** — und zwar so, dass die Analyse nicht nur schlechter, sondern **aktiv falsch** wird (erfundene Fälschungsindizien), während gleichzeitig die dichtesten PII-Kanäle **trotzdem** offen bleiben.

---

## 0. Kontext für die neue Session

### 0.1 Wie man den Ausgangs-Chat wieder ansieht

```bash
sqlite3 agents/main/chats.db \
  "SELECT id, role, substr(content,1,200) FROM messages
   WHERE session_id='58e3c521438a' ORDER BY id;"
```

Tool-Calls stecken in `messages.metadata` (JSON, Key `tools`), inkl. Args und Results:

```bash
python3 -c "
import sqlite3, json
db = sqlite3.connect('agents/main/chats.db')
for mid, meta in db.execute(\"SELECT id, metadata FROM messages WHERE session_id='58e3c521438a' AND role='assistant' ORDER BY id\"):
    if not meta: continue
    m = json.loads(meta)
    for t in (m.get('tools') or []):
        print(mid, t.get('name'), json.dumps(t.get('args'), ensure_ascii=False)[:160])
"
```

`traces.db` enthält für diese Session **nichts** (nur `sched-*`-Sessions) — die Metadata ist die Quelle.

### 0.2 Was der Chat tat — die 5 Evidenzklassen

Die Analyse stand auf fünf Beinen. Jedes bricht anders unter Anonymisierung — **das ist die Landkarte für alles Folgende**:

| # | Evidenzklasse | Wie im Chat erzeugt |
|---|---|---|
| **E1** | **Identitäts-Join über 34 Jahre** | Name/DOB/Passnr. quer über Kontoeröffnung 1992, US-Pass 2007, US-Pass 2026, Excel-Kundenblatt, Risk-Review, WebID-Screenshots. Kernbefund: „Alle Personalien identisch über 34 Jahre" |
| **E2** | **Arithmetik auf den geschützten Werten selbst** | ICAO-9303-MRZ-Prüfziffern (`5606837078USA4702058F2701264` → alle 5 gültig); Verlängerungslogik (alt abgelaufen 18.01.2017 → neu ausgestellt 27.01.2017 = normaler 10-Jahres-Zyklus); Gültigkeit 26.01.2027 > heute; Alter 79 aus DOB 05.02.1947 |
| **E3** | **Retrieval mit Klarnamen** | 5× `mempalace_query("Stark Bonnie …")`, ~12× `read_document` auf Pfade **mit Name + Kundennr. im Dateinamen** (`CF_-_…_STARK_Bonnie_M_Mrs._107625_…`), `find`/`grep` via `execute_command` |
| **E4** | **Web-Korroboration mit Klarnamen** | ~15× `searxng_search` („Bonnie M Stark Oregon City OR age 79 born 1947", „…obituary…", Kepler Drive), `web_fetch` bizapedia. Lieferte das **Positiv-Signal** (Adresse+Alter öffentlich konsistent) und das **Negativ-Signal** (kein Obituary) |
| **E5** | **Byte-Forensik** | `python_exec`/`execute_command` auf `/tmp/brain-attachments/...`: EXIF (GPS = 0/0), Samsung-SEFT-Trailer (ShadowRemoval/rotation/reSize), ELA, DCT, Schärfeprofile. Plus wörtliche Zitate im Report (Citation-Discipline war aktiv) |

Artefakte des Chats: 2 PDF-Reports, 1 HTML-Gesamtbericht, 3 JPGs — in `agents/main/artifacts/2026-07-13_58e3c521438a/`.

### 0.3 Verifizierter Ist-Zustand der PII-Pipeline (Code-Trace, 2026-07-14)

**FORWARD (real → fake):**

| Seam | Ort |
|---|---|
| Getippter User-Text (dieser Turn) | `handlers/chat.py:3651` |
| Wire-History (alle Vorturns), **deterministisch aus dem Ledger** | `_apply_pii_decisions_to_wire`, `handlers/chat.py:1864` (aufgerufen `chat.py:4200`) |
| Tool-**Ergebnisse** | `brain._gdpr_anon_tool_text`, `brain.py:3114` |
| Data-Review-Override (vorab geprüfte Doks) | `brain.py:3160-3180` |

`_gdpr_anon_tool_text` ist **per-Tool-Opt-in**. Verdrahtet in:
`file_tools.py:176` (read_file), `:501-508` + `:575` + `:599` (read_document), `:3784` + `:3895` (execute_command stdout), `:4505` (python_exec stdout), `ocr_tools.py:234`, `xlsx_tools.py:1106/1168/2352`, `diff_tools.py:188`.

**REVERSE (fake → real):**

| Seam | Ort |
|---|---|
| Gestreamte `text_delta` | `StreamingDeanonymizer`, `handlers/chat.py:2404-2475` |
| Finale Assistant-Antwort (persistiert) | `handlers/chat.py:5007-5074` |
| Vom Modell geschriebene **Artefakt-Dateien** | `brain._after_file_write` (`brain.py:15843`) → `make_gdpr_after_file_write_cb` (`chat.py:2155`) → `engine/file_pseudonymize.py:276` |

**Ersatzwert-Erzeugung** (`pseudonymizer.py`):
- `SHAPE_PRESERVING` (`:70-84`) = `iban, credit_card, phone, name, address, organisation, email, date` → Shape-Fakes.
- Alles andere (inkl. **`passport`**, `bare_identifier`, alle nationalen IDs) → opake Tokens `<KIND_N_SALT>` (`:46`, `:541`).
- IBAN mod-97-gültig (`:170`), Kreditkarte Luhn-gültig (`:133`), Telefon mit Fake-Ländercode `999` (`:191`).
- **`_fake_date` (`:438-497`)**: Jahr + Monat **bleiben**, **Tag wird gejittert** (`new_d = 1 + seed%28`, `:469`). Formate: ISO, `eu_dot`, `eu_dash`, `us_slash`, 2-stellige Jahre (`_DATE_PATTERNS`, `:424`). **Textuelle Monate (`5 FEB 1947`, `26. Jan 2027`) und EXIF (`2026:07:02`) matchen NICHT** → bleiben roh.
- Mapping ist **exakt-String-gekeyt**, session-stabil, AES-GCM-verschlüsselt in `pseudonym_maps`.

**Ledger** `pii_decisions` (`server_lib/db.py:761`): append-only, `value_hash` = sha256(rule_id|value), Spalte `fake_value` (v9.201). Treibt `_apply_pii_decisions_to_wire` **ohne** Neuscan/Neu-Mint.

**Sticky-Auto-Anonymise:** Sobald die Session **eine** `pseudonym_maps`-Zeile hat, anonymisiert jeder Folge-Turn automatisch (`chat.py:7165`), außer der User widerruft (Schild-Button) oder der Turn läuft lokal (`_is_local_turn`, `chat.py:7152`).

**System-Prompt-Clamp** `_GDPR_ANON_CLAMP` (`engine/prompt_build.py:701-717`): nur bei `_gdpr_anonymising=True`. Sagt dem Modell u. a. **„Shape-Fakes sind KEINE Platzhalter — behandle sie wie echte Werte"** (relevant für L6, siehe unten).

### 0.4 Die verifizierten Lücken (Ist-Zustand, alle im Code belegt)

1. **`mempalace_query` / `mempalace_kg_*` liefern ROH-PII an das Cloud-Modell.** `engine/mempalace_glue.py` hat **null** GDPR-Referenzen. Bewusste Entscheidung in v9.96.0 („verified raw today → stay raw"). **Einziger Read-Tool-Pfad ohne Seam.**
2. **Web-Tools sind in BEIDE Richtungen ungeschützt.** `engine/tools/misc_tools.py` (`tool_web_fetch:947`, `_searxng_query:1211`, `tool_searxng_search:1326`, `exa_search:1394`) — keine PII-Referenz. Args gehen wörtlich raus, Results kommen ungescannt rein. Kein PII-getriebenes Tool-Gating (`exclude_tools` wird nur von Websuche-Lockout / `disable_web_search` / Task-Classifier gefüttert).
3. **Der Attachment-OCR-Block wird NIE gescannt.** `[Bild-Anhänge — automatisch, ohne KI erkannt …]` wird in die Attachment-**Notice** gehängt (`chat.py:7094-7097`), und `_split_attachment_notice` (`chat.py:158-180`) nimmt die Notice **absichtlich** vom Scan aus (damit Pfade nicht zerschrieben werden). → **MRZ, Name, Passnr., DOB eines fotografierten Ausweises gehen roh in die Cloud, auch im Anonymise-Modus.**
4. **Tool-Argumente werden nie zurückübersetzt.** `engine/llm_loop.py:712` ruft `fn(args)` verbatim. Kein Reverse-Mapping im Dispatch-Pfad (weder `llm_loop.py` noch `tool_exec.py` haben GDPR-Referenzen).
5. **Websuche-Basket-Prefetch, Pinned Sources, BG-Task-Preambles**: nach dem Ledger-Rewrite injiziert (`chat.py:4246/4259/4269`) → ungescannt.
6. **Cloud-Vision-Pixel**: ein multimodales Modell bekommt die Ausweis-**Pixel**. Prinzipiell nicht pseudonymisierbar. Nur die v9.330-Bild-Typ-Klassifikation (`passport → strict`) greift — und die ist bei `server_block=false` heute nur „warn".
7. **`_pseudonymize_history_for_wire` (`chat.py:1958`) ist TOTER CODE.** Nicht aufgerufen. Wer es reaktiviert, doppel-anonymisiert. **Nicht anfassen ohne den Ledger-Pfad zu verstehen.**

### 0.5 Aktuelle Config (`config.json → gdpr_scanner`) — wichtig!

```
enabled: false          ← Scanner ist AUS. Das ist der Ausgangszustand.
server_block: (fehlt)   → block wird zu warn degradiert
background_pii_action: "anonymise"
name_precision_gate: true
categories: secrets=block, national_id=block, national_id_ctx=block,
            financial=block, business_id=ignore, contact=IGNORE,
            network=ignore, personal=warn, bare_id=warn
rule_overrides: organisation=ignore, email=block, phone=warn,
                address=block, dob=block
```

**Konsequenz, die man leicht übersieht:** `name` gehört zur Kategorie `contact` = **`ignore`**. Im Anonymise-Modus bliebe **„Bonnie Stark" also ROH**, während `dob`/`address`/`email` ersetzt würden → ein **Halb-Hybrid** aus echtem Namen und Fake-Umfeld. Jeder Web-Gate, der nur „actionable findings" prüft, wäre für den wichtigsten Wert **blind** (→ L4 §4.2).

---

## 1. Der Failure-Katalog (das WARUM hinter L1–L7)

### F1 — Der Identitäts-Join zerbricht *(trifft E1)*
Das Mapping ist **exakt-String-gekeyt**. Dieselbe Person erscheint im Chat in ≥8 Oberflächenformen:
`STARK, BONNIE MARIE` · `Bonnie M Stark` · **`Bonnie N. Stark`** (OCR-Fehler in der Akte!) · `STARK<<BONNIE<MARIE` (MRZ) · `Stark Bonnie M Mrs.` (Dateiname) · `kbstark@pacbell.net` · OCR-Garble `Bonnie MASE` / `BONNT DCMARTE`.

Jede erkannte Variante bekommt einen **anderen** Fake; die Garbles werden gar nicht erkannt. Das Modell sieht **3–5 verschiedene Personen** plus Echtnamen-Fragmente. Der Kernbefund „Personalien konsistent über 34 Jahre" wird unmöglich — schlimmer: es entstehen **falsche Betrugssignale** („Name im neuen Pass weicht von der Akte ab!").
→ **Partielle Anonymisierung ist hier schlechter als keine** — für Qualität *und* für Datenschutz.

### F2 — Rechen-Checks liefern falsche Fälschungsindizien *(trifft E2 — GEFÄHRLICHSTER FAILURE)*
- **MRZ-Prüfziffern:** `passport` → opaker Token; die MRZ-Zeile wird teils gar nicht, teils via bare-identifier zerstückelt erwischt. Die ICAO-9303-Mathematik, die der Chat **zweimal als zentrales Echtheitsargument** durchführte, ergibt auf zerschriebenen Strings **„Prüfziffer ungültig" → falsches Fälschungsindiz in einem Compliance-Bericht.**
- **Tag-Jitter zerstört Datums-Arithmetik:** Alter (79) und „gültig bis 2027-01" überleben (Jahr+Monat bleiben). Aber: alt-abgelaufen-**18**.01. vs. neu-ausgestellt-**27**.01. kann zu *Ausstellung vor Ablauf* **invertieren**; „exakt 10 Jahre − 1 Tag" (27.01.2017→26.01.2027) bricht **immer**; EXIF-Aufnahme 02.07. vs. Dokumentendatum 07.07. kippt beliebig. Grenzfälle nahe am Ablaufdatum können das **Gültigkeitsurteil drehen**.
- **Formatblindheit = selbsterzeugte Widersprüche:** `5 FEB 1947` und `2026:07:02` bleiben roh, während `07.07.2026` daneben gejittert wird → **dasselbe Datum existiert in zwei Wahrheiten im selben Kontext** → das Modell „findet" Widersprüche, die die Anonymisierung erzeugt hat.
- Fake-Passnr. `560683707` vs. `5606837078` (mit Prüfziffer) → **zwei verschiedene Tokens** für dasselbe Dokument.

### F3 — Split-Brain an der Tool-Grenze *(trifft E3, E5)*
Das Modell denkt in Fakes, die Tools arbeiten auf Rohdaten:
- `mempalace_query("<Fake-Name> KO Kunde")` → Embedding-Suche über Drawer mit **Echtnamen** → **null Treffer** → „keine historischen Aufzeichnungen gefunden".
- `find`/`grep`-Output läuft durch `_gdpr_anon_tool_text` → Namen **in Pfaden** werden ersetzt → Modell kopiert den **Fake-Pfad** in `read_document` → *File not found*, **systematisch**.
- **Perfider noch:** Werte, die anderswo gemintet wurden (Kundennr. `107625`), werden vom Ledger-Rewrite **auch innerhalb der Attachment-Notice-Pfade** der History ersetzt — `_apply_pii_decisions_to_wire` (`chat.py:1906-1917`) splittet die Notice **nicht** (anders als der Scan-Pfad!) → beim Folge-Turn sind selbst die eigentlich ausgenommenen Pfade kaputt.
- Python-Skripte mit inhaltsabhängiger Logik (`if "STARK" in mrz:`) laufen mit **Fake-Konstanten gegen echte Bytes** → 0 Treffer → falsche Schlüsse. (Reine Byte-Forensik GPS/ELA/DCT funktioniert weiter — bis ihre stdout-Ausgabe PII enthält.)
- **Substring-Korruption:** Ledger-Replace ist `str.replace` → eine anonymisierte Hausnummer/Kurz-ID zerschreibt zufällig gleiche Zahlen in technischen Ausgaben (`GPSInfo: 807`, Byte-Offsets).

### F4 — Web wird zum Leak ODER zur Gift-Evidenz *(trifft E4)*
- **Heute** (`name=ignore`): Klarname bleibt im Wire → Modell sucht wie gehabt → **PII geht an Google/Bing/exa raus**, im Auto-Modus **ohne jede Rückfrage**.
- **Mit gefaktem Namen:** Suchen nach „Anna Weber Oregon City" treffen **echte andere Personen** (Shape-Fakes sind reale Namen!) → Obituaries/Adressen einer **fremden Person** fließen als „Evidenz" in die Betrugsbewertung.
- Oder null Treffer → das Modell berichtet **„kein Obituary gefunden"** als Befund, obwohl die Suche semantisch leer war → **negative Evidenz wird zur Lüge**, sieht aber nach Diligence aus.
- **Inbound** ungescannt: die echte Person aus einer Personensuchmaschine erscheint im Kontext **neben ihrem Fake** → zwei Identitäten (→ F1) plus Roh-PII in der Cloud.

### F5 — Roh-PII-Kanäle bleiben trotz „Anonymisierung an" offen
OCR-Preamble (MRZ+DOB+Passnr. roh), `mempalace_query`-Drawer (**das gesamte Projektwissen!**), Web-inbound, Bild-**Pixel** an multimodale Cloud-Modelle, Websuche-/Pinned-/BG-Preambles.
→ Der Nutzer glaubt „anonymisiert", faktisch ist der dichteste Teil ungeschützt. **Falsches Sicherheitsgefühl ist ein eigener Schaden.**

### F6 — Deanonymisierung: der Report lügt leise
- **PDF ist NICHT reversibel.** Der Chat erzeugte **2 PDF-Reports** (reportlab via `python_exec`). `engine/file_pseudonymize.py` unterstützt nur `.docx/.pptx/.xlsx/.csv` + plain (`.txt/.md/.log/.html/.htm/.json`); alles andere wird **unverändert durchgereicht** (`file_pseudonymize.py:283-286`). → **Der KYC-Report enthält plausible Fake-Passnummern und Fake-Namen, ohne Kennzeichnung.** Worst Case: er wird weitergeleitet.
- **Reformatierung schlägt Reverse.** `deanonymize_text` ersetzt **exakte Strings**. Schreibt das Modell den Fake `17.02.1947` als „17. Februar 1947", als Initialen „E. M.", im Genitiv „Webers", oder rechnet abgeleitete Werte (Tagesdifferenzen, Prüfziffern) → **Fake-Substanz bleibt unerkannt im Endtext**, gemischt mit rückübersetzten Echtwerten. **Der Clamp fördert das sogar** („Shape-Fakes wie echte Werte behandeln").
- **Zitat-Disziplin kollidiert:** wörtliche `[Quelle: … — "…"]`-Zitate sind im Wire Fakes; der Citation-Validator vergleicht gegen Originale → Zitate stimmen nie wörtlich → Re-Round-Schleifen, gestrippte Zitate.

### F7 — Erkennungslücken auf genau diesem Material
- OCR-Garble zerstört Kontext-Gates (`Rasseport No.` matcht die `passport`-Regel nicht).
- **MRZ ist keine eigene Regel.** Die „MRZ-Kappung" (v9.331, `doc_convert.collapse_ocr_filler:761`) kappt nur **Füllzeichen-Läufe** (`<<<<` → 8×`<`) — **keine Wert-Maskierung**.
- `date` feuert nur mit Geburts-/Namens-Kontext (30-Zeichen-Fenster `_birth_context_distance:1330`, 120-Zeichen-Namensnähe `_DATE_ADDRESS_NAME_PROXIMITY:1291`). Auf einem Pass liegen DOB und Expiry nah beieinander → **mal wird beides, mal nichts erwischt** → nicht deterministisch, welche Hälfte einer zusammengehörigen Wertemenge ersetzt wird.

---

## 2. Leitidee

> **Diese Workload ist ein Join + Arithmetik AUF den geschützten Werten selbst.** Ein String-Rewriter auf dem Wire kann das nie verlustfrei überleben.

Drei Hebel, die zusammen Parität herstellen:
- **(a)** Checks dorthin verlagern, **wo die Rohdaten liegen** (Server/lokal, LLM bekommt nur Verdikte) → **L1**
- **(b)** Pseudonymisierung von **String-** auf **Entitäts-Ebene** heben → **L2**
- **(c)** Die **Tool-Grenze symmetrisch** machen (Args rein-übersetzen, Results raus-übersetzen) → **L3**, plus die bewusste Ausnahme Web → **L4**

---

## 3. Umsetzungsreihenfolge

| # | Baustein | Repariert | Aufwand |
|---|---|---|---|
| 1 | **L1** — Deterministische Verifikations-Tools (`doc_checks`) | **F2** komplett, F1 teilweise | M |
| 2 | **L3** — Dispatch-Symmetrie (Args-Deanon + Results-Anon) | **F3**, F5 (mempalace + web-inbound) | M |
| 3 | **L2** — Entitäts-Map + MRZ-Fakes + Datums-Offset | **F1**, Rest von **F2**, F7 | **L (größter Brocken)** |
| 4 | **L4** — Web-Egress-Policy, **Phase 1 + Phase 2** | **F4** | M–L |
| 5 | **L5** — OCR-Preamble scannen + als Entity-Seed | **F5**, F7, speist L2 | S–M |
| 6 | **L6** — Report-Fidelity (PDF + Reverse-Linter + Clamp) | **F6** | M |
| 7 | **L7** — KYC-Preset + Degradations-Transparenz | UX/Vertrauen | S |

**Begründung der Reihenfolge:** L1 eliminiert den gefährlichsten Schaden (falsche Fälschungsindizien) mit kleinstem Eingriff und etabliertem Muster. L3 repariert Retrieval/Pfade und schließt die zwei größten Leaks — und liefert die Infrastruktur, auf der L2 aufsetzt. L2 ist der größte Qualitätshebel, aber auch der aufwendigste; er profitiert davon, dass L1/L3 schon stehen. L4 braucht L2/L3 für die Rück-/Hinübersetzung in Phase 2. L5 speist L2. L6/L7 sind Vertrauens-Schicht.

---

## L1 — Deterministische Verifikations-Tools (`doc_checks`)

**Ziel:** Die Rechen-Checks laufen **serverseitig auf Rohdaten** und geben **PII-freie Verdikte** zurück. Damit sind sie **immun gegen jede Anonymisierung** — sie funktionieren, als wäre der Scanner aus.

**Muster:** Exakt wie das `xlsx`-Toolset (v9.262) und das `ocr`-Toolset (v9.293.1): *„Das Modell liefert INTENT, der SERVER rechnet."* Entspricht CLAUDE.md-Regel 5 („if code can answer, code answers").

**Vorlage lesen:** `engine/tools/ocr_tools.py` (Header erklärt die Philosophie; `_require_tesseract`, `_resolve_input`, `_ok`/`_err`-Konvention).

### Tools

**`mrz_verify(path?, text?)`** — parst die MRZ (TD1/TD2/TD3), prüft **alle ICAO-9303-Prüfziffern**.
Rückgabe **ohne** Nummer und **ohne** Namen:
```json
{"mrz_found": true, "format": "TD3", "checksums": {"document_number": true, "dob": true,
 "expiry": true, "personal_number": true, "composite": true}, "all_valid": true,
 "doc_type": "P", "issuer": "USA", "nationality": "USA",
 "expiry_state": "valid", "expiry_month": "2027-01", "age_years": 79, "sex": "F"}
```
ICAO-9303-Prüfziffer: Gewichte `7,3,1` zyklisch; `0-9`→Wert, `A-Z`→10..35, `<`→0; Summe mod 10.

**`doc_dates_check(sources)`** — nimmt Pfade und/oder benannte Datumswerte, rechnet **Relationen** statt Absolutwerte:
```json
{"checks": [
  {"name": "passport_valid_today", "result": true, "detail": "expiry 2027-01-26 > today"},
  {"name": "renewal_gap", "result": "9 days", "detail": "old expiry 2017-01-18 → new issue 2017-01-27"},
  {"name": "validity_span", "result": "10y - 1d", "conforms_to": "US 10-year passport"},
  {"name": "photo_vs_doc_date", "result": "-5 days", "detail": "EXIF 2026-07-02, doc date 2026-07-07"}]}
```

**`identity_consistency(sources)`** — serverseitiger Feldvergleich (Name normalisiert, DOB, Passnr. alt/neu) über Drawer/Dateien/Attachments:
```json
{"sources_compared": 6, "name_match": "6/6", "dob_match": true, "dob_sources": 5,
 "passport_chain": "old 2007-2017 → new 2017-2027 (consecutive)",
 "discrepancies": [{"field": "name", "note": "one source reads 'Bonnie N. Stark' (OCR variant of 'Bonnie M.')"}]}
```
Name-Normalisierung: Case, Reihenfolge, Initialen, MRZ-Form `NACHNAME<<VORNAME`, Fuzzy für OCR-Garble.
→ **Diese Normalisierungslogik ist dieselbe, die L2 braucht** — von Anfang an in ein gemeinsames Modul legen (Vorschlag: `engine/identity.py`), damit L2 sie wiederverwendet und nicht dupliziert.

### Design-Entscheidungen (getroffen)
- **Neue Tool-Gruppe `doc_checks`** (nicht in `documents` einhängen) → sauber gate-bar, eigener Warmup-Footprint.
- **Tools arbeiten primär auf PFADEN** (Rohdaten), nicht auf vom Modell übergebenen Werten. Grund: robust **auch vor L3** — ein Fake-MRZ-String als Arg würde sonst falsch prüfen.
- Optionaler `text=`-Parameter für den Fall, dass der Wert schon im Kontext steht — aber Doku im Schema: *„bevorzugt `path`; `text` nur wenn kein Pfad verfügbar."*

### Verdrahtung (4-Site-Regel, CLAUDE.md)
1. Schema-Dicts in `TOOL_DEFINITIONS` (`engine/tool_schemas.py`, Anthropic-Flat-Shape)
2. Neue Gruppe in `TOOL_GROUPS` (`brain.py:1411`, direkt neben `"ocr"` bei `:1425`)
3. Impl in `engine/tools/doc_checks.py` (NEU; lazy `import brain as _brain`)
4. `TOOL_DISPATCH`-Einträge (`brain.py`, ~`:14745`) — **direkte Fn-Refs, keine Lambdas** (Dispatch-Identity-Regel)
Plus: `TOOL_ICONS` (`brain.py:14220`) + `TOOL_VERBS` (`:14247`).

### Verifikation
- **Golden-Test gegen das echte Material:** MRZ des alten Passes (2007, `3099879889USA4702058F1701186`) und des neuen (2026, `5606837078USA4702058F2701264`) — beide müssen `all_valid: true` liefern (der Chat hat das manuell bestätigt).
- `tests/test_doc_checks.py`: Prüfziffer-Mathematik (inkl. absichtlich verfälschter MRZ → `false`), Datums-Relationen, Name-Normalisierung über die 8 Oberflächenformen aus F1.
- `py_compile` + 4-Site-Konsistenzcheck.

---

## L3 — Dispatch-Choke-Point-Symmetrie

**Ziel:** Das Modell denkt in Fakes, die Tools arbeiten auf Rohdaten — **ohne dass eines vom anderen weiß**.

**Ort:** Die Tool-Exec-Pipeline (built-in pre → external pre → execute → built-in post → external post → `_after_file_write`; siehe `engine/CLAUDE.md`). Dispatch: `engine/llm_loop.py:712` (`fn(args)`), MCP-Fallback `llm_loop.dispatch_tool:704`.

### L3a — Args-Deanonymisierung (NEU, built-in **pre**-Hook)

Für **lokal ausführende** Tools: Fakes + opake Tokens → **Echtwerte**, bevor das Tool läuft.

**Whitelist** (nur diese!): `mempalace_query`, `mempalace_kg_*`, `read_document`, `read_file`, `list_directory`, `search_files`, `execute_command`, `python_exec`, `ocr_*`, `xlsx_*`, `text_diff`, `doc_checks`-Tools (L1).

**KRITISCHE AUSNAHME:** **Web-Tools NIEMALS.** Args-Deanon für `web_fetch`/`searxng_search`/`exa_search`/`image_search`/`news_search`/`dev_search`/`science_search` wäre ein **stiller Egress** — genau das, was L4 explizit regelt. Diese Trennung ist die wichtigste Invariante von L3.

Implementierung: `pseudonymizer.deanonymize_text` auf alle String-Args (rekursiv durch Listen/Dicts). Mapping aus `get_request_context()._gdpr_mapping_id`.

**Löst:** `mempalace_query` findet wieder (F3), Pfade funktionieren wieder (F3), Python-Skripte mit Wert-Literalen laufen korrekt (F3).

### L3b — Results-Anonymisierung vervollständigen

Die fehlenden Seams nachziehen — **konsistent zum bestehenden Per-Tool-Muster** (nicht als generischer Post-Hook, sonst Doppel-Anonymisierung bei den 12 Tools, die `_gdpr_anon_tool_text` schon selbst rufen):

- `engine/mempalace_glue.py`: `tool_mempalace_query` (`:318`), `tool_mempalace_kg_query` (`:1563`), `mempalace_kg_search` (`:1607`), Drawer-Serialisierung (`:994-1023`) → durch `_gdpr_anon_tool_text`.
- `engine/tools/misc_tools.py`: `tool_web_fetch` (`:1173-1190`), `_searxng_query`-Results (`:1276-1307`) → durch `_gdpr_anon_tool_text`.

**Nebeneffekt (erwünscht):** Web-Treffer über die echte Person mappen **auf dieselbe Fake-Identität** wie die Akten (sobald L2 steht) → **der Web-Abgleich funktioniert wieder, ohne dass das Cloud-Modell den Echtnamen sieht.** Das ist die Grundlage von L4-Phase-2.

**Achtung:** Der `_classification_gate_tool_text`, der in `_gdpr_anon_tool_text` **vorgeschaltet** läuft (`brain.py:3142`), greift damit **auch** für mempalace-Drawer und Web-Inhalte. Das ist konsistent, ändert aber Verhalten → in den Release-Notes erwähnen.

### L3c — Notice-Split im Ledger-Rewrite

`_apply_pii_decisions_to_wire` (`chat.py:1906-1917`) muss `_split_attachment_notice` genauso anwenden wie der Scan-Pfad (`chat.py:1980`, `:2004`, `:3649`) — sonst zerschreibt der Ledger-Replace die Dateipfade in der History (F3, „perfider noch").
**Ausnahme:** Der neue OCR-Block aus L5 wird bewusst **doch** gescannt → er bekommt einen **eigenen** Marker und wird **nicht** vom Notice-Split erfasst.

### Verifikation
- Reproduktion aus dem Original-Chat: `mempalace_query("Stark Bonnie KO Kunde")` in einer anonymisierenden Session muss **dieselben 10 Drawer** liefern wie mit Scanner=aus (nur die Ergebnistexte anonymisiert).
- `read_document` auf einen Pfad, dessen Dateiname den Kundennamen enthält, muss **funktionieren** (nicht *File not found*).
- **Negativtest (Sicherheit):** `searxng_search` mit einem Fake im Arg darf **KEINEN** deanonymisierten Wert an das Netzwerk schicken. Diesen Test explizit schreiben — er schützt die wichtigste L3-Invariante.
- `tests/test_request_context_isolation.py` muss grün bleiben (Args-Deanon läuft auf dem Worker-Thread mit dem `RequestContext`).

---

## L2 — Entitäts-konsistente Pseudonymisierung

**Der größte Brocken — und der größte Qualitätshebel.** Hebt das Mapping von **String-** auf **Entitäts-Ebene**.

### L2a — Entitäts-Schicht

Neue Schicht über `pseudonymizer.Mapping`: **eine Fake-Identität pro Person**, mit **Varianten-Generator** pro Oberflächenform.

**Alias-Resolver** (nutzt/erweitert die Normalisierung aus L1/`engine/identity.py`):
- Case-, Reihenfolge- und Initialen-Varianten (`Bonnie M Stark` ≡ `STARK, BONNIE MARIE` ≡ `B. Stark`)
- **MRZ-Form** `NACHNAME<<VORNAME<MITTELNAME`
- **E-Mail-Localpart** (`kbstark@pacbell.net` → gehört zur Entität)
- **Fuzzy-Match für OCR-Garble** (`Bonnie MASE`, `BONNT DCMARTE`, `Bonnie N. Stark`) — Levenshtein/Token-Sort-Ratio mit konservativer Schwelle
- **Dateinamen-Formen** (`STARK_Bonnie_M_Mrs._107625`)

**Varianten-Generator:** Zur Fake-Identität `Muster, Erika Marie` werden **passende Varianten** erzeugt:
`Erika Muster` · `MUSTER<<ERIKA<MARIE` · `emuster@example.net` · `E. Muster` · `MUSTER_Erika_M_Mrs._<fake-id>`
→ Jede Oberflächenform des Originals mappt auf die **formgleiche** Fake-Variante. Das Modell sieht eine **kohärente synthetische Welt** statt eines Flickenteppichs.

**Persistenz:** Die Entitäts-Zuordnung muss in `pseudonym_maps` (verschlüsselt) und im `pii_decisions`-Ledger überleben. Vorschlag: `Mapping` bekommt ein `entities: dict[entity_id, {canonical, variants, fake_canonical, fake_variants}]`-Feld; `_serialize_mapping`/`_deserialize_mapping` (`pseudonymizer.py:878/892`) erweitern. **Rückwärtskompatibel** — alte Mappings ohne `entities` müssen weiter laden.

### L2b — Passnummern als Shape-Fake mit **gültigen MRZ-Prüfziffern**

Konsequente Fortsetzung der bestehenden Philosophie (IBAN mod-97-gültig, Kreditkarte Luhn-gültig — `pseudonymizer.py:133/170`):

- `passport` + `passport_ctx_loose` in `SHAPE_PRESERVING` (`:70`) aufnehmen.
- `_fake_passport(original, salt)` → gleiche Länge, gleiches Alphabet.
- **Neu: eine komplette Fake-MRZ**, die zur Fake-VIZ **konsistent** ist und deren **Prüfziffern stimmen** (ICAO-9303-Rechner aus L1 wiederverwenden!).
→ Dann funktioniert sogar die **LLM-eigene MRZ-Mathematik** wieder. F2 ist damit doppelt abgesichert (L1 serverseitig, L2 im Wire).

### L2c — Datums-Policy: konstanter Offset statt Tag-Jitter

**Entscheidung (getroffen):**
- **Konstanter Offset pro Session** (z. B. −11 Tage), **nicht** Tag-Jitter. → Ordnung, Deltas, „10 J − 1 T", EXIF-Abstände bleiben **exakt** erhalten.
- **Rollen-bewusst:** Offset **nur auf geburts-/lebensereignis-kontextierte Daten** (`dob`, `date` mit Birth-Kontext). **Dokument-Lebenszyklus-Daten (Ausstellung/Ablauf) bleiben UNVERÄNDERT** — ihre Identifikationskraft ist nach Fake-Nummer und Fake-Name gering, und „Ist der Pass gültig?" stimmt dann **exakt**.
- `_fake_date` (`pseudonymizer.py:438`) entsprechend umbauen; der Offset lebt im `Mapping` (aus dem Salt abgeleitet → deterministisch, persistiert).

### L2d — Datumsformate vervollständigen

`_DATE_PATTERNS` (`pseudonymizer.py:424`) **und** die Scanner-Regel `date` (`engine/pii_ner.py:1240`) um die Formen erweitern, die auf **genau diesem Material** vorkommen:
- **Textuelle Monate:** `5 FEB 1947`, `05 Feb 1947`, `26. Jan 2027`, `19 JAN 2007`
- **EXIF:** `2026:07:02 14:24:48`
→ Sonst bleibt dieselbe Datumsangabe in einer Form roh und in der anderen gefaket (F2, „Formatblindheit").

### Verifikation
- **Der F1-Testfall:** Alle 8 Oberflächenformen von „Bonnie Stark" müssen auf **eine** Fake-Entität mappen, jeweils in der **formgleichen** Variante.
- **Der F2-Testfall:** Fake-MRZ muss `mrz_verify` (L1) mit `all_valid: true` passieren.
- **Der Datums-Testfall:** `27.01.2017` und `26.01.2027` (Fake) müssen weiterhin exakt „10 Jahre − 1 Tag" auseinanderliegen; `18.01.2017` → `27.01.2017` muss weiterhin +9 Tage sein.
- Bestehende Tests: `tests/test_pseudonymizer.py`, `tests/test_pseudonymizer_persistence.py`, `tests/test_pii_ner.py` — **alle müssen grün bleiben** (bzw. bewusst angepasst werden, mit Begründung im Commit).
- **Parität-Regression:** `tests/test_chat_worker_helpers.py` (deckt den Reverse-Pfad ab).

---

## L4 — Web-Egress-Policy (Phase 1 **und** Phase 2)

**Der einzige Punkt mit echtem Zielkonflikt:** Personensuche im offenen Web mit Klarnamen **ist** inhärent Preisgabe. Es gibt keine Lösung, die beides hat — nur eine **ehrliche, auditierbare Entscheidung**.

### 4.1 Der Gate-Mechanismus (gemeinsame Basis beider Phasen)

**`_gdpr_guard_web_args(tool_name, args)`** als **built-in pre-Hook am Dispatch** (die Pipeline hat die Stufe schon — siehe L3), gescopet auf `TOOL_GROUPS['web']`:
`searxng_search`, `science_search`, `dev_search`, `image_search`, `news_search`, `exa_search`, `web_fetch`.

Geprüft werden **alle String-Args** — bei `web_fetch` **auch die URL**: im Original-Chat steckte der Name im **URL-Slug** (`bizapedia.com/people/bonnie-stark.html`).

**Der entscheidende Design-Punkt: NICHT primär per PII-Scanner prüfen, sondern gegen die bekannten geschützten Werte der Session.**
Quellen: `mapping.forward`-Keys (Originale), `mapping.reverse`-Keys (Fakes/Tokens), `pii_decisions.raw_value`.
Plus Normalisierungen: lowercase, Space → `-` / `_` / `+` / `%20` (URL-Slugs!).

| Query enthält… | Bedeutung | Reaktion (in **jedem** Modus) |
|---|---|---|
| **Fake / Token** (`Erika Muster`, `<PASSPORT_1_ab12>`) | Suche wäre semantisch leer **oder trifft echte Fremdpersonen** (Gift-Evidenz) | **IMMER refuse.** Eine Fake-Suche freizugeben ist sinnlos. In Phase 2 wird stattdessen die **Rückübersetzung** angeboten |
| **Bekanntes Original** (Klarname, Adresse …) | Echter Egress an Google/Bing/exa/Zielhost | Policy entscheidet: refuse / ask / durchlassen (wenn `released`) |
| **Frische PII** (Zusatz-Scan, nur Personen-Kategorien) | z. B. dritte Person, nie im Mapping | Wie „bekanntes Original" |
| **Nichts davon** („Samsung S23 EXIF GPS null", „ICAO 9303 check digit") | Technische Query | **IMMER frei durchlassen** |

### 4.2 Zwei Kalibrierungen, die den Gate praxistauglich machen

**(1) Der Zusatz-Scan ignoriert die `category`-Actions.**
Mit der heutigen Config (`contact` = `ignore`, siehe §0.5) steht der **Klarname weder im Mapping noch im Ledger**. Ein Gate, der nur „actionable findings" prüft, wäre also **für den wichtigsten Wert blind**.
→ Der Web-Gate fragt den Scanner **„was IST PII"**, nicht **„was ist actionable"** — mit eigener Kategorien-Whitelist:
- **gaten:** `name`, `dob`, `date`(birth-context), `address`, `email`, `phone`, `national_id*`, `passport*`, `financial`, `bare_id`
- **durchlassen:** `organisation`, `network`, `business_id` — sonst blockt NER „Samsung" / „WebID Solutions GmbH". **Im Original-Chat war über die Hälfte der ~15 Queries technisch** — die dürfen **nie** ein Modal auslösen.

**(2) FP-Kosten sind asymmetrisch.** Ein Fehlalarm kostet eine Rückfrage/Umformulierung; ein Miss kostet ein **Leak**. → Konservativ gaten, **aber nur auf den Personen-Kategorien**.

### 4.3 Die drei Modi

Config: **`gdpr_scanner.web_egress: "refuse" | "ask" | "block_group" | "allow"`**
Global · überschreibbar per Projekt (KYC-Preset → `ask`, siehe L7) · überschreibbar per Session.
**Default: `refuse`.**

---

#### **Phase 1 — `refuse` (Default)**

Tool-Call wird abgelehnt mit **strukturiertem, handlungsleitendem** Error:

```json
{"error": "web_query_blocked_pii",
 "blocked": [{"value_kind": "name", "released": false}],
 "hint": "Geschützter Wert in Web-Query. Optionen: (1) Prüfung im Bericht als 'nicht prüfbar (Datenschutz)' ausweisen — NIE als 'keine Treffer'. (2) Den Nutzer per ask_user um Freigabe bitten. (3) Query ohne den Wert umformulieren, falls sinnvoll. Wiederhole den Call NICHT unverändert."}
```

**Wichtig:** `value_kind`, **nicht** der Wert selbst — der Error-String geht ja an das Modell zurück.

**Plus ein Satz im `_GDPR_ANON_CLAMP`** (`engine/prompt_build.py:701`):
> *„Websuche zu geschützten Werten: als ‚nicht prüfbar (Datenschutz)' ausweisen; behaupte NIE ‚keine Treffer' für eine nicht ausgeführte Suche."*

→ **Das repariert die Negative-Evidenz-Lüge („kein Obituary gefunden") an der Wurzel.**
Der klare Error verhindert auch Retry-Schleifen (der Tool-Dedup würde erst beim 2. Dup greifen — so weit kommt es gar nicht).

**Auch `block_group`** (für strikte Projekte) hier mitbauen: Web-Gruppe bei aktivem Mapping komplett via `exclude_tools` raus. Mechanik existiert bereits (Websuche-Basket-Lockout, `chat.py:6923`-Region; `resolve_active_tools` subtrahiert `exclude_tools`, `brain.py:2376`). → Die Tools erscheinen gar nicht erst, das Modell **plant ohne Web**, statt gegen Wände zu laufen.

---

#### **Phase 2 — `ask` (Ziel-Modus)**

Der Flow, der **E4 (Web-Korroboration) zurückholt**.

**(a) Consent-Dialog — per WERT, nicht per Query.**
Erster geblockter Call → **ein** `AskUserQuestion` für die Session:

> *„Web-Recherche möchte geschützte Werte verwenden — freigeben?"*
> ☑ Name „Bonnie Stark" ☑ Ort „Oregon City" ☐ Geburtsdatum ☐ Passnummer

**Nicht 15 Modals für 15 Queries.** Der Original-Chat wäre mit **einem** Dialog durchgelaufen.
(Mechanik existiert: `AskUserQuestion` blockiert via `_pending_answers[session_id]` + `Event`, entsperrt durch `POST /v1/chat/answer`. **Vorsicht:** `run_turn` muss `make_artifact_event_callback` installiert haben, sonst hängt der Tool-Call — die v9.101.12-Failure-Mode; siehe CLAUDE.md § Agentic Loop.)

**(b) Freigabe → Ledger.**
`pii_decisions`-Zeile pro Wert mit **neuer Disposition `release_web`**. Der Ledger ist dafür gebaut: per-`value_hash`, session-scoped, auditierbar. **Session-sticky**, im GDPR-Panel sichtbar + **widerrufbar**.

**(c) Ausführung mit Hin-Übersetzung.**
Sucht das Modell mit dem **Fake**, ersetzt der Gate bei **freigegebenem** Wert Fake → Original — **nur für den ausgehenden Request**. Das Modell selbst sieht weiterhin **nur Fakes**.

**(d) Ergebnis-Rück-Anonymisierung.**
Bevor das Ergebnis zum Modell geht: Known-Value-Replace **Original → Fake** auf dem Ergebnistext (SERP-Snippets, gefetchte Seiten).
→ „Erika Muster, age 79, Oregon City" aus dem Web matcht wieder auf **dieselbe Fake-Identität** wie die Akten → **der Web-Join funktioniert, ohne dass das Cloud-LLM je den Klarnamen sieht.**
Das braucht **kein volles L2** — exakte bekannte Werte + Normalisierungen reichen; die Entitäts-Varianten aus L2 verfeinern es. (Technisch ist (d) derselbe Seam wie L3b — `_gdpr_anon_tool_text` auf Web-Results; L4 Phase 2 setzt darauf auf.)

**(e) Teilfreigabe verhält sich natürlich.**
`"Bonnie M Stark Oregon City OR age 79 born 1947"` → Name+Ort released, `born 1947` (dob) **nicht** → refuse mit Hinweis → Modell formuliert um zu `"Bonnie M Stark Oregon City"` → **praktisch gleiches Suchergebnis, minimale Preisgabe.**

**Invariante:** **Der Server schreibt Queries NIEMALS selbst um.**
Umformulieren = LLM-Arbeit · Entscheiden = User-Arbeit · Prüfen = Server-Arbeit.

**(f) Websuche-Basket = implizite Freigabe.**
Die vom User **selbst kuratierten** URLs im Websuche-Tab gelten als freigegeben. **Aber:** der Prefetch-Inhalt (`_build_web_sources`, `chat.py:183`) läuft durch **dieselbe Ergebnis-Rück-Anonymisierung** wie (d), bevor er in die Wire-Preamble geht (`_inject_web_preamble_into_wire`, `chat.py:296` / `:4246`). **Heute ist er ungescannt** (F5).

**(g) Audit.**
Jede Gate-Entscheidung (refuse / release / ausgeführte rückübersetzte Query) → Audit-Zeile (`audit.db`, wie `pii_local_swap` / `pii_blocked`). **Nachvollziehbar, WAS die Maschine WANN verlassen hat.**

### 4.4 Was das am Original-Chat geändert hätte (`ask`-Modus)

Turn 1: 3 technische Queries laufen **frei** durch · erste Personen-Query → **EIN Dialog** → User gibt Name+Ort frei, DOB/Passnr. nicht → 10 Queries laufen (rückübersetzt) · 2 Queries mit `born 1947` werden refused → Modell formuliert **ohne Geburtsjahr** um · `web_fetch` bizapedia läuft (Name released, **auch im URL-Slug** erkannt) · alle Ergebnisse kommen **rück-anonymisiert** ins Modell.
→ **Adress-/Alters-Korroboration und der Obituary-Negativbefund bleiben als Evidenz erhalten** — mit **einem Klick** Overhead und **ohne** DOB-/Passnummer-Egress.

### 4.5 Warum nicht nur eine Stufe?

- **Nur `block_group`:** verliert E4 komplett → widerspricht dem Paritätsziel.
- **Nur `ask`:** ohne den refuse-Unterbau (Fake-Detection, Error-Disziplin, Clamp) bleibt die **Negative-Evidenz-Lüge** in jedem Nicht-Freigabe-Fall bestehen.
- **Nur `refuse`:** sicher + ehrlich, aber die Web-Evidenzklasse bleibt **dauerhaft** weg.
→ Als **Phase 1** ist `refuse` trotzdem genau richtig: kleiner Eingriff, **sofort** wirksam gegen das akuteste Problem (heute würde der Auto-Modus den Klarnamen **kommentarlos** an externe Suchmaschinen schicken).

### Verifikation
- **Sicherheits-Negativtest (der wichtigste):** In einer anonymisierenden Session ohne `release_web` darf **kein** Klarwert das Netzwerk erreichen. Über den Gate testen, **und** über L3 (Web-Tools dürfen **nicht** in der Args-Deanon-Whitelist stehen).
- **FP-Test:** Die ~8 technischen Queries aus dem Original-Chat (`"Samsung Galaxy S23 Ultra EXIF"`, `"ICAO 9303 check digit"`) müssen **ungehindert** durchlaufen — **kein** Modal.
- **URL-Slug-Test:** `web_fetch("https://www.bizapedia.com/people/bonnie-stark.html")` muss den Namen im Slug **erkennen**.
- **Phase-2-E2E:** Consent → `release_web`-Ledger-Zeile → Query läuft rückübersetzt → Ergebnis kommt rück-anonymisiert an → Modell sieht Fake-Identität.
- **Widerruf:** `release_web` zurückziehen → nächste Query wird wieder geblockt.

---

## L5 — OCR-Preamble scannen + als Entity-Seed nutzen

**Zwei Fliegen:** Das größte verbliebene Leck schließen **und** L2 mit sauberen Ankerwerten füttern.

### L5a — Den OCR-Block vom Notice-Exempt trennen
Der `[Bild-Anhänge — automatisch, ohne KI erkannt …]`-Block (`chat.py:7094-7097`) ist **kein Boilerplate, sondern Content**. Heute fällt er unter `_split_attachment_notice` (`chat.py:158-180`) und wird **nie gescannt** (F5).

→ **Eigenen Marker** einführen (z. B. `\n\n[Bild-Anhänge — Inhalt:]`), der **nicht** in `_ATTACH_NOTICE_PREFIXES` steht. Der Pfad-Teil („User attached files saved to disk") bleibt **exempt** (Pfade dürfen nicht zerschrieben werden — das ist der ursprüngliche, korrekte Grund für den Split, `chat.py:167-171`).
→ Der Content-Teil geht durch den **normalen User-Text-Scan** (`chat.py:3651`) und damit durch Anonymisierung.

**Konsistenz-Check:** Das muss an **allen drei** `_split_attachment_notice`-Aufrufstellen (`chat.py:1980`, `:2004`, `:3649`) **plus** im Ledger-Rewrite (L3c) einheitlich sein.

### L5b — MRZ strukturiert parsen → Entity-Seed
Die MRZ ist die **sauberste maschinenlesbare Identitätsquelle** im ganzen Material.

→ Beim Attachment-Handling: MRZ **strukturiert parsen** (ICAO-9303-Parser aus L1 wiederverwenden) und daraus die **Entitäts-Map (L2) seeden**: Name, DOB, Passnummer **inkl. erwartbarer Varianten** sind dann **ab Turn 1** konsistent gemappt.
→ Die OCR-Garbles (`Bonnie MASE`, `BONNT DCMARTE`) werden per Fuzzy-Match gegen die geseedete Entität eingefangen.

**Die Lücke wird vom Leck zum Anker.**

### Verifikation
- Der Original-Attachment-Satz (10 JPGs, `/tmp/brain-attachments/58e3c521438a/`) → nach L5 darf **kein** Klarname/DOB/Passnr. im Wire stehen.
- Die Pfade in der Notice müssen **unverändert** bleiben (`read_document` muss weiter funktionieren).
- Der Seed muss alle 8 Oberflächenformen aus F1 einfangen.

---

## L6 — Report-Fidelity (Deanonymisierung, die man merkt)

### L6a — PDF-Pfad
`engine/file_pseudonymize.py` kann **kein PDF** (`:283-286`: unsupported → unverändert durchreichen). Der Original-Chat erzeugte **2 PDF-Reports** → sie enthielten Fake-Werte, **ohne Kennzeichnung** (F6).

**Optionen (eine wählen):**
1. In Anonymise-Sessions PDF-Erzeugung über den **HTML→Server-Render-Weg** leiten (HTML **ist** reversibel — `.html` steht in `SUPPORTED_EXTS`) und **serverseitig nach der Deanonymisierung** rendern. *(Bevorzugt: es gibt bereits zwei HTML-Report-Renderer — siehe `[[project_two_html_report_renderers]]` / `report_html.py`.)*
2. Direkte PDF-Writes in Anonymise-Sessions **blocken** mit Hinweis auf den HTML-Weg.

### L6b — Reverse-Linter (fail loud)
Nach dem Deanonymize-Pass (`chat.py:5007-5074` für Text, `chat.py:2155` für Dateien) den Endtext auf **verbliebene Fakes** prüfen:
- exakte Fakes aus `mapping.reverse`
- **semantisch gleiche Datumswerte in anderem Format** (der Fake als „17. Februar 1947" statt `17.02.1947`)
- Fuzzy-Namensreste (Genitiv „Webers", Initialen „E. M.")

→ Badge/Warnung: **„⚠️ N Werte konnten nicht zurückübersetzt werden"** — statt **stiller Falschdaten**.
Entspricht CLAUDE.md-Regel 12 (fail loud).

### L6c — Clamp ergänzen
`_GDPR_ANON_CLAMP` (`engine/prompt_build.py:701`) um die Anweisung erweitern:
> *„Gib geschützte Werte in Berichten EXAKT in der Oberflächenform wieder, in der du sie erhalten hast — nie reformatieren, übersetzen, in Initialen kürzen, deklinieren oder abgeleitete Werte daraus berechnen."*

**Achtung — Spannung zum bestehenden Clamp:** Der sagt heute *„Shape-Fakes sind KEINE Platzhalter — behandle sie wie echte Werte"*. Das ist für die **Analyse** richtig (das Modell soll damit rechnen), **fördert aber die Reformatierung**. Die neue Anweisung ist die nötige Ergänzung, kein Widerspruch: *rechnen ja — reformatieren nein.* Beim Formulieren sorgfältig sein.

### Verifikation
- HTML-Report aus dem Original-Chat (`Analyse_Stark_Bonnie_M_Gesamtbericht.html`, 67 KB) durch den Reverse-Pfad → **alle** Werte real, Linter meldet 0.
- Ein absichtlich reformatierter Fake im Modell-Output → Linter **muss** anschlagen.

---

## L7 — KYC-Preset + Degradations-Transparenz

### L7a — Projekt-Preset „PII-Analyse (KYC)"
Bündelt: `gdpr_scanner.enabled=true` · `web_egress="ask"` · Kategorien so, dass **Namen anonymisiert werden** (heute `contact=ignore`! — siehe §0.5) · `doc_checks`-Gruppe aktiv · Research-/Citation-Discipline an.

**Alternative, die man dem User anbieten sollte:** `force_local` — heute die **einzige wirklich saubere** Route (kein Egress überhaupt), Preis: Qualitätsverlust durch das lokale Modell.

### L7b — Degradations-Anzeige
Pro Turn eine kleine Anzeige, **warum** der Output anders aussieht:
> *„Web-Personensuche: Consent nötig · Vision-Vergleich: lokal · MRZ-Check: serverseitig"*

Der Analyst muss verstehen, **welche Evidenzklasse gerade eingeschränkt ist** — sonst hält er eine datenschutzbedingte Lücke für einen Befund. (Das ist die UX-Seite von F4/F6.)

---

## 4. Was auch mit L1–L7 NICHT 1:1 erreichbar ist

Ehrlich benennen (CLAUDE.md-Regel 12):

- **Cloud-Vision-Gesichtsvergleich.** Pixel sind **nicht pseudonymisierbar**. Bleibt lokal (MLX-Vision) oder unterbleibt. *Im konkreten Chat war der Pixel-Vergleich ohnehin methodisch schwach (Korrelation 0.22 über verschiedene Kameras — der Chat hat das selbst als „intrinsisch unzuverlässig" eingeräumt) → der Verlust ist klein.*
- **Offene Personensuche ohne Namens-Release.** Prinzipiell unmöglich. **L4-Phase-2 (Consent) ist die ehrliche Antwort darauf** — nicht eine technische Umgehung.

**Erwartete Parität nach L1–L7:** E1/E2/E3/E5 **vollständig** (teils **robuster** als heute, weil deterministisch statt LLM-geraten), E4 **hinter einem Klick** oder ehrlich als „nicht prüfbar" markiert. → **~90 % des heutigen Ergebnisses, ohne Klardaten-Egress.**

---

## 5. Sofortmaßnahme (unabhängig von allem)

> Die Kombination **heutige Config + Auto-Anonymise** (`contact=ignore` → Klarname bleibt; Web-Tools offen) würde den **Klarnamen kommentarlos an externe Suchmaschinen schicken**.

**Wenn vor den großen Umbauten nur EINE Sache geändert wird: den Web-Tool-Args-Gate bei aktivem Mapping einziehen** (L4-Phase-1, `refuse` — wenige Zeilen am Dispatch + ein Clamp-Satz).

---

## 6. Pflichten vor dem Commit/Push (CLAUDE.md)

- **VERSION an ZWEI Stellen** bumpen: `brain.py:4` (`VERSION`) **und** `CHANGELOG` (`brain.py`, Liste ab `:7`) — `[[feedback_version_two_places]]`.
- **Kuratierter Changelog** (`engine/changelog_curated.py → CURATED_CHANGELOG`): L1/L4/L6/L7 sind **user-/admin-sichtbar** → neuer Eintrag **oben**, Deutsch, „Sie", **nutzenorientiert**. L2/L3/L5 sind teils internal — aber der **Effekt** (Analyse funktioniert trotz Anonymisierung) ist sichtbar → gebündelt eintragen.
- **`brain-agent-guide`-Skill im SELBEN Commit** (`[[feedback_update_skill_before_push]]`): `02-tools.md` (neue `doc_checks`-Gruppe!), `01-api.md` (falls Endpoints für `release_web`), `05-internals.md` (GDPR-Seams), `06-user-manual.md` (Consent-Dialog, Degradations-Anzeige — **Deutsch**), `SKILL.md`-Version. Pre-Push-Hook warnt sonst.
- **`py_compile` nach jedem Edit** + `/v1/status`-Version prüfen (`[[feedback_compile_check_brain_py]]`).
- **Server-Restart** via `launchctl` (**nie SIGKILL** — `[[feedback_never_sigkill_brain]]`); Listener braucht >6 s.
- **Direkt auf `main`**, keine Branches/PRs (`[[feedback_commit_to_main]]`).
- **INVARIANTS.md** § GDPR/PII aktualisieren (Seam-Liste, Web-Egress-Policy, Entitäts-Map).
- Schema-Änderungen (neue Tools) ⟶ **Warmup-KV-Prefix re-primt einmalig** (legitim, in den Notes erwähnen).

---

## 7. Offene Design-Entscheidungen für die neue Session

Diese wurden **getroffen** (nicht neu aufrollen, außer es gibt neue Evidenz):

1. **L2c Datums-Policy:** konstanter Offset pro Session, **nur** auf geburts-kontextierte Daten; Dokument-Lebenszyklus-Daten unverändert.
2. **L1 Tool-Gruppe:** neue Gruppe `doc_checks` (nicht in `documents`).
3. **L1 Tool-Inputs:** primär **Pfade** (Rohdaten), `text` nur als Fallback.
4. **L4 Default:** `refuse`. Ziel-Modus für KYC: `ask`.
5. **L4 Gate-Basis:** bekannte Session-Werte (Mapping + Ledger), **nicht** nur „actionable findings".
6. **L3 Results-Anon:** per-Tool (konsistent zum Bestand), **nicht** generischer Post-Hook (Doppel-Anon-Gefahr).
7. **L3 Args-Deanon:** Whitelist lokaler Tools; **Web-Tools NIEMALS**.

**Wirklich offen:**
- **L6a:** HTML→Server-Render **oder** PDF-Write-Block? (Empfehlung: Render-Weg, weil `report_html.py` schon existiert.)
- **L2a Persistenz-Schema:** `entities` als neues Feld in `Mapping` — Migrationspfad für bestehende `pseudonym_maps`-Zeilen festlegen (Vorschlag: fehlendes Feld = leer, kein Migrations-Script nötig).
- **Fuzzy-Schwelle** für OCR-Garble-Matching (L2a/L5b): konservativ starten, am echten 10-JPG-Satz kalibrieren.

---

## 8. Referenzen

- **Chat:** `58e3c521438a` (chats.db) · Projekt `ko-kunden` (`project_id=f973980be1b4`) · Artefakte in `agents/main/artifacts/2026-07-13_58e3c521438a/`
- **Testmaterial:** 10 echte Pass-/Videoleg-JPGs (`/tmp/brain-attachments/58e3c521438a/`) — dasselbe Material, an dem v9.329–9.333 die OCR-Halluzinationen gemessen haben. **Jede Änderung an L1/L2/L5 an DIESEM Material messen, nicht an synthetischen Scans** (`[[feedback_depth_over_speed]]`, und v9.329 hat genau diesen Fehler schon einmal gemacht).
- **Code-Landkarte:** `INVARIANTS.md` § GDPR/PII (`:5-17`) · `pseudonymizer.py` · `engine/pii_ner.py` · `handlers/chat.py` (Worker + Seams) · `brain.py:3114` (`_gdpr_anon_tool_text`) · `engine/llm_loop.py:704-723` (Dispatch) · `engine/mempalace_glue.py` (Lücke) · `engine/tools/misc_tools.py` (Web, Lücke)
- **Muster-Vorlagen:** `engine/tools/ocr_tools.py` + `engine/tools/xlsx_tools.py` (deterministische Toolsets für L1)
