---
name: project_matched-regions-read_2026-05-27
description: "v9.39.0 matched-regions auto-read — read_document returns only matched chunk windows; −71% read bytes, −0.07 quality (accepted)"
metadata: 
  node_type: memory
  type: project
  originSessionId: 0ca7eafb-960a-4ccc-9737-da9c99b1c5d3
---

2026-05-27 (v9.39.0): `read_document(.md)` now returns ONLY the matched regions of a file instead of the whole thing, when that file came from a `mempalace_query` this session. User's idea, after we measured that 11/15 eval questions read whole files (461 KB total, 3× the snippet bytes; F1 alone 135 KB).

**Why regions, not a single window:** a file often matches on MULTIPLE SCATTERED chunks (measured: a Löschkonzept matched chunks 2/18/20/48). A single ±N window misses most; a full read drags the whole doc. So read_document returns the UNION of ±2-chunk windows around every matched chunk, `format:"text-regions"`, gaps marked.

**Mechanism:** `engine/tool_exec.py` `_record_match_regions`/`_get_match_regions` — per-session map `{abs .md path → {chunk_index}}`, mirrors the existing `_session_read_paths` tracker (keyed on current_session_id, shared across the query+read tool calls of a turn). `mempalace_query` records at drawer-build; `engine/tools/file_tools._read_matched_regions` reads the union from the chunk store. Fallbacks to full read: model paginated (offset/limit), file wasn't a query hit, or regions ≈ whole file. AUTOMATIC — no flag, no model decision.

**Eval (2×15Q, mistral-medium-3.5, reranker on, reused Opus gold):** text-regions fired 18–24×/run; read bytes 461→~130 KB (−71%); F1 135→7.6 KB. **HONEST COST: brain mean 0.804→0.734 (both runs 0.728/0.740 — consistent, NOT noise).** The regions occasionally clip context precision/citation questions (C1/P2/R1/R3) needed. **User accepted the −0.07 for the 71% token cut.**

**Two alternatives tested + rejected this session** (see also the failed approaches): `read_chunk_window` opt-in tool — Mistral ignored it 0/15 (lesson: models won't elect optional read-discipline tools, must be automatic); ±3 stitch-radius widening — automatic but blunt, regressed to mean 0.67 (more inline bytes ≠ better). Matched-regions is the only approach that's both automatic AND targeted.

**Scope = GLOBAL, deliberate (user decision 2026-05-27).** The recording fires for ANY mempalace_query file-backed hit in any context (normal chat, project, brain_code/helpdesk, scheduled task), NOT just project chats — gated only on `_has_readable`, not on current_project. Self-limits in practice: (1) only the `.md` plain-text read path triggers it (PDF/DOCX/etc route through _do_extract and return before the regions check — reading read_path_original = full doc), (2) only files that were a query hit THIS session (anything else has no recorded regions → full read), (3) per-session keyed. The −71%/−0.07 numbers were measured in PROJECT chats only; non-project trade-off is unmeasured but low-reach (personal/chat wings rarely have file-backed drawers). User chose to leave it global rather than gate on current_project.

**v9.40.0 extended it two ways.** (1) Same idea for Brainy's source reads: brain_code query records {repo-path → chunk texts}; web_fetch of a GitHub-raw URL fetches the FULL live file but returns only the matched regions (located by fingerprint, ±8 lines), fetch_method='+brain_code_regions'. Cache keeps full file; trim only on returned copy. brain.py 959KB→16KB. Brainy reads GitHub (not local .md) so it needed its own path — text-keyed (brain_code chunks have no line positions). Not eval'd (no Brainy harness; token-shape change only). (2) SMART GATES on both region-readers (user-required): return WHOLE file when (a) small (≤8 chunks / ≤6KB) OR (b) trimmed regions would be ≥75% of full (many scattered small matches add up and negate the saving). Only trims a large file with sparse matches.

If the −0.07 ever needs clawing back: widen region radius ±2→±4, or make it opt-in per-project (both considered, not done). Related: [[project_reranker_enabled_2026-05-27]], [[project_mempalace_multi_source_coverage]], [[project_helpdesk_brainy]].
