# Common Task Recipes

Each recipe is "user asks X" → "do this, then report Y". Always **execute**
the commands and present results; don't dump the recipe as instructions
unless the user asked to learn how.

## Authentication helper (run once per session if you need HTTP)

```bash
TOKEN=$(curl -s -X POST http://127.0.0.1:8420/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"admin"}' | python3 -c \
  'import sys,json; print(json.load(sys.stdin)["access_token"])')
echo "$TOKEN" > /tmp/.brain_token
```

Then for subsequent calls:
```bash
AUTH="Authorization: Bearer $(cat /tmp/.brain_token)"
curl -s -H "$AUTH" http://127.0.0.1:8420/v1/...
```

For read-only data inspection, SQLite is faster than HTTP — see below.

---

## "Give me an overview of my projects"

```bash
ls agents/main/projects/
# Then for each, read project.json:
for p in agents/main/projects/*/; do
  name=$(basename "$p")
  jq -r '"\(.id // "?")  \(.research_mode // false)  \(.input_folders | length) folders  \(.status // "active")"' "$p/project.json" 2>/dev/null | sed "s|^|$name  |"
done
```

Or via HTTP:
```bash
curl -s -H "$AUTH" http://127.0.0.1:8420/v1/agents/main/projects | jq
```

Then summarize: name, status, research_mode on/off, number of input folders,
last sync. Show counts not raw JSON.

---

## "Create a scheduled task that does X every day at 8am"

1. Translate the description into a cron expression. Common patterns:
   - `0 8 * * *` — daily at 08:00
   - `0 */6 * * *` — every 6 hours on the hour
   - `@every 30m` — every 30 minutes
   - `0 9 * * 1` — Mondays at 09:00

2. Pick a name (slug: `lowercase_with_underscores`).

3. POST it:
```bash
curl -s -H "$AUTH" -H 'Content-Type: application/json' \
  -X POST http://127.0.0.1:8420/v1/schedule \
  -d '{
    "action": "add",
    "name": "daily_email_summary",
    "task": "Read my Gmail inbox from the last 24h and produce a 5-bullet summary of anything that needs a reply.",
    "schedule": "0 8 * * *",
    "agent": "main",
    "model": "<pick from /v1/models>",
    "timeout": 600,
    "tool_profile": "interactive"
  }'
```

4. Verify with `GET /v1/schedule`, then offer to `action: "run_now"` so
   the user sees a result immediately.

---

## "Run that schedule now and show me the result"

```bash
curl -s -H "$AUTH" -H 'Content-Type: application/json' \
  -X POST http://127.0.0.1:8420/v1/schedule \
  -d '{"action":"run_now","name":"daily_email_summary"}'
# → {"status":"triggered","name":"..."}
```

