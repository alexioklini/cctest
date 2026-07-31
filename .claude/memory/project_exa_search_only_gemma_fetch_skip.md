---
name: project_exa_search_only_gemma_fetch_skip
description: "exa_search is search-only by design (returns {title, link}, real target URLs, no content); gemma-4-26B sometimes fetches only 1 of the 5 returned URLs but the fetch+summary still works correctly — accepted as a local-model limit, not a regression"
metadata: 
  node_type: memory
  type: project
  originSessionId: 94888652-c9bc-4cd7-b8da-e14934da5ed4
---

2026-05-22: Investigated chat 98d85572 where GUI showed "one web search (5 URLs) → one web_fetch of exa.ai → response" for a Vienna weather query.

**`exa_search` is search-only by design and is intact:**
- `brain.py:5307-5312` extracts ONLY `title` + `link` per result; the request body sends no contents/text/highlights field. Exa's own page content never reaches the model.
- Verified live: Exa returns REAL target URLs (bbc.com, weather.com, meteoblue, metoffice, AND sometimes exa.ai itself as a legitimate result).
- `tool_settings.exa_search.description` mandates: "After exa_search you MUST fetch ALL returned URLs with web_fetch... up to 5 in parallel. Never answer from titles/URLs alone."
- GUI web_fetch label = hostname of the URL the model passed (`web/js/chat.js:2925`).

**What actually happened in 98d85572 (per user — NOTHING odd):** the turn ran `rounds=3 tools=2` — exa_search THEN a real web_fetch. URL #2 of the 5 returned by exa was an exa.ai page; the model fetched that one, got real content, and summarized it correctly. **No hallucination, no wrong URL** — exa.ai was a legitimate search result. The only (acceptable) deviation from the "fetch ALL 5" prompt rule was fetching 1 of 5.

(Separately, a fresh repro run on gemma-4-26B showed non-deterministic variability — it narrated the fetch in prose and skipped web_fetch entirely. Just the local model's inconsistency on multi-step fetch.)

**No code bug. User decision: ACCEPTED, no change.** Do not propose exa auto-fetch / web_fetch URL guardrails unprompted — the search-only design is deliberate and Mistral/cloud follows the fetch step fine. See [[project_local_model_tool_quality]].
