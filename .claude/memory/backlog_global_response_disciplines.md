---
name: Global response-discipline settings (idea, 2026-04-30)
description: Lift REFUSAL/PRECISION/CITATION out of project Instructions into an org-wide setting that also applies to normal chats and scheduled tasks
type: project
originSessionId: 14f9509b-52ac-46d7-b685-5a694fa330a7
---
Idea raised after shipping `DEFAULT_PROJECT_INSTRUCTIONS` (4KB of REFUSAL + PRECISION + CITATION rules as the per-project default). User pointed out the disciplines are actually two layers conflated:

1. **Generic response disciplines** — how the LLM should answer in general (refuse-on-empty-retrieval; concrete values not filler; verbatim citations). Applies equally to project chats, normal chats, and scheduled tasks.
2. **Project-specific tone / behavior** — the actual reason the per-project Instructions field exists (e.g. "marketing project: friendly tone, no jargon"; "compliance project: formal, German, regulator-facing").

Current state mixes the two: `DEFAULT_PROJECT_INSTRUCTIONS` carries the generic disciplines as the project default, so editing the field starts from a wall of generic rules that has nothing to do with the specific project's character.

**How to apply when revisited:**
- Move REFUSAL/PRECISION/CITATION (or whatever subset survives the next eval) into a **new org-wide settings tab** ("Response disciplines" or similar). Apply to every `_build_system_prompt` call regardless of project/chat/sched context.
- Strip them from `DEFAULT_PROJECT_INSTRUCTIONS`. Project Instructions field becomes empty-by-default again, with the textarea placeholder hinting at "project-specific tone, terminology, audience" rather than retrieval discipline.
- Keep `DEFAULT_PROJECT_INSTRUCTIONS` as a constant (might be empty or a small placeholder) so the existing fallback path in `_build_system_prompt` still works.
- Org-wide setting can have its own toggle per discipline (Refusal on/off, Precision on/off, Citation on/off) so non-policy projects don't get the verbatim-citation rule.

**Why we deferred:** the discipline blocks are validated together as a set on the German bank-policy canary (5.5/7 with all three vs 1.5/7 when trimmed in `feedback_prompt_bloat_regression.md`). Splitting them into a separate setting + per-discipline toggles needs its own eval pass — not a quick refactor.

**Don't apply this prematurely** — the user explicitly said "lass mal" after raising the idea. Wait until they bring it back.
