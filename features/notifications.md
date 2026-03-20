# Feature Proposal: Notification System

**Status:** Proposed
**Author:** Brain Agent Team
**Date:** 2026-03-20
**Effort:** ~7 days
**Priority:** High

---

## Problem Statement

Brain Agent runs many operations silently in the background: scheduled tasks execute at
set intervals, delegated tasks run asynchronously across agents, and long-running tool
executions can take minutes. When something goes wrong — a scheduled task fails at 3am,
a delegated task times out, or an agent encounters a repeated error — nobody knows until
they manually check the Web UI or TUI.

Current pain points:

1. **Scheduled task failures are invisible** — a nightly report task fails silently;
   the user only discovers it when they notice the report is missing days later
2. **No alerting on system issues** — QMD goes offline, oMLX stops responding, or the
   server runs out of memory — no notification
3. **Background delegations complete silently** — "delegate_task" with async mode has
   no callback; user must poll task_status
4. **No budget/usage alerts** — no way to know when API costs spike or token usage
   exceeds thresholds
5. **Agent errors accumulate** — tool call dedup aborts and other error conditions
   are logged but not surfaced

---

## Proposed Solution

A notification system with configurable delivery channels and event triggers. Each event
type can be routed to one or more channels with severity-based filtering and quiet hours.

### Event Types

| Event ID           | Severity | Description                                      |
|--------------------|----------|--------------------------------------------------|
| `task_complete`    | info     | Scheduled task finished successfully              |
| `task_failed`      | error    | Scheduled task failed or threw an exception       |
| `task_timeout`     | warning  | Scheduled task exceeded its timeout               |
| `delegate_complete`| info     | Background delegation finished                    |
| `delegate_failed`  | error    | Background delegation failed                      |
| `agent_error`      | error    | Agent hit a tool loop abort or repeated error     |
| `budget_alert`     | warning  | Token/cost usage exceeded configured threshold    |
| `service_offline`  | critical | QMD, oMLX, CLIProxyAPI, or provider went offline  |
| `service_online`   | info     | Previously offline service came back online        |
| `approval_needed`  | warning  | Agent requests human approval for an action       |
| `memory_alert`     | warning  | Server memory usage above threshold               |
| `server_restart`   | info     | Server restarted (planned or crash recovery)      |

### Severity Levels

```
critical  >  error  >  warning  >  info
```

Each channel has a minimum severity filter. For example, email might only receive
`error` and above, while in-app notifications show everything.

### Delivery Channels

| Channel    | Transport          | Use Case                               |
|------------|--------------------|----------------------------------------|
| `in_app`   | WebSocket/SSE      | Real-time badges in Web UI             |
| `telegram` | Telegram Bot API   | Push to admin's phone                  |
| `email`    | SMTP               | Formal alerts, daily digests           |
| `webhook`  | HTTP POST          | Slack, Discord, PagerDuty, custom      |

---

## Configuration

### config.json: Notification Settings

