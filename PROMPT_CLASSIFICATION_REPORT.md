# Prompt Classification — Architecture Report

How Brain classifies a user's prompt to automatically choose a model and restrict
the available tools for a turn — the **intent / auto-route** classifier. From the
high-level flow down to the source, plus the end-user GUI and settings dialogs.

> **Scope:** intent / auto-route classifier (v9.55.0–9.57.1). Deliberately
> **excludes** the document-sensitivity (ARL 20.02.02.06) and GDPR/PII classifiers,
> which are separate regex-only systems feeding a different enforcement seam.
>
> Source-grounded — `file:line` references are inline and reflect the repo at the
> time of writing (they may drift).

---

## 1. High-level overview

**One job:** turn the user's message into a model choice and a tool budget — without
the user picking either.

When a user types a prompt and the model is set to **"✨ Auto"**, Brain does not
immediately answer. It first **classifies the prompt** into task types, a complexity
level, and the set of tool families the task is likely to need. That classification
then drives two decisions:

1. **Which model** handles the turn (a cheap local model for a yes/no question; a
   reasoning model for a hard research task).
2. **Which tools** are exposed to that model for the turn (a coding prompt doesn't
   need the email or translation tools cluttering the context).

### The pipeline at a glance

| # | Step | What happens | Source |
|---|------|--------------|--------|
| 1 | **Trigger** | User sends a prompt under `model="auto"` (or an auto-agent's first turn, or an auto background task). | `handlers/chat.py:3356` |
| 2 | **Classify** | `resolve_task_analysis(message)` picks a path: keywords (regex), LLM, or hybrid. Produces `task_types`, `complexity`, `tool_groups`. | `brain.py:10820` |
| 3 | **Route model** | benchmark ranking → tier+complexity heuristic (raw `image/*` uploads narrow the pool first to vision models; convertible docs don't). | `brain.py:11070` |
| 4 | **Gate tools** | For non-warmup models only, exclude every tool group the classifier didn't ask for (keeping a `core`/`workflows` floor). | `brain.py:10771` |
| 5 | **Run & report** | The turn streams under the chosen model; a per-turn modal exposes the whole decision to the user. | — |

> **Design constraint that shapes everything:** tool gating changes the per-turn
> tool list, which **invalidates the warm KV-cache prefix** of local / warmup models.
> So gating is applied **only to models that never warm up** (cloud, no warmup). For
> warm-prefix models the classifier still picks the model but leaves the full toolset
> intact. — `brain.py:10751`

---

## 2. What the classifier produces

A closed-vocabulary structured object — no free-form labels reach routing.
Vocabularies are fixed lists (`_TASK_TYPE_TIER`, `_TASK_TOOL_GROUPS`), so a
hallucinated label can't leak into the router.

```python
{
  "purpose":     str,        # one of 5 legacy purposes: coding | analysis | creative | agentic | fast
  "task_types":  [str],      # 1–3 of: coding, math, research, analysis, reporting,
                             #         creative, orchestration, agentic, fast
  "tools":       [str],      # 0–6 of 14 labels: python, bash, files, web, memory, email,
                             #         git, code_graph, delegation, scheduler, translation,
                             #         image_gen, audio, skills
  "tool_groups": [str],      # real TOOL_GROUPS names derived from `tools`
  "complexity":  "low" | "medium" | "high",
  "reasoning":   str,        # ≤200 chars
  "source":      "llm"
}
```
`brain.py:10666–10729 — classify_task_structured()`

The **keyword** path is leaner: it returns only one of the 5 legacy `purpose` values,
by regex match-count, requiring **≥2 hits** to fire (avoiding single-word false
positives). — `brain.py:10487–10514`

---

## 3. When does it run?

Classification has **two jobs with different triggers**:

- **Model routing** — only when a model has to be *chosen* (✨ Auto, auto-agent first
  turn, auto background leaf). This is the gated path below.
- **Tool deferral** — on **every** turn, including concrete-model turns. On a non-Auto
  turn the classifier runs purely to reshape tool deferral; it does **not** change the
  model. Skipped for warm/local models (no classifier cost, KV prefix stays stable).

| Use case | Triggers… | Condition | Source |
|----------|-----------|-----------|--------|
| **Interactive chat — ✨ Auto** | model + tools | User selected `✨ Auto` → `want_auto` → `model="auto"`. | `handlers/chat.py:3239,3367` |
| **Auto-agent first turn** | model + tools | `agent.model == "auto"` **and** `len(session.messages) == 0`. | `handlers/chat.py:3356` |
| **Concrete-model turn (cloud)** | **tools only** | Any non-Auto turn on a non-warmup model → classify for deferral, model untouched. | `handlers/chat.py:3405` |
| **Background fan-out leaf** | model + tools | `background_task_model == "auto"` → each leaf classified independently. | `engine/background_tasks.py:82–205` |

> **Not cached.** The classification is recomputed on every qualifying send. The
> result is transient SSE metadata, persisted only as `msg_metadata.auto_route` so
> the per-turn modal can replay it after a reload. It never blocks the turn — on any
> classifier error it fails open.

---

## 4. Classifier modes — keywords / llm / hybrid

An admin chooses how much (if any) LLM cost to spend on classification. Mode is read
from `config.json → auto_route.classifier_mode`, default `"keywords"`.
— `brain.py:10820 — resolve_task_analysis()`

| Mode | Behavior | LLM call? |
|------|----------|-----------|
| `keywords` *(default)* | Regex heuristics only (`classify_task_purpose`). Zero latency, zero cost. | Never |
| `llm` | Call `classify_task_structured` first; **fall back to keywords** on any failure / timeout / unparseable reply. | Every qualifying turn |
| `hybrid` | Keywords first; call the LLM **only when keywords return `None`** (ambiguous prompts). | Only on keyword miss |

**Which model runs the LLM classifier?** It reuses the **summary model**
(`config.json → chat_summary_model`, Settings → Server → Summaries) if set and
enabled; otherwise the cheapest / local model via `_resolve_auto_model_tiered(None)`.
The call is bounded: `max_tokens=200`, `timeout_s=25`, `max_rounds=1`, input capped at
`message[:4000]`. — `brain.py:10615–10729`

---

## 5. How the model is routed

A three-level precedence ladder — the first level that produces a model wins.

| Level | Rule | Source |
|-------|------|--------|
| **A. Attachment MIME match** | Only for raw `image/*` uploads (the sole MIME the chat router sends raw): restrict candidates to vision models whose `raw_formats` match. Convertible documents (PDF/docx/…) are turned into markdown downstream and read by any text model, so they don't narrow the pool. Audio/video use a separate transcription path, not this router. | `brain.py:11104` |
| **B. Benchmark ranking** | If any candidate has *measured* data for the task type, rank **capable → fast → cheap** (capability floor 50%, ±20 by complexity). | `brain.py:10969,11128` |
| **C. Tier + complexity heuristic** | The fallback when no benchmark data exists (see §7). Always returns a concrete model. | `brain.py:11147` |

**What benchmarks measure:** per model × task type, a judge model (the server default)
scores answers 0–100 (`capability`), and throughput is recorded as tokens/sec (`tps`,
length-independent). Stored at `config.json → models.<id>.benchmark.<task_type>`; an
admin `override` is sticky across re-runs. — `engine/model_bench.py`

---

## 6. How tools are determined (deferral)

Classification reshapes which tools are **in the initial prompt** — it does **not**
remove anything. The classifier's `tool_groups` drive a two-way deferral adjustment:
un-needed groups are pushed *out* of the prompt (but stay `tool_search`-discoverable),
and needed groups are pulled *in* even if they were statically deferred. **This runs on
every turn** — not just ✨ Auto — and it **only touches tool deferral, never the model**.

```python
def classifier_tool_deferral(model, tool_groups):
    if not tool_groups:
        return [], []                            # no signal → static deferral stands
    if model_maintains_warm_prefix(model):
        return [], []                            # NEVER reshape a warming model (KV prefix)
    keep = set(tool_groups) | _TOOL_GATING_NEVER_STRIP   # {"core", "workflows"} floor
    defer_extra, undefer = [], []
    for gname, gtools in TOOL_GROUPS.items():
        if gname in keep:
            if gname not in _TOOL_GATING_NEVER_STRIP:
                undefer.extend(gtools)           # needed → pull INTO prompt (even if deferred)
        else:
            defer_extra.extend(gtools)           # un-needed → push OUT (still discoverable)
    return defer_extra, undefer
```
`brain.py:10785` · applied at `handlers/chat.py:1709` → request-context
`defer_extra_tools`/`undefer_tools` → folded into `resolve_active_tools` `brain.py:1307`

### Defer vs. exclude — the gate now defers, not excludes

| | **Deferred** (incl. classifier reshape) | **Excluded** |
|---|---|---|
| Set by | per-agent/global config **+ per-turn classifier `defer_extra`/`undefer`** | web-search lockouts only, via `exclude_tools` |
| Effect | omitted from the *initial* prompt, but stays **discoverable** — the model can pull its schema via `tool_search` and still use it the same turn | removed **entirely** — not in the prompt and **not discoverable** |
| Source | `resolve_active_tools brain.py:1307` | `resolve_active_tools brain.py:1329,1361` |

The classifier adjustment folds into the **deferred** column: `undefer` wins over
`defer_extra` (a needed tool is never re-deferred), and the never-strip floor
(`core`/`workflows`) is always in-prompt. A misclassification is now **recoverable**
mid-turn via `tool_search` — the earlier exclude-based gate made it a dead end.

> **When classification doesn't reshape tools:** whenever there's no signal (keyword-mode
> miss / fail-open) *or* the model maintains a warm prefix (local / warmup) —
> `classifier_tool_deferral` returns `([],[])`, so only the static deferral config
> applies. **Warm/local models are never optimized, on any turn** (the every-turn
> classification is also skipped for them, so no classifier cost is paid). The `MODEL_PROFILES`
> per-profile `deferred_tool_groups` (e.g. `speed` = `[]`) is advisory/display only;
> the real per-turn filter uses the **per-tool** tristate flags.

---

## 7. No benchmark data? The tier fallback

Benchmarking "ships dark" — a fresh install has zero measured data, and routing still
works. When `bench_cell_value(model, task_type)` is `None` for every candidate,
benchmark ranking is skipped and routing falls to the tier heuristic:

```python
tier = _shift_tier(_PURPOSE_TIER.get(purpose or "", "default"), complexity)
# _PURPOSE_TIER     : purpose → baseline tier            (brain.py:10878)
# _shift_tier       : high → bump up, low → bump down     (brain.py:11054)
#   reasoning  → first model with thinking_format != "none"
#   fast       → _pick_cheapest_cloud()  (cheapest cloud; local last)
#   default    → highest-priority configured default_model
```
`brain.py:11147–11159`

> **Always returns a concrete model** — never empty, never an error. As soon as an
> admin benchmarks a model for a task type, routing switches to empirical ranking
> **for that cell only**; un-benchmarked cells keep using the tier heuristic. The two
> coexist per `(model, task_type)`.

---

## 8. The end-user GUI

What a user actually sees and clicks. (UI strings are German; translations follow in
*italics*.)

### 8.1 — Selecting ✨ Auto in the composer

In the model dropdown, an extra option sits above the concrete models:

```
┌─ Composer · model dropdown ──────────┐
│ ✨ Auto            ← selected         │
│ Sonnet 4.6                           │
│ Opus 4.8                             │
│ devstral-small-latest                │
└──────────────────────────────────────┘
```

Tooltip on the option: *"Selects the best fitting model automatically for each
message"* (`Wählt für jede Nachricht automatisch das am besten passende Modell`).
— `settings_agent.js:218` · `utils.js:146`

### 8.2 — The status indicator during the turn

The composer label stays `✨ Auto`, but while the answer streams, the status spinner
shows the **model auto-route actually picked**, and hovering the composer shows the
reason (`chat.autoReason`, e.g. *"Detected research → Opus 4.8"*).
— `index.html:229` · `chat_send.js:831,915` · `nav.js:320`

### 8.3 — The per-turn classification modal

Every assistant reply routed via ✨ Auto gets a small ◔ icon button next to it
(tooltip: *"Show prompt classification & routing decision"*). Clicking it opens the
decision modal — `openClassificationModal(idx)`:

```
┌─ Promptklassifikation & Routing ───────────────────────────  × ─┐
│                                                                  │
│ KLASSIFIKATION · classification                                  │
│   Aufgabentypen · task types     [research] [analysis]           │
│   Benötigte Tools · needed tools [web] [memory]                  │
│   Komplexität · complexity       hoch · high                     │
│   Begründung · reasoning         "Multi-source research question │
│                                   requiring synthesis"           │
│ ─────────────────────────────────────────────────────────────── │
│ MODELLENTSCHEIDUNG · model decision                              │
│   Gewähltes Modell · chosen model   Opus 4.8                      │
│   Warum · why          Detected research, complexity high →       │
│                        reasoning tier                            │
│ ─────────────────────────────────────────────────────────────── │
│ TOOL-AUSWAHL · tool selection                                    │
│   Status     [aktiv — Toolset eingeschränkt] · active—restricted │
│   Aktive Gruppen · kept     core · workflows · web · memory       │
│   Entfernte Gruppen · removed  email · git · code_graph ·         │
│                                translation · scheduler           │
└──────────────────────────────────────────────────────────────────┘
```
`chat_render.js:1000` (button) · `1566–1627` (modal) · gating text from
`brain.py:10793 classifier_gating_decision()`

> For a **local or warmup model**, the Tool-Auswahl section flips to *"nicht
> angewendet — volles Toolset"* (*not applied — full toolset*) with the reason *"model
> keeps a warm KV prefix … — tools not gated to preserve it"*. In keyword mode with no
> signal it reads *"no LLM classification (keyword mode or no signal)"*.

---

## 9. Settings dialogs (admin)

Three admin surfaces govern the classifier. All under Settings.

### 9.1 — Auto-Routing mode

Settings → General → Server → **Auto-Routing** section.
— `settings_general_tabs.js:73–94`

- **Mode labels** (verbatim):
  - `Schlüsselwörter (Standard, ohne Kosten)` — *Keywords (default, no cost)*
  - `LLM (klassifiziert per günstigem/lokalem Modell)` — *LLM (via cheap/local model)*
  - `Hybrid (erst Schlüsselwörter, LLM nur bei Bedarf)` — *Hybrid (keywords first, LLM only if needed)*
  - Apply button: `Setzen` (*Set*)

### 9.2 — Summary model (the LLM classifier's engine)

Settings → General → Server → **Zusammenfassungen** (*Summaries*). The model chosen
here (`chat_summary_model`) is reused by the LLM / Hybrid classifier.
— `settings_general_tabs.js:64–72`

### 9.3 — Benchmarks

Settings → Models. A run button measures every enabled model × 9 task types; results
feed level C of the router. — `settings_general_tabs.js:179,321–353` ·
`POST /v1/models/config {action:"benchmark"}` · `GET /v1/models/benchmark/status`

- **Run-all button:** `Benchmark: alle aktivierten` (*Benchmark: all enabled*).
- **Columns:** `Aufgabe` (task) · `Gemessen` (measured: `capability% · tps tok/s`) ·
  `Override %` · `Override tok/s`.
- **Buttons:** `Dieses Modell benchmarken` (this model only) · `Overrides speichern`
  (save sticky overrides).
- **Progress:** `Benchmark: {done}/{total} · {model}`, then `Fertig` (*Done*).

```
┌─ Settings · Models · Benchmark ───────────────────────┐
│ [ Benchmark: alle aktivierten ]                        │
│ ┌──────────────┬──────────────┬──────────┬──────────┐ │
│ │ Aufgabe      │ Gemessen     │ Override%│ Override  │ │
│ ├──────────────┼──────────────┼──────────┼──────────┤ │
│ │ coding       │ 88% · 64 t/s │    —     │    —     │ │
│ │ research     │ 91% · 41 t/s │    —     │    —     │ │
│ │ fast         │ 72% · 120t/s │    —     │    —     │ │
│ └──────────────┴──────────────┴──────────┴──────────┘ │
│ [ Dieses Modell benchmarken ]  [ Overrides speichern ] │
└────────────────────────────────────────────────────────┘
```

---

## 10. Worked end-to-end examples

Three concrete prompts, each through the whole pipeline.

### Example A — "ja oder nein" (trivial)

**User** sends under ✨ Auto: *"Ist 17 eine Primzahl? Nur ja oder nein."*

- **Classify** (keyword mode): matches `fast` patterns (quick / yes-or-no).
  `complexity=low`.
- **Route**: tier for `fast` → complexity `low` shifts down → `_pick_cheapest_cloud()`
  → a cheap cloud model.
- **Tools**: cheap cloud model is non-warmup → gating applies. `tool_groups`
  empty/minimal → everything but the `core` / `workflows` floor excluded.
- **Modal**: Aufgabentypen `fast` · Komplexität *gering* · Status *aktiv*.

### Example B — "fix this Python traceback" (coding)

**User**: *"Here's a stack trace from my pytest run — find the bug and patch it."*
(LLM mode)

- **Classify** (LLM): `task_types=[coding]`, `tools=[python, files, git]`,
  `complexity=medium`.
- **Route**: no attachment to narrow the pool → benchmark ranking for `coding`
  (capable → fast → cheap), or the tier heuristic if `coding` isn't benchmarked yet.
- **Tools**: kept = `core`, `workflows`, `code_exec`, `documents`, `git`; removed =
  `email`, `web`, `translation`, …
- **Modal**: Gewähltes Modell **(benchmark/tier winner)** · Warum *"Detected coding,
  complexity medium"*.

### Example C — same prompt, but the model is local

Suppose the resolved/selected model is a **local** model (or a cloud model with
`warmup:true`).

- **Classify & route**: unchanged — the model is still chosen.
- **Tools**: `model_maintains_warm_prefix` is `true` → `classifier_tool_exclusions`
  returns `[]`. **No gating.** The full toolset (minus per-agent *deferred* tools) is
  exposed, preserving the KV prefix.
- **Modal**: Tool-Auswahl shows *nicht angewendet — volles Toolset*, reason *"model
  keeps a warm KV prefix … — tools not gated to preserve it"*.

---

## Code map

| Concern | Function | Location |
|---------|----------|----------|
| Trigger gate (interactive) | `want_auto` / `auto_by_agent` | `handlers/chat.py:3239,3356,3367` |
| Mode dispatcher | `resolve_task_analysis` | `brain.py:10820–10863` |
| Keyword classifier | `classify_task_purpose` | `brain.py:10487–10514` |
| LLM classifier | `classify_task_structured` | `brain.py:10666–10729` |
| LLM classifier model pick | `_resolve_classifier_model` | `brain.py:10615–10633` |
| Model routing entry | `resolve_auto_model_for_task` | `brain.py:11162–11197` |
| Tiered routing ladder | `_resolve_auto_model_tiered` | `brain.py:11070–11159` |
| Benchmark ranking | `_pick_by_benchmark` | `brain.py:10969` |
| Benchmark harness | prompts + judge loop | `engine/model_bench.py` |
| Tool gating | `classifier_tool_exclusions` | `brain.py:10771–10790` |
| Warm-prefix guard | `model_maintains_warm_prefix` | `brain.py:10751–10768` |
| Gating transparency text | `classifier_gating_decision` | `brain.py:10793–10817` |
| Final tool resolution | `resolve_active_tools` | `brain.py:1307,1329,1361` |
| Composer Auto option | model dropdown | `settings_agent.js:218` · `utils.js:146` |
| Per-turn modal | `openClassificationModal` | `chat_render.js:1000,1566–1627` |
| Auto-Routing mode setting | Auto-Routing section | `settings_general_tabs.js:73–84` |
| Benchmark settings | benchmark grid | `settings_general_tabs.js:179,321–353` |
| Status spinner / reason | `spinner-model` / `autoReason` | `index.html:229` · `chat_send.js:831,915` · `nav.js:320` |
