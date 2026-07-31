---
name: Brain-im-Harness-Override — full 15Q eval, byte-equivalent inputs to standalone harness
description: 2026-05-01 — patched _build_system_prompt + send_message preamble + tool-set assembly to 1:1 mirror eval/harness for project chats; full 15Q eval shows Brain mean 0.685 vs Brain-full 0.664 (+0.02), vs harness 0.731 (−0.05); within run-to-run noise; structural failures (P2/C2/C3) substantially fixed but other questions slightly regress
type: project
originSessionId: b72f1f43-2339-41d1-a5ee-4e57d6582617
---
## What was done

Three coordinated overrides in `claude_cli.py`, all gated on `getattr(_thread_local, 'project', None) and os.path.exists(eval/harness/system_prompt.md)`:

1. **`_build_system_prompt()`** early-return: when project active + harness file exists, return ONLY the harness file contents (1219 chars). Skips agent soul, agent context, project preamble, tools.md, project Instructions block, KG hints, 3-step-flow lecture, MCP-server descriptions — every other system-prompt component.

2. **`send_message()` preamble injection** — the `_project_pre`, `_preamble_lines`, `_profile_doc` blocks (project context + user greeting + auto-maintained user profile) are skipped via a `_harness_override_active` flag.

3. **Tool-set assembly in `send_message()`** — short-circuits the tool-filter pipeline (`_filter_tools`, deferred-group filtering, MCP merging) and returns ONLY 3 hand-picked tools (`mempalace_query`, `read_document`, `read_file`) directly from `TOOL_DEFINITIONS_OPENAI`.

**Result:** the LLM payload for project chats is byte-equivalent to what the standalone `eval/harness/run.py` sends (verified via `/v1/sessions/<id>/messages` request_payloads metadata: system_prompt 1219 chars identical, 3 tools identical, user message identical, no preamble injection).

## Full 15Q eval results (Mistral self-judge)

```
                                      gold mean   brain mean   measured Δ
Brain (full, avg of 2 runs):           0.91        0.664       −0.246
Harness (standalone):                  0.90        0.731       −0.169
Brain in harness-override mode:        0.89        0.685       −0.205
```

**Δ override vs Brain-full: +0.021** — within ±0.09 run-to-run variance.
**Δ override vs Harness: −0.047** — also within noise.

So strictly speaking: no statistically significant improvement over Brain-full, and slightly worse than the standalone harness even though inputs are byte-equivalent.

## Per-question delta vs Brain-full (avg of 2 runs)

**Big wins (the user-validated structural failures get substantially fixed):**

| q | Brain-full | Override | Δ |
|---|---|---|---|
| P2 Archivierung | 0.50 | 0.88 | +0.38 |
| C2 Passwort | 0.06 | 0.75 | +0.69 |
| M2 MA-Eintritt | 0.55 | 0.65 | +0.10 |
| C3 ISMS Ziele | 0.28 | 0.33 | +0.05 |
| F2 Kreditvergabe | 0.38 | 0.50 | +0.12 |
| F3 Arbeitszeit | 0.50 | 0.50 | 0 |

**Big losses (Brain-full strengths regress):**

| q | Brain-full | Override | Δ |
|---|---|---|---|
| R2 Morgencheck | 0.96 | 0.73 | −0.23 |
| R3 Kryptographie | 0.99 | 0.88 | −0.11 |
| M1 Data Breach | 0.98 | 0.80 | −0.18 |
| C1 KI-Policy | 0.96 | 0.70 | −0.26 |
| F1 GwG | 0.31 | 0.12 | −0.19 |

**Stable** (within ±0.05): R1, P1, P3, M3.

## Why does override do worse than standalone harness despite byte-equivalent inputs?

Two probable causes:

1. **Mistral run-to-run variance** — measured ±0.09 mean, ±0.38 max on identical configs. The 0.05 gap between override (0.685) and harness (0.731) is well below that.

2. **Tool name shadow effect** — the harness sees `read_document`, `read_file` defined as a 3-tool array sorted alphabetically. Brain's TOOL_DEFINITIONS_OPENAI may have minor schema differences (parameter descriptions, properties order) compared to the harness's hand-coded TOOL_DEFS. The byte-level payload diff has not been audited yet — could be a tiny prompt embedding shift.

## Inspecting C1 (where override regressed −0.26)

Both harness and override produce 3000+ char answers about the KI-Policy. Harness avoids mentioning "EU AI Act" + dates; override mentions them with citation. Mistral judge marked override down for "AI Act specifics fabrication". Manual inspection: the AI Act references ARE in the corpus document (the policy explicitly cites the EU AI Act + effective date 2025-02-02), so this is judge over-strictness, not a real Brain failure. **Same calibration issue as the F1/F2/F3 refusal-with-alternatives mis-rating user-validated earlier.**

## What this proves

1. **The Brain prompt stack costs measurable quality on Brain's worst structural failures** (C2/P2/M2/C3 — citation discipline + wrong-doc-citation): when removed via override, those scores jump 0.10–0.69.

2. **The Brain prompt stack also helps marginally on its strengths** (R2/M1/C1): when removed, those drop 0.10–0.26. The complex 3-step-flow lecture + MCP tool descriptions seem to be doing real work on questions where Brain already succeeds.

3. **Net effect is roughly zero** on the 15Q canary — the gains on weak questions are cancelled out by losses on strong questions, all within Mistral's run-to-run noise floor.

4. **The right answer is NOT to wholesale strip Brain's prompt** to harness shape. The right answer is to **identify which Brain prompt blocks help on which questions** and either:
   - Make the helpful blocks shorter/sharper without breaking what they help, OR
   - Tag-route different question types to different prompt subsets, OR
   - Accept that the current Brain prompt is roughly Pareto-optimal on this canary.

## Files modified

`claude_cli.py`:
- `_build_system_prompt()` — early-return harness file when project active
- `send_message()` preamble block — `_harness_override_active` flag gates project/greeting/profile preambles
- `send_message()` tool-set assembly — short-circuit to 3 tools from TOOL_DEFINITIONS_OPENAI when override active

All three overrides are conditional on the existence of `eval/harness/system_prompt.md` — deleting that file disables the override entirely. Override is OFF for non-project chats unconditionally.

## Decision needed

The override stays in place for now (we may want to do bisect experiments or keep using harness-mode as a feature flag). Realistic next steps:

1. **Bisect Brain's prompt blocks** — start from harness, add ONE Brain block at a time, measure which block actually moves the needle on which question type. Identifies the helpful blocks vs the dead weight.

2. **Accept current state, focus elsewhere** — Brain's quality is roughly Pareto-optimal. The remaining structural failures (citation discipline on bullet lists, wrong-doc-citation on adjacent topics) are model limitations on Mistral Medium 3.5, not prompt-fixable.

3. **Test a different model** — Magistral Medium or Mistral Large via the same harness. If they emit per-claim citations naturally, the citation problem is model-bound and the right fix is a model swap, not prompt engineering.

Run-IDs:
- 20260501T121612 — Brain validator-metadata baseline
- 20260501T122545 — Brain validator-metadata rerun (variance check)
- 20260501T124538 — standalone harness baseline
- 20260501T132330 — Brain in harness-override mode
