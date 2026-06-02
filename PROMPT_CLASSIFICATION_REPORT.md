# Prompt Classification — Report

How prompt classification works in Brain: what it classifies, where it runs,
how it drives tool selection and model routing, what happens on the pure-regex
path, and what happens when a model has no benchmark data. All claims are
grounded in code — file:line references inline.

---

## 1. There are two independent classification systems

Brain has **two classifiers that have nothing to do with each other** despite the
shared word "classification". Keep them separate:

| System | What it classifies | LLM? | Drives |
|---|---|---|---|
| **Intent / auto-route classifier** | The user's *prompt* → task types, complexity, needed tools | optional (keywords / llm / hybrid) | **model routing + per-turn tool gating** |
| **Document classification (ARL 20.02.02.06)** | A *document's* sensitivity level (public/internal/confidential/strict/unmarked) | no — pure regex + heuristics | **block / force-local enforcement** (a GDPR-style seam) |

Plus the **GDPR/PII scanner** (71 regex detectors + spaCy German NER), which is a
third regex-only classifier feeding the same enforcement seam as document
classification.

This report is mostly about the **intent / auto-route classifier**, with the
document/GDPR classifiers covered where they answer your specific questions
(regex-only path, strict-config enforcement).

---

## 2. Intent classification — what it produces

Source: `brain.py` (`resolve_task_analysis`, `classify_task_structured`,
`classify_task_purpose`, ~lines 10466–10863).

The structured (LLM) classifier returns a closed-vocabulary dict
(`classify_task_structured`, brain.py:10666–10729):

```python
{
  "purpose":     str,        # one of 5 legacy purposes: coding/analysis/creative/agentic/fast
  "task_types":  [str],      # 1–3 of: coding, math, research, analysis, reporting,
                             #         creative, orchestration, agentic, fast
  "tools":       [str],      # 0–6 of 14 labels: python, bash, files, web, memory,
                             #         email, git, code_graph, delegation, scheduler,
                             #         translation, image_gen, audio, skills
  "tool_groups": [str],      # real TOOL_GROUPS names derived from `tools`
  "complexity":  "low"|"medium"|"high",
  "reasoning":   str,        # ≤200 chars
  "source":      "llm"
}
```

Vocabularies are **validated/closed** — `_TASK_TYPE_TIER` (brain.py:10538) and
`_TASK_TOOL_GROUPS` (brain.py:10557) — so a hallucinated label can't leak into
routing.

The keyword classifier (`classify_task_purpose`, brain.py:10487–10514) produces
only one of the 5 legacy purposes, by regex match-count (**≥2 hits required** to
avoid false positives).

---

## 3. When does classification run? (use cases)

### Intent / auto-route classifier

It is **not** run on every turn. It runs only when a model actually has to be
*chosen*:

**A. Interactive chat — "✨ Auto" model** (`handlers/chat.py` ~3356–3402):

```python
auto_by_agent = (agent_cfg.get("model") == "auto" and len(session.messages) == 0)
# runs when: user picked ✨ Auto in the composer (want_auto), OR
#            agent is configured model="auto" AND it's the FIRST turn
```

So it runs when:
- the user explicitly selects **✨ Auto**, or
- the agent's configured model is `"auto"` **and this is the session's first turn**.

It does **not** run when a concrete model is selected, and it does **not** re-run
on follow-up turns of an `auto`-agent session (this keeps the warm-pool KV prefix
stable). Result is recomputed per send, **not cached**; it's transient SSE
metadata, persisted only as `msg_metadata.auto_route` for the per-turn modal.

**B. Background-task fan-out / leaf tasks** (`engine/background_tasks.py` ~82–205):
when `background_task_model == "auto"`, each leaf task is classified
**independently** via `resolve_task_analysis(prompt)` to pick its own model.

### Document / GDPR classification (separate)

Runs at different points entirely:
- on **attachment upload** (`/v1/attachments/scan`) → composer severity chip
- on **tool reads** (`read_document`/`read_file`/`python_exec`/`execute_command`
  output) via `_gdpr_anon_tool_text` → `_classification_gate_tool_text`
- on **every background LLM call** via the `gdpr_pick_model_for_background` seam
  (see §7).

---

## 4. Classifier mode: keywords / llm / hybrid

