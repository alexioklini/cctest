# Transparent Anonymisation — Handover

Status as of v8.41.0 (2026-05-16). Steps 1–4 landed; steps 5–6 outstanding.
This file is the single source of truth to pick the work back up — every
file path, every invariant, every gotcha is here.

---

## Goal recap

When the existing GDPR PII scanner flags personal data in an outgoing chat
message, give the user a modal with four choices:

1. **Anonymise & continue** — server pseudonymises text + (later: files)
   before the LLM call, de-anonymises text deltas + the final reply, so the
   user sees real values but the cloud LLM only ever sees opaque tokens or
   shape-preserving fakes.
2. **Use local model** — swap to the configured local fallback for this turn,
   no anonymisation needed (data stays on-prem).
3. **Continue anyway** — only enabled when worst severity is `warn`.
4. **Cancel** — abort the turn.

**Hard invariants** (never violate during step 5–6 work):

- GDPR data **must never reach cloud LLMs** after a failed anonymisation —
  recovery offers only `local_model` or `cancel`, never a "send to cloud
  anyway" escape hatch.
- Anonymise/deanonymise operations show in chat as **persisted shield-iconed
  pseudo-tool-call rows** (visible on reload, not just live SSE).
- The pseudonymisation **mapping must never leak into the LLM context** —
  rows have `metadata.synthetic=true`, written via `ChatDB.save_message`,
  not via `Session.add_message` (so they bypass `session.messages`, the
  in-memory list that's handed to the LLM).
- The user **never sees an unfinished token** like `<EMAIL_1_` flashing on
  screen — the `StreamingDeanonymizer` holds back chars after an unclosed
  `<` until the closing `>` arrives.

---

## What's done (v8.41.0 — steps 1–4)

### Step 1 — `pseudonymizer.py` (text + persistence)

Repo-root module. Public API: `new_mapping()`, `pseudonymize_text(text,
findings, *, mapping, source)`, `deanonymize_text(text, *, mapping) →
(text, restored_count)`, `close_mapping(id)`, `save_mapping(m, *,
session_id, turn_id)`, `load_mapping(id)`, `restore_mapping_to_registry(m)`,
`delete_persisted_mapping(id)`, `encrypt_mapping(m) → (nonce, ct)`,
`decrypt_mapping(id, nonce, ct)`, `StreamingDeanonymizer` lives in
`handlers/chat.py`, not here.

**Token style**: hybrid.
- Opaque `<KIND_N_SALT>` (N = per-kind index; SALT = 4 hex chars per
  mapping) for free-text PII.
- Shape-preserving fakes for these rule_ids (`SHAPE_PRESERVING` frozenset):
  - `iban` — `_fake_iban`: same length, country preserved, mod-97-valid
  - `credit_card` — `_fake_credit_card`: same digit-count, Luhn-valid, BIN '4'
  - `phone` — `_fake_phone`: same digit-count, +999 prefix (synthetic)
- Separator pattern preserved via `_re_inject_separators(template, digits)`.

**Storage**: in-memory `_REGISTRY: dict[str, Mapping]` keyed by `mapping_id`,
guarded by `_REGISTRY_LOCK`. Plus optional AES-GCM mirror in chats.db.

**Key**: `agents/main/pseudonym.key` (32 random bytes, mode 0600,
bootstrapped on first use via `_load_or_create_key()`). Cached in
`_KEY_CACHE`; test override via `_KEY_PATH_OVERRIDE`. **Refuses to load**
if the file exists but is the wrong length.

**AES-GCM AAD**: the `mapping_id` is bound as AAD so swapping ciphertext
rows between mappings raises `InvalidTag`.

**Tolerant reverse**: `_TOKEN_RE_TOLERANT` matches `< person_1_ab12 >` etc.;
salt must match (case-insensitively) before substitution.

### Step 2 — `pseudonym_maps` table + ChatDB CRUD

`server_lib/db.py` additions:

```sql
CREATE TABLE IF NOT EXISTS pseudonym_maps (
    mapping_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    turn_id TEXT NOT NULL DEFAULT '',
    nonce BLOB NOT NULL,
    ciphertext BLOB NOT NULL,
    created_at REAL NOT NULL DEFAULT (strftime('%s','now')),
    updated_at REAL NOT NULL DEFAULT (strftime('%s','now'))
);
CREATE INDEX IF NOT EXISTS idx_pseudonym_maps_session
    ON pseudonym_maps(session_id);
```

Methods on `ChatDB`:

- `save_pseudonym_map(mapping_id, session_id, turn_id, nonce, ciphertext)` —
  UPSERT (idempotent re-save during a turn extends the map).
- `load_pseudonym_map(mapping_id) → (nonce_bytes, ciphertext_bytes) | None`.
- `list_pseudonym_maps_for_session(session_id) → [(id, turn_id, created_at), ...]`.
- `delete_pseudonym_map(mapping_id)`.
- `purge_orphan_pseudonym_maps(max_age_seconds=None) → int_deleted` — drops
  rows whose session is gone; optional age filter (not used by default).

**`delete_session` cascade**: extended to `DELETE FROM pseudonym_maps
WHERE session_id = ?` in the same transaction.

**Boot recovery**: `recover_active_turns_on_boot()` in `handlers/chat.py`
calls `purge_orphan_pseudonym_maps()` before scanning active_turns.

### Step 3 — chat-worker wiring

`handlers/chat.py`:

**Module-level helpers** (above `ChatHandlerMixin`):
- `_gdpr_recovery_pending: dict[str, dict]` + `_gdpr_recovery_lock` — slot
  registry keyed by `session_id` (only one anonymisation in flight per session).
- `_gdpr_recovery_register(sid) → Event` / `_gdpr_recovery_clear(sid)`.
- `deliver_gdpr_recovery_choice(session_id, choice) → bool` — called by
  the new `/v1/chat/gdpr-recovery` endpoint. Validates choice ∈
  {`'local_model'`, `'cancel'`}; refuses anything else.
- `_emit_synthetic_tool_event(*, live, sid, kind, tool_use_id, phase,
  args=None, result=None, status='ok', duration_ms=0)` — writes the row
  via `ChatDB.save_message("tool_use" | "tool_result", json.dumps(content),
  metadata={"synthetic": True, "kind": ..., "tool_use_id": ..., "phase": ...,
  "status": ..., "duration_ms": ...})` AND emits matching SSE event
  (`synthetic_tool_use` / `synthetic_tool_result`). **Never** uses
  `Session.add_message` — the LLM must never see these.
- `PseudonymizeError(message, *, sources=[])` — exception class for the
  step-5 file walkers to raise.
- `StreamingDeanonymizer(mapping)` — per-turn helper. `feed(raw_delta) →
  safe_chunk`, `flush() → tail`, `final_text() → full_deanonymized`. Safe
  boundary = position before the last unclosed `<` in the cumulative
  de-anonymised buffer.

**`_handle_chat`**:
- Reads `body.gdpr_action` (`'anonymise'` | `'local_model'` | `'continue'` |
  null). Sets `session._gdpr_pending_action = "anonymise"` for the worker
  to pick up. **Note**: the `'anonymise'` branch defers all work to the
  worker thread (see "Why deferred" below). `'local_model'` runs inline.
- `'local_model'` branch: resolves `gdpr_scanner.default_local_fallback_model`,
  calls `self._resolve_provider(_fallback)`, mutates `session.model` under
  `session.lock`, audits `pii_local_swap`. Returns 400 if no fallback
  configured.
- **User message persistence is deferred** for `'anonymise'` — see
  `if session._gdpr_pending_action != "anonymise":` guard around
  `session.add_message("user", user_content)`. The worker adds it AFTER
  anonymising.

**Why deferred to worker**: the anonymisation flow must be able to emit
`gdpr_recovery_required` and BLOCK on a user response. If we do that
before `send_response(200)`, the client's `fetch('/v1/chat')` is still
blocked waiting for headers, so it can never see the modal payload, can
never POST the recovery choice — deadlock. The worker runs AFTER SSE
headers are flushed, so live events reach the client immediately.

**Worker body** (`worker()` inside `_handle_chat`):
- New block right at the top of the `try:`, BEFORE purpose classification:
  - `nonlocal_message = message; nonlocal_user_content = user_content` —
    closure-shadowing locals to avoid `nonlocal` declarations.
  - If `session._gdpr_pending_action == "anonymise"`:
    1. `_mapping = pseudonymizer.new_mapping()`; `_anon_tool_id = f"anon_{_mapping.mapping_id[:12]}"`.
    2. Emit dispatch synthetic row (`_emit_synthetic_tool_event` phase=`'dispatch'`,
       args=`{"sources": ["chat_text"]}`).
    3. Scan via `engine._pii_scan_text(nonlocal_message, cfg=...)`.
    4. If findings: `pseudonymize_text(...)`, update `nonlocal_message` +
       text block in `nonlocal_user_content`.
    5. `pseudonymizer.save_mapping(_mapping, session_id=sid, turn_id=_anon_tool_id)`.
    6. `session._gdpr_mapping_id = _mapping.mapping_id` and
       `session._gdpr_streamer = StreamingDeanonymizer(_mapping)`.
    7. Emit done synthetic row (status='ok') + `pii_anonymised` audit.
  - On exception:
    1. Emit done synthetic row (status='error', result has `error` key) +
       `pii_anonymise_failed` audit.
    2. `delete_persisted_mapping(...)` + `close_mapping(...)` — never send
       a half-anonymised mapping to the LLM.
    3. Emit `live.emit("gdpr_recovery_required", {session_id, error, sources})`.
    4. `_event = _gdpr_recovery_register(sid)`; `_delivered = _event.wait(timeout=300)`.
    5. On timeout/cancel: emit terminal `done` with `cancelled:true,
       reason:"gdpr_anonymise_failed"`; `pii_anonymise_failed_cancel` audit;
       `return` (worker exits, `finally` runs).
    6. On `local_model`: resolve fallback via `engine.resolve_provider_for_model`,
       mutate `session.model` under lock; `pii_anonymise_failed_local_swap`
       audit; FALL THROUGH with original content.
  - **After the try/except** (success OR local-fallback recovery):
    `session.add_message("user", nonlocal_user_content)` and rebind
    `_msg_count_before = len(session.messages)` so rollback semantics match
    the non-anonymise path.
- Then `message = nonlocal_message` so `classify_task_purpose(message)`
  sees the (possibly anonymised) text.

**`build_chat_event_callback`** — `text_delta` branch:
- If `session._gdpr_streamer` is set: `safe_chunk = streamer.feed(raw_delta)`
  → if non-empty, `live.emit("text_delta", {"text": safe_chunk})`;
  persist `streamer.final_text()` (de-anonymised) as `streaming_text`.
  **Early-return** so the raw delta is never re-emitted below.
- Otherwise: existing path (append raw to `_partial_reply`, persist raw to
  `streaming_text`).

**Worker reply finalisation** (around `session.add_message("assistant",
reply, ...)`):
- If `session._gdpr_mapping_id` set: `streamer.flush()` → emit tail;
  `pseudonymizer.get_mapping(id)` → `deanonymize_text(reply, mapping)`;
  emit `deanonymise_text` synthetic pair; `pii_deanonymise_text` audit;
  `reply = _deanon_reply`; attach `msg_metadata.gdpr_mapping_id` +
  `gdpr_restored`. Then existing `session.add_message("assistant", reply, ...)`
  runs with the de-anonymised text.

**Worker `finally`** — drop in-memory mapping (`pseudonymizer.close_mapping(...)`)
+ clear `session._gdpr_mapping_id` / `session._gdpr_streamer`. The
encrypted SQLite row persists per the user's `persist_maps=true` choice.

**New endpoint** `_handle_chat_gdpr_recovery` — `POST /v1/chat/gdpr-recovery`
body `{session_id, action: 'local_model'|'cancel'}`. Calls
`deliver_gdpr_recovery_choice`. Route registered in `server.py` next to
`/v1/chat/answer`.

**New audit action types** (extend `audit_log`):
- `pii_local_swap` — user picked "Use local model" upfront.
- `pii_anonymised` — anonymisation succeeded.
- `pii_anonymise_failed` — anonymisation raised.
- `pii_anonymise_failed_local_swap` — user picked local model after failure.
- `pii_anonymise_failed_cancel` — user cancelled (or timed out).
- `pii_deanonymise_text` — assistant reply de-anonymised.
- (step 5 will add: `pii_deanonymise_file`)

### Step 4 — web UI

**`web/js/panels.js`** — `gdprActionModal` extended:
- New green 'Anonymise & continue' button (default focus) — returns
  verdict `'anonymise'`. Wired before existing buttons in the actions grid.
- Existing buttons relabelled: Cancel / Use local model / Continue anyway.
- Returns `'anonymise'` / `'local'` / `'send'` / `'cancel'`.
- New `pii-btn-anon` CSS class (green, emerald-700/800).
- **New `gdprRecoveryModal(detail, chat)`** — recovery prompt. Two buttons:
  `Cancel turn` (returns `'cancel'`), `Use local model` (returns
  `'local_model'`). Click-outside intentionally disabled. Focus defaults
  to local-model.

**`web/js/chat.js`** — `sendMessage`:
- Maps verdicts to `gdprAction`: `'anonymise'` / `'local_model'` /
  `'continue'` / `''`. Forwards via new `streamChat` positional arg.
- `buildStreamCallbacks` adds three handlers:
  - `synthetic_tool_use`: pushes `{role:'tool_call', synthetic:true, kind,
    name:kind, args, tool_use_id, _ts}` onto `chat.messages`.
  - `synthetic_tool_result`: pushes `{role:'tool_result', synthetic:true,
    kind, name:kind, result, status, duration_ms, tool_use_id, _ts}`.
  - `gdpr_recovery_required`: `gdprRecoveryModal(d, chat).then(choice =>
    API.chatGdprRecovery(d.session_id, choice))`.
- New `renderSyntheticGdprCall(msg, idx)`: shield-iconed row.
  - Look forward for matching done row (by `tool_use_id` or `kind`).
  - Status icon: spinner / ✓ / ×.
  - Title from `kind`: `Anonymised` / `De-anonymised reply` /
    `De-anonymised file`.
  - Summary string built from `result` (`{findings} finding(s) · {cats}`
    or `{restored} token(s) restored` or error message).
  - Body: key/value table (Sources / Findings / Restored / Categories /
    Tokens minted / Mapping ID / Error). **Never includes raw values**.
- `renderToolCall` branches early when `msg.synthetic` → calls
  `renderSyntheticGdprCall`. `renderToolResult` returns `''` for synthetic
  results (rendered inside dispatch).

**`web/js/api.js`** — `streamChat(sessionId, message, callbacks, model,
files, images, gdprAction)` accepts the new arg, forwards as
`body.gdpr_action` when truthy. New `chatGdprRecovery(sessionId, action)`.

**`web/js/sessions.js`** — `openSession` reload path: when iterating
server messages, before falling into the `else` branch that pushes the row
verbatim, check `msg.metadata.synthetic === true`. If so:
- `msg.role === 'tool_use'` → push `{role:'tool_call', synthetic:true,
  kind, name, args, tool_use_id}` (parsed from `msg.content` JSON).
- `msg.role === 'tool_result'` → push `{role:'tool_result', synthetic:true,
  kind, name, result, status, duration_ms, tool_use_id}`.
- `continue` (skip the verbatim push).

This means reloaded synthetic rows go through the SAME `renderSyntheticGdprCall`
path as live ones.

### Test coverage

41 tests, all green. Run with:

```bash
python3 -m unittest tests.test_pseudonymizer tests.test_pseudonymizer_persistence tests.test_chat_worker_helpers
```

- `tests/test_pseudonymizer.py` (16) — roundtrip / shape-fakes / opaque
  tokens / tolerant reverse / stability.
- `tests/test_pseudonymizer_persistence.py` (14) — key bootstrap / AES-GCM
  AAD / tamper detection / save-load-deanonymise / cascade / orphan purge.
  Uses `_KEY_PATH_OVERRIDE` + `server_lib.db.CHAT_DB` monkeypatch to
  sandbox the real chats.db.
- `tests/test_chat_worker_helpers.py` (11) — `StreamingDeanonymizer`
  (partial token holdback) / recovery wait pattern / synthetic tool events.

JS files all parse via `node -e "new Function(fs.readFileSync(...))"`.

---

## What's left (steps 5–6)

### Step 5 — file walkers

Goal: pseudonymise attachments BEFORE the LLM sees them, de-anonymise
LLM-generated files AFTER write.

**Files to touch**:

- `pseudonymizer.py` — add `pseudonymize_file(path, *, session_id, turn_id,
  mapping_id) → new_path` and `deanonymize_file(path, *, mapping_id) →
  new_path`. Dispatch on extension. Original stays on disk; the
  pseudonymised twin lands at `<name>.anonymised.<ext>` (or in-place — TBD).
- Per-format walkers (probably a new `engine/file_pseudonymize.py`):
  - **.docx** — `python-docx`. Walk `Document.paragraphs[*].runs[*]`. A
    span split across runs needs a run-merge pre-pass (steal from Translation
    Phase B if it has one; otherwise look at the `python-docx-replace` pkg
    for the pattern).
  - **.pptx** — `python-pptx`. Walk shapes → text_frame → paragraphs →
    runs. Cover tables (`shape.table`), grouped shapes
    (`shape.shapes` recursion), charts (`chart.plots[*].categories`).
    GitHub issue scanny/python-pptx#335 documents a formatting-damage
    edge case when runs split mid-word.
  - **.xlsx** — `openpyxl`. Iterate `ws.iter_rows()` → `cell.value`. **Skip
    formulas** (values starting `=`) unless config says otherwise — replacing
    inside formulas usually breaks them. For shape-preserving categories
    (iban/credit_card/phone) in cells, the existing fake generators
    preserve formatting so cell parsers stay happy.
  - **.pdf** — Phase B already has a PDF→docx fallback for translation.
    Reuse it: convert PDF to docx, pseudonymise docx, send docx to LLM,
    de-anonymise the reply. **Do not** attempt in-place PDF text
    replacement — pymupdf docs say layout won't reflow, and pymupdf is
    AGPL (the project distributes Electron desktop builds, so AGPL is
    viral). Document the convert-to-docx fallback in the code.
  - **plain text / .md / .csv** — simple string substitution via the same
    `_pii_scan_text` + `pseudonymize_text` flow.

- `handlers/chat.py`:
  - **Pre-send file pseudonymisation**: in the worker's anonymise block,
    after the text pseudonymise step, iterate `disk_files` (the files
    routed to `/tmp/brain-attachments/<sid>/`). For each, call
    `pseudonymize_file(...)` and update both the disk file AND the path
    embedded in the user message's attachment notice (currently built at
    line ~1146 in the `notice` string — that path is what `read_document`
    will receive). On failure: same recovery flow (emit dispatch+error
    synthetic row, `gdpr_recovery_required`, etc.) — **never send the
    original file to the cloud after a failed walk**.
  - **Post-LLM file de-anonymisation**: hook into the existing
    `_after_file_write` callback (or wrap `write_file` tool dispatch).
    When a file lands in `agents/<id>/artifacts/<session_folder>/` AND
    `session._gdpr_mapping_id` is set, call `deanonymize_file(path,
    mapping_id=...)` in place. Emit per-file `deanonymise_file` synthetic
    tool-call pair. Audit `pii_deanonymise_file`.
  - Update the `args` in the initial `anonymise` dispatch synthetic event
    to include each attachment as a separate source:
    `args={"sources": ["chat_text", "attachment:report.pdf",
    "attachment:data.xlsx"]}`.