```json
{
  "notifications": {
    "enabled": true,
    "quiet_hours": {
      "enabled": true,
      "start": "23:00",
      "end": "07:00",
      "timezone": "America/New_York",
      "override_critical": true
    },
    "channels": {
      "in_app": {
        "enabled": true,
        "min_severity": "info"
      },
      "telegram": {
        "enabled": true,
        "chat_id": 123456789,
        "min_severity": "warning",
        "use_bot_token": true
      },
      "email": {
        "enabled": true,
        "smtp_host": "smtp.gmail.com",
        "smtp_port": 587,
        "smtp_user": "alerts@example.com",
        "smtp_password": "app-password-here",
        "to": ["admin@example.com"],
        "min_severity": "error",
        "digest": {
          "enabled": true,
          "schedule": "daily 08:00",
          "include_info": true
        }
      },
      "webhook": {
        "enabled": true,
        "url": "https://hooks.slack.com/services/T00/B00/xxx",
        "method": "POST",
        "headers": {"Content-Type": "application/json"},
        "min_severity": "warning",
        "retry": {
          "max_attempts": 3,
          "backoff_seconds": [5, 30, 300]
        }
      }
    },
    "events": {
      "task_complete":     {"channels": ["in_app"]},
      "task_failed":       {"channels": ["in_app", "telegram", "email", "webhook"]},
      "task_timeout":      {"channels": ["in_app", "telegram", "webhook"]},
      "delegate_complete": {"channels": ["in_app"]},
      "delegate_failed":   {"channels": ["in_app", "webhook"]},
      "agent_error":       {"channels": ["in_app", "webhook"]},
      "budget_alert":      {"channels": ["in_app", "email"]},
      "service_offline":   {"channels": ["in_app", "telegram", "email", "webhook"]},
      "service_online":    {"channels": ["in_app"]},
      "approval_needed":   {"channels": ["in_app", "telegram"]},
      "memory_alert":      {"channels": ["in_app", "email"]},
      "server_restart":    {"channels": ["in_app"]}
    }
  }
}
```

---

## Architecture

```
                    Notification Pipeline
                    =====================

  Event Source              Notification Engine           Delivery
  ============             ===================           ========

  Scheduler ----+
                |
  Delegates ----+----> NotificationManager          +--> In-App (SSE)
                |       |                           |
  Health Check -+       |  1. Filter by enabled     +--> Telegram Bot
                |       |  2. Check quiet hours     |
  Agent Errors -+       |  3. Match severity        +--> Email (SMTP)
                |       |  4. Route to channels     |
  Budget Track -+       |  5. Format per channel    +--> Webhook (HTTP)
                        |  6. Queue for delivery    |
                        |  7. Retry on failure      |
                        v                           |
                   notifications.db                 |
                   (history + queue)                 |
                                                    |
                   Retry Worker Thread              |
                   (processes failed deliveries) ---+
```

### Notification Storage

```sql
-- notifications.db (in agents/main/)

CREATE TABLE notifications (
    id          TEXT PRIMARY KEY,
    event_type  TEXT NOT NULL,
    severity    TEXT NOT NULL,
    agent       TEXT,
    title       TEXT NOT NULL,
    message     TEXT NOT NULL,
    metadata    TEXT,           -- JSON blob
    created_at  TEXT NOT NULL,
    read_at     TEXT,
    dismissed   INTEGER DEFAULT 0
);

CREATE TABLE delivery_log (
    id              TEXT PRIMARY KEY,
    notification_id TEXT REFERENCES notifications(id),
    channel         TEXT NOT NULL,
    status          TEXT NOT NULL,  -- pending, sent, failed, retrying
    attempts        INTEGER DEFAULT 0,
    last_attempt    TEXT,
    next_retry      TEXT,
    error           TEXT
);
```

---

## Web UI Mockups

### Notification Bell in Header

```
+--------------------------------------------------------------------------+
|  Brain Agent  [main]  claude-opus-4-6       [bell(3)] [sun/moon] [gear]  |
+--------------------------------------------------------------------------+

[bell(3)] = bell icon with red badge showing unread count

When badge count > 0:
  - Badge pulses gently every 30 seconds
  - Badge shows "9+" for counts over 9

When badge count = 0:
  - Bell icon shown without badge
  - Subtle outline style
```

### Notification Dropdown

```
+--------------------------------------------------------------------------+
|  Brain Agent  [main]  claude-opus-4-6       [bell(3)] [sun/moon] [gear]  |
+--------------------------------------------------------------------------+
                                               |
                                    +----------v-----------+
                                    |   Notifications      |
                                    |   [Mark all read]    |
                                    +----------------------+
                                    |                      |
                                    | [!] task_failed      |
                                    |  Nightly Report      |
                                    |  Agent: Reporter     |
                                    |  2 hours ago         |
                                    |  KeyError: 'data'    |
                                    |  in report_gen.py    |
                                    +----------------------+
                                    |                      |
                                    | [!] service_offline  |
                                    |  QMD Unavailable     |
                                    |  Port 8181 refused   |
                                    |  5 hours ago         |
                                    +----------------------+
                                    |                      |
                                    | [i] task_complete    |
                                    |  Email Digest        |
                                    |  Agent: main         |
                                    |  6 hours ago         |
                                    +----------------------+
                                    |                      |
                                    |  [View all...]       |
                                    +----------------------+

Icons:
  [!!] = critical (red)
  [!]  = error/warning (orange)
  [i]  = info (blue)

- Click notification to expand details
- Hover shows dismiss button
- "View all" opens full notification history modal
```

