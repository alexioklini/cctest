# Inline Span-Level Citations — DETAILED DESIGN SPEC

**Status:** DETAILED SPEC (mockups + workflows + edge cases). PRE-IMPLEMENTATION.
**Supersedes:** `INLINE_CITATIONS_PLAN.md` (lean scope; locked decisions hold).
**Parent:** `NOTEBOOKLM_GAP_HANDOVER.md` (Tier 4). brain-agent VERSION: 9.62.0.

> The most technically involved of the coupled trio — the drawer-anchoring +
> span-highlight pieces are genuinely new. Mockups are intent, not pixel-final.

---

## 0. Verified code anchors

| Capability | Where | Note |
|---|---|---|
| Citation format | `[Quelle: <file> — "<verbatim quote>"]` | Captures source file AND the exact span (quote). |
| Citation parser | `extractCitationsFromRaw(text)` (`web/js/chat_render.js:~1294`) | Already locates brackets (handles backtick-wrapped + no-prefix forms). |
| Server validator | `handlers/chat.py:2371+` | Scans brackets, **verifies each quote against the source file**, stores `msg_metadata.citation_validation`. |
| Per-message refs | `getReferencesForMessage(idx)` (`web/js/panels_right.js:813`) | Returns `{cited, searched}` matched by basename — the seam chips extend. |
| Drawer anchors | drawers carry `read_path` / `read_path_original` (`engine/mempalace_glue.py:1123`) | The on-disk file a drawer came from; surfaced by `mempalace_query`. |
| Web-source citations | `metadata.web_sources` + `msg-web-source-host` link (`chat_render.js:857`) | Quotes with URLs (not drawers) — chips link out. |

---

## 1. Feature summary & locked decisions

NotebookLM-style **numbered inline citation chips** in the answer text; clicking a
chip **opens the source and highlights the exact cited passage**, anchored to the
MemPalace **drawer** the quote came from.

**Locked:** full scope (inline chips + jump-to-passage) · **drawer-anchored**
(more precise than verbatim-string search).

**The data mostly exists** (file + verified quote + drawer read_path). The genuine
new work: (a) resolve+store WHICH drawer a citation came from, (b) a source viewer
that highlights the span.

---

## 2. ⚠️ The drawer-anchoring problem (core of this spec)

The model emits `[Quelle: <file> — "quote"]` — a **file**, not a drawer id. The
validator already reads the source to verify the quote. **Extend it**: at
validation time, resolve which **drawer** contains that quote (via the same
`mempalace_query` / drawer-read_path machinery), and store a drawer ref in
`citation_validation`:

```
citation_validation.citations[] = {
  n:            1,                       // chip number (render order)
  file:         "art_53.pdf",
  quote:        "providers of GPAI models shall…",
  verified:     true,                    // existing validator result
  drawer_id:    "project__<pid>/…#<chunk>",   // NEW — resolved at validation
  read_path:    "/abs/.../art_53.pdf",        // NEW
  span:         {start: 1840, end: 1922} | null  // NEW (optional, see §6)
}
```
- Resolve ONCE at validation, store on the message metadata — chips carry the
  anchor; no fragile click-time guessing.
- `span` offsets are a refinement; if unavailable, highlight by locating the
  verbatim `quote` string within the drawer (robust fallback).

---

## 3. MOCKUPS

### 3.1 Inline chips in an answer

