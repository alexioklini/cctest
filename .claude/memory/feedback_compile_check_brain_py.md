---
name: feedback_compile_check_brain_py
description: "ALWAYS python-compile brain.py after editing its CHANGELOG (German prose w/ typographic quotes) — an ASCII quote inside a string crashed import + crash-looped the server for 6 versions, undetected because js_gate doesn't import brain.py"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: f096ee00-98eb-46f0-bea7-abe0348fc4fe
---

After ANY edit to `brain.py` — especially the German `CHANGELOG` entries — run
`python3 -c "import ast; ast.parse(open('brain.py').read())"` (or `py_compile`)
BEFORE committing. 

**Why:** 2026-05-29 a changelog entry (v9.48.3) contained `„…"` where the closing
mark was an ASCII `"` (U+0022), not the typographic `"` (U+201C). That terminated
the string literal early → `SyntaxError: unterminated string literal` → brain.py
failed to import → server crash-looped from v9.48.3 through v9.49.0. It went
undetected for 6 versions because: (1) `web/js/js_gate.sh` does NOT import
brain.py (JS/CSS only), and (2) I only ast-parsed the *code* files I changed in
each commit, never brain.py after the changelog edit. The old 9.47.2 process kept
serving until a restart finally killed it — so "the server works" was a stale
process, not the new code.

**How to apply:** German changelog prose mixes „typographic" and 'ASCII' quotes
freely; one stray ASCII `"`/`'` inside the `"..."` string breaks the whole file.
Compile-check brain.py as part of EVERY commit that touches it, and after any
restart confirm `/v1/status` version == brain.VERSION (a mismatch = the daemon
didn't actually reload the new code — see [[project_bgtask_fanout_probe]] where a
stale daemon also masked a feature being "live").

**Update 2026-06-30 (v9.238.2):** bit me again, the INVERSE direction — a
*typographic* closing quote `"` (U+201D) inside the entry crashed `ast.parse`
with `SyntaxError: invalid character '—' (U+2014)` (the error points at a LATER
char, not the real culprit — misleading). Plain `—`/arrows/box-glyphs compiled
fine in adjacent entries; the curly `"`/`„` were the problem. **Simplest robust
rule: write CHANGELOG entries in brain.py in ASCII-ONLY prose** (ae/oe/ue/ss for
umlauts, `--` for dashes, straight `'`/no fancy quotes). Curated changelog
(`engine/changelog_curated.py`) is fine with full Unicode — it's the brain.py
CHANGELOG string that's fragile. Always `ast.parse` brain.py before commit
regardless.
