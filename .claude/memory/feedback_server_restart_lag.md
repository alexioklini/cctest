---
name: Brain server restart has ~6–8s cold start
description: After launchctl kickstart, curl against localhost:8420 may fail for up to ~6s before the HTTP listener binds; scripts must sleep before probing
type: feedback
originSessionId: 58566bb4-dc63-420f-b993-003d7a0654ee
---
After `launchctl kickstart -k gui/$(id -u)/com.brain-agent.server`, the server is not immediately ready. SQLite WAL replay, MemPalace import, model-catalog init, and warmup pool setup run before the HTTP listener binds on port 8420.

**Observed delay:** typically 4–8 seconds. A 3s sleep is not enough — saw `Connection refused` at 4s on Apr 22 session during Step-5 testing.

**How to apply:** In smoke tests, always `sleep 6` or longer after `kickstart`, and retry with exponential backoff if the first `curl /v1/status` fails. Don't treat an initial connection refusal as a server crash — check logs AND give it another 3–4 seconds before escalating.

**Auth DB lock side-effect:** if you hammer `/v1/auth/users` during startup, the bcrypt-heavy create_user calls can still hold the WAL lock while the server is concurrently doing its own init writes, producing `sqlite3.OperationalError: database is locked`. These cleared once startup finished.