- `web/js/chat.js` — `renderSyntheticGdprCall` already shows the source
  list; no UI change needed. The `kind: "deanonymise_file"` branch is
  already wired (handles `result.file` + `result.restored`).

**Config knob to add** in `gdpr_scanner` section of `config.json`:
```json
"transparent_anonymisation": {
  "enabled": true,
  "persist_maps": true,
  "shape_preserving_categories": ["iban", "credit_card", "phone"],
  "token_format": "hybrid"
}
```
(The pseudonymizer already respects `SHAPE_PRESERVING` as a constant; if
you want this admin-tunable, wire it through `_get_gdpr_scanner_config`.)

**Translation Phase B walkers to crib from**: grep for
`tool_translate_document` in brain.py and the surrounding helpers in
`engine/doc_convert.py`. Same in-place-rewrite pattern.

### Step 6 — finishing touches

1. **System-prompt clamp** — append a short block to `_build_system_prompt`
   when `session._gdpr_mapping_id` is set (read via thread-local). Something
   like:

   > Some values in the user's message have been pseudonymised for privacy.
   > Tokens of the form `<KIND_N_HEX>` (e.g. `<EMAIL_1_a8k2>`) are
   > placeholders. **Copy each token verbatim into your reply** — do not
   > translate, reformat, or describe them. The system will restore the
   > original values before showing your reply to the user.

   The 109-test benchmark in the research report showed this clamp pushes
   roundtrip preservation from ~93% to >99% on frontier models.

   **KV-prefix invariant**: this is per-turn, so it doesn't break warmup.
   But make sure the cache key includes whether anonymisation is active
   (otherwise prompt-cache hits on the wrong system prompt).

