---
name: Analyze and plan before coding — simple centralized architecture
description: Default behavior: always analyze the full problem and plan before writing any code. Goal is simple, centralized, easy-to-understand architecture.
type: feedback
originSessionId: 8b40445b-8569-4925-8a47-aca7f4c1ebe6
---
Before writing any code, always:
1. **Analyze** — understand all the ways the problem occurs, all callers, all edge cases
2. **Find the single choke point** — the one place that handles all cases naturally
3. **Plan out loud** — state the approach in one sentence before implementing
4. Only then write code — at the choke point, not per-caller

**Why:** The user's explicit goal is a simple, easy-to-understand codebase with centralized functionality. Patching individual callers creates duplicate logic, missed edge cases, and architectural complexity that compounds over time.

**How to apply:**
- Never start coding the first case you see and expand from there
- Ask "where does ALL of this converge?" before touching any file
- Prefer one well-placed fix over two partial ones
- If tempted to add logic to multiple places, that is a signal to find the central place instead
- Simple and obvious beats clever and complete
- **Proactively flag simplification opportunities** — when reading or touching code, notice duplicated logic, scattered responsibilities, or things that could be centralized, and mention them even if they're not part of the current task. The goal is a progressively simpler codebase, not just fixing the immediate issue.
- **Explainability test** — if a feature or mechanism cannot be summarized end-to-end in 2-3 sentences, it is a candidate for refactoring. Flag it. Complexity that requires a paragraph to explain is a design smell, not a sign of sophistication.
