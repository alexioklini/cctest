# Prompt & Tool-List Unification Plan

**Status:** Draft for sign-off, 2026-05-14
**Companion to:** `SDK_MIGRATION_PLAN.md` (Phase 3 in flight, Phases 4–5 pending)
**Reference:** `eval/sdk_harness/system_prompt_scheduler.md` (1078 chars) — the lean prompt that drives gemma-4-e4b to a cited report. Anything we ship for `interactive` should be in the same order-of-magnitude.

---

## Problem

Brain has 4 live tool-list construction paths and 2 prompt-shape branches (`chat` / `scheduled`). Each applies its own filters; they have already silently diverged:

- Sidecar chat path applies `_get_agent_tool_names` + deferral (post-Phase 2 fix this turn).
- Sidecar scheduler path applies `_get_agent_tool_names` + deferral + a hard-coded `_SCHEDULED_TASK_TOOLS = {exa_search, web_fetch, write_file}` clamp (added this turn).
- Native `_run_delegate` (Phase 4 callers + fallback) applies `_get_agent_tool_names` only — **no deferral**.
- Native `send_message` (chat fallback) applies all filters + MCP + discovered tools.
- Warmup path mirrors send_message but for cold-start.
- The scheduled-mode system prompt was 7.5 KB; gemma-4-e4b silently emitted `<eos>` against it. Shrunk to 1.5 KB this turn and e4b produced a real report — but the shrink was a one-off lean prompt, not generalised.

The current state has e4b passing Gate-2 only because of three temporary patches stacked on each other. Generalising requires consolidating the patches into a single, defensible design before Phase 4 multiplies the divergence.

---

## Design decisions (locked 2026-05-14)

### Four purposes — exhaustive, mutually exclusive

| Purpose | Used by | Wire shape | Tool surface |
|---|---|---|---|
| `interactive` | chat, project chat, workflow orchestrator turn, **scheduled tasks** | Multi-round agentic loop | Full agent tool set, minus deferred groups (unless discovered this session), plus MCP |
| `background_qa` | headless corpus queries (agent-to-agent that reads indexed docs, future "summarize my chats" jobs) | Multi-round | Corpus-read set: `mempalace_query`, `mempalace_kg_search`, `mempalace_kg_query`, `mempalace_kg_neighbors`, `read_document`, `read_file`, `write_file` |
| `transform` | refine, translate, classifier-for-memory, next-prompt, chat-title, KG extract, profile maintenance, code-graph summarise, **workflow `ask_llm` nodes** | Single `messages.create`, `tools=[]` | Empty |
| `memory_summary` | Brain-owned `_memory_summary_*` scheduled tasks | Multi-round | Memory-only: `mempalace_query`, `save_chat_to_memory`, `mempalace_get_drawer`, `mempalace_list_drawers` |

**Why these four:**
- Scheduled tasks are `interactive` (a delivery channel, not a workload shape — "scheduled task can contain anything chat can").
- Workflow `ask_llm` is `transform` (single-shot, no tools).
- `background_qa` is for **headless** corpus queries only; project chat with a human is `interactive`.
- `memory_summary` stays separate because its tool set (`save_chat_to_memory`) is wrong everywhere else.

**Rejected:**
- `research` — over-specific; scheduled tasks aren't necessarily research.
- `code` — code-mode chat is `interactive` with `execute_command` + `python_exec` in the agent's tool_groups. New purpose buys nothing.
- `scheduled` — delivery channel, not shape.
- `delegate` — inherit purpose from what the delegate is actually doing.

### Soul.md inclusion rule

| Purpose | Include soul.md? |
|---|---|
| `interactive` | YES |
| `background_qa` | NO |
| `transform` | NO |
| `memory_summary` | NO |

Soul = interactive personality framing. Everywhere else it adds tokens, biases output style, serves no function.

### `tools.md` becomes a conditional rules emitter

Current `tools.md` (4.5 KB) is unconditionally appended to every chat prompt. After this work, the file is rewritten down to three named blocks; each block is emitted only when its anchor tool is in the active tool set:

| Block | Anchor tool(s) | Approx size | Why it pays for itself |
|---|---|---|---|
| `EXEC_RULES` | `execute_command` in active set | ~600 chars | Banned commands (top/vim/nano/man) cause 15s timeouts. Output management. No-TTY rule. |
| `EXA_PROTOCOL` | `exa_search` AND `web_fetch` in active set | ~250 chars | "After exa, web_fetch every URL before answering." Without it, models answer from titles. Observed failure mode. |
| `PYEXEC_HINT` | `python_exec` in active set | ~700 chars | When-to-use vs native tools; document-processing recipes (`docx`, `openpyxl`, `pptx`). Token-saving on file-heavy turns. |

