---
name: project_pii_default_on_production
description: "AB WOCHE 2026-07-27 (Produktion, Bank-Deployment): PII-Anonymisierung ist DEFAULT AN. Retrieval-Dialog + Konsolidierung (v9.398/9.399) sind damit Produktions-Pfad für jeden Projekt-Chat, nicht Nischen-Feature."
metadata: 
  node_type: memory
  type: project
  originSessionId: f793cc6f-f4e6-4f46-938c-aecf987209a5
  modified: 2026-07-22T19:21:57.853Z
---

**Nutzer-Ansage 2026-07-22:** „ab nächster Woche in Produktion ist PII default an" —
d. h. ab der Woche vom **2026-07-27** läuft das Produktions-Deployment (Bank, vgl.
[[project_windows_deployment_package]]) mit standardmäßig aktiver PII-Anonymisierung.

**Konsequenzen:**
- Der Mid-Turn-Retrieval-PII-Dialog (v9.398.0, [[project_project_retrieval_pii_gap]]) trifft
  JEDEN Projekt-Chat — die Dialog-Konsolidierung (Turn-Vererbung + Session-Standing-Order,
  v9.399.0) ist Produktions-Pfad, nicht Komfort.
- Scheduler-/Background-Turns auf Projekten laufen ohne Standing-Order in den fail-closed
  Refusal (`retrieval_pii_withheld`) — für produktive geplante Tasks muss die Session-Standing-
  Order gesetzt sein (oder ein künftiger Admin-Default, s. u.).
- Möglicher Folgeschritt (NICHT beauftragt): admin-config Default `retrieval_auto_anon` für
  neue Sessions (Config-Knopf), damit Produktions-Nutzer den ersten Dialog gar nicht sehen;
  und/oder Projekt-Vorentscheidung (Option 3 aus der Konsolidierungs-Diskussion).
- Änderungen an GDPR-Seams ab jetzt mit Produktions-Brille reviewen (Fail-Modi, Perf des
  spaCy-Scans pro Retrieval-Turn, Dialog-Verständlichkeit für Endnutzer).
