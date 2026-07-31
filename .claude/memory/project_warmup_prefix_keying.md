---
name: project_warmup_prefix_keying
description: v9.206.0 warmup made PREFIX-keyed (not model-keyed) — fixes mid-session re-warm / mode ping-pong / project-switch re-warm
metadata: 
  node_type: memory
  type: project
  originSessionId: 9649d3b6-be55-44ad-9810-aa09c6b21c52
---

v9.206.0 (2026-06-25): fixed "warmup re-fires mid-session for no reason though it was warm a second ago". User correctly reframed it as ONE missing invariant, not three bugs: warmup must fire ⟺ the needed KV prefix isn't warm — no per-scenario if/else.

ROOT CAUSE: `_warmup_state` was `dict[model]` and the warm decision used PROXIES (warmup_mode string compare, claim() shape, hardcoded "full"). Three symptoms = same gap: (a) mode ping-pong (keeper wants minimal, `_trigger_warmup` forced full → each flipped the single model entry → re-prime every keeper tick); (b) project↔normal chat switch always re-warmed (claim() rightly serves only bare main sessions, `_trigger_warmup` fired full blind); (c) thinking re-prime flipped thinking_primed vs keeper's always-False prime.

FIX (brain.py): `compute_prefix_id(system_prompt, sorted(tool_names), thinking_in_prefix)` sha1; `_warmup_state` now `dict[model][prefix_id]`. `prefix_is_warm(model,pid,minimal=)` = THE decision, with SUBSET rule (minimal need = weights-only = covered by ANY warm full prefix; minimal prime keyed under sentinel `MINIMAL_PREFIX_ID`). thinking enters prefix_id ONLY when `prefix_thinking_relevant(model)` (oMLX provider w/ supports_chat_template_kwargs + thinking_format!=none → enable_thinking changes tokens; cloud reasoning_effort does NOT). `evict_prefixes_except` mirrors that a fresh full prefill evicts other resident GPU prefixes (oMLX LRU). `_bare_full_prefix_id()` builds the bare-main full prefix via build_first_turn_prefix (byte-identical to run_model_warmup). `invalidate_model_warmup(model)` drops all prefixes on config change (handlers/providers.py).

(a)/(b)/(c) now fall out automatically: keeper (server_daemons.py) skips via prefix_is_warm; `_trigger_warmup` (server.py) no-ops when its bare-full prefix is warm; chat worker (handlers/chat.py) dropped the blind thinking re-prime and instead calls `mark_prefix_used`+`evict_prefixes_except` after the prefix build (using a prefix IS the best warmup; thinking prefix warms on first use).

HONEST LIMIT: KV prefix lives in GPU (oMLX/vLLM LRU); Brain keeps an OPTIMISTIC mirror — a competing prime can evict a prefix Brain still thinks warm → occasional transparent cache-miss (full prefill, no correctness issue), strictly better than the guaranteed ping-pong.

REGRESSION FIXED v9.206.1: the 9.206.0 POOL-BUILD gate (2nd keeper pass, server_daemons.py) was set to prefix_is_warm(BARE-FULL) — but a minimal-mode model never warms a full prefix → pool stayed 0/N in the status bar despite green composer dot (state='warm' from minimal). A pooled slot is just a pre-created bare session (try_build fires NO prefill), useful even for minimal models (skips cold session setup). Fix = pool gate back to model-wide get_warmup_state(mid).state=='warm' (=_best_entry). The PRIME decision (1st pass) stays prefix-keyed (the real fix). Live: pool fills 1/10→10/10.

VERIFIED LIVE (9.206.0, graceful SIGTERM restart): keeper primed gemma-4-12B once (minimal, 875ms) then ZERO re-primes over 75s window (was constant before); no warmup-code tracebacks (only pre-existing BrokenPipeError noise). Unit test `tests/test_warmup_prefix_keying.py` (8/8) pins the invariant + subset + eviction + no-ping-pong. check_warmup_prefix_stable.py still green. Only warmup-enabled model in this install = gemma-4-12B-it-qat-4bit (warmup_mode=minimal, provider Lokal which DOES support chat_template_kwargs → thinking IS prefix-relevant for it). Internal infra fix → no curated changelog entry. Relates to [[feedback_compile_check_brain_py]], [[feedback_never_sigkill_brain]].
