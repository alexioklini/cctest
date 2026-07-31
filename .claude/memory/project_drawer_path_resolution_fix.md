---
name: Drawer source_file path resolution — read_path / read_path_original surfaced to the model
description: 2026-04-29 — chain-of-three-bugs fix that root-caused the IT-Risk Score hallucination on session ba3b33b8 — drawers now carry absolute paths the model can pass to read_document verbatim
type: project
originSessionId: 5b1a5332-c623-49a6-99c5-a5d75fd2a5ad
---
After the morning's KG-disabled + markitdown swap, the user retested IT-Risk Score in session `ba3b33b8` — still hallucinated. Forensic analysis revealed three independent bugs that compounded:

**Bug 1 — `mempalace_query` returns basenames, not absolute paths.** MemPalace's `searcher.py:416` calls `Path(source_file).name` on every drawer's source_file before returning. The Chroma metadata stores the full absolute path (e.g. `/private/tmp/kg-real-policies/.../.brain-extracted/<sub>/<file>.pdf.md`), but the model only sees `<file>.pdf.md`. CLAUDE.md v8.21.0 fix list documents this strip but only patched the chat-drawer false-positive case.

**Bug 2 — Model guessed the path from input-folder list.** With basename only, the model joined `<input_folder>` + `<basename>`. But the file lives in a *subfolder* the model has no way to know about (the input folder is `/private/tmp/kg-real-policies/20 Datenschutz & Informationssicherheit`, the actual file is in `.../20_2 Informationssicherheit/...`). Result: `read_document(path=...)` got a path that doesn't exist.

**Bug 3 — `read_document` accepted `source_file=` synonym AND fell back to CWD.** Model called `read_document(source_file=<guessed-path>)`. The handler did `path = args.get("path", "")` → empty string. Then `os.path.abspath("")` → CWD = `/Users/alexander/Documents/dev/cctest`. Then `os.path.exists(<dir>)` returned True. Then the format-dispatch fell through to a default text-read on the directory, returning `[Errno 21] Is a directory: ...`. The model **ignored the error and answered from training data**.

**Fix** (all three at once):

1. `tool_mempalace_query` (claude_cli.py:3787-3870): build a `basename → absolute_path` map by querying Chroma with `where={"wing": current_wing}` once per call, and additionally derive a `.brain-extracted/<x>.<ext>.md → original/<x>.<ext>` mapping. Each drawer in the response now carries:
   - `source_file` — basename, unchanged for back-compat
   - `read_path` — absolute path to the .md companion (ready to pass to read_document)
   - `read_path_original` — absolute path to the original PDF/DOCX/XLSX/PPTX, when applicable
2. New `read_hint` field at the top level of the response telling the model to use these fields verbatim, no string construction needed.
3. System prompt updated: replaced the "join with input folder" guidance (which required the model to know subfolder structure) with "use `drawer.read_path` or `drawer.read_path_original` verbatim". Added explicit "do NOT pass `source_file=...` (wrong parameter name; call silently fails)" warning.
4. `tool_read_document` (claude_cli.py:2118): accept `source_file=` and `file=` as synonyms for `path=`; reject empty path with a helpful error pointing back at `mempalace_query`'s `read_path` field; reject directories with "you meant a file, not a base path" error.

**Verified live** (after restart):
- `mempalace_query("IT-Risk Score Berechnung Risikomatrix")` returns drawer with `read_path_original=/private/tmp/kg-real-policies/20 Datenschutz & Informationssicherheit/20_2 Informationssicherheit/20_2_1_2_ARL_ISMS Risikomanagement Handbuch.pdf` (full path including the subfolder the model previously guessed wrong).
- `read_document(path=<that path>)` returns 40,641 chars including TOC entry `2.13. Berechnung des IT-Risk Scores → page 23`. Content is there for any model that follows the 3-step flow.

**How to apply:**

When the model still hallucinates after a corpus-rebuild + retrieval fix, **always check the actual tool-call sequence in `messages.metadata`** before assuming the system prompt is wrong. The session inspector or `sqlite3 chats.db "SELECT metadata FROM messages WHERE id=<assistant_msg_id>"` shows what tools actually ran with what args. The 3 bugs in this incident were each invisible without this check; the prompt-side fixes from earlier in the day were correct but blocked by a tool-implementation chain.

**KV-cache invariant**: PROJECT MEMORY block stays per-project, not in warm-pool prefix. Tool result schema gained 2 fields but the request-side schema is unchanged — no cache invalidation.

**Cost note**: each `mempalace_query` now does 1 extra Chroma `get(where={"wing":...}, include=["metadatas"])` to build the basename map. With 1,449 drawers in the kg-real-policies wing this is ~50ms; cheaper than letting the model burn tokens on path-guessing retries. If wings grow past 50K drawers, switch to LRU-cached map keyed by wing.
