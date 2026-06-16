# Plan: Evaluate Apple Foundation Models (macOS 27) as a local background model

Drafted **2026-06-16**. Target box: **Mac mini M4 24GB, macOS 27** (now has
Apple Intelligence on-device "Foundation Models"). Idea: use Apple's on-device
model for brain-agent's SHORT-input background tasks — as an alternative (or
complement) to the vllm-metal + Qwen2.5-7B path already benchmarked
(see `[[project_local_bg_model_vllmmetal_bench]]`).

This is an **investigation-first** plan: several load-bearing facts are UNKNOWN
and must be verified on the actual M4 before any integration. Do NOT wire it
into brain-agent until Phase 0 resolves the blockers. The vllm-metal+Qwen path
stays the proven baseline / fallback.

## Why consider it
- Zero install / zero model download — the model ships with the OS.
- Apple-managed updates, NPU-accelerated, designed for low-power on-device.
- Free, fully private (PII never leaves the box) — the same wins we wanted from
  vllm-metal, but with no venv/model-management overhead.

## Why be cautious (what we already know from the Qwen work)
- The background-task set includes the **auto-route classifier**, which needs
  reliable **structured/JSON (or tool-call) output**. We learned even Qwen-7B
  dropped JSON braces ~20% of the time and only became viable with FORCED
  TOOL-USE. A ~3B-class on-device model is *more* format-fragile, not less
  (gemma-e2b @2B tanked the routing eval 0.75→0.48).
- German prose quality matters (chat summary, wiki) — must be eval'd, not assumed.
- brain-agent talks to local models over **HTTP** (sidecar → Anthropic
  `/v1/messages` or OpenAI-compat). Apple's model is exposed as a **Swift
  framework**, not an HTTP server — so a shim is almost certainly required.

---

## Phase 0 — Resolve the blockers (do FIRST, on the M4; no brain-agent changes)

These three answers decide whether this is even feasible. Investigate each on
the actual macOS 27 M4 — do not assume.

### 0a. Access surface: framework-only, or is there an HTTP/CLI path?
- Apple's on-device model is the **`FoundationModels`** Swift framework
  (`LanguageModelSession`, `SystemLanguageModel`, guided generation via
  `@Generable`). As of the betas this was Swift-only — **no built-in HTTP server
  and no official OpenAI/Anthropic-compatible endpoint.**
- VERIFY on the box: is there now any CLI (`xcrun`/`mlx`-style) or a system
  service that exposes it over a socket? Check Apple's macOS 27 release notes +
  the `FoundationModels` docs for any new server/endpoint affordance.
- **If framework-only (most likely):** integration requires a **small Swift
  shim** — a local HTTP server (e.g. Vapor or a stdlib `Network` listener) that
  accepts a request, calls `LanguageModelSession`, returns the text. This shim
  is the bulk of the work. Decide: build the shim, or skip Apple FM.

### 0b. Structured output / tool-calling capability
- The framework supports **guided generation** (`@Generable` / guided decoding)
  — Swift-side schema-constrained output. That's promising for the classifier
  IF the shim can expose it.
- VERIFY: can the shim force the routing JSON shape (task_types/tools/
  complexity) reliably via `@Generable`? This is the single most important
  capability — without reliable structured output, Apple FM is unfit for the
  classifier (same bar Qwen only cleared via forced tool-use).
- Note: brain-agent's classifier fix uses Anthropic **forced tool-use**
  (`capture_forced_tool`). The Apple shim won't speak that — so the shim must
  either (i) accept the JSON-schema and use `@Generable` internally, returning
  plain JSON the broker parses, or (ii) the broker must have an
  OpenAI-`response_format` path for this provider. Map this before building.

### 0c. Model class / quality
- Apple's on-device model is ~3B-class (NPU). VERIFY the actual model + any
  size/quant details exposed in macOS 27.
- Expectation: fine for the EASY prose tasks (chat summary, wiki tag, /refine,
  memory-classifier one-word label); RISKY for the auto-route classifier (the
  3B-fragility concern). Plan to keep the classifier on cloud OR Qwen unless the
  eval proves Apple FM holds.

**Exit gate for Phase 0:** if (0a) there is no HTTP path AND you don't want to
build the Swift shim → STOP, stay on vllm-metal+Qwen. If a shim is acceptable →
proceed to Phase 1.

---

## Phase 1 — Build the access shim (only if Phase 0 says framework-only)

A minimal local HTTP server in Swift wrapping `FoundationModels`:
- One endpoint, simplest shape brain-agent already speaks. Two options:
  - **OpenAI-compat** `POST /v1/chat/completions` (non-streaming first) — Brain
    providers are "plain OpenAI-compatible config.json entries" per CLAUDE.md,
    so this is the path of least resistance. CLIProxyAPI would translate to the
    Anthropic shape the sidecar wants.
  - **Anthropic** `POST /v1/messages` — matches what the sidecar speaks
    natively (like vllm-metal's native endpoint), no CLIProxyAPI hop. More work
    in the shim (Anthropic event/stop_reason shape).
- Map `@Generable` guided generation to a JSON-schema / forced-tool request so
  the classifier can get reliable structured output (Phase 0b).
- Run it as a launchd service (mirror `com.brain-agent.vllm-metal.plist`
  pattern), pick a port (e.g. :8013 to avoid vllm-metal's :8012).
- Keep it tiny + supervised; it must degrade gracefully (down → Brain just sees
  a dead provider).

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

Same mechanism as the vllm-metal plan — Apple FM is just another **named
provider** in `config.json → providers` (its own `LocalProviderQueue` slot,
keyed by name; see `[[project_local_bg_model_vllmmetal_bench]]` § LocalProviderQueue).
- Add provider `Apple-FM` (base_url `http://<m4-ip>:8013`), `max_concurrent` per
  the Phase-2 concurrency result.
- Point the relevant config knobs at the Apple model id (the 3 knobs from the
  Qwen plan: `chat_summary_model`, `tools_config.refinement.model`,
  `mempalace.chat_sync.classifier.model`) — only the ones Phase-2 cleared.
- If the classifier goes to Apple FM, the forced-tool / JSON-schema enforcement
  (sidecar `capture_forced_tool`, already built v9.123.0) must reach it — verify
  the shim honors it, else keep classifier on Qwen/cloud.

---

## Open questions to answer on the M4 (checklist)
- [ ] macOS 27: is there ANY official HTTP/CLI access to the Foundation Model, or Swift-framework-only?
- [ ] Exact on-device model + size/quant exposed in macOS 27.
- [ ] Does `@Generable` guided generation give reliable schema-constrained JSON?
- [ ] German prose quality vs Qwen2.5-7B on the bench inputs?
- [ ] Classifier probe: valid-JSON 15/15? memory-inclusion 15/15?
- [ ] Latency per short task (sub-2s bar)?
- [ ] Effort to build + maintain the Swift shim vs. just running vllm-metal+Qwen?

## Bottom line
Apple FM is worth a **look** (zero model management, OS-managed, private), but
the realistic blocker is the **access surface** (likely Swift-framework-only →
needs a shim) and the **classifier reliability of a ~3B model**. The vllm-metal
+ Qwen2.5-7B path is already proven and remains the baseline. Treat Apple FM as
a Phase-0-gated experiment, not a commitment.
