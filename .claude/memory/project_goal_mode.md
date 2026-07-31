---
name: project_goal_mode
description: "v9.256.0 Goal-Modus — per-session/per-task goal + post-turn judge loop; key seams, invariants, and verified E2E behavior"
metadata: 
  node_type: memory
  type: project
  originSessionId: a9d62c3e-2055-47c7-910d-9150b44cc379
---

**Goal-Modus shipped v9.256.0 (2026-07-02, commit a712c012, live-verified E2E).** A goal is set per session (composer 🎯 popover / `/goal` in termchat, manage action `goal`) or per scheduled task (`schedules.goal`); after every turn `engine/goal_judge.py::judge()` (background_call + `forced_tool goal_verdict`, GDPR-gated, `cost_purpose=goal_judge`, model `config.goal_judge_model` → server default) checks the reply; unmet → continue-instruction persisted as VISIBLE user msg (`metadata.goal_continue`) and the turn re-runs.

**Why:** user wanted Claude-Code-`/goal`-style "keep adapting until really done" across chat, terminal chat, and scheduled tasks, with GUI visibility + admin knobs. User decisions: auto-end on fulfilled, visible iteration turns, judged on every send while active.

**How to apply (invariants when touching the loop):**
- chat.py worker: turn body wrapped in `while True`; ONE terminal `done`; loop repeats ONLY via the explicit `continue` in the judge step. `_msg_count_before` re-snapshotted before each continue-msg (cancel rolls back only the current iteration); per-iteration reset of `_partial_*`/created_files/`_turn_created_files`/streaming_text; Websuche fetch cached from pass 1 (`_goal_web_cache`); aggregate-cost fallback logs DELTAS (`_agg_cost_logged`); judge NEVER retried, turn error/empty reply → break unjudged; deep-research turns exempt.
- Judge `impossible=true` (legit refusal/unreachable) ends loop → `capped` — the citation-re-round lesson (never force refusals into continuation).
- Scheduler: judge on the RAW pseudonymised reply, append assistant+user in token space, only final result_text de-anonymised; ≥30s timeout-budget guard; `schedule_history.goal_iterations`.
- Caps: session/task override → `composer_defaults.goal_max_iterations` (default 5) → `GOAL_ITER_HARD_CAP=10`; kill switch `composer_defaults.goal_mode_enabled` (hides button AND disables server loop).
- SSE: `goal_judge_start`/`goal_verdict`/`goal_continue{assistant_text,text_rounds}` + `done.goal`; client `goal_continue` closes the live bubble segment-aware (mirrors done's rule) — [[project_inprocess_openai_loop]].

The latent `_tcCmdCaveman` bug (POSTed nonexistent `/v1/sessions/<sid>/manage` with `value` instead of `mode` — termchat `/caveman` never persisted) was FIXED in v9.257.0 (b9610914), the leftovers release that also shipped cache-read-token surfacing (deep_research → research_runs → report footers + per-model `cost_cache_read` field) and the project-sync "N unverändert" progress label.


v9.267.0 (8c1b3b25, 2026-07-02): Ziel-Prüfungs-Card body now shows judge `reasoning` + (on verdict=active) the `continue_instruction` — `goal_verdict` SSE gained `instruction` field (reasoning was always emitted but client-dropped); plumbing chat_send.js → ChatTurnControl.goalVerdict(+2 params) → `_tcActivityCard` goal_judge body (`.act-tc-text` is pre-wrap). Live-E2E-verified via SSE capture (NB: Brain SSE frames are `event: <name>` + `data: {...}` — the data JSON has NO type field, a naive data-only parser never sees `done`).

v9.258.0 (cfd8d26e, 2026-07-02): goal activity is ALSO mirrored client-side into `chat.turnActivity` (ChatTurnControl.goalJudgeStart/goalVerdict/goalRoundStart) and rendered as cards in the right panel's Aktivität tab (`_turnControlEntries`/`_tcActivityCard` in panels_background.js), incl. a synthetic 'Ziel-Prüfung geplant' future card while goal active + streaming. Same release moved btw to its own right-panel tab (chat.btwThread) and inject display to Aktivität cards (composer chip + tc-injected-note removed). Stale-open entries self-normalise at render time via chat.streaming — no turn-end hook.
