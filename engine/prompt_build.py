"""engine/prompt_build.py — system-prompt assembly + first-turn preambles.

Extracted from brain.py (refactor C1). Owns:
  - `_build_system_prompt` — THE system instruction builder for every LLM call
    (chat / project chat / scheduled task / memory miner / research_minimal).
    The 60s per-session cache (`_system_prompt_cache` + `_SYSTEM_PROMPT_CACHE_TTL`)
    moves WITH it — a cache lives with its filler.
  - `_apply_system_prompt_postprocess` — caveman + plan-mode + GDPR-clamp string
    transform applied AFTER the cache lookup (kept out of the cache key so a
    session that flips caveman/plan mid-stream reuses the cached base prose).
  - `_GDPR_ANON_CLAMP` — the per-turn pseudonymisation clamp constant (a
    prompt-postprocess constant, so it moves here).
  - the four first-turn preamble builders (`_project_preamble_text`,
    `_workflow_run_preamble_text`, `_artifact_folder_preamble_text`,
    `_files_in_chat_preamble_text`). These pair with prompt assembly — only
    `_artifact_folder_preamble_text` has a live caller (handlers/chat.py via the
    `engine.` alias), the other three are dead in the sidecar architecture but
    move together as the preamble cluster. All reach brain runtime via `_brain`
    and resolve unchanged through brain.py's re-export.

KV-PREFIX INVARIANT (CLAUDE.md #3 / the C1 warmup gate): the system prompt +
active-tool set built here must stay BYTE-IDENTICAL across the warm-pool prime
and the first live turn. `build_first_turn_prefix` (stays in brain.py) is the
single construction path both use, and it calls `_build_system_prompt` via the
brain re-export. Do not let an import reorder / whitespace edit here change a
single byte — `tools/check_warmup_prefix_stable.py --check` is the gate.

Seams:
  - `_thread_local` comes from engine.context (low-level base, no cycle).
  - every other brain-runtime symbol is reached lazily via the `_LazyBrain`
    proxy (`_brain.<name>`) — one-way DAG, brain imports this module. Proxied
    symbols: `_VALID_PURPOSES`, `_current_agent`, `_mcp_manager`, `_scheduler`,
    `TOOL_GROUPS`, `AGENTS_DIR`, `build_agent_registry`, `_get_token_config`,
    `_get_agent_team_info`, `_render_tool_descriptions`, `_minimal_tool_blurbs`,
    `render_research_mode_disciplines`, `_load_mempalace_config`,
    `ProjectManager`, `_workflow_history_get`, `_get_artifact_session_folder`,
    `CAVEMAN_SYSTEM_PROMPTS`, `CAVEMAN_CHAT_PROMPTS`, `PLAN_MODE_PROMPT`,
    `_caveman_compress_text`.
"""

from __future__ import annotations

import json
import os

from engine.context import get_request_context


class _LazyBrain:
    """Lazy proxy to the live `brain` module (avoids the import cycle —
    brain imports this module). Every brain-runtime symbol this module
    touches is reached through this proxy as `_brain.<name>`."""
    __slots__ = ()

    def __getattr__(self, name):
        import brain as _b
        return getattr(_b, name)


_brain = _LazyBrain()


_system_prompt_cache: dict[str, tuple[str, float]] = {}  # session_id → (prompt, timestamp)
_SYSTEM_PROMPT_CACHE_TTL = 60  # seconds — cache for 1 min (covers tool loop iterations)


