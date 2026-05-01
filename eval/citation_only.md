# Citation-only inject mode

When `claude_code_disciplines: "citation_only"` is set in `eval/config.json`, this is what Claude Code's gold run receives in addition to its default behavior.

---

**CITATION DISCIPLINE — per-claim, not per-block**:
Every factual sentence and every bullet that came from a document must carry its own citation in this exact form:

`[Quelle: <basename> — "<wörtliches Zitat 10–25 Wörter>"]`

Rules:
- The quote inside the brackets must be verbatim — copied character-for-character from the document text you retrieved. Do not paraphrase inside the quote.
- One citation per claim. If a bullet list has five claims, each bullet needs its own bracket. A single citation at the end of a list is not enough.
- Use the document's basename only (no path, no `.md` suffix even if the retrieved chunk's filename ends in `.md`).
- Do NOT invent paragraph numbers (`§154`, `§3.2`). Such numbering is usually not preserved in retrieved text.
- If you cannot find a verbatim quote that supports a specific claim, drop that claim. A shorter, fully-cited answer beats a longer, partially-cited one.
- Multiple sources for the same claim: repeat the bracket — `[Quelle: A.pdf — "..."] [Quelle: B.pdf — "..."]`.

For questions whose answer is not in the corpus, refuse cleanly — no citation needed.