2. **Sticky session preference** — mirror `save_to_memory` semantics:
   - Add `sessions.gdpr_action_pref TEXT` column (migration in
     `ChatDB.init`).
   - `Session.gdpr_action_pref` field (`server.py:Session.__init__`).
   - The web modal: when user picks a non-cancel action, offer a "Don't ask
     again for this chat" checkbox (already exists for `'send'` — extend
     to all non-cancel verdicts).
   - On send: if `chat.gdprActionPref` is set, skip the modal and forward
     the stored choice as `body.gdpr_action` directly.
   - Settings panel: per-session reset button.

3. **Composer indicator** — small shield-with-checkmark icon next to the
   composer when `chat.messages[-1]` (or current turn) has an active
   anonymise synthetic row. Similar to the translate progress indicator
   pattern. Lives in `web/js/chat.js`.

4. **Audit-view "show what was sent"** (optional, lower priority) —
   admin-only UI surfaced from session inspector, decrypting the
   `pseudonym_maps` row and rendering before/after side by side. Useful
   for compliance evidence. NOT user-facing.

---

## Gotchas + non-obvious notes

1. **Don't move the anonymise work back to `_handle_chat`** (pre-worker).
   We tried that first; the recovery SSE event can't reach the client
   before `send_response(200)` is sent, so `fetch()` blocks forever and
   the modal never opens. Deadlock. The current placement INSIDE the
   worker is load-bearing.

