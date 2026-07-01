# Handover — Option C: In-Process OpenAI Loop (drop Anthropic SDK + sidecar subprocess)

**Status:** STAGES 0-4 DONE + verified live (2026-07-01, Brain VERSION 9.246.0). Stage 5 (delete the sidecar) REMAINS — gated on a longer production soak. The new path is flag-gated (`use_inprocess_openai_loop`, default OFF in the repo); the sidecar stays as the fallback until Stage 5.
**Author context:** written after a full prompt-cache investigation + 3 code audits. All claims below are grounded in `file:line` facts verified in the current tree (baseline Brain VERSION 9.245.0, commit `f9790f45`).

---

## IMPLEMENTATION LOG (2026-07-01, v9.246.0)

- **NEW `engine/llm_loop.py`** — the hand-written in-process OpenAI streaming loop (`run_loop(...)`). Request build, single stream drain (tool_calls by index + tolerant JSON repair), direct `engine.TOOL_DISPATCH` dispatch, emits the Brain event vocabulary the chat worker already consumes, empty-round nudge + forced-tool capture ported, cache-cost split preserved, mid-stream cancel via a socket-closing watcher thread, 64MB stream cap.
- **`handlers/sidecar_proxy.py`** — added `use_inprocess_loop(model)` (per-model `models.<id>.use_inprocess_openai_loop` → global `use_inprocess_openai_loop` → False), `_run_turn_inprocess` (streaming), `_run_blocking_inprocess` (background, with `_apply_bg_context` mirroring the old `tool_mcp._apply_context`), `_build_tool_list_openai`. `run_turn` + `run_turn_blocking` branch to these at the top when the flag is on — chat.py + all background callers UNCHANGED.
- **Key finding:** the loop uses `{base_url}/chat/completions` regardless of `provider.type` — CLIProxyAPI (`:8317/v1`), vllm-metal (`:8012/v1`), oMLX (`:8000/v1`) all serve the OpenAI path on the same host. So **Stage 3 needed NO provider config flip** — the base_url is already right.
- **Verified live** (mistral-medium-3.5, mistral-small-latest, Lokal-M4/Qwen2.5-7B): interactive chat (cloud+local), tool calling (read_file end-to-end), cache split (**76/61/80% hits on warm turns** vs 0% cold — the payoff, vs 40-55% on the sidecar), auto-route classifier (forced-tool) + async summary + cost logging, mid-stream cancel (`rounds=0 cancelled=True`).
- **Remaining for Stage 5:** delete `sidecar/`, `.venv_sdk`, `server_lib/tool_mcp.py`, `/v1/tools/call`+`/v1/tools/list`, nonce layer, sidecar supervisor wiring, `_translate_anthropic_event` + `_TOOL_RESULT_CAPTURE` + `capture_tool_result` + the `_to_anthropic_messages`/`_normalise_anthropic_base_url`/`_thinking_param` sidecar helpers, the `anthropic` dep, `active_turns` sidecar-event-log recovery branch. Do this ONLY after the flag has been ON for all production models through a soak with no regressions. Flip the flag on globally first, watch, THEN delete.

---

## 1. WHY (the one-paragraph justification)