def _build_system_prompt(include_memory_summary: bool = True,
                         purpose: str = "interactive",
                         task_name: str = "",
                         task_working_dir: str = "",
                         active_tool_names: set[str] | None = None) -> str:
    """Build the full system instruction for the current agent.

    See PROMPT_TOOLS_UNIFICATION_PLAN.md for the four-purpose model.

    purpose:
      - 'interactive' (default) — chat, project chat, scheduled tasks. Soul +
        full agent context. When `task_name` is set the proactive framing is
        replaced by a NON-INTERACTIVE directive (no clarifying questions,
        write_file is a tool call not a description).
      - 'transform' — caller supplies its own prompt; this function refuses.
      - 'memory_summary' — memory miner. No soul, terse identity,
        memory-schema rules.
      - 'research_minimal' — harness-style lean prompt composed from
        `minimal_role` fragments on minimal-flagged tools. Default for
        scheduled tasks (override via schedules.tool_profile='interactive').

    active_tool_names: when provided, per-tool prompt prose configured in
        admin → Tools settings is appended via `_render_tool_descriptions`,
        gated on tool presence + per-tool `applies_with` all-of rules. When
        None, no per-tool prose section is emitted (callers must pass the
        resolved active set to opt in).

    Caches per (session, purpose, include_memory_summary, has_active_tool_names,
    has_task_name) to avoid disk I/O on every tool loop iteration.
    """
    if purpose not in _brain._VALID_PURPOSES:
        raise ValueError(f"_build_system_prompt: unknown purpose {purpose!r}")
    if purpose == "transform":
        raise ValueError(
            "_build_system_prompt(purpose='transform') is not callable — "
            "transform callers supply their own prompt directly.")

    import time as _time
    session_id = get_request_context().current_session_id or ""
    caveman_chat = get_request_context().caveman_chat or 0
    caveman_system = get_request_context().caveman_system or 0
    plan_mode = bool(get_request_context().plan_mode)
    gdpr_anon = bool(get_request_context()._gdpr_anonymising)
    # Cache key covers ONLY things that change the disk-read prose. Caveman
    # levels and plan mode are deterministic string post-processing applied
    # after the cache lookup — keeping them out of the key means flipping
    # caveman or plan mode mid-session reuses the cached base prose instead
    # of triggering a fresh read of soul.md / skills / scheduler / MCP / etc.
    # `_atn_key` records the *fingerprint* of active_tool_names so callers
    # with different tool surfaces don't share a cache entry (and so the
    # legacy None-callers keep their own cache slot).
    _atn_key = "*" if active_tool_names is None else ",".join(sorted(active_tool_names))
    # Project + research-mode-override switch the rendered prompt (project
    # context, mempalace_query.description's project-flow addendum,
    # research_mode_disciplines block). Without these in the cache key,
    # warmup (no project) and a follow-up project chat under the same
    # session_id collide and the second call returns the warmup prompt.
    _proj_key = get_request_context().project or ""
    _rmo_key = get_request_context().research_mode_override
    _rmo_key = "n" if _rmo_key is None else ("t" if _rmo_key else "f")
    cache_key = f"{session_id}:{include_memory_summary}:{purpose}:{_atn_key}:{_proj_key}:{_rmo_key}"
    cached = _system_prompt_cache.get(cache_key)
    if cached and (_time.time() - cached[1]) < _SYSTEM_PROMPT_CACHE_TTL:
        return _apply_system_prompt_postprocess(
            cached[0], caveman_system, caveman_chat, plan_mode, gdpr_anon)
    import platform
    from datetime import datetime as _dt

    os_name = platform.system()

    # Load agent soul (prefer thread-local for concurrent requests). Tools
    # guide is now rendered from per-tool admin settings via
    # `_render_tool_descriptions(active_tool_names)` — the legacy on-disk
    # tools.md path is gone.
    agent = get_request_context().current_agent or _brain._current_agent
    agent_id = agent.agent_id if agent else "main"
    soul = agent.soul if agent else ""

    # Build agent registry
    agent_registry = _brain.build_agent_registry(for_agent_id=agent_id)

    system_instruction = ""

    # --- research_minimal purpose: harness-style lean prompt, dynamically
    # composed from `minimal_role` fragments on the active tools'
    # TOOL_DEFINITIONS entries. Skips soul.md, project context, team, skills,
    # scheduler/MCP listings, DEFERRED block — those are the prompt bytes
    # gemma-4-e4b chokes on per Gate-PT-2 evidence.
    if purpose == "research_minimal":
        # Compose "Use `A` to do X, `B` to do Y, and `C` to do Z."
        # from (name, role) pairs in canonical order (alphabetical by name).
        # Collapses cleanly when zero or one tools are flagged minimal.
        _blurbs = _brain._minimal_tool_blurbs()
        def _piece(name: str, role: str) -> str:
            return f"`{name}` {role}"
        if not _blurbs:
            _tool_line = ""
        elif len(_blurbs) == 1:
            _tool_line = f"Use {_piece(*_blurbs[0])}."
        else:
            _csv = ", ".join(_piece(n, r) for n, r in _blurbs[:-1])
            _tool_line = f"Use {_csv}, and {_piece(*_blurbs[-1])}."
        system_instruction = (
            "You are an autonomous research assistant. "
            f"{_tool_line}\n\n"
            "## Workflow\n\n"
            "1. Break the user's request into 2-4 sub-topics.\n"
            "2. For each sub-topic, call `exa_search` with 2-5 focused keywords "
            "(do not paste the entire question).\n"
            "3. Pick the most promising 2-3 results per sub-topic and `web_fetch` them.\n"
            "4. Synthesize across all fetched pages into a single Markdown report.\n"
            "5. Save the report with `write_file` using the path the user specified "
            "(default `report.md`).\n"
            "6. After `write_file` returns successfully, reply with a one-paragraph "
            "confirmation including the absolute path written.\n\n"
            "## Quality\n\n"
            "- Cite sources inline as `[Title](URL)` next to each claim that came "
            "from a fetched page.\n"
            "- If a search returns nothing useful, try a different angle — do not "
            "invent content.\n"
            "- Do not exceed 6 `web_fetch` calls total; stop earlier if you have enough.\n"
            "- The Markdown report must contain proper headings (`#`, `##`), bullet "
            "lists, and inline links — not placeholder text.\n"
        )
        _system_prompt_cache[cache_key] = (system_instruction, _time.time())
        return _apply_system_prompt_postprocess(
            system_instruction, caveman_system, caveman_chat, plan_mode, gdpr_anon)

    # --- memory_summary purpose: terse, no soul, no team/skills/scheduler/MCP.
    if purpose == "memory_summary":
        system_instruction = (
            f"You are the memory miner for agent '{agent_id}'.\n"
            f"Current date: {_dt.now().strftime('%Y-%m-%d')}\n\n"
            "Your job: classify chat turns into memorable facts/preferences/"
            "decisions and file them via `save_chat_to_memory`. Use "
            "`mempalace_query` to check whether something is already on file "
            "before re-filing. Skip generic chitchat, greetings, and refusals "
            "— they are not memorable. Be terse: emit tool calls, not "
            "narration.\n\n"
        )
        # Per-tool prompt prose — usually empty for memory tools.
        if active_tool_names is not None:
            _rules = _brain._render_tool_descriptions(active_tool_names)
            if _rules:
                system_instruction += f"\n--- TOOL USAGE GUIDE ---\n{_rules}"
        _system_prompt_cache[cache_key] = (system_instruction, _time.time())
        return _apply_system_prompt_postprocess(
            system_instruction, caveman_system, caveman_chat, plan_mode, gdpr_anon)

    # --- interactive purpose (chat OR scheduled). Soul + full context.
    if soul:
        system_instruction += f"{soul}\n\n"
    # Round timestamp to the hour so the KV-prefix stays stable across
    # warmup → real-request boundaries. Minute-level precision broke prompt
    # cache reuse on every request (~15s extra first-token latency).
    # NOTE: this prompt must stay user-agnostic. Per-user greeting (see
    # `greeting_name` pref) is injected as a one-time preamble on the first
    # user message in send_message — keeping it OUT of the system prompt
    # preserves the warm-pool KV-prefix match across users.
    # Tool-use posture (proactive vs. refuse-on-empty), narrate-intent
    # rules, and the exa_search-vs-duckduckgo preference all moved out:
    #   - Per-agent posture / no-narration / OS-sandbox lines → soul.md
    #     (default agent identity; admin edits per agent).
    #   - exa_search preference → tool_settings.exa_search.description
    #     (auto-gates on tool presence).
    #   - Project refuse-on-empty → research_mode_disciplines.refusal
    #     (admin-editable Topic B section, gated on research_mode).
    # Brain.py only emits the agent's identity preamble and date/cwd/OS
    # facts here; everything posture-related lives in editable config.
    # The working-directory facts only matter when a file-writing/exec tool is
    # actually loaded this turn. Naming python_exec/execute_command/write_file
    # while they're deferred (e.g. a gated retrieval turn) is a false affordance
    # that invites a weak model to call a tool it doesn't have. So gate the
    # tool-naming detail on tool presence; keep the load-bearing relative-path
    # rule unconditional. `active_tool_names` is in the cache key (_atn_key) and
    # warm/local models are never tool-gated, so the warm-pool prefix is
    # unaffected (check_warmup_prefix_stable.py --check still gates this).
    _exec_tools = {"python_exec", "execute_command", "write_file"}
    _has_exec = active_tool_names is None or bool(_exec_tools & set(active_tool_names))
    if _has_exec:
        _cwd_line = (
            "Working directory: your session's artifact folder. This is where "
            "`python_exec` and `execute_command` run, and where relative-path "
            "file writes (`write_file`, or any file your code/commands create) "
            "land and auto-promote to the Artifacts panel. Always save output "
            "files there using a RELATIVE filename (e.g. `report.docx`, not an "
            "absolute path). Never write to an absolute path unless the user "
            "explicitly gave you one — the exact folder path is in the first "
            "message of this chat.\n")
    else:
        _cwd_line = (
            "Any files you produce go to your session's artifact folder — save "
            "them with a RELATIVE filename (e.g. `report.docx`), never an "
            "absolute path unless the user gave you one.\n")
    system_instruction += (
        f"You are agent '{agent_id}' in the Brain Agent system. "
        f"Current date and time: {_dt.now().strftime('%Y-%m-%d %H:00 %Z').strip()}\n"
        f"{_cwd_line}"
        f"Operating system: {os_name}\n"
        )
    # NOTE 1: we deliberately do NOT print `os.getcwd()` here. The Brain server's
    # process cwd is the source-code repo root; no interactive tool call ever
    # writes there (python_exec / execute_command / relative write_file all
    # resolve to the per-session artifact folder). Advertising the repo root as
    # "Current working directory" made the model save generated files into the
    # repo with an invented absolute path — invisible to the Artifacts panel and
    # polluting the source tree. The line above states the truth instead.
    # NOTE 2: the per-session artifact folder line (with the literal path) used
    # to live here, but it made the system prompt session-dependent — warmup
    # primes with no session (line omitted) while the real turn has one (line
    # present), so the oMLX KV prefix never matched and every first turn paid
    # full prefill (~20s on the 26B). The literal path moved to the
    # first-user-message preamble (`_artifact_folder_preamble_text`, prepended in
    # handlers/chat.py); the statement above stays session-agnostic (no path) so
    # the warm-pool prefix is reused.
    system_instruction += "\n"
    # Memory tool guidance is no longer hardcoded here — it lives in the
    # admin-editable `mempalace_query` per-tool description (Tools settings)
    # and gets rendered by `_render_tool_descriptions` only when the agent
    # actually has the tool. Removes a ~700-char paragraph that previously
    # shipped to every agent including those with no memory access.
    tcfg = _brain._get_token_config()
    # Project context, note editing, team, skills, scheduler, MCP listings:
    # interactive purpose always emits these. Scheduled tasks are interactive
    # too — they get the same surface as a human asking the agent the same
    # question (minus the conversational framing).
    # interactive purpose: project context, team, skills, scheduler, MCP listings.
    # Inject project context if a project is active
    active_project = get_request_context().project
    if active_project:
        proj_cfg = _brain.ProjectManager.get_project(agent_id, active_project)
        if proj_cfg:
            proj_desc = proj_cfg.get("description", "")
            system_instruction += (
                f"PROJECT CONTEXT: You are working in project '{proj_cfg.get('name', active_project)}'."
            )
            if proj_desc:
                system_instruction += f" {proj_desc}"
            # Web-locked projects: the 3 web tools are removed (disable_web_search,
            # applied in apply_domain_context). Make the boundary HONEST — the
            # model can ONLY reach the curated, mined project sources (each web
            # source is a single fetched page, not a crawl), so a thin answer must
            # read as "this is the limited curated corpus", not implied exhaustive
            # web analysis. Stable per project ⇒ KV-cache-safe (project is in the
            # cache key; same value every turn).
            if proj_cfg.get("disable_web_search"):
                system_instruction += (
                    "\nCLOSED CORPUS: Live web access is disabled for this project. "
                    "You can answer ONLY from the project's curated, inspected sources "
                    "(its memory + documents; each web source is a single saved page, "
                    "not a crawl of the wider site). You CANNOT search the web or fetch "
                    "new pages. If the curated sources do not cover the question, say so "
                    "plainly and state what is missing — do NOT fill the gap with general "
                    "knowledge or imply a broader web analysis than the corpus supports."
                )
            try:
                _kg_enabled_for_prompt = bool(
                    (_brain._load_mempalace_config().get("kg") or {}).get(
                        "enabled", True))
            except Exception:
                _kg_enabled_for_prompt = True
            # research_mode resolution: per-session override (sticky, set
            # from composer / session settings) wins; otherwise the
            # project's own `research_mode` (with legacy migration in
            # _project_research_mode). Surfaced on _thread_local so the
            # chat handler's citation validator + re-round read the same
            # value without recomputing.
            # The per-project research_mode flag (+ its per-session override) is
            # the MANUAL control — honoured ONLY in keyword-classifier mode. Under
            # LLM/hybrid mode the citation discipline is applied DYNAMICALLY from
            # the effective tool set (handlers/chat.py, a wire-only preamble), so
            # the flag is disabled and must NOT also inject via the system prompt
            # (would double it + break the warm-pool KV prefix per-project).
            if _brain.classifier_is_llm():
                _research_mode = False
            else:
                _rm_override = get_request_context().research_mode_override
                if _rm_override is None:
                    _research_mode = bool(proj_cfg.get("research_mode", False))
                else:
                    _research_mode = bool(_rm_override)
            # The dynamic counts + the on-disk input-folder list moved out
            # of the system prompt into a per-session first-user-turn preamble
            # (see _project_preamble_text() called from send_message round 0).
            # Keeps this block KV-cache-stable across warm-pool sessions and
            # avoids re-billing the index on every fresh project chat.
            #
            # research_mode gates the strict retrieval/refusal regime:
            # forced 3-step flow, REFUSE-on-error, KG decision rules,
            # citation discipline. Non-research projects (build/codegen
            # that USES indexed content) get a softer variant — memory
            # is available, use it when relevant — so the model can
            # consume project facts as inputs without being forced to
            # quote-and-cite everything it produces.
            # Soft project-info block — gives the model the context that
            # this is a project chat with its own memory store. Detailed
            # tool-usage guidance (3-step flow, KG decision rule,
            # read_document how-to, BINARY DOCUMENTS) lives in the per-tool
            # `tool_settings` descriptions and renders only when the
            # corresponding tools are in the active set. Brain only ships
            # project info from the prompt now — the tools speak for
            # themselves.
            if _research_mode:
                system_instruction += (
                    "\nPROJECT MEMORY:\n"
                    "This project has a dedicated, isolated memory store. "
                    "BEFORE answering ANY question that could draw on "
                    "project knowledge — the user's documents, files in "
                    "their input folders, facts they previously told you, "
                    "project decisions — you MUST consult the project's "
                    "memory tools first. Do not guess or rely on general "
                    "knowledge when the project may have specifics. The "
                    "memory tools' own descriptions (mempalace_query, "
                    "read_document, mempalace_kg_search) carry the "
                    "detailed retrieval flow and citation rules.\n\n"
                )
            else:
                # Soft variant for non-research projects (research_mode=False).
                # research_mode is DECOUPLED into two concerns: (1) the
                # retrieval hint "use the project's own sources first" and
                # (2) the strict output discipline (REFUSE-on-empty +
                # mandatory citations). research_mode gates ONLY (2). (1)
                # must hold whenever the owner curated sources — it makes no
                # sense to specify files/folders/URLs for a project and then
                # have a task ignore them and free-search the web instead
                # (the v9.30.x webnews bug: a project with two curated URLs
                # had research_mode off, so the task ran searxng+web_fetch and
                # never touched the mined project memory).
                _has_sources = bool(
                    (proj_cfg.get("web_urls") or [])
                    or (proj_cfg.get("input_folders") or []))
                if _has_sources:
                    # Curated sources exist → prefer them over the open web,
                    # but without the strict refuse/cite regime.
                    system_instruction += (
                        "\nPROJECT MEMORY:\n"
                        "This project has its own curated sources (uploaded "
                        "documents, input folders, and/or web URLs) mined into "
                        "a dedicated, isolated memory store. For any request "
                        "that could draw on those sources, query the project "
                        "memory FIRST (`mempalace_query`) and prefer what it "
                        "returns over an open web search — the owner curated "
                        "these sources on purpose. Only fall back to the web "
                        "tools (`web_fetch`/`searxng_search`/`exa_search`) when "
                        "the project memory genuinely lacks what's needed (e.g. "
                        "fresher information than what was last synced). You are "
                        "NOT required to quote-and-cite or to refuse when memory "
                        "is empty — that strict regime is the Research mode.\n\n"
                    )
                else:
                    # No curated sources — purely soft hint.
                    system_instruction += (
                        "\nPROJECT MEMORY:\n"
                        "This project has a dedicated, isolated memory store. "
                        "Call `mempalace_query` whenever the user's request "
                        "could plausibly draw on the project's indexed "
                        "documents, input folders, or prior facts. The tool's "
                        "own description carries the search and read flow "
                        "details — do not refuse to help when memory has "
                        "nothing to offer; fall back to general capability "
                        "and the user's own input as you normally would.\n\n"
                    )
        # Research-mode disciplines: REFUSAL + PRECISION + CITATION rules
        # that gate the strict retrieval/refusal regime. Emitted by Brain
        # directly when research_mode is on — NOT folded into the owner's
        # editable Instructions field. Owner instructions remain a purely
        # additive layer regardless of mode.
        if _research_mode:
            _disc = _brain.render_research_mode_disciplines()
            if _disc:
                system_instruction += (
                    "RESEARCH MODE DISCIPLINES (refusal, precision, citation):\n"
                    f"{_disc}\n\n"
                )
        # Inject project Instructions verbatim if the owner has set them.
        # Instructions are purely additive owner-supplied guidance — they
        # are NOT used as a fallback for the citation/refusal disciplines.
        # When `instructions` is empty, no synthetic block is appended.
        proj_instructions = (proj_cfg.get("instructions") or "").strip()
        if proj_instructions:
            system_instruction += (
                "PROJECT INSTRUCTIONS (set by the user for this project):\n"
                f"{proj_instructions}\n\n"
            )

    # Inject note context for AI-assisted note editing
    note_context = get_request_context().note_context
    if note_context:
        note_path = note_context.replace("note_editing:", "").strip() if note_context.startswith("note_editing:") else ""
        notes_dir = os.path.dirname(note_path) if note_path else ""
        system_instruction += (
            "\n\nNOTE EDITING MODE:\n"
            f"You are helping the user edit a markdown note{' at: ' + note_path if note_path else ''}.\n"
            "The user will provide the current note content in their message.\n"
            "When the user asks you to ADD, EDIT, or MODIFY the note, use the edit_file or write_file tool "
            "to make changes directly to the note file. The editor will auto-reload.\n"
            f"You can also CREATE NEW notes in the same project by writing to: {notes_dir}/<new-name>.md\n"
            "For questions or explanations, respond normally without editing files.\n\n"
        )
    # Inject team context for interactive sessions
    team_info = _brain._get_agent_team_info(agent_id)
    if team_info:
        if team_info["is_head"]:
            peers = [m for m in team_info["members"] if m != agent_id]
            system_instruction += (
                f"TEAM: You are the head of team '{team_info['name']}'. "
                f"Your team members: {', '.join(peers)}\n"
                "Delegate sub-tasks to your team members when appropriate.\n\n"
            )
        else:
            peers = [m for m in team_info["members"] if m != agent_id and m != team_info["head"]]
            system_instruction += f"TEAM: You are a member of team '{team_info['name']}'.\n"
            system_instruction += f"Team head: {team_info['head']}\n"
            if peers:
                system_instruction += f"Team peers: {', '.join(peers)}\n"
            system_instruction += "\n"

    if agent_registry:
        system_instruction += f"\n{agent_registry}\n\n"

    # Build skills registry (names + descriptions only, load on demand)
    _agent = get_request_context().current_agent or _brain._current_agent
    if _agent:
        skills = _agent.list_skills()
        if skills:
            system_instruction += "\nSKILLS AVAILABLE — call use_skill(skill=\"slug\") to load instructions before performing the task:\n"
            for s in skills:
                slug = s.get('slug', s['name'])
                source_tag = f" (from {s['source']})" if s['source'] != agent_id else ""
                display = s['name'] if s['name'] != slug else ""
                label = f"{slug}" + (f" ({display})" if display else "")
                system_instruction += f"  - {label}: {s['description']}{source_tag}\n"
            system_instruction += "\n"

    # Scheduler status
    if _brain._scheduler:
        schedules = [s for s in _brain._scheduler.list_all() if not s["name"].startswith("_memory_summary_")]
        if schedules:
            system_instruction += "\nSCHEDULER — active scheduled tasks:\n"
            for s in schedules:
                status = "active" if s["enabled"] else "paused"
                next_r = s.get("next_run", "")[:16] if s.get("next_run") else "—"
                system_instruction += f"  - {s['name']} [{status}]: {s['task'][:80]} (next: {next_r})\n"
            system_instruction += "Use schedule_list and schedule_history tools to query scheduler state.\n\n"

    # MCP servers (prefer thread-local for concurrent requests)
    mcp_mgr = get_request_context().mcp_manager or _brain._mcp_manager
    if mcp_mgr and mcp_mgr.clients:
        system_instruction += "\nMCP SERVERS — external tools available via connected servers:\n"
        for srv in mcp_mgr.list_servers():
            tools_list = ", ".join(srv["tools"][:5])
            more = f" +{srv['tool_count']-5}" if srv["tool_count"] > 5 else ""
            system_instruction += f"  - {srv['name']} ({srv['transport']}): {tools_list}{more}\n"
        system_instruction += "MCP tools are prefixed with mcp_<server>_ — use them like any other tool.\n\n"

    # Note about deferred built-in tool groups
    _deferred_groups = [g for g in (tcfg.get("deferred_tool_groups") or []) if g in _brain.TOOL_GROUPS]
    if _deferred_groups:
        system_instruction += "DEFERRED TOOLS: These tool groups are available but not loaded. Use tool_search to discover and activate them when needed:\n"
        for _dg in _deferred_groups:
            system_instruction += f"  - {_dg}: {', '.join(sorted(_brain.TOOL_GROUPS[_dg]))}\n"
        system_instruction += "\n"

    if tcfg.get("include_tools_guide", True) and active_tool_names is not None:
        # Per-tool prompt prose for the active tool set, sourced from admin
        # → Tools settings (server_config["tool_settings"], populated at
        # startup from config.json). Empty when no active tool has any
        # configured description (e.g. transform / memory_summary purposes).
        _rules = _brain._render_tool_descriptions(active_tool_names)
        if _rules:
            system_instruction += f"\n--- TOOL USAGE GUIDE ---\n{_rules}"

    # Cache the BASE prose (no caveman, no plan suffix). Post-processing
    # is applied below so the cached value is reusable across caveman/plan
    # toggles within the same session.
    import time as _time
    _system_prompt_cache[cache_key] = (system_instruction, _time.time())
    # Evict stale entries (keep cache small)
    if len(_system_prompt_cache) > 20:
        cutoff = _time.time() - _SYSTEM_PROMPT_CACHE_TTL
        for k in list(_system_prompt_cache):
            if _system_prompt_cache[k][1] < cutoff:
                del _system_prompt_cache[k]

    return _apply_system_prompt_postprocess(
        system_instruction, caveman_system, caveman_chat, plan_mode, gdpr_anon)