Mode is read from config (`resolve_task_analysis`, brain.py:10820–10863):

```python
mode = ((_sc.get("auto_route") or {}).get("classifier_mode") or "keywords").strip()
```

- **Config key:** `config.json → auto_route.classifier_mode`
- **Default:** `"keywords"` (zero LLM cost, byte-identical legacy behavior)

| Mode | Behavior |
|---|---|
| `keywords` | regex heuristics only (`classify_task_purpose`); no LLM call |
| `llm` | call `classify_task_structured` first; **fall back to keywords** on any failure |
| `hybrid` | keywords first; call the LLM **only if keywords return `None`** (ambiguous prompts) |

**Which model runs the LLM classifier** (`_resolve_classifier_model`,
brain.py:10615–10633): it reuses `config.json → chat_summary_model`
(Settings → Server → Summaries) if set+enabled; otherwise falls to
`_resolve_auto_model_tiered(None)` (cheapest/local). LLM call is bounded:
`max_tokens=200`, `timeout_s=25.0`, `max_rounds=1`, input capped at `message[:4000]`.
**Fail-open:** any error → `None` → caller falls back to keywords. Classification
never blocks a turn.

---

## 5. How tools are determined from classification

Classification does **not** add tools — it **subtracts** them. The structured
classifier's `tool_groups` are stashed on the session
(`session._auto_tool_groups`, handlers/chat.py ~3395), then the worker computes
exclusions (`classifier_tool_exclusions`, brain.py:10771–10790):

```python
def classifier_tool_exclusions(model, tool_groups):
    if not tool_groups:
        return []                              # no signal → no gating
    if model_maintains_warm_prefix(model):
        return []                              # NEVER gate a warming model (KV prefix)
    keep = set(tool_groups) | _TOOL_GATING_NEVER_STRIP   # {"core","workflows"} floor
    excluded = []
    for gname, gtools in TOOL_GROUPS.items():
        if gname not in keep:
            excluded.extend(gtools)
    return excluded
```

The worker merges this into the per-turn `exclude_tools`
(handlers/chat.py ~1706–1711):

```python
_gate_excl = engine.classifier_tool_exclusions(_auto_rm, _auto_groups)
_ctx.exclude_tools = list(set(_ctx.exclude_tools or []) | set(_gate_excl))
```

`exclude_tools` is then applied as the **final authority** in `resolve_active_tools`
(brain.py ~1329–1332, and again after the MCP merge ~1361–1362) — excluded tools
are removed from the wire entirely and are **not even discoverable** this turn.

**Gating is skipped entirely** for any model that keeps a warm KV prefix
(`model_maintains_warm_prefix`, brain.py:10751–10768): **local models**,
**warmup-enabled models**, and the conservative `auto`/unknown case all return
`[]`. Rationale (quoted): varying the tool list changes the KV prefix — free for
cloud, but it invalidates the prefix for local/warmup models, so we gate only
models that don't warm up.

---

## 6. Per-agent deferred tool list vs. classification gating

This was your explicit follow-up. **They are two different mechanisms and they
compose; classification does not "override" the deferred list — it stacks a
second, stronger filter on top.**

**Deferred** ≠ **excluded**:

- **Deferred** (per-agent `token_config.tool_overrides.<name>.deferred`, or global
  `tool_settings.<name>.deferred`): tool is **omitted from the initial prompt** to
  save tokens, but **stays discoverable** — the model can pull its schema via
  `tool_search` and use it the same turn (`resolve_active_tools`, brain.py:1307–1320).
- **Excluded** (classification gating + web-search lockouts, via `exclude_tools`):
  tool is **removed entirely** — not in the prompt, **not discoverable**
  (brain.py:1329–1332).

Resolution order inside `resolve_active_tools`:

1. base allowed set → purpose filter
2. **subtract deferred** (still discoverable) — brain.py:1317–1320
3. **subtract excluded** (gone for the turn) — brain.py:1329–1332, repeated after MCP merge 1361–1362

**When is the per-agent deferred list still the sole authority?**
- When the model is **not** "✨ Auto" / auto-route isn't producing tool_groups
  (`_auto_tool_groups` empty) → `classifier_tool_exclusions` returns `[]`, so only
  the deferred filter runs.