Mistral prompt caching (via CLIProxyAPI) bills cached tokens at **0.1×** and is the whole prize. The **reliable 95% cache hit rate only exists on the OpenAI `/v1/chat/completions` wire path** (`prompt_cache_key` works there). The Anthropic `/v1/messages` path caps at **~40-55% best-effort** — CLIProxyAPI drops `prompt_cache_key` on that path and its `cache_control` auto-injection is buggy (upstream issue #1592, the volatile `cch` header; **confirmed still broken on CLIProxyAPI 7.2.45**). The **Anthropic Python SDK has no `prompt_cache_key`** (caching is `cache_control`-only). So: to get 95% caching we must speak OpenAI shape. And once we drop the Anthropic SDK, the **sidecar subprocess loses its only first-order reason to exist** (it's an isolated venv holding *only* `anthropic` to keep its `anyio` out of Brain's process). We'd hand-write the loop anyway → run it **in-process in a Brain worker thread** → delete ~700 LOC of cross-process plumbing (nonce auth, context rebuild, tool-result capture, SSE re-translation, double stream-drain).

### Measured evidence (live, 20-25 call A/B each, mistral-medium-3.5)
| Path | mechanism | hit rate |
|---|---|---|
| CLIProxyAPI **OpenAI** `/v1/chat/completions` | `prompt_cache_key` | **95%** ✅ |
| CLIProxyAPI OpenAI | no key | 55% |
| CLIProxyAPI **Anthropic** `/v1/messages` (current sidecar path) | `prompt_cache_key` | 55% (key dropped) |
| CLIProxyAPI Anthropic | `cache_control` block | 40% (buggy) |
| Mistral **direct** (api.mistral.ai, OpenAI) | `prompt_cache_key` | 92% |

Mistral usage field on OpenAI path: `usage.prompt_tokens_details.cached_tokens`. On a hit, ~99% of the prefix caches (e.g. 2800/2825). Cache hits are **intermittent even at "95%"** — model-dependent (mistral-medium caches, mistral-large 0% in probes) and best-effort; model an average hit rate, never assume per-call.

---

## 2. WHAT EXISTS TODAY (architecture baseline)

```
launcher.py → server.py (8420)                ┌──────────────────────────┐
                ├── brain.py + engine/        ─┤ sidecar/sidecar.py 8421  │
                │   (wiring, classes, glue)    │  anthropic 0.101.0 SDK    │
                ├── handlers/sidecar_proxy.py ►│  client.messages.create  │
                ├── server_lib/tool_mcp.py ◄───┤  POSTs /v1/tools/call     │
                └── SQLite / MemPalace         └──────────────────────────┘
```

- **Sidecar** (`sidecar/sidecar.py`, separate `.venv_sdk` venv, port 8421): owns the agentic loop via `anthropic.Anthropic(...).messages.create(stream=True)` (client built `sidecar.py:517`, sole call site `sidecar.py:631`). No OpenAI path exists in the sidecar.
- **Brain → sidecar** contract: `handlers/sidecar_proxy.py`
  - `run_turn(...)` (interactive, `sidecar_proxy.py:483`) → SSE drain → `event_callback`.
  - `run_turn_blocking(...)` (`sidecar_proxy.py:764`) → non-streaming wrapper for background.
  - `background_call(...)` (`sidecar_proxy.py:894`), `helpdesk_call(...)` (`sidecar_proxy.py:1020`).
  - Payload built at `sidecar_proxy.py:556-593` (interactive) / `:824-858` (blocking): Anthropic shape — top-level `system` string, `messages` via `_to_anthropic_messages` (`:98-149`), Anthropic `tools` (`input_schema`), `thinking:{type:enabled,budget_tokens}` (`:588-590`), `disable_parallel_tool_use`, `chat_template_kwargs`, and (NEW, v9.245.0) `prompt_cache_key`.
- **Tool dispatch** (cross-process): sidecar emits `tool_use` → POSTs Brain `/v1/tools/call` → `server_lib/tool_mcp.handle_tools_call` (`tool_mcp.py:243`) validates nonce, rebuilds context (`_apply_context` `:90-182`), dispatches `engine.TOOL_DISPATCH[name](args)`, `capture_tool_result` stashes for the proxy SSE drain.
- **Providers (all serve NATIVE Anthropic `/v1/messages` today)** — `config.json → providers`:
  - `CLIProxyAPI` `http://127.0.0.1:8317/v1` (`type:anthropic`) — Anthropic→Mistral translation. **Has an OpenAI endpoint too: `/v1/chat/completions` (verified 95%).**
  - `Lokal-M4` (vllm-metal, M4) `http://192.168.1.214:8012/v1` (`type:anthropic`, `_comment: "Serves native Anthropic /v1/messages; sidecar talks to it DIRECTLY"`). vllm-metal ALSO serves OpenAI `/v1/chat/completions` — **the warmup path already uses it** (`brain.py:~7040`).
  - `Lokal` (oMLX) `http://localhost:8000/v1` (`type:openai`, `is_local`) — sidecar currently reaches it via the Anthropic SDK (base-url normalised `sidecar_proxy.py:222-232`). oMLX serves OpenAI `/v1/chat/completions` natively (it IS an openai-typed provider). NOTE oMLX gotcha (see §6).

---

## 3. KEY AUDIT FINDINGS (what's safe, what's NOT load-bearing)

These de-risk the migration — the scary parts are non-issues:

1. **Thinking-block SIGNATURES are never persisted or replayed cross-turn.** `thinking_done` (`handlers/chat.py:~1559`) persists only thinking TEXT as a UI-only `"thinking"` DB row; no signature stored. `_to_anthropic_messages` DROPS thinking rows entirely on the wire between turns (`sidecar_proxy.py:102-103`). → **OpenAI-shape reasoning (`delta.reasoning_content` / `<think>` tags) will NOT break anything.** Intra-turn signed-thinking replay (`sidecar.py:684-690`) is the only use and stays inside one turn.
2. **`cache_control` blocks are NOT used** anywhere in the request path (purged v5.7→7.2). Current caching is `prompt_cache_key` only.
3. **Resumability is 100% Brain-side** (`LiveStream` on `session.live_stream`, `streaming_text`, `active_turns` table, `recover_active_turns_on_boot`) — works identically whether the loop is a thread or a process. The sidecar's per-turn replay buffer (`/turn/<id>/events?since=N`) **never fires in production** — the supervisor kills the sidecar with Brain, so recovery always hits the 404 "turn lost" branch (`SDK_PHASE5_PROGRESS.md:365-367`). Dead machinery today; nothing lost by going in-process.
4. **No concurrency benefit** to the subprocess — both use `ThreadingMixIn`+`daemon_threads`, one OS thread per turn (`sidecar.py:894`, `server.py:609`). In-process Brain thread scales identically (loop is I/O-bound → GIL irrelevant).
5. **~700 LOC deletes** (see §5) — nonce layer + `_apply_context` (90 LOC context rebuild) + `_TOOL_RESULT_CAPTURE` + SSE envelope translation + double stream-drain, all pure boundary-bridging.

**The ONE genuine trade-off:** crash/OOM isolation. A runaway in-process loop (infinite loop, 200MB malformed stream) could take Brain down; the subprocess currently contains that. **Mitigation (must build):** bound the stream (max bytes / max rounds already exist as `max_rounds`), wrap each turn's loop body in try/except that emits `error` + terminal `done` so one bad turn can't wedge the worker, and keep the existing `max_tool_rounds` hard-stop (1.5×). This is generic-loop risk, far smaller than the SDK-anyio risk that originally drove the split.

---

## 4. TARGET ARCHITECTURE (Option C)

```
launcher.py → server.py (8420)
                ├── brain.py + engine/
                ├── engine/llm_loop.py  ◄── NEW: in-process OpenAI streaming loop
                │      httpx stream → /v1/chat/completions
                │      tool call → engine.TOOL_DISPATCH[name](args)  (direct, no HTTP)
                │      emits into session.live_stream via event_callback
                └── SQLite / MemPalace
   (no sidecar subprocess, no .venv_sdk, no tool_mcp HTTP endpoint)
```

- **New module `engine/llm_loop.py`** (or `handlers/llm_loop.py`) — the hand-written OpenAI agentic loop. One `httpx` streaming request per round to `{base_url}/chat/completions`, `stream:true`, `stream_options:{include_usage:true}`.
- Runs on the **existing Brain chat worker thread** (the one `_handle_chat` already spawns). Emits into `session.live_stream` via the SAME `event_callback` shape the proxy uses today — so LiveStream / resumable streaming / persistence are UNCHANGED.
- **Tool dispatch = direct call:** `engine.TOOL_DISPATCH[name](args)` on the worker thread that already holds the full `RequestContext`. No nonce, no `_apply_context`, no HTTP, no capture/pop.
- **Provider routing:** all chat providers flipped to their OpenAI `/v1/chat/completions` endpoints. `resolve_provider_for_model` already returns `{api_key, base_url}` — base_url stays the same host; only the path + wire shape change.

---

## 5. IMPLEMENTATION PLAN (staged, with fallback)

### Stage 0 — Prep + safety net
- Add a per-provider or global flag `use_inprocess_openai_loop` (config) so the NEW loop can be enabled per model and the OLD sidecar path stays as fallback until proven. Do NOT delete the sidecar until Stage 5.
- Snapshot: `git` clean, note `f9790f45` as the pre-migration baseline.

### Stage 1 — Build `engine/llm_loop.py` (the core)
Port `sidecar/sidecar.py:run_turn_streaming` (`sidecar.py:507-890`) to OpenAI shape. Responsibilities (all verified trivial-or-simpler per audit §6):
- **Request build (OpenAI):** `system` → `messages[0] {role:"system"}`; reverse `_to_anthropic_messages` image→`image_url`; Anthropic tools `input_schema` → OpenAI `{type:function, function:{name,description,parameters}}`; `thinking:{budget_tokens}` → `reasoning_effort` (logic already exists in `_apply_inference_to_payload`, `brain.py`); `disable_parallel_tool_use` → `parallel_tool_calls:false`; **`prompt_cache_key` = session_id (interactive) / cost_purpose (background)** — already decided, already plumbed in payload, just move to top-level OpenAI field.
- **Stream drain (single, not double):** iterate `httpx` SSE; parse `choices[].delta`. Map: `delta.content` → text; `delta.reasoning_content` (or `<think>`) → thinking; `delta.tool_calls[]` → accumulate by `index` (`function.name` + `function.arguments` JSON-string fragments). Reuse the tolerant JSON repair parser from `sidecar.py:_parse_tool_input_json` (`:375-411`).
- **Accumulator:** OpenAI equivalent of `_AccumulatedMessage` (`sidecar.py:453-504`) keyed by choice/tool-call index. (The Anthropic block-index-gap workaround is oMLX-Anthropic-specific and NOT needed on OpenAI shape.)
- **Emit events** directly via `event_callback(type, data)` — same event vocabulary the chat handler already consumes (`round_start`, `round_end` with `{tokens_in, tokens_out, cache_read_tokens}`, `usage`, `tool_call`, text deltas, `done`). Keep the v9.245.0 cache-cost split: `cache_read_tokens` from `usage.prompt_tokens_details.cached_tokens`; `tokens_in` = `prompt_tokens - cached_tokens` (full-price remainder). **This is the payoff — feeds the existing cost ledger + `⚡ cached` UI unchanged.**
- **Tool dispatch:** `engine.TOOL_DISPATCH[name](args)` directly (worker thread has context). Keep AskUserQuestion blocking (already Brain-side via `_pending_answers` + Event — zero change). For per-tool mid-flight cancel, replicate the worker-thread-abandon pattern (`sidecar.py:_dispatch_tool_cancellable` `:329`) if you want to keep it; otherwise between-round `session.cancel_token` checks suffice for turn-level cancel.
- **Empty-round nudge** (`sidecar.py:596-781`): pure loop logic, move verbatim.
- **Forced-tool capture** (structured output, classifier): OpenAI `tool_choice:{type:function,function:{name}}`; read back the tool input. `brain.classify_task_structured` already has a free-text `{...}` fallback.
- **Usage:** read authoritative usage off the final chunk (`stream_options.include_usage`).

### Stage 2 — Wire Brain to the in-process loop
- `handlers/chat.py` worker: when `use_inprocess_openai_loop` is on for the model, call `engine.llm_loop.run_turn(...)` on the worker thread instead of `sidecar_proxy.run_turn(...)`. Same `event_callback`, same `session.live_stream`. Everything downstream (LiveStream, persistence, done event, cost logging) is UNCHANGED.
- `background_call` / `run_turn_blocking` / `helpdesk_call`: add in-process equivalents (they're simpler — no SSE, just drain to a final dict).

### Stage 3 — Flip providers to OpenAI endpoints
- Point chat providers at `/v1/chat/completions`. CLIProxyAPI: same host `:8317`, OpenAI path (verified 95%). vllm-metal/oMLX: confirm their OpenAI endpoints return equivalent tool-calling + reasoning (warmup already hits vllm-metal OpenAI). **Verify oMLX `enable_thinking` chat_template_kwarg still works via `extra_body` on the OpenAI path (§6 gotcha) — and that the warmup KV-prefix stays byte-identical** (warmup path is ALREADY OpenAI-shape, so this should now be EASIER to keep in lockstep — a bonus).

### Stage 4 — Verify (gates before deleting anything)
- **Cache:** drive repeated interactive turns on cache-priced Mistral, confirm `cache_read_tokens>0` rows in `cost_log` at ~90%+ hit rate (vs 40-55% before). Harness: mint PyJWT from `config.json auth.jwt_secret` for real user (`agents/main/auth.db`, admin `17368b7961d3`), `POST /v1/sessions {model,agent}` → session_id, `POST /v1/chat`. NOTE `/v1/chat` is SSE → urllib `read()` blocks on EOF; worker persists rows regardless.
- **Tool calling:** a turn that calls tools (web_fetch, code_search) works end-to-end.
- **Thinking:** reasoning models still stream + persist thinking text.
- **Local models:** oMLX + vllm-metal turns work on OpenAI path; warmup KV prefix still hits (0 re-primes).
- **Cancel + AskUserQuestion:** mid-turn cancel stops cleanly; AskUserQuestion blocks + resumes.
- **Background:** classifier (forced-tool), summary, translate all work + log cost.
- **Concurrency:** N parallel turns don't interleave state (contextvars isolation — fresh thread = empty context, already safe per `engine/CLAUDE.md`).

### Stage 5 — Delete the boundary (once Stage 4 green on all models)
Delete / retire:
- `sidecar/` (whole dir), `.venv_sdk`, `server_lib/sidecar_supervisor.py`'s sidecar wiring, `handlers/sidecar_proxy.py` (or gut to the in-process shim).
- `server_lib/tool_mcp.py` (~365 LOC) + `/v1/tools/call` + `/v1/tools/list` endpoints in `server.py`.
- Nonce layer (`mint_nonce`/`_validate_nonce`/`_NONCES`), `_apply_context` (~90 LOC), `_TOOL_RESULT_CAPTURE`/`capture_tool_result`/`pop_tool_result`.
- `/v1/sidecar/restart` handler, `active_turns` recovery's sidecar-event-log branch (keep the Brain-side `streaming_text` promotion), `recover_active_turns_on_boot`'s 404 branch simplifies.
- `_translate_anthropic_event` + the SSE envelope protocol.
- The `anthropic` dependency entirely.
- Update `config.sidecar.*`, launchd plist (no sidecar), CLAUDE.md architecture section, `SDK_MIGRATION_*` docs (mark superseded).

---

## 6. GOTCHAS / INVARIANTS TO PRESERVE

- **oMLX `enable_thinking` KV-prefix (CRITICAL):** oMLX/vllm chat templates default `enable_thinking=true` when kwarg absent → warmup KV prefix misses silently. `_apply_inference_to_payload` must ALWAYS emit `enable_thinking` (true OR false) on every oMLX request whose model has non-`none` `thinking_format`, byte-for-byte matching warmup. On OpenAI path this rides via `extra_body:{chat_template_kwargs:{enable_thinking:...}}` (httpx passes it as a top-level wire field). The warmup path is ALREADY OpenAI-shape (`brain.py:~7040`) — so in-process the chat + warmup payloads become the SAME shape, making byte-identity EASIER, not harder. Verify with `tools/check_warmup_prefix_stable.py`.
- **Warmup byte-identity:** warmup payload must match first-turn byte-for-byte (hour-rounded timestamp, same tools sorted, `stream_options`). Since both become OpenAI shape, reconcile the two builders into one.
- **Cost ledger cache split (v9.245.0):** keep `cache_read_tokens` SEPARATE from `tokens_in` (the 4 collapse sites → now just the in-process loop's usage extraction). `tokens_in` = full-price (fresh + any cache_creation-equivalent), `cache_read` = discounted 0.1×. Rates: `cost_cache_read` per model (medium 0.15, small 0.1275/0.51/cache 0.01275 — cortecs.ai serverless, verified).
- **contextvars isolation:** in-process tool dispatch runs on the worker thread that set the context — so NO `_apply_context` rebuild needed, BUT keep the `with request_context()` discipline; never set context bare on a pooled thread (`engine/CLAUDE.md` bleed invariant). The chat worker uses `Thread().start()` (fresh thread = empty context = bleed-free).
- **Freeze + measurement already shipped (9.245.0, committed f9790f45):** `model_is_cache_priced` (explicit `cost_cache_read>0`), turn-1 classification freeze for cache-priced models, `⚡ cached` status badge, `cost_cache_read` field in model settings. These are wire-shape-AGNOSTIC and stay. See [[project_cache_cost_vs_classification]].
- **Crash isolation loss:** wrap each turn in try/except (emit `error`+terminal `done`); bound stream bytes; keep `max_rounds` + 1.5× hard-stop. One bad turn must not wedge the worker or take Brain down.

---

## 7. FILES TOUCHED (quick index)
| File | Action |
|---|---|
| `engine/llm_loop.py` | **NEW** — the in-process OpenAI streaming loop |
| `handlers/chat.py` | route worker to in-process loop (flagged); reuse event_callback/LiveStream |
| `handlers/sidecar_proxy.py` | gut to in-process shim, then delete; port background_call/helpdesk_call in-process |
| `brain.py` | reconcile warmup (already OpenAI) + chat payload builders into one; `_apply_inference_to_payload` reused |
| `config.json` | flip chat providers to OpenAI `/v1/chat/completions`; add `use_inprocess_openai_loop` flag |
| `server_lib/tool_mcp.py` | DELETE (Stage 5) |
| `server.py` | remove `/v1/tools/call`, `/v1/tools/list`, `/v1/sidecar/restart`; remove sidecar supervisor wiring |
| `sidecar/` + `.venv_sdk` | DELETE (Stage 5) |
| CLAUDE.md, SDK_MIGRATION_*.md | update/supersede |

## 8. VERIFICATION HARNESS (copy-paste ready)
```python
# mint admin JWT, create session, drive turns, check cost_log cache_read
import json, time, urllib.request, sqlite3, jwt as pyjwt
cfg=json.load(open('config.json'))
tok=pyjwt.encode({'user_id':'17368b7961d3','username':'admin','role':'admin','exp':time.time()+3600,'iat':time.time()}, cfg['auth']['jwt_secret'], algorithm='HS256')
H={'Content-Type':'application/json','Authorization':f'Bearer {tok}'}
def post(p,b): return urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:8420'+p,data=json.dumps(b).encode(),headers=H,method='POST'),timeout=90)
sid=json.loads(post('/v1/sessions',{'model':'CLIProxyAPI/mistral-medium-3.5','agent':'main'}).read())['session_id']
PREFIX=('Kontext (stabil): '+('Lorem ipsum dolor sit amet. '*300)).strip()
for t in range(6):
    try: post('/v1/chat',{'session_id':sid,'model':'CLIProxyAPI/mistral-medium-3.5','message':PREFIX+f' Sag {t}.','max_tokens':8}).read()
    except: pass  # SSE read blocks; worker persists regardless
    time.sleep(1.5)
c=sqlite3.connect('agents/main/costs.db')
rows=c.execute('SELECT tool_round,tokens_in,cache_read_tokens FROM cost_log WHERE session_id=? ORDER BY id',(sid,)).fetchall()
print('cache hits:', sum(1 for r in rows if r[2]>0), '/', len(rows))  # target: most >0 (was 40-55%, want ~90%)
```

## 9. RELATED MEMORY
- [[project_openai_inprocess_loop_decision]] — the decision record
- [[project_cliproxyapi_cache_key_blocker]] — the measured path matrix + why Anthropic path can't cache
- [[reference_mistral_prompt_caching]] — Mistral caching mechanics
- [[project_cache_cost_vs_classification]] — what shipped in 9.245.0 (freeze/measurement/UI)
