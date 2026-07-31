---
name: eval judge swap to Sonnet 4.6
description: Judge changed from Mistral self / Haiku to Sonnet 4.6 via claude -p for thinking eval runs
type: project
originSessionId: 70381c9e-811e-4c21-9e96-0d82f9481762
---
From 2026-05-03: eval judge for thinking runs uses Sonnet 4.6 (`claude-sonnet-4-6`) via `claude -p`.

**Why:** Haiku is too generous to gold and harsh on brain (calibration bias documented in project_eval_judge_swap_haiku.md). Mistral self-judging has self-bias. Sonnet 4.6 is the current balanced choice for judging thinking model outputs — same family as the gold judge (Opus), more reliable than self-judge.

**How to apply:** `run_eval_thinking.py` wires this directly (`JUDGE_MODEL = "claude-sonnet-4-6"`). For future eval runs comparing thinking variants, use Sonnet as judge for consistency. Note: Sonnet-judged scores are not directly comparable to Haiku-judged or Mistral-judged runs — always compare within the same judge.
