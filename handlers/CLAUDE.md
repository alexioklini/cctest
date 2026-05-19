# handlers/ — HTTP Request Handlers

Each module handles a slice of the server's route surface. Routes are registered in `server.py` — grep `@app.route` or `self.path` for the full list.

## Module Map

| File | Route prefix / responsibility |
|------|-------------------------------|
| `chat.py` | `/v1/chat/*` — inference (`POST /v1/chat`), reconnect to in-progress turn (`GET /v1/chat/stream`), cancel, ask-user answer, file attachments, proxy responses, citation re-round |
| `sessions_handler.py` | `/v1/sessions/*` — CRUD, list, archive, delete, next-prompt, memory toggle |
| `projects.py` | `/v1/projects/*`, `/v1/notes/*`, `/v1/ingest/*` — project CRUD, notes, file ingestion |
| `providers.py` | `/v1/providers/*`, `/v1/models/*` — provider/model config, warmup trigger |
| `admin.py` | `/v1/admin/*`, `/v1/agents/*`, `/v1/workflows/*`, `/v1/kg/*` — admin, agent mgmt, workflows, KG |
| `auth.py` | `/v1/auth/*`, `/v1/users/*` — login, token refresh, user account settings, preferences |
| `classification.py` | `/v1/classification/*` — document classification detector (ARL 20.02.02.06): scan uploads/folders/projects, scan history, admin config (keywords + extra regex). Detect-only in Phase A; enforcement seams are Phase B. See root CLAUDE.md "Data View — Document Classification". |

## Key Invariants

- `augmented_messages` strips metadata fields before the wire (only `role`+`content`) — prevents 400s from providers seeing internal keys.
- Multipart upload uses a manual boundary parser (`3.13+` removed `cgi`); preserves original filename.
- SSE streams use 5s keepalive comments — don't remove them.
- **Resumable streaming**: the chat worker runs independently of the HTTP connection — see `CLAUDE.md` § "Resumable Streaming". `event_callback` emits into `session.live_stream` (a `LiveStream`); `_stream_live_to_client` (shared by `POST /v1/chat` and `GET /v1/chat/stream`) replays the buffer then follows live events. Disconnecting a stream NEVER cancels the worker — only `POST /v1/chat/cancel`. `GET /messages` exposes `streaming: true` + persisted `streaming_text` while a turn is live.
- Citation re-round (`chat.py`) fires synchronously when validator finds >30% uncited or ≥2 unverified quotes; result replaces original assistant message in the same SSE stream.
- `_schedule_owner_check(name)` in `sessions_handler.py` / `admin.py` gates mutating schedule ops for non-admins (non-admins see only own schedules; legacy empty-`user_id` schedules stay admin-only).

## Chat Attachments (chat.py)

Files arrive as `body.files` (legacy `body.images` for Telegram) — base64. Per-file routing:
- MIME match + base64 + <20MB → OpenAI `image_url` data URI (multimodal path)
- Otherwise → `/tmp/brain-attachments/{session_id}/`, agent uses `read_document`
- Non-vision model + image → describe via `attachments.image_model`; unconfigured → metadata only
