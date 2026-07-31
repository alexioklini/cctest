---
name: feedback_bulk_delete_review_gate
description: "Bulk-Löschungen NIE list→delete in einem Skriptlauf — erst Kandidaten ausgeben, prüfen, DANN in separatem Schritt löschen. Anlass: 2026-07-22 Session-Cleanup löschte 1243 KG-Real-Policies-Sessions statt der ~5 heutigen Eval-Reste."
metadata: 
  node_type: memory
  type: feedback
  originSessionId: f793cc6f-f4e6-4f46-938c-aecf987209a5
  modified: 2026-07-22T17:29:00.046Z
---

**Regel:** Ein Skript, das Lösch-Kandidaten per Filter sammelt, darf im SELBEN Lauf nicht
löschen. Immer zweistufig: (1) Kandidaten listen + selbst prüfen (Anzahl! Titel! Zeitraum!),
(2) erst danach, in einem separaten Tool-Call, die geprüfte ID-Liste löschen.

**Why:** Beim Eval-Cleanup 2026-07-22 matchte der Filter (`project == KG-Real-Policies`)
ALLE 1243 Sessions des Projekts statt der ~5 heutigen 429-Fehlschlag-Reste — darunter ~17
Ad-hoc-Testfragen unklarer Herkunft. Session-Delete ist ein HARD delete (messages,
pseudonym_maps, pii_decisions kaskadiert); chats.db-Backups waren >1 Monat alt. Der
Kandidaten-Print stand ÜBER der Löschschleife im selben Skript — die Ausgabe kam erst,
als alles schon weg war.

**How to apply:** Zeitfenster/IDs explizit einschränken (created_at-Fenster, exakte
Session-IDs aus den Ergebnisdateien), nie nur Projekt-/Namensfilter. Bei irreversiblen
Aktionen mit Anzahl > erwartete Handvoll: abbrechen und nachfragen. Verwandt:
[[feedback_cleanup_test_sessions]] (Test-Sessions sofort löschen — aber nur die EIGENEN
des aktuellen Laufs).
