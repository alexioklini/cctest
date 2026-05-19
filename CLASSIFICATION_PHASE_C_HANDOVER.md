# Document Classification — Phase C Handover

**Status:** Not started. Phases A + B shipped as v9.6.0 on 2026-05-19.

**Audience:** Next engineer picking up the ARL 20.02.02.06 work. Assumes
familiarity with [[project_attachment_anonymisation_redesign]] and the
GDPR background-policy gate (`gdpr_pick_model_for_background`).

**Prerequisite reading:**
- Root `CLAUDE.md` → "Document Classification — ARL 20.02.02.06" section
- `memory/project_classification_phase_a.md` — Phase A scope + decisions
- `memory/project_classification_phase_b.md` — Phase B scope + subclass trick

---

## What Phase C is for

Phase A built detection. Phase B wired enforcement at the existing
inbound seams (attachment scan, tool reads, background calls). Phase C
closes the remaining gaps:

1. **Derived-artifact marking** — when a chat session has any classified
   attachment in scope and the LLM writes a new file via `write_file` /
   `edit_file`, the artifact should inherit a classification marker per
   ARL §1.7 ("Kennzeichnung ist durchgängig … durchzuführen").
2. **Three remaining background sites not yet wrapped** — closes the
   coverage matrix for both GDPR and Classification simultaneously.
3. **Cross-attachment session taint** — once a turn ingests classified
   content, ALL subsequent reads/writes in the same session should treat
   the session as classified, even if the originating attachment is no
   longer in the active context.

---

## Scope decisions already made (do not re-litigate)

- **Telegram frontend stays out of scope.** Web UI only (user decision,
  Phase A planning).
- **Derived artifacts default to `internal`.** User can re-classify
  manually. This is the Phase A spec — don't auto-detect the artifact's
  content to pick a level; assume the lowest reasonable floor.
- **No LLM-based classifier.** Detection stays regex + heuristic.
- **Strict-always-block is invariant** (ARL §1.11). The admin UI locks
  the strict row; the server-side `_classification_effective_action`
  coerces strict → block regardless of admin input when `server_block`
  is on. Don't add a bypass.

---

## Work items (in suggested order)

### 1. Session classification taint (foundation for #2)

Add `Session.classification_taint: str` (one of
`'' | 'internal' | 'confidential' | 'strict'`). Sticky for the session
lifetime — once raised, never lowered (ARL §1.5: "die Klassifizierung
ordnungsgemäß vorzunehmen, sie regelmäßig zu überprüfen" — the
*content owner* downgrades, not an automatic process).

**Set points:**
- Chat worker, when an attachment's `scan.classification.final_level`
  is internal/confidential/strict, escalate the session taint to
  `max(current, level)` using `engine.classification.LEVEL_RANK`.
- `_classification_gate_tool_text` (when a tool read returns classified
  content) — same escalation.
- Persistence: new column `sessions.classification_taint TEXT DEFAULT
  ''` via the same `try/except sqlite3.OperationalError` ALTER pattern
  the other `Session` fields use.

**Surface:**
- `GET /v1/sessions/<id>/messages` response gets
  `classification_taint: 'internal'|...|''` so the client can pin
  badges/banners across reloads.
- `web/js/chat.js`: render a sticky banner above the composer when
  taint > '' — "Diese Sitzung enthält klassifizierte Inhalte
  (Vertraulich) — nur lokale Modelle". Mirror `piiBlockActive`'s
  visual style.
- `piiBlockActive` already folds in `classificationBlockActive` (Phase
  B), but `classificationBlockActive` currently only checks pending
  attachments — extend it to also check `chat.classificationTaint`.

### 2. Derived-artifact marking

Hook into `brain._after_file_write(path, action, agent_id)`. By this
point the chat-worker thread-local already has `current_session_id`;
look up the session, read `classification_taint`. If non-empty AND the
file is registered as an artifact:

- **Markdown** (`.md`, `.markdown`, `.txt`): append a footer
  `\n\n---\nDokumentenklassifizierung: <Label>\n` (idempotent — skip if
  the marker is already present in the last 200 chars).
- **HTML**: insert `<meta name="classification" content="<level>">` in
  `<head>`, and prepend a visible `<div class="classification-banner">
  Klassifizierung: <Label></div>` (CSS lives in user space — they ship
  the styles).
- **DOCX**: use `python-docx` to add a custom document property
  `classification = <level>` AND prepend a header paragraph styled
  `Heading 2` with the label. Already a dep — see
  `engine/file_pseudonymize.py` for the openpyxl/zipfile patterns used
  for OOXML.
- **XLSX**: workbook-level custom property `classification = <level>`
  via openpyxl `wb.custom_doc_props`. No visible header — XLSX users
  don't want headers in row 1.
- **PDF**: punt for now. PDF generation in Brain is rare; flag in audit
  log as `classification_unmarked_artifact` so admins can re-mark
  manually. Don't take a `pypdf`/`pikepdf` dep just for this.
- **Other binary** (images, audio, etc.): no inline marker —
  classification carries via the parent session's taint only. Emit
  audit `classification_unmarked_artifact`.