```
┌─ Assistant ──────────────────────────────────────────────────┐
│ GPAI providers must publish a sufficiently detailed summary   │
│ of training content [1] and maintain technical documentation  │
│ per Annex XI [2]. High-risk systems additionally require a    │
│ conformity assessment [3].                                    │
│                                                              │
│ ── Sources ─────────────────────────────────────────────────│
│  [1] art_53.pdf — "…detailed summary of the content used…"   │
│  [2] art_53.pdf — "…technical documentation … Annex XI…"     │
│  [3] annex_iii.pdf — "…conformity assessment procedure…"     │
│  ⚠ [4] (unverified quote — not found in source)              │
└──────────────────────────────────────────────────────────────┘
```
- `[1]`,`[2]`,`[3]` = small clickable superscript chips **inline at the citation
  point** (replacing today's stripped-to-footer bracket).
- Footer legend maps numbers → file + quote (kept as the legend, not removed).
- Hover a chip → tooltip with file + quote (no navigation).
- Unverified/uncited claims badged distinctly (`⚠`).

### 3.2 Click a chip → source opens with the span highlighted

```
┌─ 📄 art_53.pdf ─────────────────────────────────── (chip [1]) ✕│
│  …                                                            │
│  Article 53 — Obligations for providers of GPAI models        │
│  ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓        │  ← highlighted span
│  "providers shall draw up and make publicly available a       │     (scrolled into view)
│   sufficiently detailed summary of the content used for       │
│   training the general-purpose AI model"                      │
│  …                                                            │
│                                            [ ‹ prev  next › ]  │  ← step between this msg's cites
└──────────────────────────────────────────────────────────────┘
```
- Opens in the **right-panel** (reuses the existing ref/artifact panel layout).
- Scrolls to + `<mark>`-highlights the cited span in the drawer/source.
- `prev/next` steps through the message's citations.

### 3.3 Web-source citation chip (links out, not a drawer)

```
   …per the Commission guidance [5].
   [5] digital-strategy.ec.europa.eu — "…code of practice…"  ↗
```
- Web citations (`metadata.web_sources`) render a chip that **opens the URL** (the
  existing `msg-web-source-host` pattern), not the drawer viewer.

---

## 4. END-TO-END WORKFLOWS

### W1 — Render answer with verified citations
1. Turn completes; validator runs (existing), now ALSO resolving drawer anchors
   (§2) → `citation_validation.citations[]` on the message.
2. FE `chat_render.js`: the existing parser locates brackets → replace each with a
   numbered chip; build the footer legend from `citation_validation`. ✔

### W2 — Hover a chip
- Tooltip: file + quote. No navigation, no fetch. ✔

### W3 — Click a chip → jump to passage (drawer-anchored)
1. Chip carries `drawer_id` + `read_path` (+ optional `span`).
2. FE opens the source in the right-panel viewer (reuse `renderArtifactContent`
   over the drawer's `read_path`).
3. Locate the span: by `span` offsets if present, else find the verbatim `quote`
   string in the rendered text; scroll into view + `<mark>` it (3.2). ✔

### W4 — Step prev/next through a message's citations
- `prev/next` in the viewer cycles the message's `citations[]`, re-highlighting. ✔

### W5 — Web-source citation
- Chip → open URL in a new tab (3.3), bypassing the drawer viewer. ✔

### W6 — Unverified / uncited claim
- Validator flagged it (existing). Render a distinct `⚠` chip / footer line; click
  shows "this quote was not found in the cited source" rather than a (broken)
  jump. ✔

---

## 5. EDGE CASES

- **E1 Quote not locatable in the drawer at click time** (rendering differs from
  mined text) — fall back: highlight the closest fuzzy match, or show the drawer
  with a "couldn't pinpoint the exact passage" notice. Never a hard error.
- **E2 Drawer resolution failed at validation** (quote spans 2 chunks / drawer
  purged) — chip still shows file+quote (degrade to file-level, no jump), badged.
- **E3 Source file deleted/moved since the turn** — viewer shows "source no longer
  available"; chip still shows the quote text.
- **E4 Same quote in multiple drawers** — anchor to the first/highest-scoring;
  acceptable (it's the same text).
- **E5 Old messages (pre-feature)** — no stored drawer anchors → render chips with
  hover + footer, but jump degrades to today's behavior (open file, string-find).
  No migration required.
- **E6 Non-Latin / special chars in quote** — string-match highlight must be
  unicode-safe; normalize before matching.
- **E7 Performance** — drawer resolution adds work to validation (already reads
  sources). Measure; cache the (file,quote)→drawer lookup per turn.

---

## 6. DATA & RENDERING CONTRACTS

**Metadata (extended, backward-compatible):** add `drawer_id`, `read_path`,
`span?` to each `citation_validation.citations[]` entry. Older messages lack them →
graceful degrade (E5).

**Span capture (decide at build):**
- **String-match (v1, robust):** store only the quote; locate at render time. Works
  with all current data. Imperfect when rendering ≠ mined text.
- **Offset (refinement):** if the validator/drawer can return the char range of the
  quote within the drawer, store `span{start,end}` for precise highlight.
- Plan: ship string-match; add offsets if precision is insufficient.

**No format change:** the model keeps emitting `[Quelle: file — "quote"]`. Chips +
anchors are derived; the wire/citation grammar is untouched.

---

## 7. BUILD PHASING

1. **Backend** — extend the validator to resolve + store the drawer anchor (§2).
   (The one genuinely new backend piece.)
2. **Inline chips** — `chat_render.js`: bracket → numbered chip + footer legend +
   hover tooltip (no jump yet). Shippable increment.
3. **Jump-to-passage** — right-panel source viewer with span highlight +
   prev/next; web-source chips link out; degrade per E-series.

---

## 8. OPEN ITEMS (decide at build)
1. Span offsets vs string-match highlight (ship string-match).
2. Viewer surface: right-panel (preferred) vs modal.
3. Keep the existing "Zitiert" footer as the legend (preferred) vs replace.
4. Drawer-resolution caching strategy (per-turn cache).
5. How forcefully to badge unverified claims (warning chip vs subtle marker).

## 9. Repo-convention obligations
brain-agent-guide: citation UX → `06-user-manual.md` (German); validator metadata
shape → `05-internals.md`. Likely NO new endpoint (extends existing metadata) —
verify. VERSION ×2. compile brain.py. SIGTERM-only restart. commit→main. js_gate
green (net-globals updated for chip/viewer JS).

## 10. Success criteria
Answers show numbered inline chips at each citation point with a footer legend;
hover shows file+quote; click opens the source at the **drawer-anchored** passage
and highlights the cited span with prev/next; web-source chips link out;
unverified claims badged; old messages degrade gracefully; anchors resolved+stored
at validation (not click-time guesswork); js_gate + compile + version check pass.
