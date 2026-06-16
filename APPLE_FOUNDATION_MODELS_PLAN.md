# Plan: Evaluate Apple Foundation Models (macOS 27) as a local background model

Drafted **2026-06-16**. Target box: **Mac mini M4 24GB, macOS 27** (now has
Apple Intelligence on-device "Foundation Models"). Idea: use Apple's on-device
model for brain-agent's SHORT-input background tasks — as an alternative (or
complement) to the vllm-metal + Qwen2.5-7B path already benchmarked
(see `[[project_local_bg_model_vllmmetal_bench]]`).

**UPDATE 2026-06-16 — the original "Swift-framework-only → needs a shim"
premise is WRONG, the picture is much better.** Confirmed from Apple's official
docs + WWDC26:
- There IS an official **Python SDK: `pip install apple-fm-sdk`** (apple/python-apple-fm-sdk),
  Python 3.10+, macOS 26.0+, Apple Silicon, Xcode 26+ CLI tools, on-device,
  no API key/cloud cost. (https://github.com/apple/python-apple-fm-sdk,
  https://apple.github.io/python-apple-fm-sdk/getting_started.html)
- macOS 27 ships an **`fm` CLI** built-in (not a dev preview — ships with the OS)
  that includes **`fm serve` → a local OpenAI-compatible Chat Completions HTTP
  server** (`fm serve --model system --stream`). (WWDC26 session 334;
  https://developer.apple.com/videos/play/wwdc2026/334/)
- The on-device model is **AFM 3 (Apple Foundation Model 3): ~20B sparse MoE,
  1–4B active params per prompt** (Instruction-Following Pruning), reportedly
  "outperforms 9B dense on math/coding." → a MUCH stronger model than the ~3B I
  feared — the classifier-viability odds are far better than for gemma-e2b/Qwen-7B.

**Net: no Swift shim is needed.** `fm serve` gives exactly the OpenAI-compatible
HTTP surface brain-agent already speaks (providers are "plain OpenAI-compatible
config.json entries"). This collapses Phases 0–1 into "stand up `fm serve` and
point a provider at it." The ONE thing still to verify is whether `fm serve`'s
OpenAI surface exposes reliable `response_format`/tool-calling for the
classifier — `fm schema` (guided generation) exists, but server-level structured
output is unconfirmed in the docs read so far.

## Why consider it
- **Zero install / zero model download** — model ships with the OS; `fm` CLI
  built-in. No venv-of-doom, no HF cache, no quant-format gymnastics (vs vllm-metal).
- Apple-managed updates, NPU-accelerated, low-power on-device.
- Free, fully private (PII never leaves the box).
- Stronger model than expected (AFM 3 20B-sparse) → better odds for the
  classifier than a 3–7B dense local model.

## Why still verify (lessons from the Qwen work)
- The **auto-route classifier** needs reliable **structured/JSON (or tool-call)
  output**. Qwen-7B dropped JSON braces ~20% and only passed with FORCED
  TOOL-USE; gemma-e2b @2B tanked the routing eval 0.75→0.48. Must confirm AFM 3
  via `fm serve` produces schema-valid routing JSON (the docs say guided
  generation exists — verify it reaches the OpenAI server).
- German prose quality (chat summary, wiki) — eval, don't assume.
- `fm serve` exact host/port/endpoint + whether `response_format`/tool-calling
  works on the server are NOT yet documented in sources read — confirm on the M4.

---

## Access to the M4 (status 2026-06-16 — deferred)

- **M4 mini IP: `192.168.4.65`**. SSH user `alexander`.
- **Network blocker (resolve first):** this dev box is on `192.168.1.x`, the M4
  is on `192.168.4.x` — DIFFERENT subnets. From here the M4 is currently
  unreachable (ping 100% loss, port 22 + HTTP all closed). Route goes via
  gateway `192.168.1.1` but nothing answers. Likely one of: M4 asleep/off,
  Remote Login not enabled, the two subnets aren't routed to each other
  (separate router / VLAN / guest net), or macOS firewall. **Must be fixed
  before any remote work** — the work itself (Phase 0–2) happens ON the M4.
- **Access method (decided):** KEY-BASED. Use the existing
  `~/.ssh/id_ed25519.pub` — `ssh-copy-id` it onto the M4 once (the only time the
  password is needed), then key auth only. Do NOT put the password in commands
  / shell history. (Security note: the password was shared in chat — rotate it
  once the key is in place.)
- Status: **deferred** per the user. Pick up when the M4 is reachable.

## Phase 0 — Stand up `fm serve` + nail the unknowns (on the M4, no brain-agent change)

No shim — just bring up the built-in server and confirm the 3 open facts.

1. **Confirm the toolchain:** `fm --version`; `fm serve --help`. Confirm the
   on-device model is available (`apple-fm-sdk`: `fm.SystemLanguageModel().is_available()`).
2. **Start the server:** `fm serve --model system --stream` (per WWDC26). Capture
   the EXACT host + port + endpoint path it prints (docs don't state the default
   — likely `127.0.0.1:<port>/v1/chat/completions`). Bind it to the LAN iface (or
   `0.0.0.0`) so the brain-agent host can reach it, mind the macOS firewall.
3. **Smoke a chat completion** with `curl` against `/v1/chat/completions` — text
   round-trips, latency for a short prompt.
4. **THE critical check — structured output on the SERVER:** does `fm serve`
   accept OpenAI `response_format` (json_schema) and/or `tools`+`tool_choice`?
   (`fm schema` proves guided generation exists; the question is whether the
   SERVER exposes it.) Test with a routing-shaped json_schema request. This
   decides classifier viability — without it, AFM is prose-only here.
5. **Note the model:** AFM 3, ~20B sparse / 1–4B active. No size/quant config to
   manage (OS-owned). Confirm it actually runs on the M4's RAM headroom (it
   should — Apple sizes it for the device).

**Exit gate:** server reachable from the brain-agent host + chat works → proceed.
Structured-output result decides whether the classifier is in scope (Phase 2/3).

---

## Phase 1 — Connectivity from the brain-agent host (the real first blocker)

This is the actual gating issue right now (see "Access to the M4" above): the
M4 (192.168.4.65) is on a DIFFERENT subnet and unreachable from the brain-agent
box (192.168.1.x). Resolve before Phase 2:
- M4 awake; Remote Login on; the two subnets routed to each other (or co-locate);
  firewall allows the `fm serve` port.
- Verify: from the brain-agent host, `curl http://192.168.4.65:<port>/v1/models`
  (or the chat endpoint) succeeds. Until this works, nothing downstream matters.

---

## Phase 2 — Benchmark on the M4 (mirror the Qwen methodology exactly)

Reuse the proven harness so results are comparable to Qwen2.5-7B:
- Per-task latency + quality on the SAME inputs as `/tmp/bench_local.py`
  (classifier JSON, German chat-summary).
- **The classifier gate is mandatory** — run the targeted classifier probe
  (`/tmp/clf_eval.py` over all 15 `eval/questions.json`, the real
  `_STRUCTURED_CLASSIFY_SYSTEM` prompt): valid-JSON rate + memory-inclusion +
  no-spurious-web. Compare directly to Qwen's "15/15 with forced JSON".
- German prose quality spot-check (summary truly summarizes, no garble).
- Concurrency: single-user, so even 1–2 concurrent is fine; measure but don't
  over-index.
- Variance discipline: ≥3 reps, mean±spread (per `[[feedback_eval_single_run_noise]]`).

**Decision matrix after Phase 2:**
| Apple FM result | Action |
|---|---|
| Prose good, classifier JSON reliable | Candidate to replace Qwen for ALL bg tasks (zero model mgmt — attractive) |
| Prose good, classifier shaky | Apple FM for prose-only knobs (refine/soul-chat/memory-classifier); classifier stays cloud/Qwen |
| Prose weak / German poor | Drop Apple FM; stay on vllm-metal+Qwen |

---

## Phase 3 — Wire into brain-agent (only if Phase 2 passes)

`fm serve` is OpenAI-compatible, so Apple FM is just another **named provider**
in `config.json → providers` — like the existing local providers, CLIProxyAPI
translates it to the Anthropic shape the sidecar wants. Its own
`LocalProviderQueue` slot, keyed by name (see
`[[project_local_bg_model_vllmmetal_bench]]` § LocalProviderQueue).
- Add provider `Apple-FM` (base_url `http://192.168.4.65:<fm-serve-port>/v1`),
  `max_concurrent` per the Phase-2 result (single-user → small).
- Point only the Phase-2-cleared knobs at the Apple model id (the 3 from the
  Qwen plan: `chat_summary_model`, `tools_config.refinement.model`,
  `mempalace.chat_sync.classifier.model`).
- If the classifier goes to Apple FM: it routes through CLIProxyAPI → sidecar,
  and the classifier fix uses Anthropic forced-tool-use (`capture_forced_tool`,
  built v9.123.0). Confirm that path produces schema-valid routing JSON through
  the OpenAI→Anthropic translation; if `fm serve`'s `response_format`/tool path
  is reliable (Phase 0 step 4) this should just work, else keep classifier on
  Qwen/cloud.

---

## Open questions to answer on the M4 (checklist)
- [x] Official Python/HTTP access? → YES: `pip install apple-fm-sdk` + `fm serve`
      (OpenAI-compatible HTTP server, built into macOS 27). No shim needed.
- [x] Model? → AFM 3, ~20B sparse MoE / 1–4B active (stronger than feared).
- [ ] `fm serve` exact host/port/endpoint path?
- [ ] Does `fm serve` expose OpenAI `response_format` (json_schema) / tools on
      the SERVER (not just `fm schema` locally)? ← decides classifier viability.
- [ ] German prose quality vs Qwen2.5-7B on the bench inputs?
- [ ] Classifier probe: valid-JSON 15/15? memory-inclusion 15/15?
- [ ] Latency per short task (sub-2s bar)? Concurrency behaviour?
- [ ] Reachable from the brain-agent host (subnet/firewall — Phase 1)?

## Bottom line
Much more promising than first thought: **no Swift shim** (official Python SDK +
built-in `fm serve` OpenAI server), and a **stronger model** than expected
(AFM 3 20B-sparse, beats 9B dense). It would drop in as a plain OpenAI provider
with ZERO model management — very attractive vs vllm-metal's venv/quant overhead.
Two things still gate it: (1) the M4 must be reachable (subnet/firewall), and
(2) the classifier needs `fm serve` to expose reliable structured output —
verify both, then benchmark with the Qwen harness. vllm-metal+Qwen stays the
fallback.

## Sources
- https://github.com/apple/python-apple-fm-sdk
- https://apple.github.io/python-apple-fm-sdk/getting_started.html
- https://developer.apple.com/videos/play/wwdc2026/334/  (WWDC26: fm CLI + Python SDK)
