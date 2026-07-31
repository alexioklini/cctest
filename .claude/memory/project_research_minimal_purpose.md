---
name: research-minimal-purpose
description: 2026-05-14 (commit c8ab71c) — research_minimal purpose + per-schedule tool_profile; tool-flag-driven discovery; Gate-PT-2 pass rates per model
metadata: 
  node_type: memory
  type: project
  originSessionId: 4e63f2da-da82-4e5e-a538-a10664d35872
---

Shipped 2026-05-14 in commit `c8ab71c` (`feat(unification): resolve_active_tools + research_minimal purpose + per-schedule tool_profile`). Companion to [[project_token_optimizations_validated]] and the unification work tracked in `PROMPT_TOOLS_UNIFICATION_PLAN.md`.

## What it is

`research_minimal` is one of five purposes routed through `engine.resolve_active_tools(purpose=…, agent_id=…)` — the new single source of truth for what tools the model sees on a turn. It's a harness-style lean surface for scheduled research tasks. Brain's emitted prompt is byte-identical to `eval/sdk_harness/system_prompt_scheduler.md` (the 1078-char prompt the standalone Anthropic-loop harness used to drive gemma-4-e4b through a cited report).

The other four purposes:
- `interactive` — chat, project chat, full agent surface (default everywhere except scheduler).
- `memory_summary` — terse identity + `mempalace_query`/`save_chat_to_memory`/`mempalace_get_drawer`/`mempalace_list_drawers`.
- `transform` — empty tool list; caller supplies its own prompt (refine, translate, classifier-for-memory, next-prompt, chat-title, KG extract, profile maintenance, code-graph summarise, workflow `ask_llm` nodes).
- `background_qa` — defined in `_VALID_PURPOSES` but raises `NotImplementedError` until a real caller materialises (plan's deliberate-introduction guard).

## Tool-flag-driven discovery (not a hard-coded list)

The earlier emergency patch was `_SCHEDULED_TASK_TOOLS = {exa_search, web_fetch, write_file}` — a 3-tool clamp. The unification replaced that with tool self-declaration:

Each `TOOL_DEFINITIONS` entry that participates in `research_minimal` carries `minimal: True` + `minimal_role` (a one-line phrase composed into the prompt). Currently flagged in `brain.py`:
- `exa_search` (`minimal_role`: "to find relevant sources")
- `web_fetch` (`minimal_role`: "to read full pages")
- `write_file` (`minimal_role`: "to save the final deliverable")

`_minimal_tool_names()` returns the set; `_minimal_tool_roles()` returns the ordered (name, role) pairs the prompt builder splices in. Adding a tool to the lean surface is a 2-line TOOL_DEFINITIONS edit, no resolver changes.

## Per-schedule `tool_profile` field

`schedules.tool_profile TEXT DEFAULT ''` column + UI dropdown:
- Empty string → `research_minimal` (default for scheduled tasks; matches Phase A behavior).
- `"research_minimal"` → explicit.
- `"interactive"` → opts back into the full agent surface (use when the schedule needs `execute_command`, `python_exec`, MCP, etc.).

Validated against `_VALID_TOOL_PROFILES = ("", "research_minimal", "interactive")` on insert/update.

## Gate-PT-2 results (canonical "Mistral AI News" research task)

| Model | Provider | Pass rate | Notes |
|---|---|---|---|
| gemma-4-e4b-it-4bit | oMLX (local) | 2/3 ✅ | Matches the standalone harness baseline. Original full-prompt build was 0/3 (`<eos>` against 7.5KB prompt). |
| gemma-4-26b-a4b-it-4bit | oMLX (local) | 1/1 ✅ | |
| Mistral Medium 3.5 | CLIProxyAPI | 3/3 ✅ | Required `disable_parallel_tool_use` + `top_p=0.85` — see [[project_mistral_disable_parallel]] for causality caveats. |

Gate-PT-3 (eval re-baseline on policy eval) deliberately skipped — user opted not to spend the Mistral quota until a future session.

## Why this design vs the alternatives

- **Not "pick a smaller prompt by model size."** The plan rejected per-model auto-degradation — user picks the model, the prompt is purpose-driven. If e4b can't drive interactive on a complex task, fix is "use a bigger model" or "set tool_profile=interactive on the schedule," not silent prompt magic.
- **Not "hard-coded list."** A constant gets stale; tool self-declaration keeps the prompt and the active set in sync by construction.
- **Scheduled defaults to `research_minimal`, chat stays on `interactive`.** Today only `_execute_scheduled` routes through `research_minimal`. `_run_delegate`, `delegate_task`, and chat all stay on `interactive` by design.

## How to apply

- When adding a new tool that should be visible in lean scheduled tasks, set `minimal: True` + `minimal_role: "<role phrase>"` on its `TOOL_DEFINITIONS` entry. No resolver edits needed.
- When debugging a scheduled task: check `schedules.tool_profile`. Empty → `research_minimal` (3 tools by default); `"interactive"` → full agent surface.
- When extending purposes (e.g. activating `background_qa`): remove the `NotImplementedError`, add the tool set to the resolver, document the first caller. The plan was explicit about deliberate introduction.
