# Inline Span-Level Clickable Citations — Implementation Plan

**Status:** SCOPED, not built. Greenlit in the 2026-06-03 NotebookLM-gap session.
**Parent context:** `NOTEBOOKLM_GAP_HANDOVER.md` (Tier 4). brain-agent VERSION
when scoped: 9.62.0.

---

## What this is

NotebookLM shows **numbered inline citation chips** in the answer text; clicking a
chip jumps to the **exact source passage**. We have citations + a server-side
validator, but the UI renders compact pins / a collapsed "Zitiert" footer. This
feature = inline numbered chips at the citation point + click → open source +
**highlight the cited span**, anchored to the MemPalace drawer it came from.

---

## What already exists (the data is mostly there)

- **Citation format** `[Quelle: <file> — "<verbatim quote>"]` — captures BOTH the
  source file AND the exact quote (the span). Parsed in `web/js/chat_render.js`
  (~line 1297–1337, handles the `Quelle:`/`source:`-prefixed and no-prefix forms).
- **Server-side validator** (`handlers/chat.py:2371+`) — scans the reply for those
  brackets, **verifies each quote against the actual source file**, counts uncited
  claims, stores `msg_metadata.citation_validation`. So "this claim ↔ this verified
  passage ↔ this file" already exists.
- **Per-message refs** — `getReferencesForMessage(idx)` (`web/js/panels_right.js:
  813`) groups cited vs. searched refs per message (today → the pins / Zitiert
  section).
- **Drawer anchors** — drawers carry `read_path` / `read_path_original`
  (`engine/mempalace_glue.py:1123`), resolving back to the real file on disk;
  `mempalace_query` results surface these.

## The gaps

1. **Presentation** — render inline numbered chips at the citation point (vs. the
   collapsed footer). Mostly `chat_render.js`.
2. **Drawer anchoring** — citations today reference a **file + quote**, NOT a
   specific drawer. Drawer-anchored jump needs the citation to know **which drawer**
   the quote came from. ⚠️ See the wrinkle below.
3. **Source viewer with span highlight** — clicking a chip opens the source and
   scrolls/highlights the cited span. We have a doc viewer (`renderArtifactContent`)
   but not span-locate-and-highlight.

### ⚠️ The drawer-anchoring wrinkle (decided approach = drawer-anchored)

The model emits `[Quelle: <file> — "quote"]` — a **file**, not a drawer id. To
anchor to a drawer we must map (file + quote) → the originating drawer. Options:
- (a) At validation time, the server already reads the source to verify the quote —
  extend it to also resolve which **drawer** (via `mempalace_query` / drawer
  read_path matching) contains that quote, and store the drawer ref in
  `citation_validation`. Then the chip carries a drawer id.
- (b) Keep file+quote, and at click time do a drawer lookup on demand.
Approach (a) is cleaner (resolve once, store) — **plan assumes (a)**. This is the
real new backend work; the rest is UI.

---

## Locked decisions

| Decision | Choice | Notes |
|---|---|---|
| **Scope** | **Full: inline chips + jump-to-passage** | The complete NotebookLM experience. |
| **Jump mechanism** | **Drawer-anchored** (MemPalace drawer refs) | More precise than verbatim-string search. Requires citations to track the drawer (wrinkle above). |

---

## Build steps

### 1. Resolve + store the drawer anchor (backend)

- Extend the citation validator (`handlers/chat.py:2371+`): when it verifies a
  quote against a source file, ALSO resolve the **drawer** that contains it (reuse
  the `mempalace_query` / drawer `read_path` machinery) and store a drawer ref
  (id + read_path + span offsets if available) alongside each verified citation in
  `msg_metadata.citation_validation`.
- Span offsets: if the drawer/validator can capture the char range of the quote
  within the drawer, store it (enables precise highlight). Else store the verbatim
  quote (highlight by string match within the drawer). DECIDE AT BUILD.

### 2. Inline numbered chips (frontend)

- In `chat_render.js`, render each citation as a small numbered chip `[1]`, `[2]`
  **inline at its position** in the answer text (the bracket is already located by
  the existing parser — replace the bracket with a chip instead of stripping it to
  a footer). Keep a footer legend mapping numbers → file/drawer.
- Hover a chip → tooltip with file + quote (cheap, no navigation).

### 3. Click → open source + highlight span

- Click a chip → open the source in a viewer (reuse `renderArtifactContent`),
  navigate to the anchored **drawer**, scroll to + highlight the cited span.
- Highlight: by stored span offsets (precise) or by locating the verbatim quote
  string within the rendered drawer (fallback). Visibly mark it (e.g. `<mark>`).
- Where the viewer opens: right-panel (reuses existing layout) vs. a modal. DECIDE
  AT BUILD; right-panel likely fits the existing ref/artifact panel.

---

## Open items to resolve AT BUILD

1. **Drawer resolution cost** — resolving drawer-per-citation at validation time
   adds work to a path that already reads sources. Measure; cache if needed.
2. **Span offsets vs. string-match highlight** — precise (needs offset capture) vs.
   robust (re-find the quote). String-match is the safe v1; offsets a refinement.
3. **Viewer surface** — right-panel vs. modal for the source-with-highlight.
4. **Chip↔footer coexistence** — keep the existing "Zitiert" footer as the legend,
   or replace entirely? (Keep as legend is least disruptive.)
5. **Uncited / unverified claims** — the validator flags these; show them
   distinctly (e.g. a warning chip) or unchanged? Ties to existing validator UX.
6. **Web-source citations** — `metadata.web_sources` quotes have URLs, not drawers;
   those chips link out (existing `msg-web-source-host` pattern) rather than to a
   drawer. Handle both chip kinds.

## Explicitly OUT of scope

- Changing the citation FORMAT the model emits (keep `[Quelle: file — "quote"]`).
- Re-architecting the validator's verify logic (extend, don't replace).
- Editing source documents from the viewer (read-only highlight).

---

## Repo-convention obligations (same change)

- brain-agent-guide skill: citation UX change → `06-user-manual.md` (German); if
  the validator metadata shape changes → `05-internals.md`. No new endpoint likely
  (extends existing metadata) — verify. VERSION bump in two places. python-compile
  brain.py. Graceful restart (SIGTERM, never SIGKILL). Commit to main.
  `./web/js/js_gate.sh` passes (net-globals updated for new JS).

## Success criteria

- Answers show numbered inline chips at each citation point; a footer legend maps
  them to file/drawer.
- Clicking a chip opens the source, navigates to the anchored drawer, and
  highlights the cited span.
- Web-source citations link out correctly; uncited/unverified claims are visibly
  distinct.
- Drawer anchor resolved + stored at validation time (not a fragile click-time
  guess).
- js_gate passes; brain.py compiles; version check after restart.
