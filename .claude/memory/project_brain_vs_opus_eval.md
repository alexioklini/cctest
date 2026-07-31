---
name: Brain-vs-Opus eval harness (eval/ directory)
description: 2026-05-01 — 15-question canary harness with claude -p (Opus 4.7 via Max subscription) as gold standard, Brain as deployed, third Opus call as judge against rubric.md
type: project
originSessionId: b72f1f43-2339-41d1-a5ee-4e57d6582617
---
Built `eval/` to measure the Brain-vs-Opus quality gap as Brain config changes (KG on/off, closet rerank, model swaps, prompt edits). Run before a tweak, run after, look at the delta.

**Files:**
- `eval/questions.json` — 15 questions across 5 buckets (retrieval / precision / multi_doc / refusal / citation), JSON-editable, hot-reloaded each run
- `eval/rubric.md` — 5-axis rubric (retrieval, precision, citation, refusal, composition) with explicit calibration anchors
- `eval/disciplines.md` / `citation_only.md` — optional inject for the gold side (default: `none`, Opus runs naked)
- `eval/gold_context.md` — ALWAYS injected; tells Opus the palace path + wing (`project__f201b24ff6a2`) + room (`general`). Without this, Opus refuses on procedural grounds because vanilla mempalace MCP has no project_scope.
- `eval/mcp.json` — vanilla `mempalace-mcp --palace /Users/alexander/.mempalace/brain` for Claude Code
- `eval/config.json` — runtime knobs; Brain default model is `mistral-experimental/mistral-small-2603` (the v8.22.0-validated default), `max_turns: 25` for gold (10 was too tight — vanilla mempalace needs search→list→read→compose, multi-rep)
- `eval/run.py` — orchestrator; reads BRAIN_USER/BRAIN_PASS from env

**How to run:**
```
export BRAIN_USER=admin BRAIN_PASS=admin
python3 eval/run.py                                # full 15Q run, ~50-60 min
python3 eval/run.py --only R1_multilogin           # one question
python3 eval/run.py --skip-gold --skip-brain --reuse-results <prior>  # re-judge under edited rubric
python3 eval/run.py --brain-model <model> --label <tag>   # sweep
```

**Why Opus via `claude -p` (not API):** Max subscription is OAuth-billed, `total_cost_usd: 0` in JSON output confirms it. ~45 Opus calls per full run (15 gold + 15 judge + per-question retries on flaky JSON output).

**Smoke validation (R1_multilogin):**
- Gold (Opus, no disciplines, 25 turns): 0.98 — correctly detected that no dedicated Multilogin approval rule exists, marked it "nicht spezifiziert"
- Brain (mistral-small-2603, project instructions): 0.68 — fabricated approval roles by transferring Administrationszugänge / Sammelberechtigung rules onto Multilogin
- Judge caught the exact failure mode (transfer-from-adjacent-section) we've been chasing all week

**Known gotchas:**
- `claude -p --output-format json`: when Opus hits `--max-turns`, returns `{is_error:true, subtype:"error_max_turns", result:None}`. The runner's `extract_text_from_claude_json` surfaces a `[CLAUDE_CODE_ERROR: ...]` marker so the judge sees the failure rather than dredging up a session UUID.
- `claude -p` occasionally returns just a session uuid even on success — runner retries judge once.
- Pass the prompt via stdin to `claude -p`, NOT as final argv — argparse can swallow it after a flag like `--tools`.
- Don't pass `--mcp-config "{}"` — invalid; just omit `--mcp-config` for the judge.

**Project disciplines stay out of scope on the gold side by default.** Opus runs naked because the disciplines were a Mistral crutch. Switch to `--disciplines citation_only` or `--disciplines full` for apples-to-apples prompt comparisons.

**`results/` is gitignored.** Per-run dir snapshots config + questions + rubric + disciplines so each run is fully reproducible. summary.csv + summary.md per run.
