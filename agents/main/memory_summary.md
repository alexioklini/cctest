---
name: Memory Summary
description: "Auto-generated synthesis of recent conversations and task executions, updated periodically"
type: general
agent: main
last_recalled: 2026-04-08
related:
  - file: openclaw-vs-claudecode-skills-comparison.md
    type: same_topic
  - file: memory-architecture.md
    type: same_topic
  - file: dev-workflow-feedback_cffe4d.md
    type: depends_on
  - file: memory_summary_aab3f9.md
    type: same_topic
  - file: memory-architecture_fa7efd.md
    type: same_topic
  - file: shell-env-fix-2025_59a0f4.md
    type: same_topic
  - file: shell-env-fix-2025_8c1d05.md
    type: same_topic
  - file: dev-workflow-feedback_af2401.md
    type: same_topic
  - file: agent_roster_with_minimax_3ffcde.md
    type: same_topic
  - file: user_identity_77860a.md
    type: references
  - file: openclaw-vs-claudecode-ref-memory-summary_bcb3eb.md
    type: same_topic
  - file: scheduled_tasks_87aabf.md
    type: same_topic
  - file: scheduled_task_flags_cli_ab8813.md
    type: same_topic
  - file: skills_system_1b7cd0.md
    type: same_topic
  - file: memory_system_e5dba6.md
    type: same_topic
  - file: openclaw-vs-claudecode-skills-comparison-extended-_82c838.md
    type: same_topic
  - file: memory-architecture_-_tool-expansion-analysis-2026_2bb117.md
    type: same_topic
  - file: researcher_agent_role_-_reporter_agent_summary_d7f789.md
    type: same_topic
  - file: shell-env-fix-2025_-_claude-code-version-path_35b821.md
    type: same_topic
  - file: tool-expansion-analysis-2026-03_-_reporter_agent_s_2cf7e8.md
    type: same_topic
  - file: coder-agent-scheduled-tasks_-_memory_health_report_ae6d8d.md
    type: same_topic
  - file: minimax_agent_roster_extensions_b5a832.md
    type: same_topic
  - file: infra_minimax_provider_relationships_a5e519.md
    type: same_topic
  - file: user_to_minimax_coder_relationship_9e5989.md
    type: same_topic
  - file: agent_roster_with_minimax_-_infra_minimax_provider_bfd61b.md
    type: same_topic
  - file: infra_scheduler_dependency_-_infra_minimax_provide_e0e54d.md
    type: same_topic
  - file: researcher_agent_role_-_researcher_tool_chain_2d9815.md
    type: same_topic
  - file: memory_health_reports_chain_0dfb71.md
    type: same_topic
  - file: relationship_summary_researcher_agent_acea8d.md
    type: same_topic
  - file: memory_summary_218799.md
    type: same_topic
  - file: memory_generation_pipeline_ac1bd2.md
    type: same_topic
  - file: memory_summary_-_user_identity_520e90.md
    type: co_recalled
  - file: scheduled_tasks_minimax_dependency_relationships_094b35.md
    type: co_recalled
  - file: tool-expansion-analysis-2026-03_7280e3.md
    type: same_topic
  - file: reporter_agent_role_747c13.md
    type: same_topic
  - file: relationship_summary_researcher_agent_-_user_ident_14c3b4.md
    type: co_recalled
  - file: _relationship_discovery_crow4b_-_memory_system_f15e25.md
    type: same_topic
  - file: _relationship_discovery_crow4b_-_scheduled_tasks_f0e6d1.md
    type: same_topic
  - file: crow4b_-_memory_summary_6f4362.md
    type: same_topic
  - file: reporter_agent_summary_07ba84.md
    type: same_topic
  - file: reporter_tool_chain_92a110.md
    type: same_topic
  - file: reporter_agent_role_-_researcher_counterpart_95de32.md
    type: same_topic
  - file: reporter_agent_-_web_ui_tool_toggle_presentation_79f2ec.md
    type: same_topic
  - file: reporter_agent_-_minimax_provider_integration_09b903.md
    type: same_topic
  - file: reporter_agent_role_-_memory-architecture_dependen_4332e9.md
    type: same_topic
  - file: mcp_servers_vs_native_tools_supplementary_tools_an_408255.md
    type: same_topic
  - file: system_architecture_brain-agent_on_mac_studio_m2_m_bb9752.md
    type: same_topic
---

