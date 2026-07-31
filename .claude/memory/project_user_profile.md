---
name: User profile feature (v8.17.0)
description: Auto-maintained per-user "memory from chat history" file at agents/main/user_profiles/<uid>.md, mirrored to MemPalace, injected as first-turn preamble
type: project
originSessionId: d93632c3-e42c-43cf-a97a-83b5cf83c145
---
Shipped 2026-04-26 in v8.17.0. The Claude.ai "Gedächtnis aus Chat-Verlauf" equivalent.

**File on disk**: `agents/main/user_profiles/<uid>.md` (gitignored). History at `<uid>.history/<ISO-timestamp>.md`, capped 30 entries, KEPT on Reset by design.

**MemPalace mirror**: wing `<uid>--main`, room `user_profile`, one drawer per `## section`, source_file `user/<uid>#profile/<slug>`. Purge-then-add on every save.

**Daemon**: `user-profile` thread (replaces deleted `daily-summary`). Polls every 30 min, gates on `daily_summary_enabled` + local-hour match + 23h cooldown. Cursor in `auth.db.user_daily_summary` (kept the old table name for back-compat).

**Sections** (fixed order): Work context, Personal context, Top of mind, Recent months, Earlier context, Long-term background. Hard schema in `_PROFILE_SYSTEM_PROMPT`.

**Sample policy**: 100 most-recent chats, last 90 days, per-chat = title + first user msg + last assistant msg (250 chars each), total cap 12K chars. Always pulls 90 days even on incremental update so demotion has context.

**Model**: resolves via `_profile_pick_model` (refinement → haiku → cheapest → default). Currently `mistral-vibe-cli-fast` because gemini-2.5-flash silently echoes input on polish prompts.

**Preamble injection**: first user message of each session gets `[Auto-maintained user profile … treat as background context, not as ground truth …]` block. SEPARATE from the greeting/job/comm-prefs preamble. Capped 4KB. **Why:** kept out of `_build_system_prompt` because that broke warm-pool KV-prefix matching for every authenticated turn — the system prompt MUST stay user-agnostic. **How to apply:** any future per-user injection goes in the first-user-message preamble, never in the system prompt.

**Endpoints**: `GET /v1/auth/profile-doc`, `POST` (manual edit, 32KB cap), `POST .../update-now` (synchronous, ~5-60s), `POST .../reset` (file+drawers, keeps history).

**Why the v8.14.0 daily-summary was scrapped:** that built activity logs (titles + run counts) — wrong abstraction. User wanted a single continuously-maintained profile, not a daily journal.

**Startup purge**: drops legacy `user_daily_summary` drawers from every user wing on every start. Idempotent.

**Account Settings → Memory tab** has the editor (380px tall textarea) + Update now / Reset / Save buttons + status line.