# Per-turn clamp appended when transparent anonymisation is active. Tells the
# model the `<KIND_N_HEX>` tokens in the user message are placeholders and to
# copy them verbatim into its reply. The 109-test benchmark in the research
# report behind the transparent-anonymisation rollout showed this clamp pushes
# roundtrip token preservation from ~93% to >99% on frontier models.
#
# KV-prefix invariant: this is gated on a per-turn thread-local
# (`_gdpr_anonymising`), applied AFTER cache lookup in
# `_apply_system_prompt_postprocess`, so the cached BASE prose stays anon-
# agnostic and warm-pool / non-anon turns share the same KV prefix as
# before. Only anonymised turns pay the extra bytes — which is correct, the
# clamp is load-bearing for those turns.
_GDPR_ANON_CLAMP = (
    "\n\n--- PSEUDONYMISATION ---\n"
    "Some values in the user's message (and any attached files you may read) "
    "have been pseudonymised for privacy. Tokens of the form "
    "`<KIND_N_HEX>` (e.g. `<EMAIL_1_a8k2>`, `<PERSON_2_b3f1>`, `<IBAN_1_c7d4>`) "
    "are placeholders that stand in for real personal data.\n\n"
    "**Copy each token verbatim into your reply** — do not translate, "
    "reformat, paraphrase, describe, or split them across lines. Keep the "
    "angle brackets, underscores, digits, and salt suffix exactly as you "
    "received them. Shape-preserving fakes (synthetic IBANs, phone numbers, "
    "credit-card numbers) are NOT placeholders — treat those as the real "
    "value for the purpose of the reply.\n\n"
    "The system will restore the original values before showing your reply "
    "to the user. If you mangle a token (different case, extra spaces inside "
    "the brackets, dropped salt) the restoration will fail and the user will "
    "see the placeholder instead of their data."
)


