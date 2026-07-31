---
name: Eval judge swapped Opus 4.7 → Haiku 4.5 (calibration measured)
description: 2026-05-01 — judge model changed in eval/config.json to conserve Max-subscription quota; Haiku is more generous to gold (+0.07 mean) and slightly harsher on brain (−0.06 mean), widens measured Δ from −0.28 to −0.42 on the same answers
type: project
originSessionId: b72f1f43-2339-41d1-a5ee-4e57d6582617
---
Switched `eval/config.json` → `judge.model: "haiku"` to stop burning Opus quota during fast iteration cycles. Re-judged the existing baseline with Haiku and compared to the original Opus judging.

**Calibration on the same 15 baseline answers (results dir `20260501T104726_disc-none_rejudge-haiku`):**

```
                                   Opus    Haiku    Δ
gold mean (Opus's own answers)     0.913   0.987   +0.07
brain mean (Mistral Medium 3.5)    0.635   0.570   −0.06
measured Δ (brain − gold)         −0.278  −0.417   −0.14
winner agreement                          10/15
```

**Per-axis behavior:**
- Opus + Haiku agree closely on most questions (per-question score Δ ≤ 0.10 on 11/15)
- Haiku grades gold more generously — Opus rarely gives 1.00, Haiku gave it 11 times
- Haiku is slightly harsher on partial-failure refusals (F2 0.43 → 0.00, F3 0.33 → 0.17)
- Both judges agree on the strong failure modes: F1/F2/F3 (refusal), P2/C2 (wrong-doc-citation)
- Winner-flips (5/15) all moved tie → gold; Haiku is less inclined to call ties

**One self-correction:** Opus rated R3 brain=0.98 because Opus's own gold answer was the meta-ack glitch (effectively empty). Opus didn't penalize the empty gold as hard as it should have. Haiku rated R3 gold=1.00 (still too lenient on the empty gold) but at least gold won, which is the right outcome.

**Decision:** keep Haiku as the judge for fast-iteration experiments. The new baseline against which to measure config tweaks is Haiku-judged: **gold 0.987, brain 0.570, Δ −0.417**. Do NOT compare Haiku-judged experiment numbers directly to the Opus-judged baseline (`20260501T092520_disc-none_medium-3.5`) — only to other Haiku-judged runs.

**When to switch back to Opus:** for the headline final-result run when we want a single Opus number to publish/document. The relative direction-of-improvement signal that Haiku gives is enough for iteration.

**Cost story:** Haiku is much cheaper to subscription quota than Opus, and ~3-5x faster (eval rejudge of 15 questions took ~2-3 min vs 7-8 min on Opus). Each fast-iteration experiment now costs ~one-tenth of an Opus-judged equivalent.
