---
name: Brain-Agent Guide
description: Operator + user-help skill for THIS brain-agent instance you are running inside. Load it FIRST — before exa_search / web_fetch — whenever the user's question is about brain-agent itself, in any language. Trigger phrases include "wie kann ich brain-agent …", "how can I … with brain-agent", "mit brain-agent X abfragen/machen/erstellen", "where do I find …", "how do I …" referring to the web UI or a brain-agent feature. Also load it when the user asks you to PERFORM an operation here (create/run a schedule, list projects, inspect chats, check costs, manage memory, query the KG/DBs, tail logs, restart a service) or describes a problem and you need to know where the log / DB / endpoint / UI control lives. Examples that MUST trigger this skill, not web search — "wie kann ich brain-agent das aktuelle wetter abfragen?" (answer: which Brain tool/recipe to use, not the live weather), "how do I translate a docx?", "compare two Excels", "warum blockt mir GDPR den Send?". Covers the web UI manual + FAQ + recipes, HTTP API, agent tools, SQLite schemas, file layout, log paths, and internals.
metadata:
  type: skill
  # skill_version: bump when these reference files change.
  # brain_agent_version: the brain-agent VERSION (brain.py) this skill was last
  #   reconciled against — a drift indicator. The pre-push hook warns when it
  #   falls behind brain.VERSION (override with SKILL_DOC_OK=1). Keep both in
  #   sync with the change that touches the skill.
  skill_version: 1.29.27
  brain_agent_version: 9.79.1
---

# Brain-Agent Operator Guide

You are operating inside a running brain-agent instance. This skill makes you
capable of **doing** brain-agent operations on the user's behalf — not just
explaining them.

> This skill is the knowledge base for **Brainy**, the read-only helpdesk
> bot (the floating bubble). It is `HELPDESK_ONLY_SKILLS`-gated — hidden
> from normal chat unless helpdesk_mode is active. As Brainy you are
> read-only: explain, locate, and read; never write, edit, schedule, or
> restart. The operator recipes in `04-recipes.md` that mutate state are
> for the main agent, not for you — translate them into a "here's how you
> (the user) do it / here's where it lives" answer instead of executing.
> Answer the user in **German** (the UI is German); keep tech terms English.

## When to use this skill

Load it whenever the user:

- Asks "how do I…" / "where do I find…" about the **web UI** (translate a
  document, compare two files, set up a recurring task, share a chat,
  manage memory, fix a GDPR block, recover context-full warnings, …)
- Asks you to **perform** an operation on their behalf: create/run/edit a
  scheduled task, list/search chats, inspect a session, check costs,
  query the KG, tail a log, restart a service, dump a DB table, etc.
- Asks how brain-agent works internally (architecture, sidecar, MemPalace,
  GDPR scanner, scheduler, …)
- Reports a problem and you need to know where the relevant log / DB /
  endpoint / UI control lives.

## How to operate

1. **Pick the right file** based on what the user wants:
   - "How do I … (in the UI)" → `06-user-manual.md`
   - "Do X for me" / operate the system → `04-recipes.md`
   - Endpoint / DB / tool details → `01-api.md` / `02-tools.md` / `03-storage.md`
   - "Why does it behave this way" → `05-internals.md`
   - Anything these files don't (fully) cover — an exact value, a limit, a
     default, "how does X work internally" → look it up in the actual
     brain-agent SOURCE: `mempalace_query` (searches the `brain_code` wing in
     helpdesk mode) + `code_graph_query` to find the file, then `web_fetch`
     the raw file from GitHub for the precise current value. See
     `05-internals.md` "Reading the brain-agent source".
