---
name: Eval third judge — Mistral Medium 3.5 self-judging via vibe API (calibrated)
description: 2026-05-01 — standalone eval/judge_mistral.py calls Mistral Medium 3.5 directly (no Brain, no Claude Code) for the eval rubric; self-bias is bounded and measurement-useful
type: project
originSessionId: b72f1f43-2339-41d1-a5ee-4e57d6582617
---
Built `eval/judge_mistral.py` to bypass Claude Code's subscription quota for the judge call. Hits Mistral Medium 3.5 via the vibe provider directly using `urllib` against `https://api.mistral.ai/v1/chat/completions`. Reads `config.json` for the API key, no Brain dependency.

**Calibration on the same 15 baseline answers, three judges side-by-side:**

```
                                Opus    Haiku   Mistral-self
gold mean                       0.913   0.987   0.890
brain mean                      0.635   0.570   0.651
measured Δ (brain − gold)      −0.278  −0.417  −0.239
winner agreement vs Opus               10/15   12/15
total wall time (15 Qs)        ~7-8min ~2-3min  32s
```

**Self-bias is real but bounded:**
- Mistral grades gold slightly lower (−0.02 vs Opus, −0.10 vs Haiku) and brain slightly higher (+0.02 vs Opus, +0.08 vs Haiku) → measured Δ is the smallest of the three
- BUT: on Brain's catastrophic failures (F1/F2/P2/C2), Mistral judges itself harshly (0.12-0.17), matching Opus and Haiku
- Self-bias surfaces where Opus's *taste* differs (R1: penalised gold for conservative refusal-on-missing-section, gave brain max; R3: punished Opus's meta-ack gold harder than Haiku, gave brain max)

**Decision: use Mistral self-judge for fast iteration.** The bias direction is "favour brain slightly" (Δ −0.24 vs Opus's −0.28), so improvements measured under Mistral judging are *conservative* — they will hold up or improve when re-judged with Opus. For the final headline run, switch back to Opus for the published number.

**How to use:**
```
python3 eval/judge_mistral.py eval/results/<run_dir>
python3 eval/judge_mistral.py eval/results/<run_dir> --only F1,F2
python3 eval/judge_mistral.py eval/results/<run_dir> --model mistral-vibe/mistral-medium-3.5  # default
```

Writes `judge_mistral.json` per question + `summary_mistral.csv` + `summary_mistral.md` alongside the existing Opus/Haiku judge outputs (does NOT overwrite them).

**Calibration anchor for future Mistral-judged experiment runs:**
- Baseline (medium-3.5 brain, no config tweaks): gold 0.890, brain 0.651, Δ −0.239
- An experiment that moves Δ from −0.24 to −0.18 under Mistral judging is real progress
- An experiment that moves Δ from −0.24 to −0.20 under Mistral judging is borderline — re-judge with Opus or Haiku to confirm

**Why this works despite the conflict-of-interest concern:** because the rubric is concrete (verbatim quote required, refusal sentence required, no §N inventions), even a self-biased judge can't grant brain max marks when brain literally fabricated content. The bias only surfaces in subjective composition / refusal-style judgments where Opus and Mistral genuinely disagree about taste. For the failure modes we're trying to fix, all three judges agree.
