# Refine Enhancement — Two-Tier (Polish / Engineer) Design

Status: **PROPOSAL — awaiting approval before any code change.**
Author: design pass 2026-06-05.
Scope decided with user: chat composer + scheduled-task prompts + agent soul.md. Profile fields left as-is. Two-tier (keep Polish, add Engineer). Validate with an eval harness before shipping.

---

## 1. Problem & framing

Today `POST /v1/refine` (`handlers/admin_artifacts.py:_handle_refine`) is a **conservative cleaner**: fix grammar, keep intent verbatim, output only the rewrite. Three purposes: `chat_prompt` (default), `profile_field`, `soul`. It is GDPR-gated, caveman-aware, model-selectable, one round, no tools, no system prompt (rules ride in the user message).

We want to *enhance* our own LLM prompt refinement — NOT adopt the external `prompt-master` skill wholesale. The bulk of prompt-master's value is **routing to 30+ external tools** (Midjourney/Cursor/Devin…), which is irrelevant here: our refine targets **our own agent**, on a **known model**, with a **known resolved toolset**, in a **known project**. We have *more* context than prompt-master ever gets — we're just not feeding it in.

So the enhancement is: **add an opt-in Engineer tier that is grounded in context we already hold, and borrows prompt-master's diagnostic discipline (not its tool-routing).**

What we borrow from prompt-master (adapted, attributed in code comment):
- **Diagnostic checklist** (vague verb, no success criteria, no scope lock, two-tasks-in-one, no stop conditions) → drives the Engineer rewrite.
- **Agentic discipline** (scope locks, stop conditions, "stop and ask before destructive actions") → exactly right for **scheduled-task** prompts that run unattended.
- **Memory block** idea (carry-forward context) → we already have session context; reuse it.

What we explicitly DO NOT borrow:
- External-tool routing tables (30+ tools). Target is always our agent.
- Static model-tip lists (they drift). Instead we derive light model hints from our own `MODEL_PROFILES` / known-model config so they can't go stale.

---

## 2. Two-tier model

