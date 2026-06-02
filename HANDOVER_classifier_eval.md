# Handover — auto-route classifier verification + MemPalace robustness

**Date:** 2026-06-02 · **Branch:** `main` · **Working tree:** clean · **Server:** 9.60.0 (running, healthy)

This session shipped a MemPalace corruption fix + removed memdash + ran the policy
eval. One thing remains UNVERIFIED: the LLM classifier / tool-deferral path never
fired during the eval, and I ran out of clean ways to debug it. Pick up there.

---

## TL;DR — what's done vs. open

| Item | State |
|---|---|
| MemPalace HNSW auto-recovery (self-heal on query) | ✅ DONE, verified live, committed `e023a76` |
| memdash dashboard removed | ✅ DONE, server boots clean, `/memdash` 404s, committed `e023a76` |
| Policy eval clean run: **brain 0.75 vs gold 0.93 (Δ −0.18, 0 errors)** | ✅ DONE — validates the corruption fix |
| v9.59.0 classifier tool **defer/un-defer** (committed `a1c4553`) | ⚠️ CODE SHIPPED, **NOT exercised** end-to-end |
| `classifier_mode = llm` directive | ✅ config set + loaded, ❌ but structured classifier **never runs** in a turn |
| Re-run eval with llm classifier + tool-gating measured | ❌ TODO (blocked on the bug below) |
| Benchmark mistral-small vs medium (so auto can pick small on speed tiebreak) | ❌ TODO (optional, see "user intent" below) |

**Nothing uncommitted.** All committed work is verified EXCEPT the classifier path.

---

## THE OPEN BUG (start here)

**Symptom:** with `auto_route.classifier_mode = "llm"` confirmed loaded, every
`model=auto` turn produces `auto_route.task_types = None` and
`tool_gating.applied = None`. So the structured classifier output never reaches
routing, and the v9.59.0 tool defer/un-defer never fires.