- When the model **maintains a warm prefix** (local / warmup-enabled) → gating
  returns `[]` regardless of classification → deferred list alone governs.

**When does classification "overrule" it?**
- Only for **non-warmup models under ✨ Auto** with a structured `tool_groups`
  result. Then the classifier's exclusions are applied **in addition to** deferral.
  Net effect: a tool the agent merely *deferred* (still reachable via `tool_search`)
  becomes *excluded* (unreachable) if it's outside the classifier's needed groups —
  except the never-strip floor `{"core","workflows"}` (`_TOOL_GATING_NEVER_STRIP`,
  brain.py:10748), which is always kept.

**Tristate precedence for the deferred flag itself** (brain.py:1312–1316): agent
`tool_overrides.<name>.deferred` present → wins; absent → inherit global
`tool_settings.<name>.deferred`.

**MODEL_PROFILES `deferred_tool_groups` (e.g. `speed` profile = `[]`)** is a
*group-level, display/advisory* hint (engine/model_select.py:62–95; used in
`tool_breakdown`, brain.py:1064). The **actual per-turn filtering** uses the
**per-tool** `deferred` flags, not the profile's group list — so the per-tool
agent/global tristate is what really governs deferral.

---

## 7. The "strict" classification config — where it's enforced

This was your other follow-up. "Strict" is a **document-sensitivity level**, not an
intent class — it belongs to the ARL 20.02.02.06 system.

**Per-level action config** (`brain.py` ~9562–9602):

```python
# config.json → classification_scanner.per_level_action
_CLASSIFICATION_DEFAULTS = {
  "per_level_action": {
    "public":       "ignore",
    "internal":     "warn",
    "confidential": "force_local",
    "strict":       "block",     # always — locked by invariant below
    "unmarked":     "warn",
  }
}
```

**Strict-always-block invariant** (`_classification_effective_action`,
engine/classification.py:557–579) — ARL §1.11:

```python
if level == "strict":
    return "block" if cfg.get("server_block", True) else "force_local"
```

Strict resolves to `block` **regardless of admin config** — the admin
`per_level_action.strict` value is ignored. When the master `server_block` switch
is OFF, `block` degrades to `force_local` (mirrors the GDPR scanner's master
switch), never to ignore/warn.

**Where strict (and the rest) is enforced — use cases:**

1. **Attachment upload / interactive send** (`/v1/attachments/scan` + composer
   `classificationActionModal`): strict+block → Cancel only (no model-swap option);
   confidential+force_local → swap-to-local offered. Skipped if the chosen model is
   already local.
2. **Every background LLM call** (chat summary, next-prompt, delegate, scheduler,
   KG extract, memory classifier, …): `classification_pick_model_for_background`
   is called **first** inside `gdpr_pick_model_for_background`
   (brain.py ~9218–9237), before the GDPR/PII scan.
3. **Tool reads** (`read_document`/`read_file`/`python_exec`/`execute_command`):
   `_classification_gate_tool_text` (engine/classification.py:582–663). Fail-open
   on internal error.

**Error path** (`ClassificationBlockedError`, brain.py:9546–9557): it **subclasses
`GDPRBlockedError`**. That's the zero-touch trick — the 10+ background sites that
already `except GDPRBlockedError:` (brain.py:5499, 5640, 5904, 5926, 7235, 8391,
8418, 8492, 8513, 8847, 8869, …) catch classification blocks automatically and
soft-degrade (skip / fallback summary / None).

**Local models:** the gate **no-ops for local models** (`is_model_local` early
return — engine/classification.py:615; same check in the background picker
~764–769). Local models are trusted at every level. But once a `block` action
*does* fire (e.g. strict reaching a non-local model with no usable local fallback),
it is enforced regardless — there's no escape hatch past `block` itself.

---

## 8. Pure-regex / no-LLM classification path

Two places run **regex-only**:

**A. Document classification (always regex)** — `engine/classification.py`
(`detect_classification`, lines 362–450). No LLM at any point:
- **marker scan** (185–208): German (Öffentlich/Intern/Vertraulich/Streng
  Vertraulich), English (Public/Internal/Confidential), TLP (RED/AMBER/GREEN/WHITE)
- **filename hints** (262–275)
- **content heuristic** (278–312): ~20 German business-term triggers per level +
  PII finding count
