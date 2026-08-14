# Brain-Agent (cctest)

Personal AI assistant server: Python HTTP daemon (port 8420) with an **in-process** OpenAI-compatible agentic LLM loop, SQLite storage, a vanilla-JS web UI, Telegram/TUI frontends, and an Electron desktop shell. Deep project knowledge (architecture, invariants, GDPR/PII, MemPalace, tool dispatch) lives in **[CLAUDE.md](CLAUDE.md)** and **[INVARIANTS.md](INVARIANTS.md)** — read those before touching behavior; this file is the quick orientation.

## Commands (verified)

- Run server: `python3 launcher.py start|stop|restart|status` (also `tui` / `telegram` frontends). HTTP daemon listens on **8420**; managed by launchd (`com.brain-agent.server.plist`).
- Tests: `python3 -m unittest discover -s tests -p "test_*.py"` — **stdlib `unittest`, NOT pytest** (pytest is not installed). 56 files in `tests/`.
- Python refactor gate: `./refactor_gate.sh` (PII JS-parity drift + full test suite + request-context no-bleed; uses `/opt/homebrew/bin/python3`).
- JS gate (before editing `web/js/`): `cd web/js && ./js_gate.sh` (ESLint `no-undef`/`no-redeclare` + net-globals-count invariant + Playwright smoke; component 4 needs the dev server up on 127.0.0.1:8420).
- Deps: `requirements.txt` (mostly spaCy German NER additions; most deps are pre-installed in the dev env — see file header for install commands). No build step, no npm at root (`desktop/` is the only npm project, for the Electron shell).

## Architecture

- `launcher.py` / `server.py` — gateway CLI + HTTP daemon (entry points).
- `brain.py` — core wiring: tool registry (`TOOL_GROUPS`/`TOOL_DISPATCH`), runtime classes, provider routing.
- `engine/` — extracted modules + every `tool_*` implementation; **`engine/llm_loop.py` is the ONLY LLM execution path** (in-process, no sidecar subprocess).
- `handlers/` — HTTP handler modules; `server_lib/` — DB, auth, sessions, helpers.
- `web/` — single-page UI: **global-scope `<script>` files, fixed load order, NO ES modules/bundler**; `init.js` loads last.
- `agents/<name>/` — agent configs (`soul.md`, `agent.json`, `skills/`) + SQLite DBs.
- `desktop/` — Electron shell (CORS-free IPC); `crawl4ai/`, `searxng/` — supervised rendering/search subprocess services.

## Conventions

- Python: 4-space indent, descriptive names; all `ChatDB` methods `@_db_safe`; provider routing single-sourced via `resolve_provider_for_model(model)`.
- Tests: `unittest` in `tests/`, files named `test_*.py`; encode intent (WHY), not just WHAT.
- Thread safety: mutate `Session` fields only under `Session.lock`; request context is a `contextvars.ContextVar` set ONLY inside `with request_context(...):` — never bare on pooled threads (guarded by `tests/test_request_context_isolation.py`).
- **Adding a tool** = 4 sites / 3 files: schema in `TOOL_DEFINITIONS`, `TOOL_GROUPS` (`brain.py`), `tool_*` fn (`engine/tools/<group>.py`), direct fn ref in `TOOL_DISPATCH` (`brain.py`).
- JS: every fn/var is a browser global; cross-file calls rely on load order. Moving a fn = relocating one global — net-globals count must stay constant (js_gate enforces).
- Commits: descriptive, conventional prefixes (`feat:`/`fix:`/`chore:`) + version tag; pre-push hook (`.githooks/pre-push`) warns when user-facing feature code changes without updating the brain-agent-guide skill or curated changelog — override false positives with `SKILL_DOC_OK=1` / `CHANGELOG_OK=1`.
- Daemon log gotcha: launchd routes both fds to `server.error.log` — `print()` lands there, not `server.log`.
- Never commit `config.json` (gitignored, contains provider keys).

## Notes

(Quick-add scratch space for future sessions.)