def _apply_system_prompt_postprocess(base: str, caveman_system: int,
                                      caveman_chat: int,
                                      plan_mode: bool,
                                      gdpr_anon: bool = False) -> str:
    """Apply caveman compression + plan-mode suffix to a cached base prose.

    Pure string transform; runs in microseconds. Kept out of the cache key
    so a session that flips caveman levels or plan mode mid-stream reuses
    the cached base instead of triggering a fresh disk read.
    """
    out = base
    _CAVEMAN_SYSTEM = _brain.CAVEMAN_SYSTEM_PROMPTS
    _CAVEMAN_CHAT = _brain.CAVEMAN_CHAT_PROMPTS
    if caveman_system and caveman_system in _CAVEMAN_SYSTEM:
        out = _CAVEMAN_SYSTEM[caveman_system] + _brain._caveman_compress_text(out, caveman_system)
    if caveman_chat and caveman_chat in _CAVEMAN_CHAT:
        out += _CAVEMAN_CHAT[caveman_chat]
    if plan_mode:
        out += _brain.PLAN_MODE_PROMPT
    if gdpr_anon:
        out += _GDPR_ANON_CLAMP
    return out


def _project_preamble_text(agent_id: str, project_name: str) -> str:
    """Build the per-session project preamble injected on the first user
    message. Carries the project's dynamic state — drawer count, attachment
    count, list of input folders, path-join example — that used to live in
    `_build_system_prompt`. Moving it here keeps the system prompt
    project-agnostic in shape (KV-cache stable across project chats and warm
    pool) while still giving the model the concrete absolute paths it needs
    to resolve relative drawer source_files.

    Returns "" when the project doesn't exist or has no useful state to
    report — the preamble is then skipped entirely and no extra block
    appears in the user message.
    """
    try:
        proj_cfg = _brain.ProjectManager.get_project(agent_id, project_name)
    except Exception:
        proj_cfg = None
    if not proj_cfg:
        return ""
    input_folders = proj_cfg.get("input_folders") or []
    try:
        attachment_count = int(proj_cfg.get("chunks") or 0)
    except (TypeError, ValueError):
        attachment_count = 0
    try:
        total_drawers = int(
            (proj_cfg.get("sync_status") or {}).get("total_indexed") or 0)
    except (TypeError, ValueError):
        total_drawers = 0
    try:
        total_files = int(
            (proj_cfg.get("sync_status") or {}).get("total_files") or 0)
    except (TypeError, ValueError):
        total_files = 0
    lines: list[str] = []
    # State summary — gives the model a sense of how much is indexed.
    state_bits = []
    if total_drawers:
        state_bits.append(f"{total_drawers} indexed chunks")
    if total_files:
        state_bits.append(f"{total_files} source files")
    if attachment_count:
        state_bits.append(f"{attachment_count} manual attachment(s)")
    if input_folders:
        state_bits.append(f"{len(input_folders)} input folder(s)")
    if state_bits:
        lines.append("Project memory state: " + ", ".join(state_bits) + ".")
    # Folder list with the path-join example. This is the part that
    # genuinely depends on absolute paths the model can't otherwise see.
    folder_lines: list[str] = []
    for entry in input_folders:
        p = (entry or {}).get("path", "").strip()
        if not p:
            continue
        rec = " (recursive)" if entry.get("recursive", True) else " (top-level only)"
        folder_lines.append(f"  • {p}{rec}")
    if folder_lines:
        lines.append("Input folders on disk:")
        lines.extend(folder_lines)
        first_root = (input_folders[0] or {}).get("path", "") or ""
        if first_root:
            lines.append(
                "When a mempalace_query drawer's `source_file` is a relative "
                "path, JOIN it with one of the absolute folder roots above "
                "before calling read_document. Example: source_file "
                f"`screen.py` mined under `{first_root}` becomes "
                f"`{os.path.join(first_root, 'screen.py')}`."
            )
    if not lines:
        return ""
    return "[Project context (this session):\n- " + "\n- ".join(lines) + "]"


