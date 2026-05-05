# handlers/ — HTTP Request Handlers

Each module handles a slice of the server's route surface. Routes are registered in `server.py` — grep `@app.route` or `self.path` for the full list.

## Module Map

| File | Route prefix / responsibility |
|------|-------------------------------|
| `chat.py` | `/v1/chat/*` — inference, SSE stream, file attachments, proxy responses, citation re-round |
| `sessions_handler.py` | `/v1/sessions/*` — CRUD, list, archive, delete, next-prompt, memory toggle |
| `projects.py` | `/v1/projects/*`, `/v1/notes/*`, `/v1/ingest/*` — project CRUD, notes, file ingestion |
| `providers.py` | `/v1/providers/*`, `/v1/models/*` — provider/model config, warmup trigger |
| `admin.py` | `/v1/admin/*`, `/v1/agents/*`, `/v1/workflows/*`, `/v1/kg/*` — admin, agent mgmt, workflows, KG |
| `auth.py` | `/v1/auth/*`, `/v1/users/*` — login, token refresh, user account settings, preferences |

## Key Invariants

- `augmented_messages` strips metadata fields before the wire (only `role`+`content`) — prevents 400s from providers seeing internal keys.
- Multipart upload uses a manual boundary parser (`3.13+` removed `cgi`); preserves original filename.
- SSE streams use 5s keepalive comments — don't remove them.
- Citation re-round (`chat.py`) fires synchronously when validator finds >30% uncited or ≥2 unverified quotes; result replaces original assistant message in the same SSE stream.
- `_schedule_owner_check(name)` in `sessions_handler.py` / `admin.py` gates mutating schedule ops for non-admins (non-admins see only own schedules; legacy empty-`user_id` schedules stay admin-only).

## Chat Attachments (chat.py)

Files arrive as `body.files` (legacy `body.images` for Telegram) — base64. Per-file routing:
- MIME match + base64 + <20MB → OpenAI `image_url` data URI (multimodal path)
- Otherwise → `/tmp/brain-attachments/{session_id}/`, agent uses `read_document`
- Non-vision model + image → describe via `attachments.image_model`; unconfigured → metadata only