**Format-dispatch table** lives next to the OOXML walker in
`engine/file_pseudonymize.py` (or split into a new
`engine/classification_marker.py` if it grows past ~200 LOC). Reuse
the same `MAX_RUN_CHARS` and zipfile patterns.

**Idempotency invariant:** running the marker twice on the same file
must be a no-op. Check for an existing marker before injection. The LLM
may also write the marker itself if instructed; don't double-stamp.

**Race condition to watch:** `_after_file_write` fires inside the tool
dispatch thread; the session lookup may race with session deletion.
Wrap in try/except and `sessions.peek()` (not `get()`) to avoid
resurrecting a deleted session.

### 3. Three remaining background sites

Same gap that exists for GDPR (per Phase B handover). These don't
currently route through `gdpr_pick_model_for_background`:

- `_handle_soul_chat` (`handlers/admin.py`) — agent soul refinement
- Workflow LLM nodes — `engine/workflow_engine.py` LLM-node executor
- Warmup test-call — `engine/provider.py: run_model_warmup` (the
  zero-tool 1-token probe)

For each, wrap the input texts through `gdpr_pick_model_for_background`
before the wire call, catch `GDPRBlockedError` (which now also catches
`ClassificationBlockedError` thanks to the Phase B subclass trick), and
soft-return on block. Warmup specifically: if blocked, skip the
warmup entirely and log — don't propagate.

Once these three are wrapped, both GDPR and Classification coverage are
22/22 background sites.

### 4. Composer banner refinement

The Phase B modal fires per-send. With session taint, the banner should
be persistent. Above the composer:

```
🔒 Vertrauliche Sitzung — automatisch auf lokales Modell
```

Mirror the existing `gdpr-banner` style. When user clicks the banner,
open a popover showing which attachment / tool-read raised the taint
(stored as `session.classification_taint_source = [{filename, level,
ts}]`).

### 5. Audit-log dashboard surface

Phase B audits `classification_detected` / `classification_blocked` /
`classification_auto_fallback`. Add a Settings → Audit filter chip for
"Classification events" so admins can spot policy violations. The audit
table query already supports `action_type=...` — just plumb a UI
filter. ~20 LOC in `web/js/settings.js` Audit tab.

---

## Non-goals for Phase C

- LLM-based artifact-level classification. The session-taint default
  is "inherit parent level" — this is convention, not detection.
- Cross-session classification propagation. If a user copies content
  from a classified session to a new one, the new session starts clean.
  Detection on send (Phase B) will catch it if the content carries a
  marker.
- Telegram. Still out of scope.
- Workflow run-history retroactive marking. Forward-only.

---

## Smoke-test checklist for Phase C

When you finish, run:

1. Upload a `vertraulich` PDF to a fresh chat. Verify banner appears.
   Send a follow-up turn with no new attachment — banner still there.
2. Ask the LLM to write a `.md` summary. Open the artifact — verify
   footer `Dokumentenklassifizierung: Vertraulich` appears.
3. Repeat with `.docx` — verify custom property + heading paragraph.
4. Run the eval harness against a project containing classified docs
   — verify no regression on the existing classification scan paths.
5. Audit log: `classification_blocked` row for a strict-marker file
   sent to a cloud model with `server_block=true`.

---

## Files to expect touching

- `brain.py` — Session.classification_taint field; `_after_file_write`
  hook; soul_chat wrapping (~150 LOC)
- `server_lib/db.py` — new column migration (~5 LOC)
- `handlers/sessions_handler.py` — surface `classification_taint` in
  `GET /messages` (~10 LOC)
- `handlers/chat.py` — set taint from attachment scan results
  (~20 LOC)
- `engine/file_pseudonymize.py` (or new `classification_marker.py`) —
  format-dispatch table for marker injection (~250 LOC)
- `engine/workflow_engine.py` — wrap LLM node (~30 LOC)
- `engine/provider.py` — wrap warmup (~20 LOC)
- `web/js/chat.js` — composer banner (~50 LOC)
- `web/js/panels.js` — taint-source popover (~80 LOC)
- `web/css/main.css` — banner styles (~30 LOC)
- `web/js/settings.js` — audit filter (~20 LOC)
- `CLAUDE.md` — Phase C section update
- `tests/` — new artifact-marker tests, taint-stickiness test

**Total estimate:** ~1000 LOC across 12 files. PR scope similar to
Phase B.

---

## Open questions to ask the user before starting

1. **Marker text format** — should DE labels use "Dokumentenklassifizierung:
   Vertraulich" (matches WPB ARL exactly) or the shorter "Klassifizierung:
   Vertraulich"? Phase A/B use the former in regex; Phase C should match.
2. **DOCX heading style** — paragraph styled as Heading 1 (more visible)
   or as a custom "Classification" style (cleaner but requires style
   definition)?
3. **Taint downgrade** — if the user explicitly removes the classified
   attachment from the session, should the taint clear? Default
   recommendation: no (ARL §1.5 puts re-classification on the *owner*,
   not on attachment lifecycle). But user may want auto-clear for ergonomic
   reasons.
4. **Workflow-node wrapping** — workflows may run in a server context
   without a user session (`current_session_id == ''`). Audit-only or
   full enforcement?

Don't start implementation until these four are answered — they shape
the data model.
