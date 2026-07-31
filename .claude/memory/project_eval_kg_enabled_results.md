---
name: Eval KG-enabled thinking=high results
description: 2026-05-03 eval run with KG enabled + thinking=high — +0.11 over prior baseline, refusal still the ceiling
type: project
---

Run: `20260503T112108_disc-none_kg-enabled`, Mistral Medium 3.5 + thinking=high, Mistral judge.

**Brain mean: 0.832** (gold: 0.924, Δ −0.092)

vs prior `20260503T075614_disc-none_medium-3.5-thinking-high` (13 shared Q):
- prev mean: 0.718 → kg-enabled: 0.828 → **Δ +0.110**

**Why:** KG-enabled improves retrieval across the board. Biggest wins: F3 +0.47, R2 +0.28, P3 +0.19, M2 +0.17, M3 +0.15.

**Ceiling**: F1/F2 (Geldwäsche/Kreditvergabe refusal bucket) barely move (0.23→0.25, 0.10→0.25). Refusal questions are the only remaining structural gap — not a retrieval/infrastructure problem.

**M3 anomaly**: Brain 1.00 vs gold 0.00 — gold failed to find the cloud policy, Brain succeeded.

**How to apply:** KG is confirmed net-positive; keep enabled. Next lever is the refusal bucket — needs a different approach (server-side gate or model swap), not more retrieval tuning.
