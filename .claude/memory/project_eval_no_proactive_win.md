---
name: System-prompt experiment 1 — drop "Use tools proactively" in project chats (won)
description: 2026-05-01 — replacing the engine's "Use tools proactively" prologue with a "answer based ONLY on what you can verify" line when project context is active improved Brain's mistral-judged Δ from −0.24 to −0.14
type: project
originSessionId: b72f1f43-2339-41d1-a5ee-4e57d6582617
---
First system-prompt experiment that produced a real, measurable improvement on the eval harness.

**The change:** in `claude_cli.py:_build_system_prompt`, the engine's hardcoded line `"Use tools proactively to accomplish tasks. You can chain multiple tool calls."` is now conditional on whether `_thread_local.project` is set. When project context is active, it's replaced with:

> Answer based ONLY on what you can verify from tools and source documents. When tools return nothing relevant, refuse cleanly per project instructions — do not synthesize from training-data knowledge.

For non-project chats (general assistant use), the original "use proactively" line is retained — initiative is still the right default outside project context.

**Results (Mistral self-judge, baseline `20260501T092520_disc-none_medium-3.5` vs experiment `20260501T110032_disc-none_sysprompt-no-proactive`):**

```
                            baseline    experiment    Δ
gold mean                   0.890       0.889       −0.001
brain mean                  0.651       0.752       +0.101
measured Δ (brain − gold)   −0.239      −0.137      +0.102
winner agreement                        same wins/ties
```

**Per-question gains:**
- F2 Kreditvergabe (refusal): 0.12 → 0.50 (+0.38)
- F3 Arbeitszeit (refusal): 0.60 → 0.93 (+0.33)
- P2 Archivierung (precision/wrong-doc): 0.12 → 0.50 (+0.38)
- C2 Passwort (citation/wrong-doc): 0.12 → 0.68 (+0.56)
- F1 GwG (refusal): 0.17 → 0.27 (+0.10) — still failing but less catastrophically

**Zero regressions.** Every question's Brain score either improved or stayed within ±0.05 of baseline.

**Why it works:** the "Use tools proactively" framing pushed Mistral toward initiative-as-default. When mempalace_query came up empty, that framing said "keep going, find an answer somehow" — leading to training-data fabrication. The replacement line explicitly anchors the model to "verify or refuse" before the project Instructions even appear in the prompt, so the project's REFUSAL discipline isn't fighting an upstream contradiction.

**What it didn't fix:**
- F1 GwG is still −0.73 from gold (0.27 vs 1.00). Brain still opens with "keine expliziten Schwellenwerte" then dumps training-data FM-GwG specifics. The "stop after refusal" behavior the model lacks isn't a system-prompt issue — it's a generation-control issue.
- C3 ISMS-Ziele still failing at 0.33 — citation discipline collapse on multi-claim list, unrelated to the proactive line.

**Re-judging this experiment with Opus** would give a stronger published number. Per `project_eval_judge_mistral_self.md`, Mistral self-judging is the conservative direction — Opus would likely measure a slightly larger Brain-improvement. Deferred until we have a stack of experiments to publish.

**Next experiment candidates:**
1. **Resolve the .md vs binary contradiction** (claude_cli.py:21071-21078 vs 21161-21172): two prompt blocks that recommend opposite things. Pick `.md` as canonical, demote binary to fallback note.
2. **Hoist REFUSE earlier in the project block** so it appears before the "you MUST consult ... do not guess" sentence, framing consultation as a precondition for refusal rather than a directive to find an answer.
3. **Server-side gate** (the original option from `project_eval_discipline_v2_negative_result.md`): for the F1-style "knows it's empty but writes anyway" case, classify retrieved-text vs question and short-circuit when retrieval returns no answer.

Mistral-judged comparisons within this experiment-thread are valid; do NOT compare directly to Opus-judged baseline numbers.
