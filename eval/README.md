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
