---
name: feedback_svg_not_emoji_buttons
description: "Buttons/icons in this web UI must be inline SVG (feather-style), never emoji or unicode symbol glyphs"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 1e6a7fbf-7564-4846-a3b9-775fb26ae9d2
---

In the Brain web UI (web/), button and status icons must be **inline SVG** (feather-style, `viewBox="0 0 24 24"`, `stroke="currentColor"` — see `_ptSvg` / `_PT_ICON` in panels_project_tree.js), **never emoji and never unicode symbol glyphs** (no ⟳, ●, ✕ as button content, no 🪨/🎧-style emoji). The app's existing icon set is all SVG and the user has corrected this more than once (2026-06-28, code-mode terminal tree).

**Why:** consistent monochrome look that tints with theme/row color; emoji render inconsistently across platforms and clash with the feather icon set.

**How to apply:** when adding any button/badge, copy an existing `_PT_ICON` SVG or write a feather-style `<svg>`. Reuse `_ptDot` for status dots. The only acceptable text-glyph is a plain `+`/`×` already established in a given toolbar — prefer SVG even there. Related: [[feedback_composer_controls_are_source_of_truth]].