- **mismatch detection** (315–357): marker level vs heuristic level → flags
  under-classification
- PDF-footer fallback via `fitz` when markitdown drops repeating footers (453–541)

The **GDPR/PII scanner** (71 regex detectors + spaCy German NER) is the same
shape — regex/NER only, no external API — and feeds the same enforcement seam.

**B. Intent classification, keyword mode** — `classify_task_purpose`
(brain.py:10487–10514): regex heuristics, **≥2 keyword hits** to fire, else
returns `None`. This is the path when:
- `classifier_mode == "keywords"` (the **default**), or
- `llm`/`hybrid` mode and the LLM call **failed/timed out/was unparseable**
  (fail-open fallback), or
- `hybrid` mode where keywords already produced a confident hit (LLM skipped).

When the regex path returns `None` (no confident class), routing does **not**
fail — it falls through to the tier heuristic (next section). Regex-only intent
classification is therefore **lossy but safe**: worst case is "no signal," which
just means model routing uses defaults and tool gating is skipped.

---

## 9. Model routing — and the no-benchmark-data fallback

Routing entry: `resolve_auto_model_for_task` → `_resolve_auto_model_tiered`
(brain.py ~11070–11197). Precedence, highest first:

1. **Attachment MIME match** (11104–11109): restrict candidates to models whose
   `raw_formats` match uploaded files; fall back to full set if none.
2. **Explicit use-case map** (11111–11126): `config.json → auto_route.task_models`
   `{task_type: model_id}`. First matching enabled task_type wins outright.
3. **Benchmark ranking** (11128–11145): if any candidate has *measured* data for
   the turn's task type → `_pick_by_benchmark(candidates, task_type, complexity)`
   (brain.py:10969). Ranks **capable → fast → cheap**, with a capability floor
   (50% base, +20 high complexity / −20 low). Benchmarks come from
   `engine/model_bench.py` (9 task types × 2 prompts, judge-scored 0–100;
   `capability` mean score, `tps` throughput tokens/sec; stored at
   `config.json → models.<id>.benchmark.<task_type>`, admin override sticky).

4. **Tier heuristic — the no-benchmark fallback** (11147–11159): when
   `bench_cell_value(model, task_type)` is `None` for every candidate (no measured
   data), benchmark ranking is skipped and routing uses:

   ```python
   tier = _shift_tier(_PURPOSE_TIER.get(purpose or "", "default"), complexity)
   ```

   - `_PURPOSE_TIER` (brain.py:10878) maps purpose → baseline tier
   - `_shift_tier` (brain.py:11054): complexity shifts it (high → up, low → down,
     medium unchanged)
   - tier → concrete model:
     - `reasoning` → first model with `thinking_format != "none"`
     - `fast` → `_pick_cheapest_cloud()` (cheapest cloud; local last)
     - `default` → highest-priority configured `default_model`

**Key property:** benchmarking "ships dark." A fresh `config.json` has **no**
benchmark data, so **every** model routes by tier+complexity until an admin runs
benchmarks. The fallback **always returns a concrete model** — never empty, never
an error. As soon as measured data exists for a task type, routing switches to
empirical ranking for that cell only; un-benchmarked cells keep using the tier
heuristic. The two coexist per-(model, task_type).

---

## 10. One-paragraph summary

When a model must be chosen (✨ Auto, or first turn of an `auto`-agent, or an
`auto` background leaf task), Brain classifies the prompt — by regex
(`keywords`, the default), by LLM (`llm`), or regex-then-LLM-on-miss (`hybrid`),
LLM-failures falling open to regex. The result picks a model
(use-case map → benchmark ranking → **tier+complexity heuristic when no benchmark
data exists**) and, for non-warmup models only, **excludes** tools outside the
classifier's needed groups (a harder filter than the per-agent *deferred* list,
which only hides tools from the initial prompt while leaving them discoverable;
deferral and exclusion stack, and the never-strip `core`/`workflows` floor always
survives). Separately, the regex-only **document-sensitivity** classifier and the
GDPR/PII scanner enforce per-level actions at the same background/tool/upload
seams — with **strict always resolving to `block`** (or `force_local` if the master
switch is off) regardless of admin config, surfaced via
`ClassificationBlockedError` (a `GDPRBlockedError` subclass) and no-oped for local
models.
```