def _workflow_run_preamble_text(execution_id: str) -> str:
    """Compact summary of a workflow run for chat follow-up context.

    Built from the persisted workflow_history row: workflow source +
    chronological tool-call trace + final return value or error.
    Injected into the first user message of any chat session bound to
    this run so the model can answer questions like "summarise the
    transcript" without re-executing tools to find the file. Mirror of
    engine/loop.py:_workflow_run_preamble_text — runtime resolves to
    this brain.py copy because handlers/chat.py imports brain as engine.
    """
    if not execution_id:
        return ""
    try:
        row = _brain._workflow_history_get(execution_id)
    except Exception:
        row = None
    if not row:
        return ""
    name = row.get("workflow_name") or "(unnamed)"
    status = row.get("status") or "unknown"
    src = (row.get("workflow_source") or "").strip()
    if len(src) > 4000:
        src = src[:4000] + "\n…[truncated]"
    steps_raw = row.get("steps_json") or "[]"
    try:
        steps = json.loads(steps_raw) if isinstance(steps_raw, str) else (steps_raw or [])
    except Exception:
        steps = []
    interesting = [s for s in steps
                   if s.get("kind") in ("call", "call_done", "error")]
    if len(interesting) > 50:
        head = interesting[:25]
        tail = interesting[-20:]
        interesting = head + [{"kind": "…", "line": "", "detail": f"({len(steps) - len(head) - len(tail)} steps elided)"}] + tail
    trace_lines: list[str] = []
    for s in interesting:
        line = s.get("line") or ""
        kind = s.get("kind") or ""
        detail = (s.get("detail") or "").strip()
        if len(detail) > 400:
            detail = detail[:400] + "…"
        prefix = f"L{line} {kind}" if line else kind
        trace_lines.append(f"  • {prefix}: {detail}" if detail else f"  • {prefix}")
    rv_raw = row.get("return_value")
    rv_text = ""
    if rv_raw is not None and rv_raw != "":
        try:
            parsed = json.loads(rv_raw) if isinstance(rv_raw, str) else rv_raw
            rv_text = parsed if isinstance(parsed, str) else json.dumps(parsed, indent=2, ensure_ascii=False)
        except Exception:
            rv_text = str(rv_raw)
        if len(rv_text) > 1500:
            rv_text = rv_text[:1500] + "\n…[truncated]"
    err = (row.get("error") or "").strip()
    parts: list[str] = [
        f"[Workflow run context — the user is asking follow-up questions about a workflow that already finished. Use the trace below to answer; do NOT re-execute the workflow.",
        f"Workflow: {name}  (status: {status})",
    ]
    if src:
        parts.append("Source:\n```\n" + src + "\n```")
    if trace_lines:
        parts.append("Tool-call trace:\n" + "\n".join(trace_lines))
    if rv_text:
        parts.append("Return value:\n```\n" + rv_text + "\n```")
    if err:
        parts.append(f"Error: {err}")
    return "\n\n".join(parts) + "]"


