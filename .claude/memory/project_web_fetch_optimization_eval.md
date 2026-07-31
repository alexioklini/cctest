---
name: project_web_fetch_optimization_eval
description: "web_fetch optimization eval suite — does a content-reducing optimization lose answer-critical content? gold = optimizations off + complete content, Mistral-judged"
metadata: 
  node_type: memory
  type: project
  originSessionId: 89f8a8bd-93f6-42d5-9d0e-a29ec59caa2c
---

**2026-06-03 — search-tool rule rewrite + agentic triage probe (v9.60.x):** The `exa_search`/`searxng_search` rule ("after search you MUST fetch ALL returned URLs in full, in blocks of 5, no exceptions") CONTRADICTED abstract triage — it forced full-reading every result. Rewrote it (in `config.json → tool_settings.{exa_search,searxng_search}.description`, mirrored in tracked `config.example.json`; rendered into the system prompt's TOOL USAGE GUIDE via `brain._render_tool_descriptions`, NOT the tool schema): **abstract-first triage** — `web_fetch(mode="abstract")` every result first, then `mode="full")` ONLY the relevant ones; may answer from a sufficient abstract; never answer from titles alone. Load-bearing invariant preserved (don't answer from titles). Needs server restart to load (server.py:3294 sets `engine._tool_settings` from config at startup). New agentic probe `eval/web_abstract_triage_probe.py` (runs through the live sidecar :8421, stateful tool stub returns search results + per-(url,mode) content, records which URLs got full-read): tests the model SKIPS off-topic full-reads (ambiguous-title + fictional/live-only scenarios so it can't answer from memory). Baseline mistral-medium 3/3 pass, 77-100% token saved; regression-checked the OLD rule full-reads all 3 (0% saved). This complements web_fetch_eval.py (content sufficiency) with the DECISION test.

2026-06-02: Built a permanent eval suite for the recent `web_fetch` content-reducing optimizations. **User's gold definition: optimizations OFF + web_fetch returns COMPLETE content.** Per case the SAME url is fetched twice (optimized path + that-optimization-disabled path), a model answers the case question from each fetch, and Mistral judges whether the optimized-content answer matches the gold-content answer. `content_loss=true` / `winner="gold"` = the optimization dropped answer-critical content.

**Files (in `eval/`, mirrors the existing Brain-vs-Opus harness conventions):**
- `web_fetch_eval.py` — driver. Calls `engine.tools.misc_tools.tool_web_fetch` **IN-PROCESS** (lazy `import brain`, NO running server) so both fetches are the real prod code. Gold per mode = narrowly monkeypatching exactly ONE optimization off. Reuses judge_mistral.py's provider-resolve + Mistral chat helpers.
- `web_fetch_cases.json` — 5 cases, one per optimization mode.
- `web_fetch_rubric.md` — judge scores each answer on completeness + faithfulness; `content_loss` bool is the headline.
- README section appended.

**The 4 optimizations tested (all live in `engine/tools/misc_tools.py` tool_web_fetch, shipped through v9.54.2):**
1. `abstract` — `mode="abstract"` (~1500-char survey, `_to_abstract`) vs `mode="full"`. **Scored as TRIAGE-SUFFICIENCY, not completeness** (per user: abstract's job is to summarize so the full fetch is often unneeded). Question is gist/relevance-level; gold full-page answer = truth; survey WINS (tie) + `paid_off` flag when it matches the gist AND is ≥50% smaller (full fetch avoided). content_loss fires only when the survey is too thin/wrong for even the gist. The other 3 modes stay completeness-scored. Rubric (`web_fetch_rubric.md`) branches on `mode`; driver passes mode to the judge.
2. `academic` — arxiv/biorxiv/PMC landing URL auto-rewritten to full-text PDF (`_academic_pdf_url`+`_fetch_academic_pdf`) vs rewrite bypassed (raw HTML wrapper). Should ADD completeness, no loss.
3. `brain_code` — matched-region trim of a large GitHub-raw file (`_trim_to_brain_code_regions`, ±8 lines) vs full file. Driver seeds `tool_exec._record_brain_code_region` so the trim fires, then clears for gold.
4. `conversion` — tool's auto fetch_method (raw/markitdown/crawl4ai) vs same (static pages opt==gold).

**Gotchas that cost time (encoded in the suite):**
- `_ok` returns the result dict AS the JSON (no `{ok,result}` wrapper) — `_unwrap` handles both.
- brain_code anchor must match a line VERBATIM in the fetched file (it's relocated by fingerprint). `def raise_for_status(self):` ≠ real `def raise_for_status(self) -> None:` → trim silently returns None (full file, no real test). Always grep the real signature.
- Answer-model content cap must be GENEROUS (60k, was 24k) or the GOLD reference itself truncates and masks loss — requests/models.py has `raise_for_status` at char 40274.
- abstract cases must use NON-academic URLs (arxiv `/abs` fires BOTH academic rewrite + abstract → conflates two optimizations).
- Model ids in THIS config are bare (`mistral-medium-3.5`), not `mistral-vibe/...`.
- `eval/results/` is gitignored — baseline runs stay local.

**Baseline found TWO real product defects — BOTH FIXED in v9.60.2** (engine/tools/misc_tools.py):
1. `brain_code` trim content_loss=0.75: fixed window CLIPPED THE TAIL of a longer matched method (`raise_for_status` lost the `HTTPError` tail). FIX: new `_block_end_line` extends the kept window to the END of the indentation-based code block the anchor opens (def/class body, blank-line tolerant, ≤200 lines; brace langs fall through to the old fixed window).
2. `abstract` on Wikipedia (ABS2) content_loss: `_to_abstract` returned the converted LEAD, which on chrome-heavy pages is nav/ToC/infobox, not prose. FIX: new `_lead_prose` + `_is_prose_line` ASSEMBLE the survey from real prose lines only (skip headings, list/ToC items, link-fragment + mostly-link lines, table rows, nav labels), gathering sentences until ~1500c. meta-description path unchanged.
**After fixes: eval is 0/6 content-loss, 4/6 paid_off** (ABS1/ABS2/ABS3 + BC1 pay off; ACA1 academic + CONV1 conversion tie). Re-run after touching either fn.

Run: `python3 eval/web_fetch_eval.py [--only IDS] [--answer-model X --judge-model Y]`. Related: [[project_crawl4ai_render_service]], [[project_manual_web_search_websuche]], [[project_brain_vs_opus_eval]].