**What I ruled OUT (don't re-investigate these):**
1. ✅ `classifier_mode=llm` IS loaded. Read it at `GET /v1/services` →
   `.server.auto_route_classifier_mode == 'llm'` (NOTE: fields are nested under
   `.server`, NOT top-level — I wasted time on this; `/v1/services/server` is
   POST-only and returns `{error}` on GET).
2. ✅ The classifier MODEL works. `chat_summary_model = CLIProxyAPI/mistral-small-latest`.
   A direct `/chat/completions` call to it with the classify system prompt returned
   valid JSON `{"task_types":["research"],"tools":["web"],"complexity":"low",...}`
   in 0.9s. So the model + provider are fine.
3. ✅ `background_call` signature accepts all kwargs the classifier passes
   (`messages, model, system_prompt, purpose="transform", max_tokens, max_rounds, timeout_s`).

**What I FOUND (the live lead):** I added a temp `print("[classify-debug]...")` at
`brain.py` right after the `sidecar_proxy.background_call(...)` inside
`classify_task_structured` (~line 10710), restarted, fired a turn — and **NO
debug line appeared**. That means `classify_task_structured` is **never called**.
So the failure is UPSTREAM of it. (Debug line already removed; tree is clean.)

CAVEAT: my turn-firing was flaky — the `curl -m 8 ... &` I used likely got killed
before the turn ran (no `/v1/chat` / `sidecar-proxy turn=` appeared in the log
either). So "no debug line" is SUSPECT — the turn may not have executed at all.
**Re-confirm with a reliable turn before trusting that conclusion.**

**The call chain to instrument (in order):**
```
handlers/chat.py:3369  resolve_auto_model_for_task({"model":"auto"}, message, ...)   # is this reached?
  brain.py:11187       resolve_auto_model_for_task(...)
    brain.py:11212       analysis = resolve_task_analysis(message)                   # does it return task_types?
      brain.py:10851       resolve_task_analysis: reads mode from server.server_config["auto_route"]["classifier_mode"]
        brain.py:10866-10872  mode = ... (import server as _srv_mod; getattr(_srv_mod,"server_config"))  # <-- SUSPECT
        brain.py:10878       if mode == "llm": classify_task_structured(message)     # never reached per debug
```

**PRIME SUSPECT — the dual-module `server_config` split.** `resolve_task_analysis`
does `import server as _srv_mod; _sc = getattr(_srv_mod,"server_config",None)`.
Under launchd the entry point is the `__main__` module; `import server` may get a
SECOND `server` module instance whose `server_config` is the BARE module-level
default (`brain.py`-adjacent `server.py:563`, only `{api_key,base_url,default_model,
max_context}`) — i.e. **no `auto_route` key** → `mode` defaults to `"keywords"` →
keyword path → no `task_types`. This is the EXACT footgun the v9.45.1 changelog
documents (`run_background_task` "session not loaded" — `import server` made a
second empty instance; fixed via `sys.modules['__main__']`).

BUT note the tension: GDPR background reads (brain.py:9600) use the SAME
`import server as _srv_mod` pattern and apparently work — so either they tolerate
the empty config, or `_inject_server_globals` (server.py:983, runs at import) has
already populated the `server` instance's globals by the time turns run. Resolve
this empirically, don't assume.

### Decisive next step (clean, harness-based — NOT curl)
1. Add ONE debug line in `resolve_task_analysis` right after `mode` is computed:
   `print(f"[ta-debug] mode={mode!r} sc_has_auto_route={'auto_route' in (_sc or {})}", flush=True)`
   (and optionally log `id(_srv_mod)` + whether `_srv_mod is sys.modules.get('__main__')`).
2. `python3 -m py_compile brain.py` then restart:
   `launchctl kickstart -k gui/$(id -u)/com.brain-agent.server`
3. Fire ONE turn through the RELIABLE path — the eval harness, not curl:
   `BRAIN_USER=admin BRAIN_PASS=admin python3 eval/run.py --config eval/config.json
    --brain-model auto --skip-gold --reuse-results eval/results/20260529T150952_disc-none
    --only P1_password_length --no-judge` (the `eval/run.py` SSE drain reliably
   completes turns; my hand-rolled urllib client kept timing out).
4. `grep "ta-debug" ~/.brain-agent/server.error.log` → read `mode` + `sc_has_auto_route`.
   - If `mode='keywords'` / `sc_has_auto_route=False` → confirmed dual-module bug.
     FIX: make `resolve_task_analysis` (and any sibling reading `server_config`)
     resolve the singleton like `_inject_server_globals` does:
     `_srv = sys.modules.get('__main__') or sys.modules.get('server')`. Consider a
     shared helper `_server_config()` in brain to fix ALL `import server as _srv_mod`
     readers at once (grep: brain.py:2633,2649,9600,9684,10640,10869,11051).
   - If `mode='llm'` → the bug is downstream; move the debug into
     `classify_task_structured` and check the `background_call` result/error.
5. Remove debug, restart, re-verify with a real turn that `auto_route.task_types`
   is populated AND `tool_gating.applied=true` with `memory` in `kept_groups` for a
   policy question.

### After the fix — the actual verification the user wants
Re-run the full eval and inspect, per question, `brain.json → auto_route`:
- `task_types` populated (classifier ran)
- `tool_gating.applied == true`, `kept_groups` includes the needed groups
- **`memory` is in `kept_groups`** (user's explicit expectation: "all relevant
  tools like memory etc. are given to the LLM"). NOTE: in my direct classifier
  probe, mistral-small returned `tools:["web"]` for a POLICY question — it should
  be `memory`. If that recurs, it's a **classifier PROMPT-quality issue** (the
  system prompt doesn't steer "policy/document lookup → memory"). That's a
  separate, prompt-fixable problem from the plumbing bug above — flag both.

---

## How to run the policy eval (reference — this part WORKS)

```bash
cd /Users/alexander/Documents/dev/cctest
BRAIN_USER=admin BRAIN_PASS=admin python3 eval/run.py \
  --config eval/config.json \
  --brain-model auto \
  --skip-gold --reuse-results eval/results/20260529T150952_disc-none \
  --disciplines none
```
- **DO NOT pass `--judge-model`** — it forces `judge_provider="claude_code"`
  (eval/run.py:530-531), which returns `[CLAUDE_CODE_ERROR]` and fails every judge.
  Omitting it uses `config.json` `judge.provider="mistral"` → direct API. (This bit
  me on the first run; the second run omitted it and judged fine inline.)
- Gold reuse source with 15 gold files: `eval/results/20260529T150952_disc-none`.
- Project `KG-Real-Policies` (wing `project__f201b24ff6a2`, 1474 policy drawers) must
  be present + synced (it is).
- Re-judge an existing run standalone:
  `python3 eval/judge_mistral.py <results_dir> --model CLIProxyAPI/mistral-medium-3.5`
- **Latest clean run:** `eval/results/20260602T160202_disc-none/` — brain 0.75, gold
  0.93, all 15 retrieved, 0 errors. Low outliers: R3_kryptographie 0.45 (retrieval
  miss), C1_ki_policy_bullets 0.48 (false refusal). Gap is the usual
  citation/precision discipline, NOT retrieval.

---

## User intent / decisions locked this session

- **"all queries on mistral-small"** — NOT achievable via `auto`: the router ranks
  medium over small (medium is the only cloud reasoning model + higher priority; no
  benchmark data exists so it falls to the tier heuristic, which ignores speed).
  User accepted "run auto, accept its picks" → it picked mistral-medium for all 15.
  User's rationale for wanting small: "similar capabilities but faster." To make
  auto pick small on a tie, **benchmark both** (Settings → Models → "Benchmark: alle
  aktivierten") so the `tps` tiebreaker engages. Optional follow-up.
- **classifier_mode = llm** — user directive (set; pending the bug fix to take effect).
- **memdash = removed** — user: "useless, brings nothing." Done.
- **Tool optimization NEVER for warmup/local models** — including follow-up turns.
  Already enforced (`model_maintains_warm_prefix` guard in `classifier_tool_deferral`
  AND the every-turn classification is skipped for those models). Don't regress.
- **Defer, not exclude** (v9.59.0): classifier gating marks un-needed groups
  DEFERRED (still `tool_search`-discoverable), un-defers needed groups; recoverable
  on misclassification. Never EXCLUDE for the classifier path.

---

## MemPalace corruption — context (mostly resolved, here for completeness)

- **Cause:** chromadb 0.6.3 HNSW only persists on periodic flush; a write
  interrupted before flush (unclean shutdown) or a bulk delete racing an upsert
  wedges the segment (sqlite survives). MemPalace auto-quarantines as `.drift-*`
  (the "sqlite newer than HNSW" check). Likely historical trigger: memdash's
  interactive writes (first drift 05-29, day after memdash 05-28) — now removed.
- **Fix (shipped):** `engine/mempalace_glue.py` `tool_mempalace_query` self-heals —
  on an HNSW-corruption error it calls `mempalace.repair.rebuild_index` (lock-
  serialized, 120s cooldown) + retries once. Verified live (`Staged N/12955` in log).
- **CAVEAT — rebuild BLOCKS the triggering turn** (~minutes for ~13k drawers). If a
  turn times out mid-eval, this is why. A future refinement: rebuild async +
  return "retry shortly" instead of blocking. (Not done.)
- **Manual repair recipe** (server stopped): see memory
  `project_mempalace_corruption_memdash_cause.md`. Key: `launchctl bootout` first;
  rebuild FOREGROUND (it holds `mine_palace_lock`); if a prior rebuild left sqlite=0,
  restore from `chroma.sqlite3.backup`.
- **Prevention NOT done** (optional follow-ups): (1) gate any remaining direct chroma
  writers behind the palace lock; (2) flush HNSW on graceful shutdown.
- Live mempalace venv: `~/.mempalace/venv/lib/python3.14/site-packages` (NOT system
  python; `config.json → mempalace.venv_site_packages`). Palace healthy now: 12,817
  drawers, `diverged=False`.

---

## Environment quick-ref

- Server: launchd `com.brain-agent.server`, port 8420, python `/opt/homebrew/bin/python3`,
  cwd `/Users/alexander/Documents/dev/cctest`.
- Restart: `launchctl kickstart -k gui/$(id -u)/com.brain-agent.server`
  (clean: `bootout` then `bootstrap ... ~/Library/LaunchAgents/com.brain-agent.server.plist`).
- Logs: `~/.brain-agent/server.error.log` (launchd routes fd1+fd2 here, NOT server.log).
- After any brain.py edit: `python3 -m py_compile brain.py` (memory:
  feedback_compile_check_brain_py — a stray quote in German CHANGELOG prose
  crash-looped the server). After restart confirm `/v1/status` version == brain.VERSION.
- JS edits: `cd web/js && ./js_gate.sh` (needs dev server up for smoke).
- Skill version: bump BOTH `SKILL.md` (skill_version + brain_agent_version) per
  feedback_version_two_places when touching skill files. Current: 1.29.5 / 9.60.0.

## Commits this session (all on main, pushed? CHECK — I did NOT push)
- `e023a76` mempalace auto-recover + remove memdash (v9.60.0)
- `a1c4553` classifier tool gating → defer/un-defer; classify every turn (v9.59.0)
- `e209405` remove use-case map pin + narrow attachment gate to images (v9.58.0)
- `a84491d` prompt-classification report → markdown; drop Pages
- (earlier) `42659f2`/`0c06c43`/`24a3212` — the report + Pages saga

**PUSH STATE (verified):** `git status` clean. **3 commits are local-only, ahead of
origin/main and NOT pushed:** `e023a76`, `a1c4553`, `e209405`. (`e209405`'s parent
`a84491d` and earlier ARE on origin.) The repo convention is commit+push to main
directly (memory: feedback_commit_to_main) — but I held the push because the
classifier path is unverified. Decide with the user: push now, or after the
classifier bug is fixed + eval re-validated.
