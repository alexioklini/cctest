---
name: AAAK regex encoder is unsuitable for technical documents
description: 2026-04-28 measurement — MemPalace's AAAK Dialect encoder produces tag soup on bank IT policies; don't wire it into Brain
type: feedback
originSessionId: 7486d080-f9df-4a1c-8a53-d9a3c60c884c
---
AAAK is MemPalace's `dialect.Dialect` regex encoder (lossy structured-summary format). Tested 2026-04-28 on the kg-real-policies project (58 German bank IT-policy markdowns, 840KB total). Compresses 90× in 0.16s but the output is unusable signal:

- Entity codes are 3-letter prefixes of capitalised words, not real entities (e.g. `STR+PAG+SEI` for "Strategie + Page + Seite", `PAG+VOR+SEI` repeated across most docs)
- "Key quote" field captures our `<!-- brain-source-size: NNN -->` doc-converter frontmatter marker on ~90% of files (regex matches HTML comment as a quote)
- Topic field is stopwords: `die_und_der`, `der_die_und` — German article filtering not present
- Emotion field empty for all bank policies (AAAK is built for narrative/zettel content)
- Flags reduce mechanically to `CORE+TECHNICAL` everywhere

**Why:** AAAK is purpose-built for personal narrative (emotion codes like `vul/grief/wonder`, flags like `ORIGIN/PIVOT/GENESIS`). Technical documents have none of those signals. The remaining fields (entity prefixes, topic stopwords) carry no retrieval value.

**How to apply:** don't propose AAAK as a closet alternative or system-prompt summary layer for project documents OR chat memory. The existing path (verbatim drawers + LLM-built closets via `closet_llm.regenerate_closets`, gated on `mempalace.kg.regenerate_closets`) is strictly better. The encoder is also not used by MemPalace's own `searcher`/`miner`/`closet_llm` flow (only `closet_llm` pulls in the `aaak.instruction` i18n string, which is a language directive, not the encoder).

**Chat measurement 2026-04-28:** ran the same encoder over 12 real chats (130KB total → 1.4KB AAAK in 0.02s, 95× compression). Results were better than on policies but still not retrieval-grade:
- Topic field: 2/12 useful, rest mixed (`user_assistant_*` structural noise, hallucinated words like `chef` from analogy text, German articles still leaking)
- Quote field: 1/12 captured a real decision quote (`"Astro + React + Tailwind chosen"`); the rest grabbed markdown bullet headers (`"### Key Takeaways"`, `"* **Why do it"`) or sentence fragments
- Emotion field: actively wrong. `rage` on a Vienna trip chat, `joy` on Python OrderedDict, `love+rage+curious` on a "how can I help" exchange. The emotion regex fires on word fragments inside unrelated words
- Flag field: `DECISION+CORE+TECHNICAL` applied uniformly across all chats — no discrimination

The fundamental issue is the emotion+flag schema (`vul/grief/wonder/ORIGIN/PIVOT/GENESIS`) — built for personal narrative zettels with explicit affect, hallucinates on technical chat content. Don't wire AAAK into chat memory either. Real chat-retrieval improvements should come from `regenerate_closets` (already gateable) or a chat-specific KG profile if a concrete retrieval gap appears.
