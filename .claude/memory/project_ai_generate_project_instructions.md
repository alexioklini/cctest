---
name: project_ai_generate_project_instructions
description: "Feature — AI-generate project instructions in project view from a prompt + reference files + ingested sources; agentic, fills editor for review"
metadata: 
  node_type: memory
  type: project
  originSessionId: 14c651ef-1328-47cc-a63e-94d7e8a40d7f
---

Building (started 2026-06-22): an "AI generate" mode for project **instructions** in the project view. User gives a short intent prompt; the model writes the full markdown project-instruction document, reading the project's own sources.

**Origin / gold standard**: project `risikoanalysen` instructions (38KB, hand-made) + chat `92ddc378` show the manual workflow it replaces — user opened a chat, attached 3 reference docs (NRA 2025, AML-Risikoanalyse PDFs, WPB reference .docx), wrote a ~1400-char prompt ("Du bist Mitarbeiter Compliance… erstelle umfassende Markdown-Projektanweisung… Output im Format der Referenz-docx… user liefert Zusatzdokumente + Webrecherche nötig"), agent read docs via read_document, produced the doc, user pasted into project view.

**Gold-doc structure DNA** the meta-prompt must reproduce: 1 Ziel/Umfang · 2 Referenzdokumente+regulatorische Grundlagen · 3 Methodik (Schritt-für-Schritt) · 4 Datenquellen (User-Docs+extern+Webrecherche-Strategie) · 5 EXAKTE Output-Struktur/Template des Deliverables · 6 Agent-Workflow · 7 Rollen · QA · Glossar. The meta-prompt supplies FORM; user supplies INTENT.

**Decisions** (user, AskUserQuestion 2026-06-22): (1) AGENTIC — real turn with read_document + mempalace_query enabled, max_rounds>1, so it reads instruction-files + queries ingested wing/web-urls. (2) Fill the editor textarea for review, user clicks Speichern — NON-destructive. (3) DEDICATED purpose `instruction_gen` with admin-configurable tool set in Tools-Matrix (NOT interactive). (4) DEDICATED Service-Modelle slot `instruction_gen_model` (NOT reuse default/studio); set to CLIProxyAPI/mistral-medium-3.5 for tests. (5) Transparent agentic loop in dialog (live tool-call steps) + abort + progress/error display.

**SHIPPED v9.189.0 (2026-06-22, live-tested on risikoanalysen)**: purpose `instruction_gen` in _VALID_PURPOSES; _INSTRUCTION_GEN_TOOLS default set (read_document/read_file/list_directory/search_files + mempalace_query + 3 KG tools + web_fetch/exa_search/searxng_search); backfill_purpose_column() idempotent column-add for already-seeded installs (called at boot in server.py — VERIFIED 70/76 tools got column, 10 active). engine/instruction_gen.py = agentic worker (background_call purpose=instruction_gen max_rounds=12, dedicated system meta-prompt _META_SYSTEM_PROMPT teaching how to write project instructions, sources preamble naming disk paths+folders+web-urls+wing peek), in-memory _PROGRESS registry + note_tool_call hook in server_lib/tool_mcp.py (translates tool calls → German steps), request_cancel via stored turn_id→sidecar cancel_turn. DB table project_instruction_gen (db.py CRUD + crash-reconcile). 3 routes + 3 handlers (handlers/projects.py). Service-Modelle slot instruction_gen_model (admin_observability.py registry+read+save). Frontend: api.js 3 methods, panels_projects.js KI-box+Generieren/Abbrechen+live progress (polls 1.2s, fills #project-instructions-textarea on ready), settings_general_tabs.js matrix label 'Projektanweisung'. js_gate net-globals 1461→1465.

⚠️ FOOTGUN HIT: German curly-quote „…" inside a "…" string broke py_compile ([[feedback_compile_check_brain_py]]) — fixed to single quotes. CHANGELOG prose uses single quotes only.

⚠️ READ-LOOP BUG (found live): with reference files given only as DISK PATHS, mistral-medium looped re-reading the same WPB .docx/.fulltext.md for 7+ min and never wrote (read_document NOT dedup-exempt but slightly different args evaded it). FIX: INLINE the instruction-file content server-side via brain.extract_attachment_text (shared doc pipeline), capped _INSTR_FILE_INLINE_CAP=40000/file, in the preamble; meta-prompt updated ('content already inlined, don't re-read, after few steps WRITE'). Tools stay agentic for ingested-folder/web sources. After fix: 0 tool calls on risikoanalysen (no ingested folders), straight to writing. Cancel path VERIFIED (reached 'cancelled'). Live test on mistral-medium-3.5 still SLOW (large 38KB-style doc) — acceptable, has progress+abort.

**Wiring sites** (from Explore, all verified):
- ProjectManager.update_project whitelist `instructions` already exists (brain.py ~4907). instruction_files dir = `ProjectManager._instruction_files_dir(agent,name)` (brain.py 4672); files on disk under `<project>/instruction-files/` w/ .md companions (read_document instant-resolves).
- Project sources: instruction_files (cfg list), input_folders (cfg), web_urls (cfg) — all in get_project cfg. Ingested content lives in MemPalace wing `project__<id>` via mempalace_query (auto-scoped when project active, engine/mempalace_glue.py).
- background_call (handlers/sidecar_proxy.py ~884): purpose must be in _VALID_PURPOSES = interactive/transform/memory_summary/research_minimal/helpdesk; cost_purpose is SEPARATE free tag. Provider via self._resolve_provider. Agentic needs tools enabled + a tool_context scoped to project (so mempalace_query force-scopes to project__<id>).
- Routes: server.py — note `_handle_project_generate` ALREADY EXISTS (Studio outputs, POST .../generate) — DO NOT collide; use a distinct path like `.../generate-instructions`. project-delete fallback is LAST in DELETE chain; order new POST route before generic.
- Frontend: web/js/panels_projects.js — instructions editor `#project-instructions-textarea`, saveProjectInstructions() ~1965 calls API.updateProject(agent,name,{instructions}). Web URLs section ~1787. api.js project methods ~258. js_gate before editing JS (cd web/js && ./js_gate.sh); new fns called from inline onclick must be globals → net-globals baseline bumps.

Related: [[feedback_commit_to_main]] [[feedback_compile_check_brain_py]] [[project_inline_citations]] (background_call purpose gotcha).
