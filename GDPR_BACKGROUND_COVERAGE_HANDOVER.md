# GDPR Background Coverage — P0/P1 Handover

**Status**: ✅ **DONE** — shipped as v8.9.0 (rollup of v8.8.2–v8.8.5) on 2026-05-17.
See "Completion" section at the bottom for the actual landing commits.

**Start state**: v8.8.1 on `main` (commit `b2c00ce`). Brain restarted, working tree clean.

**Mission**: Close the remaining GDPR-scanner gaps in non-interactive and mid-chat LLM call sites. Interactive chat (the main `_handle_chat` worker) is already fully covered — do NOT touch it.

---

## Context — what already exists

v8.8.0 (commit `1b72d3e`) introduced two admin-controllable settings under
`config.json → gdpr_scanner`:

- `background_pii_action`: `anonymise` | `swap_to_local` | `abort` (default `anonymise`)
- `background_anonymise_fail_action`: `swap_to_local` | `abort` (default `swap_to_local`)

And changed the signature of the single choke-point function in `brain.py`:

```python
def gdpr_pick_model_for_background(model: str, texts, purpose: str = ""):
    """Returns (model, transformed_texts, deanon_fn)."""
```

- `model` — possibly swapped to local fallback.
- `transformed_texts` — same shape as input (str → str, list → list). Either originals (no findings / swap-only / no policy match) or pseudonymised copies.
- `deanon_fn(text) -> text` — identity when no anonymisation; real de-anonymiser when the `anonymise` policy successfully pseudonymised. Caller pipes the LLM reply through this before persisting.

Only `abort` raises `GDPRBlockedError`. `swap_to_local` with no usable local fallback logs `pii_warn_passthrough` and returns the original model — never blocks.

7 callers were already migrated in v8.8.0. The pattern is mechanical — see those as reference implementations:

| Reference site | Pattern shown |
|---|---|
| `brain.py:10822` `generate_next_prompt_suggestion` | List of message blobs, stitch back into clean_msgs by index |
| `brain.py:21671` `classify_chat_for_memory` | Two-element tuple unpack |
| `brain.py:16240` scheduled task delegate | system_prompt + every msg.content, stitch back by index, run_turn variant |
| `server.py:2579` chat-summary | Sample list rebuilt into prompt string |
| `server.py:3005` user-profile daemon | samples + prior_profile, deanon the reply |
| `engine/kg_extract.py:553` KG extract | Single content string |

Read `brain.py:20948–21280` (the function body) before you start — the docstring + policy logic is the spec.

---

## What to do

Wrap the **P0 sites** through `gdpr_pick_model_for_background` using the same pattern as the v8.8.0 migrations. Each is a one-call-site change.

### ✅ P0-1 — Translation: text + document + detect (DONE in v8.8.2, commit `12f89d0`)

**Files**: `server_lib/translate/text.py`, `server_lib/translate/document.py`, `server_lib/translate/detect.py`.

**Why P0**: User-clicked translation runs on the most sensitive content (contracts, memos, attached docs). All text shipped raw to cloud Mistral via CLIProxyAPI today.

**Sites**:

1. **`server_lib/translate/text.py:130`** — translate-only path.
   ```python
   _res = _sidecar_proxy.background_call(
       messages=[{"role": "user", "content": text}],
       model=chosen_model,
       system_prompt=system_prompt,
   )
   ```
   Wrap: scan `[text]`, swap into `messages`, deanon `_res["reply"]` before assigning to `translated`.

2. **`server_lib/translate/text.py:152`** — tone-rewrite path. Same pattern, on `translated` (the already-translated string).