2. **User message persistence is conditional**. In the non-anonymise path,
   `session.add_message("user", ...)` runs in `_handle_chat` (line ~1330).
   In the anonymise path, the guard `if
   session._gdpr_pending_action != "anonymise":` skips it, and the worker
   adds it after pseudonymising. Don't accidentally remove the guard or
   the DB will briefly hold raw PII before the worker fixes it (and the
   in-memory `session.messages` would feed the original text to the LLM).

3. **`_msg_count_before` rebind**. The worker resnapshots
   `_msg_count_before = len(session.messages)` AFTER adding the
   user-message in the anonymise branch. Without this, `_rollback_messages`
   on cancel/error would strip the user message (because it'd think the
   user msg was an intermediate-tool-loop addition). Keep the rebind.

4. **`StreamingDeanonymizer` boundary**. A literal `<` in prose (e.g. `if
   x < 5`) is held back until either the matching `>` arrives or `flush()`
   runs at turn end. This is by design — `<` is ambiguous mid-stream. The
   tradeoff: a small chunk of text near a literal `<` may render later
   than it could. Acceptable.

5. **`session._gdpr_streamer` lifecycle**. Created in the worker's anonymise
   block (success path), cleared in worker `finally`. The `_streaming` ==
   false case (chat idle on reload) doesn't need the streamer — reloaded
   text comes from the persisted (already-deanonymised) assistant
   message row.

