---
name: Provider management and model sync reliability
description: Provider/model management in web UI is flaky — needs multiple attempts to work correctly
type: project
---

Provider management and model sync in the web UI is unreliable — often requires multiple attempts to work correctly.

**Why:** User reports this as a recurring pain point (2026-04-05). Needs investigation into the config save → server reload → UI refresh pipeline.

**How to apply:** Priority fix. Investigate: config save endpoint, server config reload mechanism, model auto-discovery race conditions, UI state refresh after config changes. The Mistral integration exposed one instance — server needed a full restart to pick up new config.json provider/model entries.
