---
name: feedback_announce_each_step
description: "Vor jedem Edit/Schritt kurz ankündigen, was als Nächstes passiert — auch mitten in langen Umbau-Serien"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 0269882d-cc95-41dd-b1e0-3bcb5d5c11fc
  modified: 2026-07-25T17:56:37.303Z
---

Bei mehrschrittigen Umbauten vor JEDEM Tool-Schritt einen kurzen Satz schreiben, was jetzt passiert und WARUM ("Jetzt entferne ich X aus Y, weil …"). Nicht nur am Anfang der Serie. Designentscheidungen (wo eine Einstellung im GUI liegt, was entfernt/behalten wird, welches bestehende Feature berührt wird) VOR der Umsetzung explizit als Entscheidung benennen — nicht stillschweigend treffen.

**Why:** Alexander verfolgt die Arbeit live mit und hat zweimal nachgefragt (25.07.2026): einmal, als die Ankündigungen mitten in einer Serie aufhörten, und einmal, warum das Warum fehlte. Konkrete Folge: Engine-Einstellung landete erst im Werkzeuge-Tab statt in Service-Modelle, und bestehende STT-Dropdowns im Übersetzen-Bereich blieben unhinterfragt — beides Korrekturschleifen, die ein Satz Begründung im Voraus vermieden hätte ([[project_llm_router_service]]-Repo, GUI-Konvention: Modelle zentral in Service-Modellen).

**How to apply:** Ein Satz pro Schritt (Was + Warum). Bei GUI-Platzierungen und beim Anfassen bestehender Features: erst die Annahme nennen ("Ich lege X nach Y, weil dort Z liegt — passt das?" bzw. kurz nennen und weiterarbeiten, wenn reversibel), dann bauen. Bei Kurswechseln zuerst den neuen Plan in 2-3 Punkten, dann Schritt für Schritt mit Ankündigung.
