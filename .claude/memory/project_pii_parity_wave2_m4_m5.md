---
name: project_pii_parity_wave2_m4_m5
description: "v9.344 — Org-Entitäten (M4) + Auto-Release (M5) + Ad-hoc-Egress (M10b) + Preset `screening`; die NER-Spanne ist NICHT der Firmenname"
metadata: 
  node_type: memory
  type: project
  originSessionId: 4fbf8eb8-c1dd-40da-841e-39f8c9b73432
---

**v9.344.0 — PII-Parität Welle 2, Session 2** (Fortsetzung von [[project_pii_parity_l_progress]]; Welle-2-Session-1 = 9.343.0 lieferte M1/M2/M3/M11).

Geliefert: **M4** (Org-Entitäts-Schicht), **M5** (Auto-Release), **M10b** (Ad-hoc-Egress ohne Projekt), Preset **`screening`**. OFFEN: M6 (Tabellen — Lasttest VOR Design!), M7, M8, M9.

## Die Befunde, die man NICHT raten kann (offline am echten Scanner gemessen)

1. **Die NER-Spanne IST NICHT DER FIRMENNAME.** `Wiener Privatbank SE` → Span `Wiener Privatbank` (Rechtsform fehlt); `Matejka & Partner Asset Management GmbH` → **zwei** Spans; `ABACO OVERSEAS HOLDINGS INC.` → `ABACO` + `OVERSEAS HOLDINGS INC`; `3SI Holding` → Müll-Präfix (`ENTWURF JA 3SI Holding`). ⇒ Rechtsform nie im Entitäts-Schlüssel, Müll-Präfixe strippen, Fragment-Spans müssen attachen können.
2. **Namenstragende Wörter sind KEINE Rechtsformen.** `Holding`/`Partner`/`Invest` zu strippen kollabierte die drei 3SI-Schwestern auf EINE Firma (selbstgemachter Homonym-Schaden). Nur echte Suffixe (GmbH/AG/SE/Ltd/Inc…).
3. **Der ORG-Tagger wirft gewöhnliche Substantive aus** (`Trust`, `Schwestern`). Leerer Stamm ⇒ **gar nicht faken** (nicht: anders faken) — sonst fällt der Wert auf den String-Faker durch und `Vandelay Corp` steht mitten im Fließtext.
4. **Fakes-von-Fakes schnappt auch im Fake-POOL zu.** Fake-Token, das selbst ein generisches Konzernwort ist (`Trust`/`Holding`/`Group`), wird beim nächsten Scan als frische Org-PII erkannt und erneut gefakt (gemessen: `Nordstern Trust` → `NORDSTERN Stark Corp`).
5. **Der Frisch-Scan des Gates erkennt Org-Fakes als PERSONEN wieder** (`Marbach Textil` → `rule=name`) → machte den Auto-Release im `ask`-Modus zunichte. Bekannte Fakes werden jetzt **längentreu maskiert**, bevor der Frisch-Scan läuft.
6. **Behörden/Prüflisten werden als Orgs getaggt** (`OFAC-SDN-Liste`, `Firmenbuch`, `Companies House`). Unter `kyc` egal (Orgs=Klartext), unter `screening` wurde daraus „In der **Oscorp Corp** steht …" — das Modell verlor die Liste, gegen die es prüft. Prüf**werkzeug** ≠ Prüf**subjekt** → nie faken.

## Architektur-Invarianten

- **`org_attach` ist STRIKT** (Token-Gleichheit, KEIN Substring-Merge, kein Fuzzy): ein False-Merge zweier echter Firmen = Gift-Evidenz im regulatorischen Bericht. Die Mutter/Tochter-Beziehung wird SEPARAT modelliert (`org_shares_stem`) und im Fake **gespiegelt** (Tochter erbt Mutter-Stamm, in BEIDEN Entdeckungs-Reihenfolgen).
- **M5-Typ-Brücke:** `known_fakes` trägt eine **rule_id** (`organisation`), `_WEB_GATE_PASS_CATEGORIES` hält **Kategorien** (`business_id`) → Kreuzung via `PII_RULE_CATEGORIES`. Das war der Knackpunkt.
- **Personen-Fakes refusen in JEDEM Modus** (auch `allow`); Mischquery kippt am Personenwert. Mutationsgeprüft.
- **Ein Refusal-Pfad gibt NIE übersetzte (=echte) Args zurück.**
- **Preset muss `organisation` als RULE_OVERRIDE heben**, nicht per Kategorie-Bump: die Live-Config trägt `rule_overrides['organisation']='ignore'`, und rule_overrides schlagen die Kategorie in `_pii_effective_action` → ein Kategorie-Bump wäre still geschattet und das Preset wirkungslos.

## Bewusste Grenzen (dokumentiert, nicht still)

- **`WPB` bleibt Rest-Leak** — aus den Stamm-Tokens nicht ableitbar (= W-iener P-rivat-B-ank, Intra-Wort-Zerlegung des Kompositums). Eine aggressivere Akronym-Regel würde `HTML`/`USA`/`LEI`/`ROE`/`EBIT` (die häufigsten ALLCAPS-Kürzel des Korpus) als Firmen faken.
- **M10b schützt Namen nur mit geladenem spaCy** (Name ist NER-only, es gibt keine Namens-Regex). Server lädt beim Boot.
