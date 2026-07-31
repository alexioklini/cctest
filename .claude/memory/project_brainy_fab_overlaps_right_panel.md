---
name: project_brainy_fab_overlaps_right_panel
description: "Brainy FAB (fixed bottom-right, z-90) floats OVER the right panel's bottom-right corner — interactive controls placed there are click-blocked; pad right ~84px"
metadata: 
  node_type: memory
  type: project
  originSessionId: a8c680ad-54cf-4c59-a46c-0ddcab1703bb
---

The Brainy bubble (`#brainy-dock`, `position:fixed; right:22px; bottom:22px; z-index:90`, FAB ~56px) is NOT part of the layout flow — it floats **over the right panel** (`#right-panel` is the rightmost flex column). Any interactive control placed in the bottom-right corner of a right-panel tab pane is visually covered and click-intercepted by the FAB.

**Why:** hit this 2026-07-02 (v9.258.0) when the new btw "Zwischenfragen" tab got its own composer at the pane bottom — Playwright verification failed with "brainy-bubble intercepts pointer events" on the send button; a real-user bug, not a test artifact. The Websuche pane's bottom rows tolerate it only because they're lists, not buttons.

**How to apply:** when adding bottom-anchored controls to a right-panel pane, keep the bottom-right ~84px clear (e.g. `.btw-tab-composer { padding-right: 84px }`) or place the action button left-of-input. Verify with a real click (Playwright), not just visibility.