def _artifact_folder_preamble_text(agent_id: str, session_id: str) -> str:
    """One-line per-session artifact-folder pointer for the first user message.

    Moved out of `_build_system_prompt` (v9.9.9) so the system prompt stays
    session-agnostic — warmup primes without a session, the real turn has one,
    and having this line in the system prompt broke the oMLX KV-prefix match
    (full prefill every first turn). Lives in the preamble where per-session
    context belongs. Returns "" when there's no session.
    """
    if not session_id:
        return ""
    _folder = os.path.join(
        _brain.AGENTS_DIR, agent_id, "artifacts",
        _brain._get_artifact_session_folder(session_id))
    return (f"[Session artifact folder (your working directory): {_folder} — "
            f"this is where `python_exec` and `execute_command` run. Write every "
            f"output file with a RELATIVE filename (e.g. `report.docx`) so it "
            f"lands here and auto-promotes to the Artifacts panel. Do NOT write "
            f"to an absolute path (it won't appear in the Artifacts panel) unless "
            f"the user explicitly gave you one.]")


def _files_in_chat_preamble_text(session_id: str) -> str:
    """Per-turn list of files this chat has produced or pulled in.

    Reads `artifacts` rows scoped to the session and renders them as a
    [Files in this chat: ...] block on the first user message of every
    turn at round 0. The list is the model's "memory of what's on disk"
    that survives compaction — when a tool result has been compressed
    out of the conversation history, the file's path stays in the
    preamble so the model can call read_document on it.

    Caps at the 50 most-recent rows (with "(+N more)" for the tail) so
    long-running chats don't blow up the prompt budget.
    """
    if not session_id:
        return ""
    try:
        from server import _db_conn as _chat_db_conn
        with _chat_db_conn() as conn:
            rows = conn.execute(
                """SELECT name, path, type, role, created_at
                     FROM artifacts
                    WHERE session_id = ?
                 ORDER BY created_at DESC
                    LIMIT 51""",
                (session_id,)
            ).fetchall()
    except Exception:
        return ""
    if not rows:
        return ""
    lines: list[str] = []
    truncated = len(rows) > 50
    for r in rows[:50]:
        name = r[0] or ""
        path = r[1] or ""
        ftype = r[2] or "file"
        role = r[3] or "output"
        # Best-effort size — file may have been deleted from disk.
        try:
            size = os.path.getsize(path)
            if size < 1024:
                size_s = f"{size}B"
            elif size < 1024 * 1024:
                size_s = f"{size / 1024:.1f}KB"
            else:
                size_s = f"{size / (1024 * 1024):.1f}MB"
        except OSError:
            size_s = "missing"
        # Output files written by the agent get the (output) tag; inputs
        # come from workflow-run seeding (read by the run, available for
        # follow-ups). Other roles (e.g. intermediate) fall back to type.
        role_tag = role if role in ("output", "input") else ftype
        lines.append(f"  • {path}  ({role_tag}, {size_s})")
    if truncated:
        lines.append(f"  • (+{len(rows) - 50} older)")
    return (
        "[Files in this chat — paths on disk that you wrote or that "
        "this session was given access to. If their content is not "
        "already visible in the conversation above (e.g. the trace was "
        "compacted, or you only have a summary), call read_document "
        "with the path to fetch full content. Do not guess at content "
        "from the filename.\n"
        + "\n".join(lines)
        + "]"
    )
