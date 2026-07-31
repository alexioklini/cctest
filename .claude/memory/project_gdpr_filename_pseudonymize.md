---
name: project_gdpr_filename_pseudonymize
description: v9.394.0 — Anhang-Dateinamen pseudonymisiert statt att_NN-Rename; pseudonymize_filename mit OPAKEN Tokens (pfad-sicher)
metadata: 
  node_type: memory
  type: project
  originSessionId: 2a5d4fdd-4b90-4a02-922f-e7cdc4d16758
  modified: 2026-07-21T18:37:03.314Z
---

**v9.394.0** (2026-07-21): Das M11-Disk-Rename (`att_01.pdf`) ERSETZT. Nutzer-Einwand war korrekt: das Rename schützte nur den Pfad, während der Originalname über die name_block-Preamble (`att_01.pdf = <original>`) ohnehin wörtlich ins Wire ging — reine Sachnamen (`JuliusBaer_Code-of-Ethics.pdf`) sogar ungefiltert.

NEU: Datei behält ECHTEN Namen auf Platte; Dateiname wird im Wire pseudonymisiert wie Fließtext.
- **Erkennung**: Basename in die GESCANNTE typed-half (`_NAME_BLOCK_MARKER` = `[Dateinamen der Anhänge:]`, `handlers/chat.py`) → Namens-Spans gefunden/entschieden/gemintet.
- **Anonymisierung**: `pseudonymizer.pseudonymize_filename` (NEU) im Worker-Anonymise-Branch, rewriter für name_block- UND notice-Pfadzeilen.
- **Wiederherstellung**: Args-Deanon-Seam (read_document ∈ GDPR_ARGS_DEANON_TOOLS, `brain.py:4023`) übersetzt vor Dispatch zurück; Reply-Deanon zeigt echten Namen.

KRITISCHE PFAD-SICHERHEIT (der ganze Trick): Body-Sweeps (`apply_known_values`/`apply_entity_variants`) lassen underscore/slash-Formen BEWUSST aus (Pfad-Integrität in Tool-Results), und `deanonymize_text` stellt alphabetische Name-Fakes WORTGEGRENZT wieder her → `Phillips_Scan.pdf` würde am `_` NICHT restauriert (`_` ist `\w`, siehe [[project_gdpr_tool_deanon_display]]). DESHALB muss `pseudonymize_filename` OPAKE Tokens (`<NAME_1_salt>`, enthält `<` → substring-restaurierbar) nutzen, NIE den Shape-Fake.

Mechanik von `pseudonymize_filename`:
- Erkennung aus der ENTITÄTS-Schicht (`mapping.entities`: Nachname+ALLE Vornamen+Org-Stem), NICHT nur `mapping.forward` — kurze Vornamen-Fakes (`Sam`, 3 Zeichen) sind absichtlich nicht als Standalone-Body-Token registriert (9.383.6), wären sonst im Dateinamen sichtbar.
- Opake Tokens pro Surface geMINTET, schreiben NUR `reverse[token]=surface` + privaten forward-Alias `\x00file:<surface>` (kann nie in echtem Text vorkommen). `forward[surface]` bleibt UNANGETASTET → Fließtext rendert weiter den schönen Shape-Fake.
- Nicht-Namens-Funde (Passwort-Dateiname `Alcuatmisi02026!.txt`, Kundennr.): Substring-Pass über opak-gefakte `forward`-Werte.
- Endung verbatim (read_document dispatcht auf echte Endung).

Gegatet auf `gdpr_scanner.enabled`; Scanner aus → kein Mapping, echter Pfad, unverändert.

tests/test_attachment_neutral_names.py komplett neu (11 Tests). Volle pseudonymizer+GDPR-Suite grün (155). test_pii_ner-Abweichung ist umgebungs-vorbestehend (NER-Modell), NICHT von dieser Änderung.