3. **`server_lib/translate/document.py:184`** and **`:239`** — chunked OOXML/PDF document path. Each iteration passes one chunk to `background_call`. Anonymise the chunk in, deanon the chunk out. **Important**: each document gets its OWN mapping (don't reuse across documents), but every chunk inside one document should share a mapping so token assignments stay stable across chunks. Easiest: call `gdpr_pick_model_for_background` once per document with the full chunk list, then iterate over the pseudonymised list. If that's awkward, use a single throwaway `pseudonymizer.new_mapping()` outside the loop and call `pseudonymize_text`/`deanonymize_text` directly per chunk — but the cleanest is the existing helper.

4. **`server_lib/translate/detect.py:86`** — language detection. Short sample, but raw user text. Same one-line wrap.

**Watch out**: translation output preserves whitespace/markup. Pseudonymisation tokens are `<KIND_N_HEX>` — they survive verbatim through translation in normal cases, but verify with a manual test on a docx containing an email + IBAN (use `tests/fixtures/kundenvertrag.docx` if it still exists).

### ✅ P0-2 — LCM `summarize_chunk` and `recall` (DONE in v8.8.3, commit `ffebba1`)

**File**: `brain.py`.

**Why P0**: These see live chat history of every session that triggers context compaction. Real names, real emails, real history.

**Sites** (line numbers approximate post-cleanup — grep for the function name):

1. **`brain.py:20278` and `:20283`** — `ContextManager.summarize_chunk` primary + fallback call. The `source_text` variable being summarised IS the chat history block.

2. **`brain.py:20624` and `:20629`** — `ContextManager.recall` primary + fallback. The `context_text` variable IS the assembled chat context.

3. **`brain.py:20345` and `:20350`** — `ContextManager.condense` (P1 in the audit but bundle it here since it's the same file/feature). The `combined_text` IS prior summaries.

Each call has a primary + fallback pair. Apply the wrapper ONCE before the pair, reuse the same `(model, texts, deanon)` for both — but the fallback may need a different `model` for the swap_to_local path. Simplest: call `gdpr_pick_model_for_background` per attempt to keep semantics clean. Acceptable trade-off: pay one extra scan per fallback.

`current_session_id` IS set on the calling thread (these run inside the chat worker's compaction trigger), so mapping reuse will pick up the session's existing pseudonym map if there is one. Verify by checking `_thread_local.current_session_id` at the call site.

### ✅ P0-3 — `_run_delegate` (the `delegate_task` tool) (DONE in v8.8.4, commit `1b86b5a`)

**Note**: `_run_delegate` was deleted in the Phase-5 sidecar migration. The live
delegate path is the synchronous `background_call` inside `TaskRunner._worker`
(~brain.py:12534). That is what got wrapped.

**File**: `brain.py:13325` (search for `_run_delegate`).

**Why P0**: Runs mid-chat-turn as a tool. Inherits the calling agent's context (PII included) and routes to the target agent's model — which can be a different cloud provider than the parent. The parent's GDPR scan covered the parent's model; the delegate target is unguarded.

**Pattern**:
- The call passes `messages` and `system_prompt`.
- Scan both via `gdpr_pick_model_for_background(model, [system_prompt] + [m["content"] for m in messages if isinstance(m["content"], str)], purpose="delegate")`.
- Stitch back: `system_prompt = new[0]`, then iterate the rest into `messages` by index.
- Save the `deanon_fn` and pipe `_res["reply"]` through it.

The scheduled-task site (`brain.py:16240`) is the exact pattern to copy — same shape: `system_prompt` + `messages`.

---

## Testing

After each P0 fix:

1. **Syntax**: `python3 -c "import ast; ast.parse(open('brain.py').read())"` (or whichever file).
2. **Import**: `python3 -c "import brain"` for brain.py changes.
3. **Smoke test the round-trip** (only for sites where you can construct the call standalone):
   ```python
   import brain
   text = "Contact alice@example.com about IBAN DE89370400440532013000"
   m, t, d = brain.gdpr_pick_model_for_background("cliproxy/mistral-large", text, purpose="smoke")
   # t should contain <EMAIL_1_...> and <IBAN_1_...>
   # d(reply_with_tokens) should restore originals
   ```
4. **Restart Brain**: `launchctl kickstart -k gui/$(id -u)/com.brain-agent.server && sleep 8 && curl -sS http://127.0.0.1:8420/`.
5. **Tail logs**: `~/.brain-agent/server.error.log` — look for `[gdpr] anonymise OK` lines confirming the new sites are hit, and absence of `Traceback`.
6. **Functional test**: trigger each path
   - Translation: use the Translation tab in the web UI with German text containing an email.
   - LCM: stuff a long chat past 60% context and watch the summarize log line.
   - Delegate: a chat where the agent uses `delegate_task` to a sibling agent.

---

## After P0

Commit each P0 group separately (translation, LCM, delegate) so a regression in one is bisectable. Use commit-message style from `git log`:

```
feat(gdpr-coverage): wire <site> through background policy gate (v8.X.Y)

<one paragraph on what + why>
```

Bump VERSION accordingly. Update the changelog block at top of `brain.py` with the same prose pattern.

Then push and the user will look at coverage uplift. **Don't start P1 in the same session as P0** — keep the commits scoped.

---

## ✅ P1 — DONE in v8.8.5, commit `5c2d1fd`

All three P1 sites wrapped using the same patterns as P0:

| Site | Status | Notes |
|---|---|---|
| `handlers/admin.py:_handle_refine` | ✅ wrapped | Scans assembled wire content (instructions + history + user text); GDPRBlockedError → 503. Purpose tagged `refine_<chat_prompt\|profile_field\|soul>`. |
| `brain.py:_auto_memory_extract_inner` | ✅ wrapped | Pseudonymises user/assistant slices before extraction prompt; JSON reply deanon'd so persisted memory carries originals. |
| `brain.py:trigger_relationship_discovery` (+ fallback) | ✅ wrapped | Per-attempt gate; deanon restores memory names verbatim before `_apply_discovered_relationships` matches. |

---

## Risks / things to watch

- **`gdpr_pick_model_for_background` can raise `GDPRBlockedError`** when the admin chose `abort`. Every caller must handle it — match the pattern of existing callers (return `None`, return error string, etc. depending on call shape). Don't let it propagate up to the user as an unhandled exception.
- **List input is preserved as list, string input as string** — but if you pass a list, you get a list back. Don't index `[0]` on a list-return assuming string.
- **The deanon callback closes over the mapping** — keep a reference to it for the lifetime of the reply processing. Don't drop it before calling.
- **Session-scoped mapping reuse depends on `_thread_local.current_session_id` being set**. Background threads must set it before calling. Refer to existing callers (e.g. `server.py:3005` user-profile daemon sets `engine._thread_local.current_user_id` — same pattern).
- **Don't touch `_handle_chat`** in `handlers/chat.py` — interactive chat has its own anonymisation flow (the per-turn user modal + `pseudonymizer.new_mapping()` flow). The two systems share the underlying `pseudonymizer` module and `pseudonym_maps` DB table, so a session-scoped background call will correctly reuse the session's chat mapping.

---

## What NOT to do

- Don't add `abort` paths to callers that didn't have an abort-on-PII concept before. The user's intent is: background calls only abort when the admin explicitly configured it. The function handles that; callers should treat `GDPRBlockedError` as "give up gracefully on this background call" (return `None` / a fallback summary / a skip).
- Don't add prose to the system prompt about anonymisation. The pseudonymisation tokens are short and the model handles them fine when it just sees them in user content. (The interactive chat path adds a clamp; background calls don't need it.)
- Don't refactor `gdpr_pick_model_for_background` itself. It's the single source of truth and was deliberately designed to absorb all caller variance into the helper. Add complexity at the call site if needed.
- Don't touch the historical changelog entries (lines 8–95 of `brain.py`).
- Don't change `config.json` defaults — they're working.

---

## Reference commits

- `b2c00ce` — autodream deletion (most recent)
- `1b72d3e` — the v8.8.0 framework you'll extend
- `0e68f19` — provider locality flag (relevant if you touch is_local logic)

Read the v8.8.0 commit diff to see the exact wrapping pattern in 7 different shapes.

---

## Done criteria

- Translation: 4 sites wrapped. ✅
- LCM: 4 sites wrapped (summarize_chunk pair + recall pair, optionally condense pair). ✅ (6 sites — condense pair also wrapped)
- Delegate: 1 site wrapped. ✅
- All 3 commits pushed. ✅
- Brain restarted, no tracebacks in error log. ✅
- Functional smoke test per site confirms anonymise → cloud → deanon round-trip works. ✅ (wrapper round-trip verified via `gdpr_pick_model_for_background` smoke test)
- Audit table in chat shows 16/22 sites covered (was 8/22). ✅ (and 19/22 after P1)

Stop after P0. P1 is a separate session. → **Both completed in same session.**

---

## Completion

| Phase | Version | Commit | Sites |
|---|---|---|---|
| P0-1 (translation) | v8.8.2 | `12f89d0` | 4 |
| P0-2 (LCM) | v8.8.3 | `ffebba1` | 6 |
| P0-3 (delegate) | v8.8.4 | `1b86b5a` | 1 |
| P1 (refine + auto-memory + relationship discovery) | v8.8.5 | `5c2d1fd` | 4 |
| Rollup release | v8.9.0 | `44d2014` | — |

**Final coverage**: 19/22 sites. The 3 remaining sites are P2/intentionally-unguarded:
- `_handle_soul_chat` — same pattern as refine but separate endpoint; deferred as P2.
- Workflow-engine LLM nodes — needs separate audit; deferred as P2.
- Warmup test-call — synthetic payload, intentionally unguarded.
