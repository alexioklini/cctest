---
name: project-sync daemon production hardening (worker pool + watchdog + heartbeat)
description: Backlog — needed before multi-tenant production. Today's daemon is single-threaded with no per-task timeout; one slow project blocks all others, and a hang anywhere kills the cycle silently with no recovery. Worker-pool + watchdog + heartbeat closes both gaps. ~200-400 LOC, 1-2 days.
type: project
originSessionId: 960d069c-8b9b-4582-a9f7-c2c3d1d52ba1
---
Captured 2026-04-27 during the real-corpus test run. Two related findings about `_project_sync_loop` in server.py:

## Findings

### 1. Multi-tenant blocking — sequential project iteration
The daemon's `for agent_id, proj_name in ordered:` loop processes one project at a time within a cycle. Project A's full pipeline (doc_convert → mp_miner.mine → kg_extract.run_kg_post_pass → optional closet_regen) runs to completion before Project B starts. With a 6-8h project A and four other projects, the others wait 6-8h each. Manual Sync now requests jump to the front of the *next* cycle but never preempt the in-flight one.

Fine for single-tenant single-corpus (the current 150-PDF bank-policy use case). Untenable at multi-customer scale.

### 2. Hang = silent total stop
Outer `try/except Exception` at cycle scope catches Python exceptions; daemon survives and retries on the next 6h tick. Process-level fatal (segfault in chromadb/tree-sitter/fitz, OOM-kill) gets restarted by launchd's `KeepAlive`.

But: **a hang inside the cycle is not caught by anything**. If gemini's HTTP call hangs without timeout, or chromadb deadlocks on a write, or the daemon's `_run_delegate` blocks on a network read with no TCP timeout, the thread sits forever. `Event.wait()` only resumes when the daemon returns to it — and it never does. No exception fires, no log line, no recovery. The cycle is dead and every subsequent project never gets processed.

Today's mitigations:
- `_run_delegate` has a 300s default HTTP timeout (per CLAUDE.md), so KG extraction calls are bounded
- `mp_miner.mine` and `closet_llm.regenerate_closets` come from upstream MemPalace and may or may not have timeouts on their chromadb / network operations — not audited
- The hang risk is real but low-probability today

## Single fix that closes both

The two findings share the same shape: each project's work needs to run in an isolated worker with a wallclock budget. One design solves both:

```python
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout

def _project_sync_loop():
    pool = ThreadPoolExecutor(max_workers=N)
    while True:
        # ... enumerate projects ...
        futures = {p: pool.submit(_process_one_project, p) for p in ordered}
        for p, fut in futures.items():
            try:
                fut.result(timeout=PER_PROJECT_BUDGET_S)
            except FutureTimeout:
                print(f"[project-sync] {p}: per-project budget exceeded; abandoning")
                # NOTE: future keeps running; can't cancel a running thread
            except Exception as e:
                print(f"[project-sync] {p}: failed {type(e).__name__}: {e}")
        # heartbeat
        _persist_heartbeat()
        # ... wait ...
```

Plus:
- **Per-call timeouts everywhere.** Audit `kg_extract.py` (already mostly OK via `_run_delegate`), `doc_convert.py` (no network calls), `closet_llm.regenerate_closets` (upstream — needs an `httpx.Client(timeout=...)` or similar wrap), `mp_miner.mine` (chromadb has its own timeout knobs).
- **Heartbeat metric** in `chats.db.project_sync_heartbeat` row: `last_cycle_started_at`, `last_cycle_finished_at`, `last_project`, `last_status`. External check (or existing `audit.db` pattern) flags `no cycle finished in >24h`. Today the only signal is daemon log lines, and `feedback_brain_log_file.md` already established those are unreliable.
- **`LocalProviderQueue` semaphore** already exists in `claude_cli.py` — wire it as the gateway-protection so N parallel workers don't dogpile cliproxyapi/oMLX. The queue's per-provider `max_concurrent` knob caps real concurrency; worker-pool just affects scheduling latency.

## Cancellation caveat

`ThreadPoolExecutor.submit(...).result(timeout=...)` doesn't actually kill the worker thread on timeout — Python threads can't be force-cancelled. The watchdog only abandons the future and moves on; the worker eventually finishes (or hangs forever, leaking a thread). For a production-grade fix:

- Either accept thread leaks (fine for occasional timeouts; pool size eventually saturates and demands a process restart)
- Or run each project as a separate child *process* (`multiprocessing.Pool`), where SIGTERM actually kills it. More complexity, more memory, but robust.

For step 1 the thread-pool with leak-on-timeout is fine; revisit if hangs become frequent.

## Sequencing

Build this **before** any of:
1. Multi-customer / multi-tenant deployment
2. Letting end-users (not admins) trigger KG cycles on demand
3. Adding more daemon-loaded extractors (code graph profile, future profiles)
4. Closet regen at full corpus size with `regenerate_closets: true` (more LLM calls per cycle = more hang surface)

Don't build for:
- Single-tenant single-corpus static deployments (current usage)
- The 150-PDF bank-policy run (fine sequential)

## Estimate

- **Worker pool + per-project timeout**: ~100 LOC in server.py
- **Heartbeat table + external check**: ~50 LOC, idempotent CREATE TABLE pattern (matches `kg_extraction_log`)
- **Audit + add per-call timeouts** to closet_llm + mp_miner paths: ~50-100 LOC, mostly upstream hand-coding around third-party calls
- **Tests**: contrived hang + slow project + concurrent multi-project, ~150 LOC

Roughly **200-400 LOC, 1-2 focused days of work**, no schema migrations, opt-in via config (`mempalace.project_sync.parallelism: N`, default 1 to preserve current behavior).
