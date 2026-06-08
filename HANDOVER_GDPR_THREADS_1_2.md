# Handover — GDPR roadmap threads 1 & 2 (next session)

Two remaining threads from the user's GDPR-stability roadmap. Threads 0 (skip
policy) and 3-precision (per-rule min_occurrences + context gates) are DONE and
on `main` (commit `45e6083`, v9.93.0). See `[[project_gdpr_skip_policy_and_precision]]`.

**Current state:** GDPR scanner is **DISABLED** (`gdpr_scanner.enabled=false`) on
the dev box — so both threads below are testable only by temporarily enabling it
(`POST /v1/services/server {gdpr_scanner:{enabled:true}}`, restore after). Server
9.93.0, KG intact 2696.

The user's framing (verbatim intent): *"gdpr detection only when really a gdpr
problem (min false positives) [DONE in 9.93]; fallback works in non-interactive
mode (mining) [THREAD 1]; in interactive the user can choose anonymise/
deanonymise and watch the success, otherwise try fallback; the last decision is
remembered and used in subsequent turns; we need a way to give feedback if it
worked or the user decides to try again or use another mode [THREAD 2]."

---

## THREAD 1 — Non-interactive local-fallback reliability (mining)

**Goal:** when a background/mining call hits PII on a cloud model and policy is
`swap_to_local`, it must RELIABLY extract on a local model (full text, stays on
host) — not silently fall through to the cloud, and not skip.

**The problem today (the gap):** `_try_swap_to_local()` in `brain.py:9519`
already swaps to `default_local_fallback_model` — BUT if no usable local fallback
is configured/enabled, it does a **warn-passthrough to the ORIGINAL cloud model**
(audit `pii_warn_passthrough`, `brain.py:9568`), i.e. the PII still goes to the
cloud. So `swap_to_local` only "works" if a local model is correctly configured;
otherwise it silently leaks. For KG specifically, the whole-doc decision in
`engine/kg_extract.py _process_source` only branches on skip/block — a
`swap_to_local` outcome currently means the per-chunk path re-runs the policy and
may passthrough.

**What to build:**
1. **Make swap reliable + observable.** When `swap_to_local` can't find a usable
   local model, it should NOT passthrough-to-cloud for mining — it should either
   skip (treat like `skip`) or fail loud, configurable. Decide with the user:
   passthrough is the current behaviour and is arguably wrong for a
   data-protection policy.
