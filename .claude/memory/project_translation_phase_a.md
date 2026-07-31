---
name: Translation Sektion — Phase A geshipped
description: Bank-Übersetzungs-Feature (DeepL-Replacement) — Phase A (Text + Glossare) live; Phase B (docx/pptx/pdf) und Phase C (Audio/Video + Live-Mic) ausstehend
type: project
originSessionId: 0d578374-c1db-4ac7-a1f0-da04c186f64d
---
2026-05-05: Translation-Sektion in Brain gestartet als DeepL-Alternative für Bank-Workflows. Voxtral schon vorher integriert für Audio (Memory: feedback_voxtral_audio).

**Phase A SHIPPED (heute):**
- `server_lib/translate/` Modul: detect.py (lingua + Mistral fallback), glossary.py (JSON CRUD agents/main/glossaries/), text.py (translate_text via _run_delegate)
- 4 neue Tools in TOOL_DEFINITIONS: `translate_text`, `detect_language`, `list_glossaries`, `get_glossary` + tool_groups["translation"] in DEFAULT_TOOL_GROUPS
- `transcribe_audio` Tool um optionalen `translate_to`+`glossary` Param erweitert (chained transcribe→translate)
- `handlers/translate.py` Mixin mit /v1/translate/{detect,text,glossaries[/<slug>]}
- UI: Sidebar-Eintrag, 4-Tab-View (Text aktiv, Document/Audio/Live als disabled-Stubs), Toolbar mit Auto-Detect-Pill + Target-Pill + Glossary + Model Picker, Split-Pane, Glossar-CRUD-Modal
- `tools_config.json` → translation block: default_model="mistral-vibe/mistral-medium-3.5", detection_fallback_model="mistral-vibe-cli-fast"
- Smoke-Test in Chrome bestanden: DE→EN Banking-Text in 0.7s mit korrekter Terminologie (Eigenkapitalquote→equity ratio, BaFin verbatim, Aufsichtsrat→supervisory board, Vorstand entlasten→discharge management board)

**Why:** User will dass alle Funktionen via UI + Chat + Workflow + Scheduled Task aufrufbar sind. Tools-first Architektur erfüllt das automatisch — HTTP-Endpoints sind dünne Wrapper.

**How to apply:**
- Phase B (next session): docx/pptx/pdf Pipelines + `translate_document` Tool + In-Memory Job-Table mit SSE; Document-Tab aktivieren. XML-in-place-Edit für docx (`<w:t>`) + pptx (`<a:t>`) für 1:1 Layout-Treue. PDF best-effort über markitdown→docx-Output.
- Phase C: Audio/Video-Tab als UI-Wrapper über bestehende `transcribe_audio` (mit translate_to). Live-Mic via WebSocket + MediaRecorder-Chunks → server-seitig Voxtral oder mlx-whisper.
- KEY DESIGN: `server_lib/translate/` ist single source of truth — Tools ↔ HTTP ↔ UI rufen alle dieselben Funktionen auf.
- KV-cache safe: Translation-Calls gehen über `_run_delegate(tools=False)`, kein Impact auf Chat-Warmpool.
- Lingua dependency: pip install --break-system-packages lingua-language-detector (172MB für ML-Models, läuft offline)
- Glossar-Format: `agents/main/glossaries/<slug>.json` — `{name, description, source, target, entries:[{src,tgt}], do_not_translate:[]}` — exact-match Lookup, NICHT MemPalace.

**Bekannte Constraints:**
- `tool_ask_llm` und `tool_translate_text` reusen `_run_delegate` — der returnt error-strings statt zu raisen; muss in text.py auf `Delegation error:`-Prefix prüfen und re-raisen.
- `escapeHtml` wird mehrfach in workflows.js / favourites.js definiert — neue JS-Files dürfen es NICHT nochmal definieren, sonst SyntaxError.
- `_TOOLS_CONFIG_DEFAULTS` in brain.py:5681 muss neue Tool-Configs registrieren, sonst filtert get_tool_config() sie raus.
