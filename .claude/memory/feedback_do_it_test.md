---
name: Refactoring criteria — deduplication first
description: When to refactor and when to act without asking
type: feedback
originSessionId: 34125a16-aa2f-4ae7-bcd0-316b787d8db0
---
**Primary criterion — deduplication:** Same or nearly the same functionality spread across multiple locations. This has the highest value: it directly reduces complexity and eliminates future maintenance burden (change in one place, not N).

**Secondary criterion — general complexity reduction:** Makes the code easier to read and understand for both humans and LLMs reading cold.

**How to apply:** Apply these tests internally before acting. If either criterion is clearly met, do the refactor without asking. Only pause when the change has real risk (behavior change, restart required, large blast radius) or the tradeoff is genuinely ambiguous.

**Why:** User corrected this twice. Asking "should I also do X?" wastes a round-trip when the answer is derivable from the goal. Deduplication is the strongest signal — any duplicated logic is a maintenance liability regardless of how small it looks today.