2. **KG should actually extract locally on swap_to_local.** Thread the swapped
   model through so the per-chunk extraction uses the local model (it already
   gets `resolved_model` back from `gdpr_pick_model_for_background` in
   `extract_triples_from_drawer` — verify it's honoured, not discarded).
3. **Surface the swap per-document.** The `/folder-tree` per-file KG badge
   (added 9.91) could show "extracted locally (PII)" distinct from kg/skipped.
4. **Doctor check:** if `background_pii_action ∈ {swap_to_local, anonymise}` but
   `default_local_fallback_model` is unset/disabled, flag it (the swap silently
   can't happen). `engine/doctor.py` is the place.

**Key seams:**
- `brain.py:9519` `_try_swap_to_local()` — the swap + passthrough fallback.
- `brain.py:9479` `gdpr_pick_model_for_background` — returns `(model, texts, deanon)`.
- `engine/kg_extract.py extract_triples_from_drawer` (~line 600) — uses
  `resolved_model` from the picker.
- `engine/kg_extract.py _process_source` — whole-doc decision (skip/block now;
  add swap-local handling).
- Config: `gdpr_scanner.default_local_fallback_model`.

**Acceptance:** with PII + `swap_to_local` + a configured local model, a mined
doc gets KG-extracted on the LOCAL model (verify via the executing-model in the
extraction log / cost row). With no local model configured, the behaviour is the
user's chosen explicit one (skip or loud), NOT silent cloud passthrough.

---

## THREAD 2 — Interactive: choose + watch + remember + feedback loop

**Goal:** in chat, the user picks anonymise / local-model / continue, SEES whether
it worked, the choice is REMEMBERED for later turns, and there's a way to give
feedback / retry / switch mode.

**What already exists (build on it, don't rebuild):**
- **Per-send modal → `body.gdpr_action`** (`handlers/chat.py:3584`): anonymise /
  local_model / continue. Honoured before the user message lands.
- **Sticky pref** (`handlers/chat.py:3600-3611`): once a session chose
  anonymise, later turns auto-anonymise until cleared via the composer shield
  button. `session.gdpr_action_pref` + `_gdpr_skip_auto`.
- **Streaming de-anonymiser** (`session._gdpr_streamer`, `StreamingDeanonymizer`)
  restores tokens in the reply live (`handlers/chat.py:1135, 2508`).
- **Anonymise-FAILURE recovery modal** — a whole state machine already exists:
  `handlers/gdpr_recovery.py` (`_gdpr_recovery_register/pending/clear`,
  `deliver_gdpr_recovery_choice`) + `POST /v1/chat/gdpr-recovery`. This is the
  "it didn't work → ask the user what to do" seam — REUSE its pattern.
- **Audit signals already emitted** (the "did it work?" data is already logged):
  `pii_detected`, `pii_anonymised`, `pii_anonymise_failed`, `pii_auto_fallback`,
  `pii_warn_passthrough`, `pii_skipped`, `pii_blocked` (`brain.py:9500-9693`).

**What's MISSING (the actual thread-2 work):**
1. **"Watch the success" — surface the outcome in-chat.** Today anonymisation is
   mostly invisible to the user mid-turn. Add a per-turn indicator: "X PII tokens
   anonymised → reply de-anonymised" / "anonymise failed → fell back to local" /
   "sent to local model". Data is in the audit rows above + the mapping; needs a
   per-turn metadata field (like `metadata.web_sources` / `metadata.gdpr`) +
   rendering in `renderAssistantMessage` (web/js/chat_render.js).
2. **"Remembered choice visible + changeable."** The sticky pref exists but isn't
   shown. Surface the current session GDPR mode (anonymise/local/continue) as a
   visible composer chip with a way to change it (the shield button clears, but
   there's no "switch to local" toggle).
3. **"Retry / switch mode" feedback loop.** When a mode didn't satisfy the user
   (e.g. anonymise gutted the answer), let them re-run the SAME turn with a
   different mode. The recovery-modal pattern (`gdpr_recovery.py`) handles the
   failure case; extend it to a user-initiated "redo this turn as <mode>".

**Key seams:**
- `handlers/chat.py:3574-3621` — gdpr_action handling + sticky pref.
- `handlers/gdpr_recovery.py` + `POST /v1/chat/gdpr-recovery` — the choice-
  delivery pattern to reuse.
- `session.gdpr_action_pref`, `_gdpr_skip_auto`, `_gdpr_streamer`,
  `_gdpr_mapping_id` (set in `handlers/chat.py` worker).
- Client: the per-send modal lives in `web/js/panels.js` (`classificationActionModal`
  pattern / the PII modal in `sendMessage`); per-turn render in chat_render.js.
- Audit: `server_lib` audit_log + the `pii_*` action types.

**Acceptance:** user sees per-turn whether anonymise/local/continue happened +
succeeded; the session's mode is visible + switchable; user can redo a turn in a
different mode; the choice persists across turns (already does — make it visible).

---

## Shared gotchas
- Scanner is DISABLED now — enable temporarily to test, restore after.
- `_pii_rules` ORDER is a correctness invariant — never reorder.
- **py_compile does NOT catch module-level NameError** — runtime-import modules
  after edits (a module-level regex using a function-local import crash-looped the
  server in 9.93; see `[[project_gdpr_skip_policy_and_precision]]`).
- NEVER SIGKILL brain-agent — graceful `launchctl kill SIGTERM` only
  (`[[feedback_never_sigkill_brain]]`).
- Compile-check brain.py after CHANGELOG edits; confirm `/v1/status` ==
  `brain.VERSION` after restart (`[[feedback_compile_check_brain_py]]`).
- Don't restart while KG/mining is extracting (check the kg_extraction_log tail +
  daemon idle — project-sync interval is 6h).
- Commit convention: directly to `main` (`[[feedback_commit_to_main]]`); the repo
  carries pre-existing uncommitted changes that aren't yours — confirm scope.