### Notification Settings Tab (in Settings Dashboard)

```
+--------------------------------------------------------------------------+
|  Settings                                                         [x]    |
+--------------------------------------------------------------------------+
|  [Server] [QMD] [Models] [Telegram] [Providers] [*Notifications*]       |
+--------------------------------------------------------------------------+
|                                                                          |
|  Notifications                                           [x] Enabled    |
|                                                                          |
|  Quiet Hours                                             [x] Enabled    |
|  +----------------------------------------------------------------+     |
|  |  Start: [23:00]  End: [07:00]  Timezone: [America/New_York v]  |     |
|  |  [x] Override for critical events                              |     |
|  +----------------------------------------------------------------+     |
|                                                                          |
|  --- Channels ---                                                        |
|                                                                          |
|  In-App                                                  [x] Enabled    |
|  +----------------------------------------------------------------+     |
|  |  Min severity: [info v]                                        |     |
|  +----------------------------------------------------------------+     |
|                                                                          |
|  Telegram                                                [x] Enabled    |
|  +----------------------------------------------------------------+     |
|  |  Chat ID: [123456789        ]                                  |     |
|  |  Min severity: [warning v]                                     |     |
|  |  Uses existing bot token from Telegram config                  |     |
|  |  [Send Test]                                                   |     |
|  +----------------------------------------------------------------+     |
|                                                                          |
|  Email (SMTP)                                            [ ] Disabled   |
|  +----------------------------------------------------------------+     |
|  |  Host: [smtp.gmail.com    ]  Port: [587  ]                    |     |
|  |  User: [alerts@example.com]  Pass: [********]                 |     |
|  |  To:   [admin@example.com ]                                   |     |
|  |  Min severity: [error v]                                       |     |
|  |  [x] Daily digest at [08:00]                                  |     |
|  |  [Send Test]                                                   |     |
|  +----------------------------------------------------------------+     |
|                                                                          |
|  Webhook                                                 [x] Enabled    |
|  +----------------------------------------------------------------+     |
|  |  URL: [https://hooks.slack.com/services/T00/B00/xxx         ] |     |
|  |  Min severity: [warning v]                                     |     |
|  |  Retry: [3] attempts  Backoff: [5, 30, 300] seconds          |     |
|  |  [Send Test]                                                   |     |
|  +----------------------------------------------------------------+     |
|                                                                          |
|  --- Event Routing ---                                                   |
|                                                                          |
|  +----------------------------------------------------------------+     |
|  | Event             | In-App | Telegram | Email | Webhook       |     |
|  |-------------------+--------+----------+-------+-------------- |     |
|  | task_complete     |  [x]   |   [ ]    |  [ ]  |   [ ]         |     |
|  | task_failed       |  [x]   |   [x]    |  [x]  |   [x]         |     |
|  | task_timeout      |  [x]   |   [x]    |  [ ]  |   [x]         |     |
|  | delegate_complete |  [x]   |   [ ]    |  [ ]  |   [ ]         |     |
|  | delegate_failed   |  [x]   |   [ ]    |  [ ]  |   [x]         |     |
|  | agent_error       |  [x]   |   [ ]    |  [ ]  |   [x]         |     |
|  | budget_alert      |  [x]   |   [ ]    |  [x]  |   [ ]         |     |
|  | service_offline   |  [x]   |   [x]    |  [x]  |   [x]         |     |
|  | service_online    |  [x]   |   [ ]    |  [ ]  |   [ ]         |     |
|  | approval_needed   |  [x]   |   [x]    |  [ ]  |   [ ]         |     |
|  | memory_alert      |  [x]   |   [ ]    |  [x]  |   [ ]         |     |
|  | server_restart    |  [x]   |   [ ]    |  [ ]  |   [ ]         |     |
|  +----------------------------------------------------------------+     |
|                                                                          |
|                                                    [Save]  [Cancel]     |
+--------------------------------------------------------------------------+
```

