---
name: Composer toolbar icon style
description: User prefers literal, symbolic icons over abstract ones for composer toggle buttons in the web UI
type: feedback
originSessionId: e26b4f37-bd35-40ac-9853-f17b81479b72
---
For composer toolbar toggle buttons in `web/index.html` (e.g. caveman mode, save-to-memory), pick icons that literally depict the concept, not abstract shapes. Filled accents plus a clear silhouette work better than pure line art at small sizes.

Concrete example: caveman mode ended on a **campfire** — filled flame on top + two crossed logs (X) beneath. Prior attempts (generic flame, fireplace mantel, flame + log row) were rejected as "not easy to depict what it means."

**Why:** User iterates on icon legibility until the metaphor is obvious at a glance. Abstract / multi-element icons get rejected even when technically correct.

**How to apply:** When adding or changing a composer-btn SVG, lead with the strongest single metaphor (crossed logs = campfire, chain link = memory, etc.), use `fill="currentColor" fill-opacity="0.15"` to add visual weight to key shapes, and keep stroke-width ≥1.5. If the user says it doesn't land, switch metaphor entirely rather than tweaking the same one.

**Caveman mode icon set (final, 2026-04-19):** distinct icons per level instead of a single icon color-coded by level — user prefers depicting the "primitive ↔ modern" axis directly. Mapping is off → **spaceship**, lite → **car**, full → **horse**, ultra → **campfire**. Implementation is `cavemanIconFor(mode)` in `web/index.html`, wired through `updateStatusBar()`.

**Palace/MemPalace icon:** classical building with triangular pediment + columns (filled pediment + four column strokes + base platform). Animated in place via `.mp-storing` (green pulse) and `.mp-retrieving` (blue pulse) classes toggled by `MempalaceActivityMonitor`.