6. **Encrypted map persistence outlives the in-memory mapping**. On worker
   `finally`, `pseudonymizer.close_mapping(id)` drops the in-memory entry
   but the SQLite row stays (per `persist_maps=true`). On reload, you'd
   need `load_mapping(id)` + `restore_mapping_to_registry(m)` to use the
   mapping again — not needed for the current flow (the assistant message
   is already de-anonymised when persisted), but step 5 audit view will
   need it.

7. **No `_KEY_PATH_OVERRIDE` in prod** — only used by tests. Production
   uses `agents/main/pseudonym.key` via `_default_key_path()`.

8. **AAD binding**. `encrypt_mapping` binds `mapping_id` as AES-GCM AAD.
   If anyone in the future tries to "migrate" mapping rows (rename
   `mapping_id`, copy ciphertext to a different row), the new row won't
   decrypt. Don't do that.

9. **`ChatDB` bare-name resolution in tests**. The chat helpers reference
   `ChatDB` as a bare global (injected at server.py boot via
   `_inject_server_globals()`). In tests that import `handlers.chat`
   directly, that injection hasn't run — `test_chat_worker_helpers.py`
   stubs `ChatDB` on the module manually. Same trick applies if step 5
   tests need it.

10. **Don't add the recovery modal to `/v1/chat/answer`'s path**. That
    endpoint is for `ask_user` tool-call answers (an unrelated blocking
    primitive). The recovery flow uses its own endpoint
    `/v1/chat/gdpr-recovery` to keep concerns separated and the
    "no `send_to_cloud_anyway` action" invariant enforceable on the
    server.

