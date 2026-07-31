---
name: project_skill_gen_from_chat
description: "Feature \"Skill aus Chat erstellen\" — SKILL.md aus Chat/MoA-Plan generieren, per-user + chat-artiges Sharing; klont workflow_gen"
metadata: 
  node_type: memory
  type: project
  originSessionId: 88c3ed7d-6161-42f0-b71e-028a910d555d
---

**v9.294.0 (fertig, live E2E verifiziert 2026-07-07):** **Skill aus Chat/MoA-Plan generieren** — Analog zum Workflow-Generator, aber Deliverable ist eine `SKILL.md`. Per-user Skills, geteilt wie Chats (private/users/team/global).

**Architektur = 1:1-Klon von `engine/workflow_gen.py`:** neue `skill_gen`-Tabelle (Klon `workflow_gen`, `server_lib/db.py`), Daemon-Thread, EIN forced-tool `background_call(purpose="transform", cost_purpose="skill_gen", max_rounds=1, forced_tool=submit_skill)`. Quell-Extraktion `workflow_gen._chat_source_material` WIEDERVERWENDET (bringt MoA-Plan `ausfuehrungsplan.md` + Executor gratis). Keine `.flow`-DSL, keine AST-Validierung — Body ist freies Markdown; Validierung nur slug-regex + Felder nicht-leer + nicht wörtliche Transkript-Kopie.

**Speicher:** `agents/<agent>/user_skills/<slug>/SKILL.md` + `skill.meta.json` (identischer 6-Key-Block wie Workflows: owner_user_id/visibility/owner_team_id/extra_member_user_ids/excluded_user_ids/created_at). Sharing gratis via `"skill"`-Eintrag in `handlers/share.py` `_LOADERS/_SAVERS/_TRANSFERRERS` + generischem `can_access` ([[project_workflow_run_artifacts_and_binary_fix]] Sharing-Muster).

**KV-INVARIANTE (kritisch, selbst entdeckt):** die Skills-Registry steht im GECACHTEN System-Prompt (`engine/prompt_build.py:609`, `list_skills()`). Base-`list_skills()` MUSS user-agnostisch bleiben (own+main), sonst bricht der Warm-Pool-KV-Prefix pro Nutzer. Siehe [[feedback_kv_cache_stability]].

**DISCOVERY-TOOL statt Preamble (User-Feedback 'Skills wie Tools behandeln'):** per-user Skills werden NICHT im Prompt gelistet, sondern über das Tool **`find_skills(task)`** entdeckt (engine/tools/misc_tools.py, 4-Site-Wiring, skills-Gruppe). → [{slug,name,description,score,matched_via}] → dann use_skill. Tool-DEFINITION ist statisch = cache-sicher; per-user Treffer reisen im Tool-ERGEBNIS (nie im gecachten Prompt) → skaliert + kein Preamble-Hack. Eingebaute Skills bleiben in der Prompt-Registry (klein, universell). Statische Prompt-Zeile 'PERSONAL SKILLS … call find_skills' weist das Modell hin. Der frühere `_user_skills_preamble_text` + Wiring wurde ENTFERNT.

**SKILL⇄WORKFLOW-VERZAHNUNG (v9.294.2):** (1) Cross-Wing-Suche: _search_skills_semantic(task, visible, limit) nimmt {owner:{slugs}}-Karte, sucht über alle Owner-Wings sichtbarer Skills (wing $in), Treffer nur bei sichtbarem (owner,slug) → kein Leak; schliesst die 9.294.1-Lücke (nur eigene). (2) `agent_step skill="<slug>"` (engine/tools/ask_tools.py + Schema): lädt Skill-Body als Plan des Schritts, resolved Workflow-OWNER via ctx.current_user_id. (3) Ausweisprüfung-Workflow nutzt jetzt agent_step skill= statt plan_md; .plan.md gelöscht. (4) Workflow-Gen: start_generation nimmt skill_ref (referenzieren) + extract_skill (_extract_and_save_skill generiert+speichert Skill blockierend, dann referenzieren); System-Prompt-Direktive bei skill_ref (plan_md leer). GET /v1/skills/match für Modal-Auswahl. WICHTIG: user_skills/ ist GITIGNORED (per-user Runtime wie user_profiles/; Vektor in Qdrant) — die Ausweisprüfung-.flow referenziert einen Skill, der pro Umgebung existieren muss.

**SEMANTISCHE SUCHE (v9.294.1):** find_skills mergt zwei Signale — SEMANTIK (MemPalace-Vektorsuche über die EIGENEN Skills, eingebettet in `user__<uid>`/room=skills, source_file='skill/<uid>/<slug>', embeddinggemma-MLX) fängt Paraphrase/Cross-Language ('verify a passport' → deutscher 'Ausweisprüfung'-Skill, live 0.83); KEYWORD über die VOLLE sichtbare Menge (geteilte Skills liegen im Owner-Wing, nicht im eigenen). 3 Helfer in engine/mempalace_glue.py: _embed_and_store_skill (delete-then-add, drawer_id ist content-addressiert), _search_skills_semantic, _delete_skill_vector (scan-then-delete-by-ids), alle unter _palace_write_lock, alle best-effort (Fallback auf Keyword wenn Qdrant/Embedder weg). save_user_skill embeddet, delete_user_skill räumt den Vektor. WICHTIG: NICHT über tool_mempalace_query (dessen _wing_visible-Gate), sondern direktes col.query. Siehe [[project_qdrant_live_int8]].

Endpoints (neu, `handlers/admin_skills_gen.py`, spiegelt `admin_workflows.py`): POST `/v1/skills/generate`, GET `/v1/skills/generate/<id>`, POST `.../cancel`, POST `/v1/skills/save`. Service-Modell-Slot `skill_gen_model` (+ `_server_config()`-Boot-Kopie nicht vergessen). Frontend `web/js/skills_gen.js` klont Generator-Block aus `workflows.js`; Composer-🎓-Button neben `btn-workflow`.

Design-Dok: scratchpad `skill-gen-design.md`.
