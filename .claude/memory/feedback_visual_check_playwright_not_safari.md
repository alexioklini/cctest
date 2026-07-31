---
name: feedback_visual_check_playwright_not_safari
description: Visuelle UI-Checks der Brain-Web-UI mit Playwright (web/js) machen — Safari-MCP-Screenshots liefern leere Bilder
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 5054f74b-4d4d-4705-a7b6-dfa5d8217f4a
  modified: 2026-07-23T09:03:35.498Z
---

Der Safari-MCP-Screenshotter (`mcp__safari-mcp-stp__screenshot`) liefert für die
Brain-Web-UI (127.0.0.1:8420) leere/weiße PNGs, obwohl das DOM sichtbare,
korrekt dimensionierte SVGs meldet (auch nach `switch_tab`; vermutlich
Snapshot-API + foreignObject/unfokussiertes Fenster). Beobachtet 23.07.2026
bei der Mermaid-Verifikation (v9.401.0).

**Why:** Kostet sonst mehrere vergebliche Screenshot-Runden; DOM-Checks allein
beweisen die Optik nicht.

**How to apply:** Für visuelle Checks ein Node-Skript mit Playwright fahren —
`node_modules` liegt in `web/js` (via `NODE_PATH=web/js/node_modules` für
Skripte außerhalb). Muster: Login admin/admin → per `page.evaluate` Container
injizieren → echte App-Funktion (z. B. `renderMermaidBlocks`) aufrufen →
`page.screenshot`. Testet die realen Browser-Globals ohne LLM-Turn und ohne
Test-Session (nichts zu löschen, vgl. [[feedback_cleanup_test_sessions]]).
