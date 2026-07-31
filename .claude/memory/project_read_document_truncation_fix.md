---
name: read_document silent 500-line truncation on .md and unknown extensions — fixed 2026-04-29
description: tool_read_document's else-branch capped output at 500 lines for any extension without a dedicated handler — silently truncated 1903-line .md companions; also added read_document to execution.profiles as heavy:False
type: project
originSessionId: 5b1a5332-c623-49a6-99c5-a5d75fd2a5ad
---
After fixing the chroma-direct retrieval, the model still scored 5/7 on the IT-Risk Score canary — missing the Prozent→Score table (1.0/1.2/1.5/2.0/2.5/2.8/3.0/3.2/3.5/3.8/4.0). Forensics on session 87bf6124 revealed two cascading silent truncations:

**Bug 1 — `tool_read_document`'s plain-text fallback hard-capped at 500 lines** (claude_cli.py line ~2328 in the `else:` branch). Any extension without a dedicated handler (.md, .txt, .log, .csv-as-text, etc.) hit this. The ISMS Handbuch markitdown companion is 1903 lines; section 2.13 + Prozent table sit at lines 1267–1380. The model only saw lines 1–500 → TOC + frontmatter, never the body. Even when the model correctly called read_document on the .md (per the system prompt's drill-down instructions), the table was unreachable.

Fixed by removing the `limit = 500` cap and honoring explicit offset+limit args from the model. Default = read whole file. The downstream tool_result_char_limit middleware compacts if needed.

**Bug 2 — `read_document` was missing from `execution.DEFAULT_PROFILES`**, so it fell through to the default `"auto"` heuristic. Output >8KB triggered `maybe_retroactive_isolate` → wrapped in worker subagent → model got a 500-char summary instead of full content. The whole point of `read_document` is to put document content in front of the model; summarising it defeats the purpose. Added explicit `heavy: False` for `read_document`, `write_document`, `edit_document`.

**Verification (post-fix)**: read_document on the same 41K .md returns 53,995 chars (with line numbers, hence > raw file size), all 1903 lines. All 7 canary tokens present at correct offsets — `= 100` at 34583, `4.0` at 37488, `Sofortmaßnahmen` at 35886, `ITRMP erlaubt` at 35235, etc.

**How to apply**:

When a tool's purpose is to put content in front of the model (read_*, get_*, fetch_*), default to "give the full thing" and rely on the tool_result middleware to compact downstream. Don't pre-truncate at the tool. Specifically:
- Any new file/document reading tool MUST be in DEFAULT_PROFILES with `heavy: False` — otherwise the auto-isolate path strips content.
- Any plain-text fallback in a multi-format reader MUST honor explicit offset+limit args, not hard-cap at a magic number.
- Silent truncation in a reader is the worst kind of bug: the model can't tell it didn't get the full document, so it confabulates the missing parts. Always log/return total_lines and showing-range so the model can check.

**Score progression on the IT-Risk Score canary** (see `project_it_risk_score_canary_answer.md`):
- Pre-Brain debugging session: 0/7 (full hallucination)
- After chroma-direct retrieval fix: 5/7 (5 sections present, missing Prozent table + citation)
- After this read_document fix: TBD — will retest

The remaining issue if it persists is either the citation discipline (model not adding [Quelle: ...] despite system prompt instruction) or the model's habit of summarising tables instead of reproducing them. Both are model-behavior issues, not infrastructure bugs.