Dropped from `tools.md`:
- "Remote Nodes" — feature dead, never seen in traces.
- "Context Tools" — being deleted in Phase 5.
- "write_file artifact-folder note" — already in the tool schema's description.

Total when all three apply: ~1.5 KB. When none apply (transform calls): empty string.

### Single tool-list resolver

```python
def resolve_active_tools(
    *,
    purpose: str,                   # "interactive" | "background_qa" | "transform" | "memory_summary"
    agent_id: str,
    discovered_tools: set[str] | None = None,
    mcp_manager: MCPManager | None = None,
    is_openai_shape: bool = False,
) -> list[dict]:
    """Single source of truth for what tools the model sees on a given turn.

    Returns the wire payload (Anthropic shape by default; OpenAI shape via flag).
    Every caller — chat handler, scheduler, warmup, _run_delegate, settings UI
    tool-breakdown — goes through this function.
    """
```

Logic:
1. `purpose=="transform"` → return `[]` immediately.
2. Compute `base_set` per purpose:
   - `interactive` → `_get_agent_tool_names(agent_id)` (None = all)
   - `background_qa` → `_BACKGROUND_QA_TOOLS ∩ _get_agent_tool_names(agent_id)` (intersection — agent's restrictions still apply)
   - `memory_summary` → `_MEMORY_SUMMARY_TOOLS ∩ _get_agent_tool_names(agent_id)`
3. Pick definition list: `TOOL_DEFINITIONS_OPENAI if is_openai_shape else TOOL_DEFINITIONS`.
4. Filter to `base_set` via `_filter_tools`.
5. Subtract deferred-group tools (from agent's `token_config.deferred_tool_groups`), except those in `discovered_tools`. **Always applied** — was being skipped in the native `_run_delegate` path.
6. Add MCP tools (when `mcp_manager` is passed and not deferred per `defer_mcp_tools`).
7. Sort by name for KV-cache stability.

### Unified prompt builder

```python
def _build_system_prompt(
    *,
    purpose: str,
    agent_id: str,
    task_name: str = "",          # interactive-scheduled only: surfaces in directive
    task_working_dir: str = "",
    include_memory_summary: bool = True,
    active_tool_names: set[str],  # NEW — needed for the conditional rules block
) -> str:
```

Branches:

**`purpose=="interactive"`:**
- Soul.md (1 KB)
- Identity line: `You are agent '<id>' in the Brain Agent system. <datetime>. <cwd>. <os>.`
- Interactive framing line: proactive (chat) or restrained (project chat — research_mode aware)
- Memory hint (mempalace_query + save_chat_to_memory) — only if those tools are active
- Project context block (when `_thread_local.project` set) — research-mode disciplines if research_mode is on, soft variant otherwise
- Note-editing block (when `note_context` set)
- Team / agent_registry / skills / scheduler listing / MCP listing
- DEFERRED block (lists deferred groups + tool_search hint)
- **Tool rules block** (the conditional 3-block emitter — replaces unconditional `tools.md` injection)
- **Scheduled-task overlay** (when a scheduled task is firing): replaces the proactive framing with the noninteractive directive (no clarifying questions, follow through on every verb, terse log-style closing) + working_dir override. Keeps everything else — same prompt shell, one switched paragraph.

**`purpose=="background_qa"`:**
- NO soul
- Identity line (terse): `You are a headless corpus-query worker for agent '<id>'. <datetime>.`
- Project-memory framing if project active (3-step flow when research_mode, soft variant otherwise)
- DEFERRED block (only if deferred groups have corpus tools — usually empty in this purpose, skipped)
- Tool rules block (typically empty — corpus tools have no rules entries)

**`purpose=="transform"`:**
- Caller passes their own system prompt verbatim. Builder is not called.
- Documented as such — `_build_system_prompt` may raise if called with `purpose=="transform"`.

**`purpose=="memory_summary"`:**
- NO soul
- Identity line: `You are the memory miner for agent '<id>'. <datetime>.`
- Memory-schema rules (drawer naming, when to call save_chat_to_memory, what counts as a fact)
- Tool rules block (empty — none of the memory tools have rules entries)

### Scheduled-task interactive overlay

Scheduled = `purpose="interactive"` plus a flag the caller passes. The overlay's job is to switch:
- "Use tools proactively, ask clarifying questions if unclear" → "NON-INTERACTIVE: no user watching, do not ask questions, follow through on every verb in the task, write_file is a tool call not a description, terse log-style closing."

Implementation: a single `scheduled: bool` parameter on `_build_system_prompt` that re-routes one paragraph. Soul, memory, project context, etc. all preserved — same as a human asking the agent the same question, minus the conversational framing.

### Revert this-turn's tactical patches

After this lands, the following turn's emergency patches go away:
- `_SCHEDULED_TASK_TOOLS = {exa_search, web_fetch, write_file}` — delete; scheduled tasks get the full agent tool set.
- The lean scheduled-mode system prompt I inlined in `_build_system_prompt` — replace with the new unified `interactive` + scheduled-overlay path.
- The strengthened "write_file is a tool call" rule embedded in the lean prompt — moves into the scheduled overlay's NON-INTERACTIVE block (still need it; it's an autonomous-mode rule).
- The `delegation` addition to `deferred_tool_groups` — keeps (this was an honest fix, not an emergency patch).
- The `sidecar_proxy._build_tool_list` local deferral logic — keeps until `resolve_active_tools` lands; then `_build_tool_list` becomes a one-liner that calls `resolve_active_tools(purpose=..., …)`.

---

## File-level plan

### `brain.py` changes

1. **Add `resolve_active_tools()`** at the top of the tool-resolution section (near `_filter_tools` at line ~2106).
2. **Add `_BACKGROUND_QA_TOOLS` + `_MEMORY_SUMMARY_TOOLS` constants** near `_MEMORY_TOOL_NAMES`.
3. **Rename `_build_system_prompt(mode=…)` → `_build_system_prompt(purpose=…, scheduled=…)`**. Four-purpose dispatch. Inside `interactive` branch, the scheduled-overlay swaps one paragraph.
4. **Cache key includes purpose + scheduled flag.**
5. **Delete `_SCHEDULED_TASK_TOOLS`** (the 3-tool hack).
6. **Delete the inline lean scheduled prompt** (moved into the `interactive` branch + scheduled overlay).
7. **Update all `_build_system_prompt` callers** to pass `purpose=` and `active_tool_names=` (tool names come from `resolve_active_tools` output).
8. **Update `_run_delegate`** to:
   - Take an optional `purpose` arg (default `"interactive"` for backward compat).
   - Call `resolve_active_tools(purpose=...)` instead of `_resolve_delegate_tools`.
   - Delete `_resolve_delegate_tools`.
9. **Update warmup path** (brain.py:12842) to call `resolve_active_tools(purpose="interactive", agent_id, mcp_manager=mcp_mgr, is_openai_shape=True)`. Must produce the same wire bytes as a first-turn chat request for KV-prefix matching — verify by diff before merging.
10. **Update settings UI tool-breakdown** (`_get_tool_breakdown`, ~line 2010) to call `resolve_active_tools` for honest "what does the model actually see" reporting. Show one column per purpose.

### `handlers/sidecar_proxy.py` changes

1. `_build_tool_list(allowed)` → `_build_tool_list(purpose, agent_id, discovered)`. Becomes a one-liner calling `engine.resolve_active_tools(purpose=..., agent_id=..., discovered_tools=..., is_openai_shape=False)`.
2. `run_turn(...)` and `run_turn_blocking(...)` gain a `purpose: str` kwarg. Caller decides; default `"interactive"`.

### `handlers/chat.py` changes

1. `_handle_chat` passes `purpose="interactive"` to `run_turn`.
2. Workflow node runs (if they touch chat handler) pass `purpose="transform"` — but workflows currently call `tool_ask_llm` not `chat`, so this may be a no-op here.

### `brain.py:_execute_scheduled` changes

1. Pass `purpose="interactive", scheduled=True` to `_build_system_prompt`.
2. Drop the `if sched_tools == "memory_only":` special case in favor of `purpose="memory_summary"` for `_memory_summary_*` tasks (cleaner — and exposes the memory-summary identity at the prompt-builder level).
3. Drop `sched_tools = False` path (returning empty tools) — if a future schedule wants no tools, that's `purpose="transform"`, which means a single non-streaming call, which is a different scheduler shape entirely. Park as a follow-up if anyone asks.

### `tools.md` changes

1. Rewrite to three named blocks with explicit anchor markers:
   ```
   <!-- @anchor:execute_command -->
   ## Shell command execution
   ...rules...

   <!-- @anchor:exa_search,web_fetch -->
   ## Web research protocol
   ...rules...

   <!-- @anchor:python_exec -->
   ## Python execution
   ...rules...
   ```
2. Add `_render_tool_rules(active_tool_names: set[str]) -> str` helper in brain.py that parses the anchors and returns only the blocks whose anchor tools are all present in `active_tool_names`.
3. Remove "Remote Nodes" + "Context Tools" + "write_file artifact-folder" sections.

### `engine/` / other files

No changes anticipated. `engine/loop.py` resolves tool names via bare-name lookup in `brain.py`'s namespace; once brain.py's resolver is the single source, engine/ inherits.

---

## Validation

Three gates, run in order:

**Gate-PT-1 — Code-level invariants:**
- `grep -rn '_filter_tools\b' brain.py handlers/ engine/` shows only callers, no parallel reimplementations.
- `grep -rn '_resolve_delegate_tools\|_SCHEDULED_TASK_TOOLS\|TOOL_DEFINITIONS\[' brain.py handlers/` returns nothing (the hacks are gone).
- Warmup path's payload bytes match a first-turn chat request bytewise for the same agent. KV-prefix must stay valid.

**Gate-PT-2 — e4b on the unified interactive surface:**
- Run "Mistral AI News" schedule on `gemma-4-e4b-it-4bit`.
- Expected: produces `report.md`, ≥3 KB, ≤120s, ≥5 tool calls.
- This is the load-bearing test. If e4b can't drive the new lean interactive prompt + full agent tool set, the design is wrong and `tools.md` filtering wasn't enough — would need to revisit.

**Gate-PT-3 — Eval re-baseline on Mistral Medium 3.5:**
- Run `eval/run.py --skip-gold --reuse-results …` against the policy eval.
- Bar: brain mean ≥ 0.82 (matches current post-Phase-2 baseline). The point is not to improve the eval — it's to confirm the consolidation doesn't regress it.
- If it drops > 0.05, debug before merging. The likely suspects would be: missing memory hint in the interactive prompt because `mempalace_query` wasn't recognised as active when it should have been; project-context block accidentally omitted.

---

## Out of scope (deferred to follow-ups)

- **Per-schedule tool override.** If a user wants a specific scheduled task to see only `{exa, web_fetch, write_file}`, that's a new schedule-editor field (`tool_groups: ["web"]` overriding agent default). Not in this pass; resurrect if needed.
- **Per-model auto-degradation.** Brain doesn't pick prompt shape based on model size; the user picks the model, the prompt is purpose-driven. If e4b can't drive interactive on a complex task, the answer is "use a bigger model for that task" or "add a per-schedule override" — not silent prompt magic.
- **Phase 4/5 deletions** (`send_message`, `_run_delegate`, middleware suite, LCM, citation validator). This plan only ensures the surviving wire shapes have one resolver each; it doesn't delete the legacy paths yet.

---

## Risk register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Warmup KV-prefix breaks because `resolve_active_tools` produces tools in different order than `send_message` did | Med | High (every warm-pool session pays cold cost) | Sort-by-name is the only stable order. Diff the two payloads byte-wise before merging. |
| e4b survives Gate-PT-2 only because of cherry-picked task; fails on `delete_old_files` or anything that needs `execute_command` | High for non-e4b tasks | Med | Document explicitly: e4b is supported for the canonical research-task shape; not validated for shell-tool workflows. Other small models likely behave similarly. |
| `background_qa` purpose is invoked from a code path I didn't enumerate (some daemon, some hook) | Med | Low | First version raises `NotImplementedError` if `background_qa` is requested without an explicit caller; force every introduction to be deliberate. Loosen after Phase 4 audit. |
| `transform` callers that today inadvertently use `_build_system_prompt(mode="chat")` lose interactive framing they were silently relying on | Low | Med | Audit grep — listed in Phase 4 of SDK_MIGRATION_PLAN. Convert each to caller-supplied prompt; verify output shape unchanged. |
| Trimming `tools.md` removes a rule some model in the wild depends on | Low | Low | The dropped sections aren't observed in traces. Memory note captures the rule; can re-add a block if a regression surfaces. |

---

## Implementation order (single pass)

1. Write `resolve_active_tools` + the two new constants.
2. Rewrite `tools.md` into three anchored blocks; write `_render_tool_rules`.
3. Refactor `_build_system_prompt(mode=…)` → `_build_system_prompt(purpose=…, scheduled=…)`. Move the lean scheduled prompt into the `interactive` + scheduled overlay path.
4. Update `sidecar_proxy._build_tool_list` and `run_turn`/`run_turn_blocking` signatures.
5. Update `_run_delegate` (purpose arg + resolver call); delete `_resolve_delegate_tools`.
6. Update `_execute_scheduled` to use `purpose="interactive", scheduled=True` (or `purpose="memory_summary"` for memory-summary tasks).
7. Update warmup path; diff-check KV-prefix match.
8. Update settings UI tool-breakdown.
9. Restart, run Gate-PT-1, Gate-PT-2, Gate-PT-3 in sequence.
10. Commit. Tag the SDK migration changelog with a new entry referencing this plan.

Estimated effort: 4–6 hours of focused work; a half-day with eval gate.

---

## Open architectural questions

1. **Should `background_qa` exist before a real caller needs it?** Today no code path uses it (the v8.17 user-profile maintenance daemon and KG-extraction are `transform`). Adding the purpose now is defensive; alternative is to defer it until Phase 4's first headless corpus caller surfaces. **Recommendation:** define the constant + branch, but raise NotImplementedError if invoked, until the first caller materialises.

2. **`active_tool_names` plumbing.** `_build_system_prompt` needs to know the active tool set so it can emit the right `tools.md` blocks — but the tool set is built by `resolve_active_tools` which is downstream. Either:
   - (a) Caller calls both: builds tools first, passes names to prompt builder.
   - (b) Prompt builder calls `resolve_active_tools` internally.
   **Recommendation:** (a). Keeps the prompt builder pure-ish; caller knows which tools they're sending anyway.

3. **Cache invalidation for tools.md changes.** System prompt is cached (60s TTL keyed on session + purpose). If someone edits `tools.md` on disk, the cache won't reflect it until TTL expiry. **Recommendation:** ignore — TTL is short, edits to `tools.md` are rare, and the rule blocks are small enough to live as Python constants once we stabilise (a follow-up after this plan lands).

---

End of plan. Next session: read this, get sign-off, execute steps 1–10. Validate via the three gates. If any of them fail, revert and re-plan rather than patching forward.

---

# Post-implementation status (2026-05-14, commit c8ab71c)

Steps 1–10 landed. Gate-PT-1 passed. Gate-PT-2 went through a course-correction
(the plan's "scheduled = full interactive surface" bet regressed gemma-4-e4b
from 100% to 20% pass; surfaced and addressed via Phases A/B below). Final
Gate-PT-2 result: 2/3 gemma-4-e4b, 1/1 gemma-4-26B, 3/3 Mistral Medium 3.5.
Gate-PT-3 deferred — user opted to skip the eval quota spend.

## What landed beyond the original plan

- **Phase A — `research_minimal` purpose (tool-flag-driven).** Each tool opts
  in via `minimal: True` + `minimal_role` on its `TOOL_DEFINITIONS` entry.
  Currently flagged: `exa_search`, `web_fetch`, `write_file`. The system
  prompt is composed dynamically from those fragments; Brain's emitted prompt
  for `research_minimal` is byte-identical to
  `eval/sdk_harness/system_prompt_scheduler.md`.
- **Phase B — `schedules.tool_profile` per-task field + UI dropdown.** Empty
  → `research_minimal` (default for scheduled tasks); `interactive` opts
  back into the full agent surface; values validated against
  `_VALID_TOOL_PROFILES`.
- **Mistral fix.** Sidecar gained `disable_parallel_tool_use` plumbing
  (maps to Anthropic SDK's `tool_choice.disable_parallel_tool_use=True`);
  `_execute_scheduled` reads the model's existing `parallel_tool_calls`
  flag and forwards. Brings Mistral on the canonical task from 1/3 to 3/3.
  Causality not fully proven (n=3, top_p was also changed); see Open
  Architectural Question §4 below.
- **Sidecar gained a `stream: bool` knob.** Wired but no caller currently
  sets it; left as a future per-model/per-schedule toggle.

## Proposed order for next session

(Recommended sequence; each item is one commit's worth.)

1. **Memory hygiene** *(low cost, high session-knowledge ROI)*. Write the
   memory notes for this work:
   - `project_research_minimal_purpose.md` — Phase A/B design, the
     tool-flag-driven (`minimal: True` + `minimal_role`) approach, Gate-PT-2
     pass-rate table per model. Reference commit `c8ab71c`.
   - `project_mistral_disable_parallel.md` — symptoms, fix, the Mistral
     stochastic write_file failure, what's proven vs unproven, link to
     "Investigation §4" below.
   - `feedback_phase_a_then_validate.md` — the user's "Phase A first, then
     validate" pacing call caught the e4b regression before Phase B added
     UI scope; future scope-creep decisions should follow the same pattern.
   - Update `project_token_optimizations_validated.md` to reference the
     new resolver (the file still describes the pre-unification per-session
     read_document cache and project preamble).

2. **Code cleanup** *(one commit, ~30 min)*. All hygiene from the
   investigation, none of which materially changes behavior:
   - Dedent the `if True:  # purpose=='interactive' …` block at
     `brain.py:~25125`. ~300-line indent reduction.
   - Remove the `scheduled: bool` kwarg from `_build_system_prompt`. The
     original "scheduled overlay" branch was deleted during Phase A; the
     parameter is dead.
   - Decide on `background_qa`. Either delete the NotImplementedError stub
     (until a real caller materialises) or leave it in place with a
     `# pending: Phase 4 audit` comment.
   - Decide on the sidecar `stream` knob. Either expose it (per-model
     config + UI) or remove the unused branch.

3. **Eval runner JSON resilience**. `eval/run.py` and ad-hoc monitor scripts
   choked on `\X` escapes in `schedule_history.result`. Root-cause:
   `Scheduler.complete_execution` (and possibly other writers) aren't
   running their `result` payload through `json.dumps`, so embedded
   backslashes from tool output land in the row verbatim. Fix at the source
   so every downstream parser doesn't need a sanitizer.

4. **Gate-PT-3 (eval re-baseline)** *(burns quota — schedule when fresh).*
   Run `eval/run.py --skip-gold --reuse-results <baseline>` against the
   policy eval to confirm the unification didn't regress Mistral Medium 3.5.
   Bar: brain mean ≥ 0.82 (post-Phase-2 baseline). Resume from
   `eval/results/20260514T112200_disc-none_phase2-braintools-clean`
   (brain_mean=0.818 was the most recent baseline before this work).

5. **Mistral causality check** *(only if you want firm evidence — optional)*.
   Three sub-experiments to disentangle what actually fixed Mistral:
   - 10-run sample with `top_p=0.85` only (no disable_parallel) to see if
     top_p alone explains the fix.
   - 10-run sample with `disable_parallel_tool_use=True` only (no
     top_p change) to isolate that variable.
   - Wireshark / mitmproxy on the SDK → CLIProxyAPI hop to confirm
     CLIProxyAPI actually forwards `tool_choice.disable_parallel_tool_use`
     to Mistral's underlying API (it may be silently dropping the field
     and the fix is purely from `top_p` and/or noise).

## Out of scope (still deferred from original plan)

- Per-model auto-degradation — plan rejects; user picks model.
- Phase 4/5 deletions (`send_message`, `_run_delegate`, middleware suite,
  LCM, citation validator). The unification ensured each surviving wire
  shape has one resolver; deleting legacy paths is a separate effort.

## Pre-existing parallel work (untouched by this session)

- `SDK_MIGRATION_PLAN.md` Phases 2–5 — the broader SDK migration the
  checkpoint commit `1d60c47` captured mid-flight. The unification commit
  `c8ab71c` landed *on top* of Phase 1–3 state without finishing 4–5.

## Open architectural questions raised by the implementation

4. **Did `tool_choice.disable_parallel_tool_use` actually disable parallel
   tool calls on the Mistral wire?** Confirmed end-to-end through Brain →
   sidecar → Anthropic SDK (debug log showed the kwarg arrived). Unknown
   whether CLIProxyAPI honors the Anthropic-shape field when proxying to
   Mistral's actual API. The "9 tool calls at +0s" observation in
   passing runs (837, 838) suggests parallel batching may still have
   happened upstream. The 3/3 pass rate is consistent with the fix
   working AND with it being silently dropped (could be top_p alone, or
   noise — see §5 above). Strongest defensible claim: the wiring is
   correct on Brain's side; provider behavior is opaque.

5. **`research_minimal` is currently the scheduler default. Should it
   ever apply elsewhere?** Today only `_execute_scheduled` routes through
   `research_minimal`. The `tool_profile` field gives per-schedule
   override, but interactive chat, `_run_delegate`, and `delegate_task`
   all stay on `interactive`. That's intentional (chat needs the full
   agent surface), but worth a comment in `_build_system_prompt`'s
   docstring so the next implementer sees it.
