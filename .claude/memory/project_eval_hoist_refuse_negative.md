---
name: System-prompt experiment 2 — hoist REFUSE earlier + resolve .md/binary contradiction (regressed)
description: 2026-05-01 — restructuring the project-block prologue and trimming/rewriting the binary-companion text regressed brain mean from 0.75 (no-proactive win) back to 0.66; specifically broke retrieval discipline on R2 (0.98→0.12) and reverted F2/F3 refusals
type: project
originSessionId: b72f1f43-2339-41d1-a5ee-4e57d6582617
---
Tested two coordinated changes on top of the no-proactive win:

1. **Hoist REFUSE earlier in the project block.** Replaced "BEFORE answering ANY question... you MUST consult the project's memory tools first. Do not guess or rely on general knowledge" with a REFUSE-first opening that frames consultation as a precondition for refusal.
2. **Resolve the .md vs binary contradiction.** Rewrote the BINARY DOCUMENTS block from "open the ORIGINAL binary with read_document for full fidelity" (which contradicted the .md-prefer 3-step block) to "the .md companion is the default, the binary is a fallback".

Also trimmed the 3-step flow block — removed verbose "Skipping Step 2... is the documented cause of wrong answers" warning, removed the worked-example paragraph showing a real read_path call.

**Results (Mistral self-judge, results dir `20260501T111035_disc-none_sysprompt-hoist-refuse`):**

```
                          baseline    no-proactive   hoist+md    Δ vs no-proactive
brain mean                0.65        0.75           0.66        −0.09
measured Δ               −0.24       −0.14          −0.21        +0.07
```

**Per-question regressions vs no-proactive (the win we're trying to extend):**
- R2 morgencheck: 0.98 → **0.12** (failed retrieval — couldn't find the document at all)
- F2 Kreditvergabe: 0.50 → **0.12** (back to fabricating)
- F3 Arbeitszeit: 0.93 → 0.60 (partial regression on refusal)
- P2 Archivierung: 0.50 → 0.25 (lost the right doc)
- M2 MA-Eintritt: 0.68 → 0.55

**Per-question gains vs no-proactive (small, not enough to offset):**
- F1 GwG: 0.27 → 0.50 (+0.23 — only question that responded to the rewrite)
- C2 Passwort: 0.68 → 0.73 (+0.05)
- R1 Multilogin: 0.98 → 1.00

**Why it failed — the strong imperatives were doing real work.** I removed:
- "BEFORE answering ANY question... you MUST consult the project's memory tools first" — this WAS the imperative driving Mistral to actually use the tools instead of answering from training data
- "Skipping Step 2 — or proceeding past a Step 2 error — is the documented cause of wrong answers on this project" — the explicit "documented cause" framing was anchoring the model to the failure mode
- The worked example showing an exact `read_document(path="...")` call

The rewritten block was leaner, but Mistral needs the strong, explicit framing to actually follow the 3-step flow. Without "you MUST consult", the model either skips retrieval (R2 morgencheck) or treats the search results as suggestive rather than dispositive.

**Lesson:** the failure pattern from `feedback_prompt_bloat_regression.md` ("longer prompts hurt Mistral") does NOT mean shorter is always better. Some prompts have load-bearing strong-language imperatives that are doing real work. Trim WHAT IS REDUNDANT, not what is forceful.

**Reverted to no-proactive only via git stash + selective re-apply.** The proactive-line edit (validated brain mean 0.75) is preserved in `claude_cli.py:_build_system_prompt`. Stashed full edits at `stash@{0}: wip-sysprompt-experiments` for reference; can be dropped after this note exists.

**Next experiment candidates** (alternatives to today's three):
- Find the redundant text in the project block and trim THAT, leaving the strong "MUST consult" + "Skipping Step 2 is the documented cause" intact.
- Move project Instructions BEFORE the 3-step flow block (so the REFUSE rule is first thing the model reads in the project section). Less invasive than rewriting the block contents.
- Remove the OPTIONAL KG block entirely (it's currently disabled anyway per `project_kg_disabled_markitdown_swap.md`) — pure dead text taking attention budget.