### Notification Detail View

```
+------------------------------------------------------+
|  Notification Detail                           [x]   |
+------------------------------------------------------+
|                                                      |
|  [!] TASK FAILED                                     |
|                                                      |
|  Event:     task_failed                              |
|  Severity:  error                                    |
|  Agent:     Reporter                                 |
|  Task:      Nightly Report Generation                |
|  Time:      2026-03-20 03:00:05 UTC                  |
|                                                      |
|  --- Error ---                                       |
|                                                      |
|  Traceback (most recent call last):                  |
|    File "claude_cli.py", line 892, in _run_task      |
|      result = execute_tool(tool_name, args)          |
|    File "claude_cli.py", line 445, in execute_tool   |
|      return handlers[name](**args)                   |
|  KeyError: 'data'                                    |
|                                                      |
|  --- Delivery Status ---                             |
|                                                      |
|  In-App:    delivered  03:00:05                      |
|  Telegram:  delivered  03:00:06                      |
|  Email:     delivered  03:00:08                      |
|  Webhook:   failed (retry 2/3)  next: 03:05:06      |
|                                                      |
|  [Dismiss]  [Re-run Task]  [View Task History]       |
+------------------------------------------------------+
```

---

## Webhook Payload Example

```json
{
  "event": "task_failed",
  "severity": "error",
  "timestamp": "2026-03-20T03:00:05Z",
  "agent": "Reporter",
  "title": "Scheduled Task Failed: Nightly Report Generation",
  "message": "Task 'Nightly Report Generation' failed after 45 seconds with error: KeyError: 'data'",
  "metadata": {
    "task_id": "sched_abc123",
    "schedule": "daily 03:00",
    "duration_seconds": 45,
    "error_type": "KeyError",
    "error_message": "'data'",
    "traceback": "Traceback (most recent call last):\n  File \"claude_cli.py\"..."
  },
  "brain_agent": {
    "version": "1.6.0",
    "instance": "brain.alexklinsky.dev"
  }
}
```

### Slack-Formatted Webhook (Alternative)

```json
{
  "text": "Task Failed: Nightly Report Generation",
  "blocks": [
    {
      "type": "header",
      "text": {"type": "plain_text", "text": "Task Failed"}
    },
    {
      "type": "section",
      "fields": [
        {"type": "mrkdwn", "text": "*Agent:* Reporter"},
        {"type": "mrkdwn", "text": "*Time:* 03:00 UTC"},
        {"type": "mrkdwn", "text": "*Error:* KeyError: 'data'"},
        {"type": "mrkdwn", "text": "*Schedule:* daily 03:00"}
      ]
    }
  ]
}
```

---

## Email Notification Example

```
From: Brain Agent Alerts <alerts@example.com>
To: admin@example.com
Subject: [BRAIN AGENT] Task Failed: Nightly Report Generation

=============================================
  BRAIN AGENT ALERT — TASK FAILED
=============================================

Agent:    Reporter
Task:     Nightly Report Generation
Schedule: daily 03:00
Time:     2026-03-20 03:00:05 UTC
Duration: 45 seconds

ERROR:
  KeyError: 'data'

TRACEBACK:
  File "claude_cli.py", line 892, in _run_task
    result = execute_tool(tool_name, args)
  File "claude_cli.py", line 445, in execute_tool
    return handlers[name](**args)
  KeyError: 'data'

---
View in Brain Agent: http://brain.alexklinsky.dev/#schedule
Manage notifications: http://brain.alexklinsky.dev/#settings
```

