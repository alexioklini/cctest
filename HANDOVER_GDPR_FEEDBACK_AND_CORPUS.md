# Handover — GDPR feedback modal + rule-set single-source + KG-corpus analysis

Session 2026-06-08. Supersedes nothing; complements `HANDOVER_GDPR_THREADS_1_2.md`
(thread 2 = the feedback work below; thread 1 = mining swap-reliability, STILL OPEN).

Committed + pushed: **commit `b352e49`, v9.95.0** on `main`. Server live at 9.95.0.
EXCEPTION: `config.json` is gitignored — the config reconciliation + email tuning
below are **live-box only** (documented here, not in git).

---

## 1. Interactive GDPR feedback modal (v9.94.0, folded into b352e49)

Opt-in, post-turn modal — NOT a passive badge (an earlier badge impl was built then
removed at user request). Flow:

- **Pre-send modal** (`gdprActionModal`, `web/js/panels_gdpr.js`) gained a checkbox
  **"Frag mich nachher wies gelaufen ist"** (off by default) → resolves
  `{verdict, askAfter}`. `askAfter` sets sticky per-session `gdpr_feedback_ask`.
- **Persistence** mirrors `allow_further_web` end-to-end: `sessions.gdpr_feedback_ask`
  INTEGER col (`server_lib/db.py` migration + `update_session_gdpr_feedback_ask`),
  `Session.gdpr_feedback_ask` (server.py load), manage action `gdpr_feedback_ask`
  (`handlers/sessions_handler.py`), GET /messages echo (both branches),
  `API.updateGdprFeedbackAsk`, client load/reset in `sessions.js`.
- **Post-turn** (`maybeRunGdprFeedback` in `chat_send.js`, fired from the `done`
  SSE handler): when `chat.gdprFeedbackAsk` AND `d.gdpr.active` → opens
  **`gdprFeedbackModal`** ("Hat es gepasst?" + per-mode summary + retry buttons for
  the two methods NOT just used + "Passt so" + checked "Frag mich weiter").
  Unchecking "Frag mich weiter" clears `gdpr_feedback_ask`.
- **`active` gate (IMPORTANT):** modal fires ONLY when `metadata.gdpr.active=true` =
  THIS turn anonymised the user's OWN input (typed PII or attachment submitted now)
  or swapped the model. FALSE when anonymise merely re-pseudonymised prior history
  (happens every turn of a sticky-anonymise session). Without this, the modal popped
  on history-only turns. local_model + anonymise_failed_local are always active=true.
- **Retry-clean (user directive):** `redoTurnAsGdprMode` (`chat_render.js`) DELETES
  the discarded turn server-side first (`delete_messages` by msg id) BEFORE re-sending
  — the server's `session.messages` is the wire source of truth; client-only slicing
  would leave the failed attempt on the server and pollute the retry.
- **metadata.gdpr** set in the chat worker (`handlers/chat.py`) at the three decision
  points (anonymise / anonymise_failed_local / local_model); signals =
  `RequestContext._gdpr_turn_outcome` + `session._gdpr_local_swap`; rides on `done`
  SSE + persisted metadata; wire-stripped (audit/display-only).

---

## 2. Rule set = config.json is the SINGLE source of truth (v9.95.0)

**The bug found:** the PII rule set lived in TWO layers that disagreed —
`engine/pii_ner.py` code defaults AND a partial `config.json` snapshot:
- config carried `categories.contact=warn` + `categories.network=warn`, frozen since
  the April v8.12.0 settings snapshot (verified UNCHANGED across all
  `config.json.bak-*` backups — it was NEVER a recent override, despite first
  appearances). Code default for both is `ignore`.
- the v9.93 `min_occurrences` table existed ONLY in code (absent from config).

**Resolution (per user: "config is the only truth, no double logic"):** `config.json
gdpr_scanner` now carries the FULL rule set equal to the code defaults —
`contact`/`network` → `ignore`, `business_id` added (`ignore`), all 27
`min_occurrences` materialised. Resolution order is unchanged
(`rule_override > config category > code default`); the point is config now never
falls back silently.

