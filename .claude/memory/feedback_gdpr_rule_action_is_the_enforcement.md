---
name: feedback_gdpr_rule_action_is_the_enforcement
description: GDPR-Enforcement läuft AUSSCHLIESSLICH über Regel/Kategorie-Aktionen (warn/block) — keine zusätzlichen Enforcement-Ebenen (default_preset etc.) bauen
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 148121fa-c7e1-4a21-963c-10d48b755d01
---

**Die Regel-Aktions-Matrix des PII-Scanners IST das zentrale Enforcement — keine zweite Ebene daneben bauen.** Zwei User-Entscheidungen in einer Session: (1) ein zentrales `gdpr_scanner.default_preset` ABGELEHNT bevor gebaut; (2) die GDPR-Projekt-Presets (`kyc`/`kyc_local`/`screening`, v9.341–9.347) KOMPLETT ENTFERNT in v9.348.0 („presets raus - wird keiner verwenden oder wird umgangen werden").

**Why:** Das bestehende Modell ist vollständig und verständlich: die REGEL sagt zentral, wann sie greift (Admin, global, gilt in Projekten UND projektlosen Chats); die AKTION definiert den Handlungsspielraum des Users — `warn` → ignorieren/weiter erlaubt; `block` → nur anonymisieren / lokales Modell / abbrechen, KEIN Klartext-Send. Schutz, der pro Projekt aktiviert werden muss, wird nicht aktiviert oder umgangen; eine zweite Config-Ebene über derselben Frage „versteht kein User mehr", und zwei Stellen können sich widersprechen. Auch Firmen-Schutz (Org-Entitäten + Auto-Release) hängt an der globalen `organisation`-REGEL, nicht an einem Modus — „entweder die Erkennung funktioniert und die Ersetzung gut und nachvollziehbar, dann ist es ein globales Setting, das immer greift".

**How to apply:** Bei GDPR-/PII-Feature-Ideen zuerst fragen: lässt sich das über Regel-/Kategorie-Aktion + Konfidenz-Bänder ausdrücken? Wenn ja → dort, KEINE neue Config-Ebene, KEIN Preset, KEIN per-Scope-Overlay. Der Session-Sticky-Flow (einmal Consent im Modal → Session bleibt anonymisierend) ist der einzige legitime „stehende Consent". Schutz, der Arbeit blockiert, wird umgangen — die Antwort darauf ist Analyse-Parität (Schutz ohne Qualitätsverlust), nicht mehr Zwang. INVARIANTS.md § „GDPR project presets REMOVED" hält das fest.
