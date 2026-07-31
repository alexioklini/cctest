---
name: project_next_prompt_dead_pin_precedence
description: Next-Prompt-Vorschlag blieb leer — toter Agent-Pin auf ein nach M4-Split disabled Modell; GUI-Label log irreführend; model_used-Feld falsch
metadata: 
  node_type: memory
  type: project
  originSessionId: d029b009-0fdb-4d15-8bbf-65b608fe93cd
  modified: 2026-07-18T13:38:13.852Z
---

Commit e8a5854c/caebcf88 (2026-07-18). Prompt-Vorschlag (Ghost-Text) erschien in einem Chat nicht.

**Ursache (kein Logik-Bug):** `agents/main/agent.json → next_prompt_suggestions.model` war auf `Lokal-M4/Qwen2.5-7B-Instruct-4bit` gepinnt. Dieses Modell ist seit der [[project_m2_m4_split_simulation]] `enabled:False` (vllm-metal vom M4 entfernt) → LLM-Aufruf liefert konstant `raw=''` → kein Vorschlag. Debug-Zeile `[next_prompt] model=... raw=...` (brain.py:8238) schreibt den rohen Output ins server.error.log — DAS ist der schnellste Diagnoseweg.

**Modell-Präzedenz Next-Prompt (brain.py:8155-8160):** Agent-Override (`agent.json next_prompt_suggestions.model`) → globaler Service-Knopf (`config.json next_prompt_model`, nur wenn `_is_model_available`) → `session.model`. Ein LEERER Override bedeutet also NICHT Sitzungsmodell, sondern das global konfigurierte `next_prompt_model` (aktuell `mistral-small-latest`); Sitzungsmodell ist nur der letzte Fallback.

**Zwei GUI-Fehler mitgefixt (settings_agent.js):**
- Ein gepinnter Override, der nicht in `enabledModels` steht, fiel im `<select>` STILL auf die erste Option zurück und zeigte "Sitzungsmodell" — verbarg damit den toten Pin. Jetzt sichtbar als "⚠ <modell> — nicht verfügbar (deaktiviert/entfernt)".
- Die Leer-Option war fälschlich "(Sitzungsmodell …)" beschriftet → korrigiert zu "(Standard — global konfiguriertes Modell, sonst Sitzungsmodell)".
- Response-Feld `model_used` (sessions_handler.py) zeigte `cfg.model or session.model` statt des real genutzten Modells (nach Präzedenz + GDPR-Swap). `generate_next_prompt_suggestion` setzt jetzt `session._next_prompt_model_used`; Handler liest es im Live- UND Cache-Pfad.

**Muster (allgemein):** nach jedem M2/M4-Split/Host-Umzug können Agent-Overrides + Service-Modell-Knöpfe auf jetzt-disabled lokale Modelle zeigen. Ein toter Pin äußert sich als still leere Ausgabe, nicht als Fehler. Prüfroute: real genutztes Modell im server.error.log gegen `enabled`-Status in config.json halten. Verwandt: [[feedback_composer_controls_are_source_of_truth]], [[project_glm_kimi_direct_providers]].