**GUI save made uniform** (`web/js/nav.js` `collectGdprFormConfig` +
`settings_general_tabs.js`): `min_occurrences` now writes EVERY rule (full snapshot,
blank/invalid → floor 1) and each input renders its effective value (never blank), so
editing a field can't silently revert to a hidden code default. `rule_overrides`
stays deltas BY DESIGN (empty = "use category", a real state, not a shadowed default).
Server validator (`handlers/admin_config.py`) accepts the full map (clamps ≥1, rejects
unknown rule_ids; client/server rule sets verified identical).

**All 10 gdpr_scanner config fields are GUI-reachable** (Settings → Allgemein → GDPR):
enabled/server_log/server_block (checkboxes), fallback/bg-pii-action/bg-fail-action
(dropdowns), per-category action, per-rule override, per-rule min_occurrences,
email_allowlist (textarea). Full round-trip verified.

---

## 3. ipv4 false-positive fix (v9.95.0)

A bare octet-valid dotted quad (`20.2.4.3`) is byte-identical to a document
clause/section number. The old `_ipv4_ok` only rejected a few prefixes, so policy
section numbers registered as IPs (34 false positives in the corpus). FIX
(`engine/pii_ner.py`): `ipv4` is now CONTEXT-GATED — fires only when an IP keyword
(IP/Adresse/Gateway/Subnet/Netmask/DNS/Host/Server/Router/Firewall) precedes the
address; `_ipv4_ok` extracts+validates the captured quad (still rejects
0./127./255./169.254.). Verified: section refs/numbered lists no longer match;
`Gateway 192.168.1.1` / `DNS 8.8.8.8` still match; `127.0.0.1` rejected. `_pii_rules`
ORDER unchanged (rule body edited in place — the correctness invariant holds).

---

## 4. KG-policy corpus analysis (read-only; tool kept)

Re-runnable scan tool: **`scripts/scan_kg_policies_gdpr.py`** (committed). Reads the
`.brain-extracted/*.md` companions (the verbatim text the KG miner saw — markitdown/OCR
already applied; a standalone `_do_extract` gives near-empty text on scanned image
PDFs and would falsely read clean). Strips brain frontmatter, scans full text with the
live config, reports worst action per doc + per-rule + per-value breakdown.

**Corpus = 58 docs** (`/Users/alexander/Documents/kg-real-policies/`, project
`f201b24ff6a2` "Regelwerk der Bank", wing `project__f201b24ff6a2`).

### Findings progression
- With the OLD stale config (contact/network=warn): 31 clean / 27 warn / 0 block.
  All warn from email(47) + phone(8) + ipv4(34, ALL false-positive section numbers).
  **`date` rule fired on ZERO docs** — the 9.93 context-gate + min_occurrences=10 fix
  killed the 2026-06 incident (215× date-as-personal that gutted policy KG).
- After config reconciliation (contact/network=ignore) + ipv4 gate: **58/58 clean**.
- After re-enabling external-email detection (decision below): **51 clean / 7 flagged**.

### The email decision (live config)
`contact=ignore` silenced ALL emails — including genuine EXTERNAL personal data
(vendor individuals). User chose: **`rule_overrides.email = "warn"`** (keeps the
contact category off, so phone stays off) **+ `email_allowlist = ["@wienerprivatbank.com"]`**
(drops own-domain noise). Result: 7 docs flag, on 29 distinct EXTERNAL emails only.
External role mailboxes (`info@`, `dsb@dsb.gv.at`, `office@cpb-software.com`) still
warn — the regex can't tell role from personal; warning on external org contacts is
the accepted default. Add more `@domain` allowlist entries to trim further.

### Background/mining semantics (established this session)
- In NON-interactive (mining/background): **warn == block** — `background_pii_action`
  fires on ANY finding (no human to defer to). In INTERACTIVE chat: warn lets the
  user "Trotzdem senden" to cloud; only block forces anonymise/local. Asymmetry is
  intentional (human present vs. absent). Lever to make a category not act in
  background = set it `ignore` or raise `min_occurrences` — NOT the warn/block level.
