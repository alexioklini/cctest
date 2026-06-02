# Brain vs Opus eval

Compares Brain-as-deployed (whatever's in `agents/main/projects/KG-Real-Policies/project.json` + current model config) against Claude Code with Opus 4.7 + vanilla MemPalace MCP, both querying the same palace at `/Users/alexander/.mempalace/brain`. A third Opus call judges both answers against `rubric.md`.

The point is to measure the gap **as Brain config changes** (KG on/off, closet rerank on/off, model swaps, prompt edits). Run before a tweak, run after, look at the delta.

## Setup

Claude Code must be authenticated with your Max subscription (`claude auth status`). When `total_cost_usd: 0` shows up in the gold-run JSON, that confirms it's billing OAuth, not API.

Brain server must be running on `http://127.0.0.1:8420`. The `KG-Real-Policies` project must exist with the corpus mined into the palace.

```
export BRAIN_USER=admin
export BRAIN_PASS=admin
```

## Usage

```bash
# Full run (15 questions × 3 calls each ≈ 45 Opus calls + 15 Brain calls)
python3 eval/run.py

# Subset
python3 eval/run.py --only R1_multilogin,F1_geldwaesche,C1_ki_policy_bullets

# Override discipline injection on the gold side
python3 eval/run.py --disciplines citation_only
python3 eval/run.py --disciplines full

# Re-grade an existing run with the same answers (e.g. after editing the rubric)
python3 eval/run.py --skip-gold --skip-brain --reuse-results eval/results/<earlier_dir>

# Sweep a Brain model change
python3 eval/run.py --brain-model mistral-experimental/mistral-small-2603 --label small-2603
python3 eval/run.py --brain-model gemma-4-26B-A4B-it-MLX-4bit --label gemma-26b
```

## Output

Per run, under `eval/results/<timestamp>_disc-<mode>[_<label>]/`:

```
run.json                # full config snapshot for this run
questions.json          # snapshot of the question set used
rubric.md               # snapshot of the rubric used
disciplines_active.md   # snapshot of injected disciplines (if any)
summary.csv             # one row per question, all axis scores
summary.md              # human-readable table with means + win counts
<question_id>/
  question.json
  gold.json             # full claude -p output (text + tool calls + cost=0 on Max)
  brain.json            # Brain SSE done-event payload + tool events
  judge.json            # rubric output: per-axis scores + comparison
```

## Editing the question set

`eval/questions.json` is plain JSON, hot-reloaded each run. To add a question:

1. Pick a unique `id` (convention: `<bucket-letter><n>_<short_slug>`).
2. Set `bucket` to one of `retrieval`, `precision`, `multi_doc`, `refusal`, `citation`.
3. List `expected_docs` (basenames, no path). Empty list for refusal questions.
4. Set `expected_refuse: true` if the topic is NOT in the 58-PDF corpus.

The runner reads the file fresh each invocation; no rebuild step.

## Editing the rubric

`eval/rubric.md` defines axes, scoring anchors, and total formula. Changes to the rubric should be paired with `--skip-gold --skip-brain --reuse-results <prior>` to re-judge old answers under the new rubric — that's how you tell whether a rubric edit actually behaves the way you want before burning fresh inference on it.

## Disciplines

Three modes via `claude_code.disciplines` in `config.json` or `--disciplines`:

- `none` (default) — Opus runs naked. This is the honest baseline.
- `citation_only` — Opus is told to use the per-claim verbatim-quote citation form. Useful to isolate "is the citation gap a model gap or a prompt gap?"
- `full` — Opus receives the same disciplines block Brain has in its project instructions. Apples-to-apples on prompt; only the model differs.

Brain always runs as deployed — never inject anything extra on the Brain side.

## Cost / quota notes

Each full eval = ~45 Opus calls (15 gold + 15 judge + judge has a small input footprint compared to gold). On a Max subscription this is OAuth-billed against your quota, not per-call API.

Watch out for CLIProxyAPI tunneling: per `feedback_cliproxy_quota.md`, runaway tool loops can exhaust the shared 5-hour Claude quota. The runner caps `max_turns: 25` for gold (vanilla mempalace MCP needs search → list → read → compose, often multi-rep — 10 was too tight) and `max_turns: 1` for the judge to keep that bounded.

## A full gold run takes ~3-4 minutes per question

That's the honest number with `max_turns: 25` against vanilla mempalace MCP. The smoke test with `max_turns: 10` ran in 27s but ended in `error_max_turns` and produced no usable answer. Don't lower `max_turns` to save time — Opus needs that headroom or you get garbage gold answers and an unfair comparison.

A full 15-question run = 15 × ~3min gold + 15 × ~10s Brain + 15 × ~30s judge ≈ 50–60 min wall clock.

---

# web_fetch optimization eval (`web_fetch_eval.py`)

A SEPARATE, self-contained suite (no Brain server, no Claude Code) that measures
whether the `web_fetch` optimizations behave well. **Gold = optimizations OFF,
web_fetch returns the COMPLETE page.** For each case the same URL is fetched twice
— the optimized path and the optimization-off (gold) path — a model answers the case
question from each fetch, and Mistral judges (per-mode rubric) whether the optimized
answer is good enough vs the gold answer.

**Two kinds of optimization, scored differently:**
- **Lossless-completeness** (`academic`, `brain_code`, `conversion`) — must NOT lose
  answer-critical content. `content_loss=true` / `winner="gold"` = it dropped
  something the answer needed (a defect).
- **Triage-sufficiency** (`abstract`) — abstract's JOB is to summarize so the full
  fetch is OFTEN UNNEEDED. So it's scored on whether the ~1500-char survey is
  SUFFICIENT for a gist/relevance-level question (gold = the full-page answer as
  truth). The success signal is **`paid_off`** — survey sufficient AND ≥50% smaller
  ⇒ the full fetch was avoided. `content_loss` fires for abstract ONLY when the
  survey is too thin/wrong for even the gist (e.g. it returned page nav, not prose).

It calls `engine.tools.misc_tools.tool_web_fetch` **in-process** (it only needs
`import brain` lazily), so the two fetches run the real production code. Each mode's
gold is produced by narrowly disabling exactly ONE optimization (monkeypatch), so the
only difference between the two answers is that optimization.

## Modes (one per optimization)

| mode | optimized path | gold (optimization off) |
|------|----------------|-------------------------|
| `abstract` | `mode="abstract"` (~1500-char survey) | `mode="full"` (whole page) — full-page answer is truth; survey scored on triage sufficiency |
| `academic` | landing URL auto-rewritten to full-text PDF | rewrite bypassed → raw HTML wrapper |
| `brain_code` | matched-region trim of a large GitHub-raw file (seeds a recorded brain_code region so the trim fires) | no recorded region → full file |
| `conversion` | the tool's auto `fetch_method` (raw/markitdown/crawl4ai) | same auto path (static pages: opt==gold ⇒ confirms conversion lost nothing) |

`abstract` should mostly **pay off** (sufficient survey, full fetch avoided) — that's
the whole point of the optimization; it only flags loss when the survey genuinely
fails the gist. `academic` should show NO loss (it *adds* completeness: full PDF ≫
wrapper). `conversion` should show no loss on the content it converts. `brain_code`
trades context for token savings — watch for it clipping the matched region.

## Usage

```bash
# Full run (5 cases × 2 fetches + 2 answers + 1 judge each). Needs internet; no server.
python3 eval/web_fetch_eval.py

# Subset
python3 eval/web_fetch_eval.py --only ABS1_mdn_http_caching,BC1_region_trim

# Swap answer / judge model (provider-scoped id from config.json[models])
python3 eval/web_fetch_eval.py --answer-model mistral-medium-3.5 --judge-model mistral-medium-3.5
```

Output → `eval/results/webfetch_<ts>/`: per-case `fetch.json` (both raw fetches +
lengths + fetch_method), `result.json` (both answers + judge), plus `summary.{csv,md}`.

## Editing cases

`eval/web_fetch_cases.json` — each case: `id`, `mode`, `url`, `question`,
`rationale`, and (brain_code only) `brain_code_anchor` (a line that exists VERBATIM
in the fetched file — it simulates the chunk a brain_code query matched; the trim
relocates it by fingerprint, so it must match exactly). For **abstract** cases use a
GIST/relevance-level question and a NON-academic URL (arxiv `/abs` would also fire
the academic rewrite). For the **lossless** modes pick a `url` whose answer lives
PAST the optimization's reduction point, and one small enough that the gold
reference fits under the 60k answer-model cap (RFC-sized pages get truncated and mask
loss — web_fetch's own `max_length` cap is 50k).

## Baseline (2026-06-02, mistral-medium-3.5 answer+judge) — after the v9.60.2 fixes

**0/6 content-loss; 4/6 paid_off.** All three abstract cases + brain_code now pay
off (full fetch avoided); academic + conversion are ties with no loss.

This baseline originally surfaced TWO real defects, both since FIXED in v9.60.2
(`_trim_to_brain_code_regions` + `_to_abstract` in `engine/tools/misc_tools.py`):
- **brain_code** (BC1) had `content_loss=0.75` — the trim's fixed window clipped the
  tail of a longer matched method (`raise_for_status` losing the `HTTPError` tail).
  `_block_end_line` now extends the window to the end of the matched code block.
- **abstract** on Wikipedia (ABS2) had `content_loss` — `_to_abstract` returned the
  converted lead, which on chrome-heavy pages is nav/ToC/infobox, not prose.
  `_lead_prose`/`_is_prose_line` now assemble the survey from real prose lines only.

Re-run after touching either function to confirm they stay at 0 content-loss.