---

## Files changed in v8.41.0

```
M handlers/chat.py        # worker integration + helpers + recovery endpoint
M server.py               # /v1/chat/gdpr-recovery route
M server_lib/db.py        # pseudonym_maps table + CRUD + delete cascade
M web/js/api.js           # streamChat gdprAction arg + chatGdprRecovery
M web/js/chat.js          # SSE handlers + renderSyntheticGdprCall + verdict mapping
M web/js/panels.js        # gdprActionModal extended + gdprRecoveryModal
M web/js/sessions.js      # reload-path synthetic row remap
?? pseudonymizer.py       # NEW — text-only pseudonymisation + encryption
?? tests/test_chat_worker_helpers.py
?? tests/test_pseudonymizer.py
?? tests/test_pseudonymizer_persistence.py
M brain.py                # VERSION + CHANGELOG
```

---

## Picking it back up

1. Read this file end-to-end. Then read the relevant sections of
   `CLAUDE.md` (the GDPR scanner section + resumable streaming section).
2. Skim `pseudonymizer.py` — it's the contract everything else builds on.
3. Re-run the test suite to confirm green:
   ```bash
   python3 -m unittest tests.test_pseudonymizer tests.test_pseudonymizer_persistence tests.test_chat_worker_helpers
   ```
4. For step 5, start with `pseudonymize_file()` + `deanonymize_file()` in
   `pseudonymizer.py` (text only path is the model — same scan-then-replace
   shape, dispatched on extension). Add `tests/test_pseudonymizer_files.py`
   alongside the existing suites. Then wire into `handlers/chat.py`'s
   worker anonymise block. Then the `_after_file_write` hook for output
   files.
5. For step 6, the system-prompt clamp is the highest-value piece (small,
   measurable quality lift). Sticky preference + composer indicator are
   polish.

Good luck.