## User Profile & Context
The user is **Alexander**, a developer actively building and configuring the **Brain Agent** platform (this system). He operates from `/Users/alexander/Documents/dev/cctest`. He is technically proficient — comfortable with API configurations, provider setups, model selection, agent architectures, and hands-on feature development. He speaks both **English and German** fluently and switches between them naturally. He also uses Brain Agent for casual day-to-day queries (weather forecasts, image descriptions, web research). Based in or near **Vienna, Austria** (frequent weather queries confirm this).

## Communication & Working Style
- Prefers **direct, concise** interactions — often initiates with brief greetings or dives straight into requests.
- Comfortable with **casual conversation** and brief exchanges; doesn't always have a task.
- Can be exploratory — asks questions to understand the system's capabilities (memory architecture, agent delegation, workflow system, available teams).
- Will say "Vergiss es" (forget it) or abandon a thread quickly if a conversation isn't going in the right direction. Don't over-pursue abandoned topics.
- Expects the agent to be responsive in whatever language he uses (German or English).
- Dislikes when the agent misunderstands or over-complicates simple requests.
- **Notices and calls out quality issues** — expects autonomous error recovery and continuous execution without stalling.
- Likes **well-formatted tabular data** for weather and similar informational queries.

## Technical Preferences
- **Platform:** macOS (Darwin), working directory `/Users/alexander/Documents/dev/cctest`
- **Brain Agent setup:** Actively configuring providers, models, and building new features. Has integrated **MiniMax** as a provider (API at `https://api.minimax.io/anthropic`, model MiniMax-M2.5/M2.7). Fixed provider config issue where `/v1/models` endpoint returned 404 — resolved by manually setting `default_model` and adding model entries.
- **Agent architecture understanding:** Knows the agent registry, delegation model, memory scoping, and workflow system well. Confirmed that main agent memory = shared memory (hub-and-spoke model).
- **Model preferences:** Main agent runs on Claude Opus. Coder agent uses MiniMax-M2.7. Research team uses claude-sonnet-4-6.
- **Vision capability:** Uses image analysis for identifying content in pictures.

## Active Projects & Ongoing Work
- **Brain Agent Platform Development** — Actively building features and configuring the multi-agent system:
  - **Web UI tool calls toggle (completed 2026-03-20):** Implemented a 🔧 toggle button in the web UI input bar to show/hide tool call blocks. Also added `/tools` slash command. State persists in localStorage.
  - Configured MiniMax as an API provider (resolved model discovery issue).
  - Explored workflow system capabilities — confirmed it's fully implemented but no workflow YAML files created yet.
  - **Opus fallback fixes (v4.3.1, 2026-03-25):** Fixed SSE streaming transient error detection so `overloaded_error`/`rate_limit_error`/`api_error` trigger retries instead of immediate fallback. Also added message snapshot/rollback before fallback to prevent Anthropic-format tool blocks corrupting the fallback call.

## Task Execution Insights
- **Relationship discovery tasks** run daily at 04:15 for all agents. On **2026-04-03**, the task failed with `ValueError: unknown url type: '/messages'` — indicates a misconfigured base URL (relative path instead of absolute) in the HTTP client. On **2026-04-04**, the task completed successfully but used 0 tools — it logged the intent without actually executing. This pattern suggests the relationship discovery task may not be functioning as intended even when it reports success.
- The `/messages` URL error is a recurring infrastructure concern: a relative path is being passed where an absolute URL is expected. Worth investigating the scheduled task runner's HTTP client configuration.
- All other scheduled tasks (memory summary, health reports) appear to be running reliably.

## Key Decisions & Context
1. **Memory architecture confirmed:** Main agent memory IS shared memory — same store. Other agents access it via `memory_shared`. Hub-and-spoke model.
2. **MiniMax provider fix:** API doesn't expose `/v1/models`, so models must be manually configured with explicit `default_model` and `models` entries.
3. **Agent roster:** Research Team (Researcher head + Reporter, on claude-sonnet-4-6), Coder (standalone, MiniMax-M2.7), crow (standalone, Crow-4B-Opus-4.6-Distill).
4. **Don't stall on errors:** When `edit_file` or similar tools fail, self-recover immediately — re-read, adjust, retry. Don't stop and wait for user prompt. (Explicit user feedback, 2026-03-20.)
5. **Abandoned request:** Alexander asked to switch the Telegram chat model to Opus but then said "Vergiss es" — do not revisit unless he brings it up again.
6. **Workflow system:** Fully implemented in codebase but not yet in active use — no YAML workflow files created.
7. **Relationship discovery reliability issue (2026-04-04):** Task errors with `ValueError: unknown url type: '/messages'` on some runs, and completes with 0 tool calls on others — needs investigation.