---

## TUI Support

```
> /notifications
Recent Notifications (last 24h):

  [!] 03:00  task_failed     Reporter  Nightly Report — KeyError: 'data'
  [!] 02:15  service_offline QMD       Port 8181 connection refused
  [i] 02:16  service_online  QMD       Service recovered
  [i] 00:00  task_complete   main      Daily Memory Summarize

  3 unread | /notifications read | /notifications clear

> /notifications settings
Notification channels:
  in_app:    enabled   min_severity=info
  telegram:  enabled   min_severity=warning   chat_id=123456789
  email:     disabled
  webhook:   enabled   min_severity=warning

> /notifications test telegram
Sending test notification to Telegram... sent!
```

---

## Workflows

### Workflow 1: Scheduled Task Failure Alert

1. Scheduler fires "Nightly Report" task for Reporter agent at 03:00
2. Task fails with `KeyError: 'data'` after 45 seconds
3. Scheduler calls `NotificationManager.notify("task_failed", ...)`
4. NotificationManager checks event routing: in_app, telegram, email, webhook
5. Filters by severity: all channels accept "error" severity
6. Checks quiet hours: 03:00 is in quiet window, but `override_critical` applies to
   errors routed to critical channels — telegram delivers, email queues for morning digest
7. Webhook fires to Slack — team sees alert in #brain-agent-alerts channel
8. In morning, admin opens Web UI, sees bell badge (1), clicks to see details
9. Admin clicks "Re-run Task" to retry manually

### Workflow 2: Approval Request via Telegram

1. Agent encounters a destructive operation (e.g., deleting files) that requires approval
2. Agent calls a hypothetical `request_approval` tool
3. NotificationManager sends `approval_needed` event
4. Telegram channel delivers: "Reporter needs approval: Delete 47 old report files?"
5. Admin replies in Telegram: "/approve" or "/deny"
6. Telegram bot relays decision back to the waiting agent
7. Agent proceeds or aborts based on response

### Workflow 3: Budget Threshold Alert

1. Token tracker in engine accumulates usage per session
2. After each API call, checks against configured threshold (e.g., 1M tokens/day)
3. At 80% threshold: `budget_alert` with severity=warning
4. Routed to in_app and email channels
5. Admin sees badge in Web UI, receives email with usage breakdown
6. Admin can adjust model routing to use cheaper local models

### Workflow 4: Service Recovery Notification

1. Health check thread monitors QMD (port 8181), oMLX (8000), CLIProxyAPI (8317)
2. QMD becomes unreachable — `service_offline` event fires (severity=critical)
3. All channels fire: in-app badge, Telegram push, email, webhook to Slack
4. Memory falls back to file-scan mode (existing behavior)
5. QMD comes back online — `service_online` event fires (severity=info)
6. Only in-app channel fires (per event routing config)
7. Admin sees both events in notification history

---

## Implementation Plan

### Day 1-2: Core Notification Engine

- Create `NotificationManager` class in `claude_cli.py` (or separate `notifications.py`)
- Implement event registration and routing logic
- Severity filtering and quiet hours
- `notifications.db` schema and storage
- `@_db_safe` wrapper for notification DB access

### Day 3: Delivery Channels

- **In-App**: SSE event stream for real-time push to Web UI
- **Telegram**: Reuse existing bot token, send to configured chat_id
- **Email**: SMTP client with TLS, HTML + plain text templates
- **Webhook**: HTTP POST with configurable headers, JSON payload
- Retry worker thread for failed deliveries (exponential backoff)

### Day 4: Event Integration

- Hook into scheduler: task_complete, task_failed, task_timeout
- Hook into delegate: delegate_complete, delegate_failed
- Hook into health check: service_offline, service_online
- Hook into error handling: agent_error
- Add token tracking for budget_alert

### Day 5-6: Web UI