2. **Read the file before acting.** These curated files are your primary
   ground truth — do not invent endpoints, column names, tool args, button
   names, or file paths. The local source code is **not on disk in
   production**, but as a fallback you CAN read it from the public GitHub
   repo (see step 1's last bullet) when these files don't cover something.
3. Prefer **tools** for actions: `execute_command` for `curl`/`sqlite3`/`tail`,
   `read_file` for configs, `python_exec` for ad-hoc data work.
4. The HTTP API is on `http://127.0.0.1:8420`. You are on the same host.
   Most endpoints need auth — see `01-api.md`.
5. When the user says "do X", **do X and report the result**. Don't dump
   a how-to and stop. Example: "create a schedule that runs daily at 8am
   and summarizes my unread email" → call the API, verify it landed,
   show the user the created row, offer to run-now.
6. When the user asks "how do I", give them the **shortest correct
   answer** from `06-user-manual.md` — don't paste the whole section.
7. **If these files don't (fully) answer it, do NOT guess and do NOT say "it
   doesn't exist" — go to the source.** Whenever the docs are silent,
   incomplete, or you're unsure about an internal mechanism (a limit, a
   default, exact behaviour, "what happens when…"), you MUST look it up in the
   brain-agent source before answering: `mempalace_query` (the `brain_code`
   wing) + `code_graph_query` to locate the file, then `web_fetch` the raw
   GitHub file to read the precise value. THEN answer from what the source
   actually says, citing the file. Inventing an answer or wrongly claiming a
   feature is absent (e.g. "there is no tool-round limit" when the code has
   one) is the worst outcome — the source is there precisely so you don't
   have to guess. Only if the source genuinely doesn't cover it do you say
   so plainly. A persistent doc gap also means the skill needs updating.

## Reference files (read on demand)

`use_skill` returns the companion pages as **absolute** paths (the
`companion_pages` map) — pass those straight to `read_file`. If you only
have a filename, resolve it under
`agents/main/skills/brain-agent-guide/<file>`.

| File | Contents |
|---|---|
| `06-user-manual.md` | **User-facing manual.** Web-UI walkthrough (sidebar, composer, projects, translation, scheduled, settings), FAQ, concrete recipe walkthroughs (translate docx, compare two Excels, daily summary, project-from-PDFs), best practices, tips & tricks, "when to use what" table. Read this for any "how do I" question. |
| `01-api.md` | Full HTTP API: every `/v1/*` endpoint, methods, auth, request/response shape, admin-only flags. |
| `02-tools.md` | Every agent tool name + when to reach for it. Groups, purposes, the `use_skill` / `tool_search` flow, dispatch path. |
| `03-storage.md` | Disk layout (`agents/<id>/…`, artifacts, schedules, MemPalace, projects, attachments), SQLite DB locations + schemas (chats, schedules, costs, auth, traces, audit, context, code-graph). |
| `04-recipes.md` | Operator recipes — call the API / SQLite from inside an agent turn: list projects, create+run a schedule, inspect chats, check costs/quotas, search MemPalace, manage models/providers, restart services, debug from logs. |
| `05-internals.md` | Architecture: sidecar loop, warm pool, provider queue, GDPR scanner, MemPalace daemons, project sync, KG extraction, scheduler internals. |

## First step for every task

Before acting:

```
# User asks "how do I X in the UI" / "where do I find …" / "what's the right way to …":
read_file("agents/main/skills/brain-agent-guide/06-user-manual.md")

# User wants you to DO something:
read_file("agents/main/skills/brain-agent-guide/04-recipes.md")
read_file("agents/main/skills/brain-agent-guide/01-api.md")     # if HTTP is involved

# User asks why something behaves a certain way:
read_file("agents/main/skills/brain-agent-guide/05-internals.md")
```

Then either answer with a tight, specific response (UI/FAQ questions), or
`execute_command` the curl/sqlite call, parse the result, and present a
short, concrete result (operations).

## Authentication shortcut

The current chat already has an authenticated user (`current_user_id`
thread-local). For server-side calls from inside an agent turn, prefer:

- **SQLite reads** — go direct, no auth needed (`sqlite3 agents/main/<db>`).
- **HTTP API calls** — need a bearer token. Get one with the admin
  credentials documented in `04-recipes.md` (login flow). For read-only
  inspection of your own data, SQLite is faster.

## Self-update protocol

If you notice that something in these reference files is **wrong** or
**out of date** (e.g. an endpoint moved, a column was renamed, a new
feature was added), say so to the user and offer to update the skill
file. The skill is read fresh from disk on every `use_skill` call — no
restart needed.

**Maintenance contract**: this skill is meant to stay in lockstep with the
codebase. Whenever a user-facing feature, HTTP endpoint, agent tool, DB
schema, or UI control changes, the matching reference file here must be
updated **in the same change**. CLAUDE.md carries this as a standing rule,
and a git pre-push hook (`.githooks/pre-push`) warns when feature code
changed without a touch to this directory. The hook is a backstop, not a
substitute — keep the docs honest as you go.
