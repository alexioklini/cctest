---
name: project_brainy_model_advisor
description: v9.314.0 — Brainy-Tool helpdesk_config (Modell-Beratung aus Live-Config); mistral-small verweigerte Tool-Nutzung → deepseek-v4-flash; Helpdesk-Verlauf kontaminiert Modellvergleiche
metadata: 
  node_type: memory
  type: project
  originSessionId: c9eb024c-901b-4625-93fd-bddec25162df
---

v9.314.0 (2026-07-13): Brainy beantwortet Modell/Kosten/Speed/lokal-Fragen via neuem Read-only-Tool `helpdesk_config({section})` (engine/tools/helpdesk_tools.py; Sections: models/coding_plans/quotas/providers/cost_rates/service_models).

**Design-Iteration mit dem User (wichtig für künftige Vorschläge):**
1. Mein Vorschlag "gezieltes Berater-Tool mit vorverdauter Antwort" → User wollte GENERELLEN Settings-Zugriff ("brainy soll sich das selbst zusammensuchen").
2. Erste Umsetzung las config.json direkt → User-Einwand: "kein blindes lesen der config.json — wir haben doch schon api". KORREKT: das Tool geht jetzt durch DIESELBEN Seams wie die HTTP-Endpunkte (`resolve_model_plan_id`, `model_is_flat_plan`, `get_user_allowed_models`-Scoping, `_server_config()`-Live-Mirror, `get_coding_plans`, `get_config_cost_rates`). Direkt-Datei-Lesen hätte Rechte-Scoping umgangen + zweite driftende Wahrheit geschaffen. Muster: Fakten-JSON liefern, Modell analysiert — aber benchmark-Blob GEFLATTET ({task:{capability,tps}}, override>measured) sonst sprengen 140 Modelle den Kontext.

**Modell-Tool-Treue (E2E über POST /v1/helpdesk gemessen):**
- `mistral-small`: ruft das Tool trotz expliziter Prompt-Anweisung (Schritt 2a) NIE auf — rät aus dem Quellcode-Wing und halluzinierte ABRECHNUNGSKONTEN als Modelle ("kimi-moderato" als Coding-Modell, "glm-lite" als lokale Alternative). Deckt [[feedback_mistral_small_stochastic_quality]].
- glm-5.2, deepseek-v4-pro, deepseek-v4-flash: nutzen das Tool zuverlässig, korrekte Antworten. **Brainy jetzt auf deepseek-v4-flash** (<1 Cent/Frage vom Kilo-Guthaben; schont Z.ai-Kontingent).

**TEST-FALLE:** Der Helpdesk-VERLAUF persistiert (helpdesk_history, session-übergreifend bei leerer session_id) — Modellvergleiche OHNE `POST /v1/helpdesk/clear` davor messen den kontaminierten Verlauf des Vorgängermodells (flash sah erst fälschlich "ruft nichts auf", nach clear: 3 Tool-Calls). Erst clear, dann testen.

**SSE-Format** von /v1/helpdesk: `event: <name>` + `data: {json}` in getrennten Zeilen — der Event-Name steht NICHT als type-Feld im JSON.

**Beifang:** die 6 aktivierten Kilo-GPT-5.6 (`openai/gpt-5.6-*`) hatten keinen coding_plan-Link → Verbrauch lief am Kilo-Guthaben vorbei; alle auf kilo-credit verlinkt (Kilo trackt jetzt 10 Modelle). `kimi-k2.6` (ID) heißt im display_name "kimi-k2.7" — kein Modellfehler, Config-Realität.

**Folge-Fix 9.314.1 (fail-closed):** Der rote test_helpdesk_tools deckte einen ECHTEN Fail-open auf — eine fehlende Matrix-Zelle machte ein Tool default-AKTIV in jedem Kanal (auch write/exec im Read-only-Helpdesk; erreichbar: ungeseedete Installation, nach-Boot registriertes Tool). Fix in `_global_tool_state_for`: ohne explizite states[purpose]-Zelle entscheidet die Code-Basismenge (`_purpose_base_members`, von Seed UND Fallback geteilt); Nicht-Mitglied ⇒ inactive. interactive bleibt unrestricted (KV-Prefix byte-identisch); mit geseedeter Matrix ändert sich nichts (7 Purposes verifiziert). 13/13 grün. Resolver-Wahrheit ist seit 9.101.1 die Matrix; `_HELPDESK_TOOLS` ist Seed + jetzt auch Fallback-Basismenge.

**Suite seit 9.314.2 komplett grün (378/378)** — die 3 letzten Failures waren veraltete Tests: memory_recall existiert nicht mehr (Wiki ersetzte memory_*), organisation liegt unter business_id (nicht contact), Pseudonymisierer erzeugt realistische Surrogate (nicht NAME_-Token). Dabei echter Bug gefixt: scan_text stempelte ORG-Findings hart mit category='contact'. LEHREN: (a) PII-Tests müssen `rule_overrides={}` pinnen — sonst entscheidet die Live-config.json des Entwicklers über den Testausgang; (b) neue Suite-Failures ab jetzt nie als "vorbestehend" abtun.
