---
name: feedback_omlx_plist
description: oMLX 0.3.0 no longer needs SSD cache plist overrides
type: feedback
agent: main
last_recalled: 2026-04-03
---

As of oMLX 0.3.0, SSD cache flags are no longer needed in the plist — omlx auto-detects /Volumes/Scratch/omlx-cache as default. Use the clean formula plist (just `omlx serve`). Previous versions (0.2.x) required manual --paged-ssd-cache-dir flags.