Add a `tier` parameter to `/v1/refine`: `"polish"` (default, = today's behavior, unchanged) and `"engineer"` (new). `tier` composes with the existing `purpose` (chat_prompt / soul / profile_field) and `caveman`.

| purpose × tier | Polish (today) | Engineer (new) |
|---|---|---|
| **chat_prompt** | grammar/clarity, intent verbatim | intent-extract + restructure into a grounded, specific prompt: add role if complex, make success criteria explicit, scope to named files/project if implied, fix the diagnostic patterns. Still a single clean paste-able prompt — NO headings/commentary in output. |
| **soul** | strict polish, preserve voice/structure | suggest structural improvements: tighten identity, surface missing stop-conditions/guardrails, dedupe rules — still second-person, still preserves name/role/tools/code blocks. |
| **profile_field** | (unchanged) | **N/A** — profile fields stay Polish-only. Engineer is meaningless for a bio. Server falls back to polish if `tier=engineer` + `purpose=profile_field`. |

Scheduled-task prompts use `purpose=chat_prompt` today; Engineer mode for them additionally injects the **agentic discipline** sub-block (scope/stop-conditions/success-criteria) because they run unattended. We detect this via a new explicit `purpose=scheduled_task` (cleaner than overloading chat_prompt) — see §4.

---

## 3. Grounding context we add (Engineer tier only)

All already available server-side; we just assemble it:

1. **Active model** — `refine_model` is already resolved. Map it through a NEW tiny helper `_refine_model_hint(model)` that returns 1–3 light, non-drifting tips derived from our own config:
   - reasoning-native (from model's `inference.thinking_level`/known flags) → "do not add step-by-step scaffolding."
   - local/open-weight (from `is_model_local`) → "keep flat and explicit."
   - otherwise → no hint.
   This reads OUR model config, so it never drifts to a wrong external claim.
2. **Resolved tool list** — `resolve_active_tools(purpose='interactive', agent_id=agent_id)` gives the exact tools the agent will see. Engineer mode passes the **tool NAMES only** (not full schemas) so the rewrite can say e.g. "read the file with read_document first" when relevant. Names only = cheap, no schema bloat.
3. **Project instructions** — if `project` set, include the project's `instructions` (first ~400 chars) so the rewrite respects project discipline (e.g. research-mode citation rules).
4. **Session context** — reuse the existing last-5-messages + soul-summary block (already built for chat_prompt).

All of this rides in the user message (same as today — refine doesn't use `_build_system_prompt`).

---

## 4. Concrete changes (files + shape)

### 4.1 `handlers/admin_artifacts.py` — `_handle_refine`
- Parse `tier = (body.get("tier") or "polish").strip().lower()`; clamp to {polish, engineer}.
- Accept new `purpose` value `"scheduled_task"` (treated like chat_prompt for context-building, but Engineer adds the agentic sub-block).
- When `tier == "engineer"` and purpose ∈ {chat_prompt, scheduled_task, soul}: build the new Engineer instructions (below) and assemble the grounding block (§3). Otherwise: unchanged Polish path.
- Engineer chat output must still be **paste-able**: the instructions keep "Output ONLY the rewritten prompt, no headings/commentary" — Engineer restructures *content*, not output shape. (A multi-section prompt with `<context>`/`<task>` XML is allowed for complex cases, but no meta-commentary.)
- GDPR gate: extend the `purpose=` label to `refine_{purpose}_{tier}` so audits distinguish tiers. Grounding block (tools/project/session) flows through the SAME single GDPR scan already in place — no new seam.
- Add the model-hint + tool-name assembly behind `if tier == "engineer":` so Polish path cost is unchanged (no extra `resolve_active_tools` call on the hot one-click path).

### 4.2 New Engineer prompts (verbatim drafts — to be reviewed)

**Engineer / chat_prompt + scheduled_task (shared core):**
```
You are a PROMPT ENGINEER for an AI assistant. The user gives you a rough
draft of what they want the assistant to do. Rewrite it into a single,
production-ready prompt that gets the right result on the first try.

CRITICAL RULES:
- Output ONLY the rewritten prompt. No headings, no commentary, no "here is".
- Preserve the user's actual intent and language. Do NOT answer the request.
- Make the task a precise operation (replace vague verbs).
- If success is checkable, state it ("Done when: ...").
- If the request implies files/scope, name them; do not widen scope.
- If two unrelated tasks are mixed, keep the primary one and note the split
  in ONE trailing line "(Second task: ...)" — do not silently drop it.
- Put the most important constraint first.
- Use the strongest signal words (MUST/NEVER over should/avoid).
- Keep it as short as it can be while load-bearing. Every word earns its place.
<model_hint>           # injected: e.g. "Target model reasons internally — do
                       #   NOT add step-by-step scaffolding."
<tool_hint>            # injected: "Available tools: read_document, web_fetch,
                       #   ... — reference them by name when the task needs one."
<project_hint>         # injected: project instructions excerpt, if any
<context_block>        # existing: soul summary + last 5 msgs
```

**Engineer / scheduled_task — APPEND this sub-block (unattended discipline):**
```
This prompt runs UNATTENDED on a schedule. Additionally:
- State a clear stop condition — when is the task complete?
- Scope filesystem/command actions explicitly; never imply "the whole thing".
- Add "Stop and report instead of guessing if information is missing."
```

**Engineer / soul:**
```
You are an EDITOR for an AI agent's soul.md (its system prompt, second person:
"You are ...", "Your job is ..."). Improve it structurally without changing
who the agent is.

CRITICAL RULES:
- Output ONLY the improved soul. No commentary.
- Keep second-person voice. Keep the agent's name, role, and listed tools.
- You MAY: tighten wording, remove redundancy, group related rules, surface a
  missing stop-condition/guardrail that the existing rules clearly imply.
- You MUST NOT: invent new capabilities, tools, or behaviours the user didn't
  imply; remove an existing rule; change tone (terse/playful/formal).
- Preserve all Markdown structure and code/inline `code` exactly.
- If already tight, return it unchanged.
```

### 4.3 New helper `_refine_model_hint(model)` 
Lives in `handlers/admin_artifacts.py` (handler-local; no new engine surface). Reads `engine.is_model_local(model)` and the model's known thinking/reasoning flag from config; returns a short string or "". Pure read, no LLM.

### 4.4 Frontend — three surfaces get a tier toggle

Shared small control (mirror the existing `_refineCavemanButton` pattern in `web/js/init.js`):
- **Chat composer** (`init.js:148`): add a Polish/Engineer segmented toggle next to the refine button; pass `tier`. **Default Polish.** Persist last choice in localStorage `refine-tier:chat`.
- **Scheduled-task prompt** (`settings_schedule.js:31`): same toggle; when Engineer, send `purpose:'scheduled_task'`. **Default Polish** (per locked decision — opt-in everywhere). `refine-tier:sched`.
- **Agent soul editor** (`settings_agent.js:1368`): same toggle; `purpose:'soul'`, `tier`. **Default Polish.** `refine-tier:soul`.
- **Profile fields** (`user_admin.js`): unchanged. No toggle.

JS gate: `cd web/js && ./js_gate.sh` after edits (ESLint no-undef + net-globals count + Playwright smoke). New globals: keep count invariant — relocate/add carefully.

### 4.5 brain-agent-guide skill (standing rule)
`/v1/refine` gains a `tier` param + new `purpose=scheduled_task` → update `01-api.md` (endpoint), `06-user-manual.md` (German UI walkthrough of the new toggle). Bump version in both places + CLAUDE.md changelog. Python-compile brain.py after CHANGELOG edit (per feedback memory).

---

## 5. Validation — `eval/refine_eval.py` (build BEFORE shipping the UI)

Mirror the existing `eval/` discipline (Opus-judged, before/after).

- **Corpus**: ~15 real-ish draft prompts across the three purposes — terse chat asks ("fix auth bug"), vague scheduled tasks ("summarize my emails every morning"), a rough soul draft. Hand-authored, checked in.
- **Runs**: for each draft, call refine OLD (polish) vs NEW (engineer) — in-process against the handler logic or via the live endpoint with both tiers.
- **Judge** (background model or Opus via existing harness): score each output 0–1 on four axes:
  - **clarity** — is the rewrite unambiguous?
  - **intent_preserved** — did it keep the user's actual goal (no drift/hallucinated scope)? *Hard gate: if intent drifted, the whole sample fails regardless of other scores.*
  - **actionability** — would the agent produce the right result first try?
  - **token_cost** — penalize bloat; load-bearing words only.
- **Pass bar**: Engineer ≥ Polish on clarity+actionability, with **zero** intent_preserved regressions. If Engineer inflates tokens >1.5× with no actionability gain → fail, tighten the prompt.
- Output a small table (like other eval runs) + save to memory per repo habit.

The intent-drift hard gate is the safety valve: Engineer is allowed to restructure, NEVER to invent scope the user didn't ask for (the exact failure mode of aggressive refiners, and of prompt-master's "Make it a full engineer" path we declined).

---

## 6. Risks / non-goals

- **Risk: Engineer over-engineers** (the Opus-4.x failure mode the skill itself warns about). Mitigated by: opt-in (Polish stays default on chat), the "do not widen scope" rule, and the eval's intent-drift hard gate.
- **Risk: cost on hot path.** Mitigated: all extra context assembly is behind `if tier=='engineer'`; Polish one-click is byte-for-byte unchanged.
- **Non-goal: external-tool routing.** We are not building Midjourney/Cursor prompt modes. If ever wanted, that's a separate feature.
- **Non-goal: warm-cache for refine.** Out of scope; refine stays a cold background call.

---

## 7. Build order (once approved)

1. `eval/refine_eval.py` + corpus, run it against CURRENT refine to get the Polish baseline numbers.
2. Backend: `tier` param, `scheduled_task` purpose, Engineer prompts, `_refine_model_hint`, grounding assembly, GDPR label. Compile + restart, confirm version.
3. Re-run eval (Polish vs Engineer); tune prompts until pass bar met.
4. Frontend toggles on the 3 surfaces; `js_gate.sh`.
5. brain-agent-guide skill update + version bumps + CHANGELOG; commit to main (per repo convention).

---

## 8. Decisions (locked 2026-06-05)
- **Default tier: Polish on ALL three surfaces** (chat, scheduled, soul). Engineer is purely opt-in per click everywhere. Most conservative — no surface changes its current default behavior; the toggle just exposes the new tier. (`refine-tier:<surface>` localStorage still remembers the user's last choice per surface.)
- **Eval judge: configured background model** (`_background_model_default()`), not Opus. Cheaper, no quota; sufficient for relative old-vs-new comparison. Note in the eval output that scores are background-judged (lower authority than Opus-gold).
- **Engineer chat mode MAY emit `<context>/<task>/<constraints>` XML** for genuinely complex/multi-section drafts; stays plain prose for simple ones. Add to the Engineer chat prompt: "For simple requests output plain prose. For complex multi-part requests you MAY use <context>/<task>/<constraints>/<output_format> XML sections — but still NO commentary outside the prompt."