Then poll history (it's async):
```bash
sleep 5
curl -s -H "$AUTH" -X POST http://127.0.0.1:8420/v1/schedule \
  -H 'Content-Type: application/json' \
  -d '{"action":"history","name":"daily_email_summary","limit":1}' | jq
```

Or read directly:
```bash
sqlite3 -readonly agents/main/schedules.db \
  "SELECT id, status, started_at, finished_at, substr(output,1,500) FROM schedule_history WHERE schedule_name='daily_email_summary' ORDER BY id DESC LIMIT 1;"
```

Wait until `status` is no longer `running`, then present `output` to the
user. Artifacts produced live under `agents/main/artifacts/<date>_sched-<run_id>/`.

---

## "List my recent chats"

```bash
sqlite3 -readonly agents/main/chats.db \
  "SELECT id, title, model, status, datetime(last_active,'unixepoch','localtime')
   FROM sessions
   WHERE status='active' AND user_id=(SELECT id FROM users WHERE username='<user>')
   ORDER BY last_active DESC LIMIT 20;"
```

For the current user, get the id from `/v1/auth/me`.

---

## "Show me a session's full transcript"

```bash
sqlite3 -readonly agents/main/chats.db \
  "SELECT role, substr(content,1,400)
   FROM messages WHERE session_id='<sid>' AND compacted=0
   ORDER BY id;"
```

Or via API for fully-formatted output:
```bash
curl -s -H "$AUTH" http://127.0.0.1:8420/v1/sessions/<sid>/messages | jq
```

---

## "How much have I spent this month?"

```bash
sqlite3 -readonly agents/main/costs.db \
  "SELECT model, COUNT(*) as calls,
          ROUND(SUM(total_cost),4) as cost_usd,
          SUM(input_tokens) as in_tok, SUM(output_tokens) as out_tok
   FROM cost_log
   WHERE ts > strftime('%s','now','start of month')
     AND user_id='<uid>'
   GROUP BY model ORDER BY cost_usd DESC;"
```

Or `GET /v1/quotas/me` for limits + current usage.

---

## "Search my memory for X"

In a project chat: use `mempalace_query(query="X")` directly — it auto-scopes.

Outside a project, or to inspect cross-wing:
```bash
curl -s -H "$AUTH" "http://127.0.0.1:8420/v1/mempalace/drawers?wing=user__<uid>&q=<term>&limit=20" | jq
```

For the global KG:
```bash
curl -s -H "$AUTH" "http://127.0.0.1:8420/v1/mempalace/kg/entity?wing=project__<pid>&entity=<name>" | jq
```

---

## "Why was my chat blocked by GDPR?"

1. Check audit log:
```bash
sqlite3 -readonly agents/main/auth.db \
  "SELECT datetime(ts,'unixepoch','localtime'), action_type, metadata
   FROM audit_log
   WHERE action_type LIKE 'pii_%'
   ORDER BY ts DESC LIMIT 10;"
```

2. Show category actions:
```bash
jq '.gdpr_scanner.category_actions' config.json
```

3. To downgrade `server_block` for a category, edit `config.json` →
   `gdpr_scanner.category_actions.<cat>` from `"block"` to `"warn"`, then:
```bash
curl -s -H "$AUTH" -X POST http://127.0.0.1:8420/v1/restart
```

---

## "List models / providers / change default"

```bash
curl -s -H "$AUTH" http://127.0.0.1:8420/v1/models | jq '.[] | {id, display_name, provider, enabled}'
curl -s -H "$AUTH" http://127.0.0.1:8420/v1/providers | jq
```

Default model lives in `config.json → default_model` (top-level, since
9.21.4). The Settings → Server → Standardmodell dropdown saves it via
`POST /v1/services/server`; that path persists it (no manual edit needed).
To set it programmatically:
```bash
curl -s -H "$AUTH" -H 'Content-Type: application/json' \
  -X POST http://127.0.0.1:8420/v1/services/server \
  -d '{"action":"save","default_model":"<model_id>"}'
```

---

## "Re-sync a project's input folders"

```bash
curl -s -H "$AUTH" -X POST \
  http://127.0.0.1:8420/v1/agents/main/projects/<name>/sync-now
# or for full wipe + re-mine:
curl -s -H "$AUTH" -X POST \
  http://127.0.0.1:8420/v1/agents/main/projects/<name>/full-resync
```

Check progress:
```bash
curl -s -H "$AUTH" http://127.0.0.1:8420/v1/agents/main/projects/<name>/sync-status | jq
```

---

## "Restart Brain / a daemon"

```bash
# Brain server (graceful):
curl -s -H "$AUTH" -X POST http://127.0.0.1:8420/v1/restart

# Via launchctl (when curl can't reach the server):
launchctl kickstart -k gui/$UID/com.brain-agent.server

# Sidecar only (anthropic SDK loop host):
curl -s -H "$AUTH" -X POST http://127.0.0.1:8420/v1/sidecar/restart

# Mempalace daemons / telegram:
curl -s -H "$AUTH" -X POST http://127.0.0.1:8420/v1/services/telegram \
  -H 'Content-Type: application/json' -d '{"action":"restart"}'
```

After kickstart, wait ≥6 s before retrying HTTP (the listener needs to bind).

---

## "Tail the logs / something broke"

The launchd plist routes BOTH stdout and stderr to `server.error.log`,
NOT `server.log`. Always tail the error log:

```bash
tail -n 200 ~/.brain-agent/server.error.log
tail -f ~/.brain-agent/server.error.log
# Sidecar (LLM-loop) errors:
tail -n 200 ~/.brain-agent/pi-sidecar.log
# Daemons:
curl -s -H "$AUTH" "http://127.0.0.1:8420/v1/services/log?name=mempalace-miner&lines=200" | jq -r .lines[]
```

Available service names: `mempalace-miner`, `mempalace-chat-sync`,
`mempalace-project-sync`, `user-profile`, `scheduler`, `telegram`.

---

## "Web search isn't working / check SearXNG + crawl4ai"

```bash
# Self-hosted SearXNG subprocess (backs searxng_search + the Websuche tab):
curl -s -H "$AUTH" http://127.0.0.1:8420/v1/searxng/status | jq
curl -s -H "$AUTH" http://127.0.0.1:8420/v1/searxng/engines | jq   # per-engine health
curl -s -H "$AUTH" -X POST http://127.0.0.1:8420/v1/searxng/test-engines | jq
curl -s -H "$AUTH" -X POST http://127.0.0.1:8420/v1/searxng/restart

# crawl4ai headless render service (web_fetch fallback for JS pages, port 8422):
curl -s -H "$AUTH" http://127.0.0.1:8420/v1/crawl4ai/status | jq
curl -s -H "$AUTH" -X POST http://127.0.0.1:8420/v1/crawl4ai/restart

# A raw search (no fetch, no LLM — same path the Websuche tab uses):
curl -s -H "$AUTH" -H 'Content-Type: application/json' \
  -X POST http://127.0.0.1:8420/v1/web/search -d '{"query":"<q>"}' | jq
```

If `searxng_search` returns nothing, check that the tool is enabled
(`config.json → tool_settings.searxng_search.enabled` — default false) and
that the subprocess is up. crawl4ai no-ops entirely unless
`config.json → crawl4ai.auto_start` is set.

---

## "Show / clear Brainy's (helpdesk) conversation for a user"

Brainy history is per-USER in `chats.db → helpdesk_history`:
```bash
sqlite3 -readonly agents/main/chats.db \
  "SELECT datetime(created_at,'unixepoch','localtime'), role, substr(content,1,200)
   FROM helpdesk_history WHERE user_id='<uid>' ORDER BY id DESC LIMIT 20;"
```
Brainy's config (admin): `GET/POST /v1/helpdesk/config`
(`{enabled, model, max_rounds, system_prompt}`).

---

## "Cancel a stuck turn / running schedule"

```bash
# A live chat turn:
curl -s -H "$AUTH" -X POST http://127.0.0.1:8420/v1/chat/cancel \
  -H 'Content-Type: application/json' -d '{"session_id":"<sid>"}'

# A running scheduled task:
curl -s -H "$AUTH" -X POST http://127.0.0.1:8420/v1/schedule/cancel \
  -H 'Content-Type: application/json' -d '{"name":"<sched_name>"}'

# Provider queue items:
curl -s -H "$AUTH" "http://127.0.0.1:8420/v1/queue/status" | jq
curl -s -H "$AUTH" -X POST http://127.0.0.1:8420/v1/queue/cancel \
  -H 'Content-Type: application/json' -d '{"item_id":"<id>"}'
```

---

## "What's currently running?"

```bash
curl -s -H "$AUTH" http://127.0.0.1:8420/v1/status | jq         # uptime, version
curl -s -H "$AUTH" http://127.0.0.1:8420/v1/services | jq       # daemon health
curl -s -H "$AUTH" http://127.0.0.1:8420/v1/schedule/running | jq
curl -s -H "$AUTH" http://127.0.0.1:8420/v1/queue/status | jq   # provider queues
curl -s -H "$AUTH" http://127.0.0.1:8420/v1/warmup/status | jq  # warm-pool state
```

---

## "Show artifacts produced by my last chat / scheduled run"

```bash
# Session artifacts:
curl -s -H "$AUTH" "http://127.0.0.1:8420/v1/artifacts?session_id=<sid>" | jq

# Scheduled run artifacts live under sessions named "sched-<run_id>":
curl -s -H "$AUTH" "http://127.0.0.1:8420/v1/artifacts?session_id=sched-<run_id>" | jq

# Or directly:
ls -la "agents/main/artifacts/$(ls -t agents/main/artifacts | head -1)"
```

---

## "Inspect a session in detail"

```bash
curl -s -H "$AUTH" http://127.0.0.1:8420/v1/sessions/<sid>/inspect | jq
```

Returns model, system prompt, message counts, token usage, attached files,
streaming state — everything needed for debug.

---

## When the user describes a task in natural language

Pattern:

1. **Restate** what you understood ("You want me to set up a job that…").
2. **Translate** to concrete API call(s).
3. **Execute** them.
4. **Report** the result (created id, run output snippet, link via
   `agents/main/artifacts/...`).
5. **Offer** the obvious follow-up ("Want me to run it now to verify?").

Do not stop at step 2. Operating brain-agent on the user's behalf is the
whole point of this skill.
