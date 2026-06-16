# Dependency Upgrade Plan

Status snapshot taken **2026-06-16**. This is a staged, low-risk plan to upgrade
the externally-pulled components. Do the stages **independently, one at a time**,
each with its own verify + rollback — never batch them. Brain-agent restarts are
graceful SIGTERM only (`launchctl kill SIGTERM gui/$(id -u)/com.brain-agent.server`),
NEVER SIGKILL (corrupts MemPalace — see `feedback_never_sigkill_brain`).

## Current vs. latest (2026-06-16)

| Component       | Installed       | Latest          | Env / how installed                              | Risk  |
|-----------------|-----------------|-----------------|--------------------------------------------------|-------|
| **anthropic**   | 0.101.0         | **0.109.2**     | `.venv_sdk` (sidecar, py3.14); pin `>=0.101.0`    | HIGH  |
| **crawl4ai**    | 0.8.6           | **0.8.9**       | `.venv_crawl4ai` (py3.13); render_service subproc | MED   |
| **playwright**  | 1.60.0          | 1.60.0          | `.venv_crawl4ai` (crawl4ai dep)                   | —     |
| **beautifulsoup4** | 4.14.3       | **4.15.0**      | `.venv_crawl4ai` + system py3.14                  | LOW   |
| **pdfminer.six**| 20251230        | **20260107**    | system py3.14 (PDF extraction / pdfplumber)       | LOW   |
| markitdown      | 0.1.6           | 0.1.6 (current) | Homebrew py3.14 `/opt/homebrew/bin/markitdown`    | — (checked 2026-06-16, nothing newer) |

**playwright is already current.** markitdown is already current. So the real
work is **anthropic, crawl4ai, beautifulsoup4, pdfminer.six**.

> ⚠️ The sidecar pins `anthropic>=0.101.0` (a FLOOR, not a ceiling). A fresh
> `.venv_sdk` rebuild would already resolve to 0.109.2 — the installed 0.101.0
> is just what was resolved when the venv was first built. So "rebuild the
> sidecar venv" silently performs this upgrade. Pin a tested version explicitly
> (see Stage 1) so a future rebuild is reproducible.

---

## Stage 1 — Anthropic SDK 0.101.0 → 0.109.2 (do first + alone; risk re-assessed LOW–MED)

**Changelog reviewed 2026-06-16 (0.102→0.109.2): NO breaking changes that hit
the sidecar's standard messages/streaming path.** Most of the 8 releases are
Managed-Agents (CMA) work we don't use. The only stream-touching changes are
ADDITIVE:
- **0.104.0** — new thinking-block delta event (token estimates while streaming).
- **0.105.0** — `claude-opus-4-8` support, **mid-conversation system blocks** (a
  system block can appear AFTER the initial position), `usage.output_tokens_details`.
- **0.108.0** — `claude-fable-5` + server-side refusal fallbacks + client-side
  fallback middleware (opt-in; additive).
- **0.109.2** — "remove retired models" (chore). Confirm no model WE use is
  removed — we route Mistral via CLIProxyAPI, not native Anthropic model IDs, so
  this is moot, but double-check.
- 0.102 added a beta search-result content-block type (Managed Agents only).

