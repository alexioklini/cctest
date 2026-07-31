---
name: oMLX plist cache flags
description: After upgrading oMLX via brew, re-add SSD cache flags to the launchd plist
type: feedback
---

After `brew upgrade omlx`, the plist at `/opt/homebrew/opt/omlx/homebrew.mxcl.omlx.plist` resets to defaults. Must re-add these ProgramArguments after `serve`:

```
--paged-ssd-cache-dir /Volumes/Scratch/omlx-cache
--paged-ssd-cache-max-size 100GB
--hot-cache-max-size 8GB
```

**Why:** Brew overwrites the plist on upgrade, losing custom flags. The SSD cache on /Volumes/Scratch is important for KV prefix caching performance.

**How to apply:** Whenever upgrading oMLX (`brew upgrade omlx`), stop the service, edit the plist to add the cache flags back, then restart.
