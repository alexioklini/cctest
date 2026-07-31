---
name: Magistral (Mistral reasoning) is wrong tool for "reproduce the table" tasks — Mistral Small 4 is better
description: 2026-04-29 session 60e305a7 — Magistral scored 1/7 on IT-Risk Score canary vs Mistral Small 4's 5.5/7; reasoning models plan in thinking and produce terse answers, killing reproduction tasks
type: feedback
originSessionId: 5b1a5332-c623-49a6-99c5-a5d75fd2a5ad
---
Tested `mistral-experimental/magistral-medium-2509` on the IT-Risk Score canary. Result: 1/7 (only the citation was correct). Mistral Small 4 had scored 5.5/7 on the same prompt + corpus.

**Why:** Magistral is Mistral's reasoning variant. The thinking blocks (565 + 467 chars in 60e305a7) were planning blocks ("Okay, the user is asking..., I need to first search... Once I have relevant drawers, I'll read the full documents...") — NOT content extraction blocks. The model used reasoning capacity to plan its tool calls, then wrote a 429-char essential-summary answer. Tool calls were correct (mempalace_query → read_document with full 53K-char .md returned), so the data WAS available. The model just chose not to reproduce it.

**Why this matters as a general lesson:** Reasoning models are optimised for "produce a correct conclusion concisely after deliberation." That conflicts directly with reproduction tasks like "list all 11 percent-to-score values from the table" — those want verbatim transcription, not deliberation. The REPRODUCTION DISCIPLINE block in the system prompt that specifically targets bullet-list compression is more effective on non-reasoning models like Mistral Small 4 because they default to longer-form prose answers anyway.

**How to apply:**

For project-document Q&A on this corpus (and probably similar policy/regulatory corpora):
- **Use Mistral Small 4 as default** — current 5.5/7 with REPRODUCTION DISCIPLINE prompt; subscription-covered, low latency.
- **Avoid Magistral** for reproduction tasks. Keep it as an option for genuine reasoning tasks (math, complex multi-step logic, contradiction-detection across documents).
- **Don't burn budget testing other Mistral reasoning variants** for this; the tendency is structural to the model class, not specific to Magistral.

**What to try next if Mistral Small + REPRODUCTION DISCIPLINE still scores below 7/7:**
1. Add a few-shot example of a passing answer to the system prompt (the 7/7 vanilla answer captured in `project_it_risk_score_canary_answer.md` is the obvious candidate). Costs prompt tokens but is the nuclear option for compression bias.
2. Lower the `max_drawer_chars` cap so drawer text is less of a temptation to answer from snippets — forces the model to drilldown via read_document. Currently 6000; could try 1500.
3. Stop chasing 7/7 — 5.5/7 with infrastructure that mirrors vanilla MemPalace (verified in `project_chroma_direct_search_fix.md` etc.) is a defensible end state. The remaining gap is a known model-behavior limitation that doesn't reflect a Brain bug.

**Related**:
- `project_brain_canary_55_of_7.md` — Mistral Small 4 baseline.
- `project_drilldown_tools_added.md` — get_drawer + list_drawers + REPRODUCTION DISCIPLINE prompt.
- `project_it_risk_score_canary_answer.md` — the 7/7 reference answer.
- `project_local_model_tool_quality.md` — earlier note that Gemma 4 26B > Qwen3.6-35B for tool calling on local; reasoning vs non-reasoning matters more than parameter count for most use cases.
