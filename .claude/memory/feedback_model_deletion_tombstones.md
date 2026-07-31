---
name: model deletion uses tombstones, never auto-rediscover
description: Model deletions in the UI must persist as tombstones so startup/sync don't auto-rediscover them; only an explicit user-initiated Full Resync clears the tombstone set
type: feedback
originSessionId: dc90b168-03e4-4f63-8ae4-ee4226a77bd8
---
When a user deletes a model from the Models tab, that deletion must persist across restarts and provider syncs. Don't let `init_models_config` re-add a model that the user explicitly removed.

**Why:** `init_models_config` discovers models from each provider's `/models` endpoint on startup and on every sync. Without a tombstone list, deleted models reappear on the next server restart — frustrating, breaks user trust in the UI ("I deleted that, why is it back?").

**How to apply:**
- Persist tombstones in `config.json` → `deleted_models: []`
- The `delete` action on `POST /v1/models/config` adds to tombstones; `save`/`update`/manual-add strip from tombstones (re-adding revives an id)
- Auto sync (`action: 'sync'`) respects tombstones
- Only `action: 'resync_provider'` (user-clicked "Full Resync" button per provider) clears tombstones, and only for that provider's ids
- Never add an automatic path that clears tombstones — auto-clear defeats the whole point