- KG mining is **policy-driven, NOT a hardwired skip** (v9.91 hardwired skip was
  removed in v9.92). `_process_source` (`engine/kg_extract.py`) calls
  `gdpr_pick_model_for_background` and branches on what it RAISES: `skip`→GDPRSkipError
  →doc skipped (KG⊘); `abort`/classification-block→doc skipped; `anonymise`→proceeds
  (per-chunk anonymise+extract); `swap_to_local`→extracts on local model.

### With current config (`background_pii_action=anonymise`): 7 docs would be anonymised
20_2_4_6S_Verzeichnis Drittparteien.xlsx(20), 4_8a_PB_CHECK24.pdf(7), 4× single-email
docs (Data Breach ×2, Leitfaden, Archivierung, MorgenCheck). All worst=warn, 0 block.
They are NOT skipped under `anonymise` — only `skip`/`abort` policy skips.

### Does anonymise HARM KG extraction for these 7? — NO (verified structurally)
Char-level: 6 of 7 docs change <1.3% (only email tokens replaced; all policy/process
content untouched). The outlier — the supplier register xlsx — changes 36%, but
side-by-side proof shows that's ENTIRELY the email column; the substantive content is
byte-identical:
- Vendor names (Artaker, SWIFT, CPB Software…) — UNCHANGED
- Contact PERSON names (Georg Broucek, Michael Nikolov…) — UNCHANGED (name/organisation
  are `ignore`, so spaCy never touches them)
- Table structure (Vendor | Ansprechpartner | Kontakt) — UNCHANGED
- Only the email VALUES become shape-preserving realistic fakes
  (`g.broucek@artaker.at` → `pat.phillips@example.at`), which keep the text
  well-formed (BETTER for the LLM than `<EMAIL_n>` masking).
The `normative` KG profile extracts supplier→service→risk relationships, not email
values, so every triple survives. CONCLUSION: anonymise is safe for KG here — unlike
the 2026-06 date-rule incident, the only thing stripped is non-triple contact emails.

CAVEAT ON RIGOR: this is a STRUCTURAL proof (what the model sees), NOT a measured
triple-count diff. The live LLM diff could not run from a standalone process
(`import brain` has no server_config → `CLIProxyAPI/mistral-small-latest` resolves to
empty provider → sidecar 500; the dual-module footgun). A measured diff must run
INSIDE the live server (real project re-mine), still TODO if wanted.

### Optional follow-up (not done): `background_pii_action=swap_to_local`
Would keep those 7 docs WHOLE on the local model (zero placeholders, nothing leaves
the box). `anonymise` is also fine (only emails stripped). `skip` would needlessly
drop 7 real policy docs from the KG.

---

## Live config snapshot (2026-06-08, gitignored — record for rebuild)
```
gdpr_scanner.enabled = false        # scanner DISABLED on this box (test by enabling)
background_pii_action = anonymise
background_anonymise_fail_action = swap_to_local
categories = secrets:block, national_id:warn, national_id_ctx:warn, financial:warn,
             contact:ignore, network:ignore, personal:warn, bare_id:warn, business_id:ignore
rule_overrides = name:ignore, organisation:ignore, email:warn
email_allowlist = ["@wienerprivatbank.com"]
min_occurrences = 27 rules (= code defaults; date=10, jp_mynumber=10, *=5/3 tiers)
```
Backups: `config.json.bak-gdpr-single-source-*`, `config.json.bak-email-warn-*`.

## Gotchas (carry forward)
- NEVER probe live provider/server_config via standalone `python3 -c "import brain"` —
  returns defaults, looks like a phantom bug, and breaks cloud-model resolution.
- NEVER SIGKILL brain-agent — graceful `launchctl kill SIGTERM` only.
- py_compile does NOT catch module-level NameError — runtime-import after edits.
- config.json gitignored — config changes are live-box; document them here.
- Real LLM sidecar = port 8421 (pi-sidecar on 8422 is unrelated).