**Why still verify carefully:** the sidecar (`sidecar/sidecar.py`) is the ONLY
LLM execution path; `run_turn_streaming` iterates raw `client.messages.create`
events and `_AccumulatedMessage` reassembles content blocks. The additive
new event/block types (0.104 thinking-delta, 0.105 mid-conv system block) must
be TOLERATED, not break the accumulator. Good news: `_AccumulatedMessage`
already keys blocks by their own index and tolerates gaps/unknown types (built
that way for oMLX's index jumps) — so it should pass them through, but verify.

**Prep / sanity-grep:**
1. Grep the sidecar for SDK surfaces: `anthropic.`, `client.messages.create`,
   `.model_dump`, `content_block`, `stop_reason`, `usage`, `APIError`,
   `tool_use` (mostly in `run_turn_streaming` + `_AccumulatedMessage`). Confirm
   nothing assumes a CLOSED set of block/delta types.

**Execute (isolated, reversible):**
3. Build a SIDE venv, don't mutate the live one:
   `python3.14 -m venv .venv_sdk_test && .venv_sdk_test/bin/pip install 'anthropic==0.109.2'`
   (mirror any other sidecar deps from `sidecar/pyproject.toml`).
4. Point a TEST sidecar at it on a spare port, or temporarily run
   `sidecar/sidecar.py` under `.venv_sdk_test` against the running providers.
5. Verify against BOTH wire paths the sidecar serves:
   - Anthropic Messages turn (cloud mistral via CLIProxyAPI) — text + a tool
     round (`read_document` on an attachment) → confirm streaming events
     accumulate, `final_text` correct, tool dispatch loop works.
   - The forced-tool path (`capture_forced_tool`) — the classifier route still
     returns clean `forced_tool_input`.
   - `/v1/messages` native shape if used by the local-model plan.
6. If clean: swap `.venv_sdk` → rebuild from a PINNED version. Update
   `sidecar/pyproject.toml`: `"anthropic>=0.101.0"` → `"anthropic==0.109.2"`
   (pin exact so rebuilds are reproducible; revisit pin policy if you prefer a
   range). Then graceful Brain restart (it respawns the sidecar) + confirm
   `/health` `anthropic_version: 0.109.2` and a real chat turn works.

**Rollback:** keep the old `.venv_sdk` (rename, don't delete) until a few real
turns pass; revert the pyproject pin + point back. The CLAUDE.md notes the
sidecar is the only LLM path — if `:8421` is wrong, chat returns
`*(Sidecar error…)*`, so a bad upgrade is loud, not silent.

**Verify checklist:** interactive chat (text), tool-call turn, forced-tool
classifier, thinking level on a reasoning model, cancel mid-turn, cost logging
still writes rows.

---

## Stage 2 — crawl4ai 0.8.6 → 0.8.9 (MEDIUM, own venv = isolated)

**What it is:** headless-Chromium render service (`crawl4ai/render_service.py`,
own supervised subprocess on :8422, `Crawl4aiSupervisor`). web_fetch's
markitdown→crawl4ai fallback for JS-rendered pages. 3 patch releases (bugfixes).

**Risk:** crawl4ai pulls a big dep set incl. a playwright/Chromium pairing —
a version bump may want a different playwright/Chromium. Isolated in
`.venv_crawl4ai`, so it can't break the main server.

**Execute:**
1. `.venv_crawl4ai/bin/pip install 'crawl4ai==0.8.9'` (let it resolve deps).
2. Check whether it bumped playwright; if so, run
   `.venv_crawl4ai/bin/playwright install chromium` to match.
3. Restart the render service via the admin endpoint (`POST /v1/crawl4ai/restart`)
   — NOT a full Brain restart.
4. Verify: `POST /v1/crawl4ai/render {url}` on a known JS-rendered page returns
   non-empty markdown; then a real `web_fetch` of a JS page (e.g. a SPA) shows
   `fetch_method: crawl4ai`. Check `/v1/crawl4ai/status`.

**Rollback:** `pip install 'crawl4ai==0.8.6'` in the same venv; restart service.
Worst case the supervisor no-ops and web_fetch degrades to the HTTP+markitdown
result (graceful, per CLAUDE.md `_crawl4ai_render` degradation).

---

## Stage 3 — beautifulsoup4 4.14.3 → 4.15.0 (LOW)

**Where:** both `.venv_crawl4ai` (crawl4ai dep) and system py3.14. A minor bump;
bs4 is conservative but a minor can shift parser edge behavior.

**Execute (do the two envs separately so you can isolate a regression):**
1. system: `python3 -m pip install 'beautifulsoup4==4.15.0'`
2. crawl4ai venv: `.venv_crawl4ai/bin/pip install 'beautifulsoup4==4.15.0'`
   (or let Stage 2's crawl4ai resolve pull it — check after Stage 2 whether
   crawl4ai already moved bs4).

**Verify:** anything that parses HTML — `web_fetch` on a normal HTML page
(`fetch_method: markitdown`/`raw`), and any ingest/HTML path. Low blast radius.

**Rollback:** `pip install 'beautifulsoup4==4.14.3'` per env.

---

## Stage 4 — pdfminer.six 20251230 → 20260107 (LOWEST)

**Where:** system py3.14, used in the PDF extraction path (pdfplumber tables in
`engine/doc_convert._extract_pdf`). One dated snapshot (~1 week), tiny.

**Execute:** `python3 -m pip install 'pdfminer.six==20260107'`

**Verify:** `read_document` on a PDF with tables (`include_tables=true`) —
extraction + table layout still sane. Compare against a known-good PDF.

**Rollback:** `pip install 'pdfminer.six==20251230'`.

---

## Cross-cutting notes

- **Order matters:** Stage 1 alone first (verify a full day of real chat if
  possible). Stages 2–4 are independent and low-risk; can be done together in a
  later session if desired, but still verify each.
- **No git commit needed for the venv installs themselves** (the venvs are
  gitignored). The ONLY tracked change is the `anthropic` pin in
  `sidecar/pyproject.toml` (Stage 1, step 6). Bump brain.py VERSION + a CHANGELOG
  line for that pin change so the upgrade is recorded; skill bump only if a
  reference file changes (it won't for a dep pin).
- **markitdown / playwright:** already current — re-check next time with
  `curl -s https://pypi.org/pypi/<pkg>/json | jq -r .info.version`.
- **Python runtime:** sidecar=3.14, crawl4ai=3.13, system=3.14 — don't
  cross-install; each component stays in its own interpreter.
