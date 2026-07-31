---
name: chat-summary-model-setting
description: "2026-05-17 — `config.json → chat_summary_model` admin setting drives both per-chat synopsis and user-profile daemon. Empty = server default_model. Settings → Server → \"Summaries\" section."
metadata: 
  node_type: memory
  type: project
  originSessionId: 60a6bac4-188b-4b35-b05f-befa1c3d976b
---

Single admin-configurable model setting that drives both background summariser paths.

**Why:** The per-chat synopsis (sidebar hover tooltip + collapsible block) and the user-profile daemon (auto-maintained `agents/main/user_profiles/<uid>.md`) used to silently auto-pick "cheapest haiku → cheapest enabled" which was wrong on Anthropic-less installs. User wanted one knob for both.

**How to apply:**
- Setting lives at `config.json` top-level: `"chat_summary_model": "model-id"`. Empty string or absent = fall through to server `default_model`.
- Surface: Settings → Server card → "Summaries" section (dropdown of chat-capable enabled models + "Auto" option).
- API: `GET /v1/services` returns `server.chat_summary_model`; `POST /v1/services/server {"chat_summary_model": "..."}` validates + persists.
- Loaded into `server_config["chat_summary_model"]` in `server.py:main` from `file_config`.
- Validation: model must be enabled in `_models_config`; empty clears it.
- GDPR auto-fallback (cloud → local on PII detection) still applies on top of the configured model.

## Code paths reading it

- `server.py:_generate_chat_summary` — per-chat 1-sentence synopsis (max_tokens=80).
- `server.py:_profile_pick_model` — user-profile daemon (multi-section Markdown).

## Renamed: chat title vs summary

Same change ticket also separated title from summary in the UI:
- **`session.title`** — primary label, auto-derived from first user message (with `\n\n[User attached files...` notice stripped). Manual rename now writes here (was writing to `summary` column).
- **`session.summary`** — LLM-generated synopsis only. Shown as hover tooltip on chat list rows + chat header, and as a collapsible `<details>` block under turn 1 in the chat body. State `chat._summaryOpen` survives re-renders; summary refresh never auto-expands.

Related: [[unified-background-model-policy]], [[llm-call-catalog]].