- Notification bell icon with badge count
- Dropdown with recent notifications
- Full notification history modal
- Notification settings tab in Settings dashboard
- Event routing matrix (checkboxes)
- Channel configuration forms with "Send Test" buttons
- SSE listener for real-time badge updates

### Day 7: TUI & Testing

- `/notifications` command with subcommands (list, read, clear, settings, test)
- End-to-end testing of each channel
- Test quiet hours, severity filtering, retry logic
- Test notification fatigue scenarios
- Documentation in tools.md

---

## API Changes

### New Endpoints

| Method | Path                           | Description                        |
|--------|--------------------------------|------------------------------------|
| GET    | `/v1/notifications`            | List notifications (paginated)     |
| POST   | `/v1/notifications/read`       | Mark notification(s) as read       |
| POST   | `/v1/notifications/dismiss`    | Dismiss notification(s)            |
| GET    | `/v1/notifications/unread`     | Get unread count                   |
| GET    | `/v1/notifications/config`     | Get notification settings          |
| POST   | `/v1/notifications/config`     | Save notification settings         |
| POST   | `/v1/notifications/test`       | Send test notification to channel  |
| GET    | `/v1/notifications/stream`     | SSE stream for real-time updates   |

### SSE Event Format (for /v1/notifications/stream)

```
event: notification
data: {"id":"n_abc","event_type":"task_failed","severity":"error","title":"...","unread_count":3}
```

---

## Notification Fatigue Mitigation

| Strategy              | Implementation                                          |
|-----------------------|---------------------------------------------------------|
| Quiet hours           | Suppress non-critical during configured window          |
| Severity filtering    | Per-channel minimum severity level                      |
| Event routing         | Fine-grained control over which events go where         |
| Deduplication         | Same event+agent within 5 minutes = single notification |
| Daily digest          | Batch info-level notifications into morning email       |
| Rate limiting         | Max 10 notifications per channel per hour               |
| Cooldown per event    | After 3 service_offline for same service, 1-hour pause  |
| Snooze                | "Snooze this type for 1h/24h" in notification dropdown  |

---

## Benefits

1. **Visibility into background operations** — no more silent failures
2. **Faster incident response** — alerts reach the right people immediately
3. **Flexible routing** — right channel for right severity
4. **Reduced noise** — quiet hours, severity filters, dedup prevent fatigue
5. **Audit trail** — full notification history with delivery status
6. **Integration-ready** — webhook channel connects to any external system

---

## Risks & Mitigations

| Risk                                | Mitigation                                        |
|-------------------------------------|---------------------------------------------------|
| Notification storm during outage    | Rate limiting, cooldown, dedup                    |
| SMTP credentials in config.json     | Warn about .gitignore, support env vars           |
| Webhook target unreachable          | Retry with exponential backoff, max 3 attempts    |
| SSE connection drops                | Auto-reconnect in Web UI, fetch unread on connect |
| Notifications DB grows indefinitely | Auto-prune notifications older than 30 days       |
| Quiet hours miss critical events    | Override flag for critical severity                |

---

## File Changes Summary

| File              | Changes                                                     |
|-------------------|-------------------------------------------------------------|
| `server.py`       | Notification API endpoints, SSE stream                      |
| `claude_cli.py`   | NotificationManager, event hooks in scheduler/delegates     |
| `web/index.html`  | Bell icon, dropdown, history modal, settings tab            |
| `tui.py`          | /notifications command family                               |
| `telegram.py`     | Notification delivery channel, approval flow                |
| `config.json`     | notifications section (channels, events, quiet hours)       |

---

## Open Questions

1. Should notifications be per-agent or global? (Global with agent field for filtering)
2. Should we support push notifications via Web Push API? (Phase 2 — requires service worker)
3. Should the approval flow block the agent or timeout? (Timeout after 30 min, configurable)
4. Should we support custom notification templates? (Phase 2)
5. Should digest emails include a summary generated by an agent? (Nice idea for Phase 2)
