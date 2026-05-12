# eval/data/ — Data Workbench chart-pipeline canary

10-question canary for the `data_render_chart` path (PR1b). Deterministic — no
LLM judge: each question is sent as a chat turn to a fresh workbench session
with `fixture.csv` loaded (table `fixture`); we watch the SSE for a
`data_render_chart` tool call and score the spec's mark + encoded fields
against `questions.json`'s `expect`. The two `A*` questions are deliberate
ambiguities — they pass when the model *clarifies* instead of charting.

Run:

```
BRAIN_USER=admin BRAIN_PASS=admin python3 eval/data/run.py
BRAIN_USER=admin BRAIN_PASS=admin python3 eval/data/run.py --only C1_bar_open_per_entity
```

Server must be running (`launchctl kickstart -k gui/$(id -u)/com.brain-agent.server`).

Files:
- `fixture.csv` — 15-row synthetic risk-register table (entity, tier, status, findings_open/closed, opened_month, region).
- `questions.json` — the 10 prompts + per-question `expect` (chart yes/no, acceptable marks, required raw-column fields, optional y-aggregate-or-field check).
- `run.py` — the runner + the deterministic scorer.

This is a smoke/regression check, not a quality benchmark — chart-spec
correctness is far more checkable than prose, so a deterministic scorer beats
an LLM judge here. Run it before/after any change to `data_render_chart`, the
chart cheat-sheet in `data_viz_prompts.py`, or the workbench system prompt.
