# Feature Proposal: Discord and Slack Channel Adapters

**Status:** Proposed
**Priority:** Medium
**Effort:** Medium-High (6-8 days)
**Affects:** New discord.py, slack.py, server.py (adapter management), web/index.html (settings), config.json

---

## Problem

Brain Agent currently has three frontends:

1. **Web UI** (web/index.html) — full-featured, browser-based
2. **TUI** (tui.py) — terminal, Rich + prompt_toolkit
3. **Telegram** (telegram.py) — bot, streaming, per-chat sessions

Teams and communities that primarily use **Discord** or **Slack** cannot interact with
Brain Agent from their primary communication tool. They must context-switch to the
Web UI or share a Telegram bot, which creates friction and limits adoption.

Common requests:

- "Can I talk to my Research agent in our #research Discord channel?"
- "We want Brain Agent in Slack threads so the whole team can see responses"
- "Our Discord server already has channels per project — map those to agents"
- "Need approval workflows in Slack before the agent executes destructive tools"

---

## Proposed Solution

Discord bot and Slack app adapters that connect to the existing Brain Agent server API,
following the same architecture pattern as the Telegram adapter (telegram.py):

- In-process threads managed by server.py (same as Telegram)
- Full streaming support with platform-native formatting
- Per-channel agent assignment
- Thread-based conversations with session persistence

---

## Architecture

```text
┌──────────────┐  ┌──────────────┐  ┌──────────────┐
│  Discord Bot │  │  Slack App   │  │  Telegram Bot│
│  discord.py  │  │  slack.py    │  │  telegram.py │
└──────┬───────┘  └──────┬───────┘  └──────┬───────┘
       │                 │                  │
       └─────────────────┼──────────────────┘
                         │  Internal API calls
                         │  (same as telegram.py pattern)
                 ┌───────┴────────┐
                 │   server.py    │
                 │  ┌──────────┐  │
                 │  │ Engine   │  │──▶ LLM Providers
                 │  │ Tools    │  │──▶ QMD Memory
                 │  │ Agents   │  │──▶ MCP Servers
                 │  └──────────┘  │
                 └────────────────┘

Each adapter:
  - Runs as an in-process thread (like Telegram)
  - Calls server.py internal functions directly (no HTTP round-trip)
  - Manages its own session mapping (channel/thread → chat session)
  - Handles platform-specific formatting (Markdown → Discord embeds / Slack blocks)
```

---

## Discord Bot Mockups

### Channel Response with Tool Calls

```text
┌──────────────────────────────────────────────────────────────────────┐
│  #research                                                     ▼ ⚙  │
├──────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  ┌─ alex ──────────────────────────────── Today at 2:34 PM ────────┐│
│  │ @BrainAgent what's the latest on the competitor analysis?        ││
│  └─────────────────────────────────────────────────────────────────┘│
│                                                                      │
│  ┌─ BrainAgent (Researcher) ──────────── Today at 2:34 PM ────────┐│
│  │ Let me check my memory for the competitor analysis...           ││
│  │                                                                  ││
│  │ ┌────────────────────────────────────────────────────────────┐   ││
│  │ │ 🔧 Tool: memory_recall                                    │   ││
│  │ │ Query: "competitor analysis"                               │   ││
│  │ │ Found: 3 results                                          │   ││
│  │ └────────────────────────────────────────────────────────────┘   ││
│  │                                                                  ││
│  │ Based on the analysis stored on March 15:                       ││
│  │                                                                  ││
│  │ **Key Findings:**                                               ││
│  │ - Competitor A launched new API pricing (20% cheaper)           ││
│  │ - Competitor B acquired a vector DB startup                     ││
│  │ - Market trend: consolidation around multimodal models          ││
│  │                                                                  ││
│  │ Want me to do a fresh web search for updates?                   ││
│  │                                                                  ││
│  │ 🤖 Researcher · claude-sonnet-4-6 · 3.2s                       ││
│  └─────────────────────────────────────────────────────────────────┘│
│                                                                      │
│  ┌─ sarah ─────────────────────────────── Today at 2:35 PM ────────┐│
│  │ @BrainAgent yes, search for updates from this week              ││
│  └─────────────────────────────────────────────────────────────────┘│
│                                                                      │
│  ┌─ BrainAgent (Researcher) ──────────── Today at 2:35 PM ────────┐│
│  │ Searching the web for recent competitor updates...              ││
│  │                                                                  ││
│  │ ┌────────────────────────────────────────────────────────────┐   ││
│  │ │ 🔧 Tool: exa_search                                       │   ││
│  │ │ Query: "AI competitor analysis March 2026"                 │   ││
│  │ │ Results: 8 pages found                                    │   ││
│  │ └────────────────────────────────────────────────────────────┘   ││
│  │                                                                  ││
│  │ Here are this week's updates:                                   ││
│  │ 1. **Competitor A** announced enterprise tier...                ││
│  │ ...                                                             ││
│  └─────────────────────────────────────────────────────────────────┘│
│                                                                      │
│  Message #research ─────────────────────────────────────── ▶ Send   │
└──────────────────────────────────────────────────────────────────────┘
```

### Discord Thread Continuation

```text
┌──────────────────────────────────────────────────────────────────────┐
│  Thread: Competitor Analysis Deep Dive                          ✕    │
├──────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  alex: @BrainAgent compare their pricing with ours                  │
│                                                                      │
│  BrainAgent (Researcher):                                           │
│  ┌────────────────────────────────────────────────────────────────┐  │
│  │ 🔧 Tool: read_file                                            │  │
│  │ Path: /app/docs/pricing.md                                    │  │
│  └────────────────────────────────────────────────────────────────┘  │
│                                                                      │
│  | Feature        | Us     | Comp A  | Comp B  |                    │
│  |---------------|--------|---------|---------|                     │
│  | Base plan      | $49/mo | $39/mo  | $59/mo  |                    │
│  | API calls      | 10K    | 8K      | 15K     |                    │
│  | Models         | 5      | 3       | 7       |                    │
│  ...                                                                 │
│                                                                      │
│  alex: store this comparison in memory for the team                 │
│                                                                      │
│  BrainAgent (Researcher):                                           │
│  ┌────────────────────────────────────────────────────────────────┐  │
│  │ 🔧 Tool: memory_store                                         │  │
│  │ Title: "Pricing comparison March 2026"                        │  │
│  │ Status: Stored successfully                                   │  │
│  └────────────────────────────────────────────────────────────────┘  │
│  Done! Stored as "Pricing comparison March 2026" in Researcher's    │
│  memory. Any team member can recall it later.                       │
│                                                                      │
└──────────────────────────────────────────────────────────────────────┘
```

---

## Slack App Mockups

### Thread Response with Block Kit

```text
┌──────────────────────────────────────────────────────────────────────┐
│  #engineering                                                        │
├──────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  alex  2:34 PM                                                      │
│  @BrainAgent check if the deploy pipeline passed for PR #247        │
│                                                                      │
│    BrainAgent (Coder)  2:34 PM                                      │
│    ┌──────────────────────────────────────────────────────────────┐  │
│    │  🔧 execute_command                                          │  │
│    │  `gh pr checks 247 --repo org/app`                          │  │
│    │                                                              │  │
│    │  ✅ build        passed   2m 34s                             │  │
│    │  ✅ test         passed   4m 12s                             │  │
│    │  ✅ lint         passed   0m 45s                             │  │
│    │  ❌ deploy-stg   failed   1m 02s                             │  │
│    └──────────────────────────────────────────────────────────────┘  │
│                                                                      │
│    The staging deploy failed. Let me check the logs...              │
│                                                                      │
│    ┌──────────────────────────────────────────────────────────────┐  │
│    │  🔧 execute_command                                          │  │
│    │  `gh run view 12345 --log-failed`                           │  │
│    │                                                              │  │
│    │  Error: Connection refused to staging DB at stg-db:5432      │  │
│    │  The staging database appears to be down.                    │  │
│    └──────────────────────────────────────────────────────────────┘  │
│                                                                      │
│    **PR #247 status:** 3/4 checks passed. Staging deploy failed    │
│    because the staging database is unreachable. This is likely an  │
│    infrastructure issue, not a code problem.                        │
│                                                                      │
│    *Coder · claude-sonnet-4-6 · 8.1s*                              │
│                                                                      │
│    ┌──────────┐  ┌──────────────┐  ┌──────────────┐                │
│    │ 👍 Helpful│  │ 🔄 Re-check │  │ 📋 Full Logs │                │
│    └──────────┘  └──────────────┘  └──────────────┘                │
│                                                                      │
│  4 replies ──────────────────────────────────────────────────────── │
│                                                                      │
└──────────────────────────────────────────────────────────────────────┘
```

### Slack Interactive Approval (Destructive Tool Guard)

```text
┌──────────────────────────────────────────────────────────────────────┐
│  Thread in #engineering                                              │
├──────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  alex  2:40 PM                                                      │
│  @BrainAgent delete the old staging logs from /var/log/app/staging  │
│                                                                      │
│    BrainAgent (Coder)  2:40 PM                                      │
│    ┌──────────────────────────────────────────────────────────────┐  │
│    │  ⚠️  Approval Required                                       │  │
│    │                                                              │  │
│    │  The agent wants to execute a destructive command:           │  │
│    │                                                              │  │
│    │  `rm -rf /var/log/app/staging/*.log`                        │  │
│    │                                                              │  │
│    │  This will delete 847 files (2.3 GB).                       │  │
│    │                                                              │  │
│    │  ┌───────────┐  ┌────────────┐                              │  │
│    │  │ ✅ Approve │  │ ❌ Deny    │                              │  │
│    │  └───────────┘  └────────────┘                              │  │
│    └──────────────────────────────────────────────────────────────┘  │
│                                                                      │
│    alex clicked ✅ Approve                                          │
│                                                                      │
│    BrainAgent (Coder)  2:41 PM                                      │
│    Done. Deleted 847 log files from staging (freed 2.3 GB).         │
│                                                                      │
└──────────────────────────────────────────────────────────────────────┘
```

---

## Configuration

### config.json

```text
┌──────────────────────────────────────────────────────────────────────┐
│  config.json                                                          │
├──────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  {                                                                   │
│    "server": { "host": "0.0.0.0", "port": 8420 },                   │
│    "providers": { ... },                                             │
│                                                                      │
│    "telegram": {                                                     │
│      "bot_token": "123456:ABC...",                                   │
│      "allowed_users": [12345678]                                     │
│    },                                                                │
│                                                                      │
│    "discord": {                                                      │
│      "bot_token": "MTIz...abc",                                      │
│      "application_id": "1234567890",                                 │
│      "allowed_guilds": [9876543210],                                 │
│      "channel_agents": {                                             │
│        "1111111111": "Researcher",                                   │
│        "2222222222": "Coder",                                        │
│        "3333333333": "main"                                          │
│      },                                                              │
│      "show_tool_calls": true,                                        │
│      "thread_mode": "always",                                        │
│      "max_response_length": 4000                                     │
│    },                                                                │
│                                                                      │
│    "slack": {                                                        │
│      "bot_token": "xoxb-...",                                        │
│      "app_token": "xapp-...",                                        │
│      "signing_secret": "abc123...",                                  │
│      "allowed_workspaces": ["T01234567"],                            │
│      "channel_agents": {                                             │
│        "C01111111": "Researcher",                                    │
│        "C02222222": "Coder"                                          │
│      },                                                              │
│      "show_tool_calls": true,                                        │
│      "thread_mode": "always",                                        │
│      "approval_channels": ["C02222222"],                             │
│      "max_response_length": 3000                                     │
│    }                                                                 │
│  }                                                                   │
│                                                                      │
└──────────────────────────────────────────────────────────────────────┘
```

### Configuration Options

| Option               | Discord                  | Slack                    |
|---------------------|--------------------------|--------------------------|
| Bot token            | `bot_token`              | `bot_token` (xoxb)       |
| App token            | N/A                      | `app_token` (xapp, Socket Mode) |
| Auth                 | `allowed_guilds` (server IDs) | `allowed_workspaces` (team IDs) |
| Channel mapping      | `channel_agents`         | `channel_agents`         |
| Tool call display    | `show_tool_calls`        | `show_tool_calls`        |
| Thread mode          | `always` / `mention_only` | `always` / `mention_only` |
| Approval workflow    | N/A (future)             | `approval_channels`      |
| Response length      | 4000 chars (Discord limit) | 3000 chars (Slack limit) |

---

## Web UI: Settings Tab

```text
┌──────────────────────────────────────────────────────────────────────┐
│  Settings                                                            │
├────────┬────────┬─────────┬──────────┬──────────┬─────────┬─────────┤
│ Server │  QMD   │ Models  │ Telegram │ Discord  │  Slack  │Providers│
├────────┴────────┴─────────┴──────────┴──────────┴─────────┴─────────┤
│                                                                      │
│  Discord Bot                                           [● Running]  │
│  ─────────────────────────────────────────────────────────────────── │
│                                                                      │
│  Bot Token:     [MTIz...abc                                ] [Test] │
│  App ID:        [1234567890                                ]        │
│                                                                      │
│  Allowed Guilds:                                                     │
│  ┌──────────────────────────────────────┐                            │
│  │ 9876543210 — "Brain Agent Dev"  [✕]  │                            │
│  │ [+ Add Guild]                        │                            │
│  └──────────────────────────────────────┘                            │
│                                                                      │
│  Channel → Agent Mapping:                                            │
│  ┌──────────────────────────────────────────────────────────────┐    │
│  │ #research (1111111111)  →  [Researcher ▾]             [✕]   │    │
│  │ #coding   (2222222222)  →  [Coder      ▾]             [✕]   │    │
│  │ #general  (3333333333)  →  [main       ▾]             [✕]   │    │
│  │ [+ Add Channel Mapping]                                      │    │
│  └──────────────────────────────────────────────────────────────┘    │
│                                                                      │
│  Options:                                                            │
│  [✓] Show tool calls as embeds                                      │
│  [✓] Always reply in threads                                        │
│  [ ] Require @mention to respond                                    │
│                                                                      │
│  Status: Connected to 1 guild, 3 channels mapped                    │
│  Uptime: 4h 23m | Messages handled: 47                              │
│                                                                      │
│  [Stop Bot]  [Restart Bot]                                           │
│                                                                      │
│──────────────────────────────────────────────────────────────────────│
│                                                                      │
│  ℹ Create a Discord bot at https://discord.com/developers            │
│    Required intents: Message Content, Guild Messages                 │
│    Required permissions: Send Messages, Embed Links, Read History    │
│    Invite URL: https://discord.com/api/oauth2/authorize?client_id=.. │
│                                                                      │
└──────────────────────────────────────────────────────────────────────┘
```

---

## Workflow: Setting Up Discord Bot

### Step 1: Create Discord Application

1. Go to https://discord.com/developers/applications
2. Click "New Application", name it "Brain Agent"
3. Go to Bot tab, click "Add Bot"
4. Copy the bot token
5. Enable "Message Content Intent" under Privileged Gateway Intents

### Step 2: Configure Brain Agent

Option A — Edit config.json directly:
```text
"discord": {
  "bot_token": "MTIz...abc",
  "application_id": "1234567890",
  "allowed_guilds": [9876543210]
}
```

Option B — Use Web UI Settings > Discord tab (paste token, click Save)

### Step 3: Invite Bot to Server

Use the invite URL generated in Settings > Discord, or construct manually:
```text
https://discord.com/api/oauth2/authorize?client_id=APP_ID&permissions=274877910016&scope=bot
```

### Step 4: Map Channels to Agents

In Web UI Settings > Discord, or in config.json:
```text
"channel_agents": {
  "CHANNEL_ID": "AgentName"
}
```

Unmapped channels use the `main` agent by default.

### Step 5: Start Using

```text
User in #research:  @BrainAgent what do you know about quantum computing?
BrainAgent:         [Researcher responds with memory-backed answer in a thread]
```

---

## Comparison with Telegram Adapter

The Discord and Slack adapters should reuse patterns from telegram.py:

| Pattern                     | Telegram (existing)        | Discord / Slack (new)      |
|----------------------------|----------------------------|----------------------------|
| Process model               | In-process thread          | In-process thread          |
| Start/stop                  | Server manages lifecycle   | Server manages lifecycle   |
| Session mapping             | chat_id → session          | channel+thread → session   |
| Streaming                   | Edit message chunks        | Edit message / blocks      |
| Formatting                  | HTML subset                | Markdown / Block Kit       |
| Tool call display           | Inline text                | Embeds / Blocks            |
| Auth                        | `allowed_users` list       | Guild/workspace allowlist  |
| Config location             | `config.json.telegram`     | `config.json.discord/slack`|
| Server API                  | Direct internal calls      | Direct internal calls      |
| Multiple agents             | Single bot, switch agents  | Per-channel agent mapping  |

### Code Reuse Opportunities

- Session management logic (create, resume, map external ID to internal session)
- Streaming response accumulation (buffer chunks, update message periodically)
- Tool call formatting (extract tool name + args, format as text block)
- Error handling (provider errors, timeout, cancellation)
- Config validation and hot-reload

Estimated code reuse: ~40% of telegram.py logic can be extracted into a shared
`adapter_base.py` module used by all three adapters.

---

## Multi-Agent in Channels

### Per-Channel Assignment

Each Discord/Slack channel maps to one agent. This enables team workflows:

```text
#research     →  Researcher agent   (memory_recall, exa_search, web_fetch)
#engineering  →  Coder agent        (execute_command, read_file, edit_file)
#general      →  main agent         (delegates to specialists)
#reports      →  Reporter agent     (gmail_send, memory_shared)
```

### Cross-Agent Delegation

When a user asks a question that requires a different agent's expertise:

```text
User in #research:  @BrainAgent can you also check the code for that bug?

BrainAgent (Researcher):
  I'll delegate this to the Coder agent...

  ┌──────────────────────────────────────────────────────┐
  │ 🔧 delegate_task                                     │
  │ Agent: Coder                                         │
  │ Task: "Check codebase for bug related to..."         │
  │ Status: completed (12.3s)                            │
  └──────────────────────────────────────────────────────┘

  The Coder agent found the issue in server.py line 342...
```

### Direct Agent Mention (Future Enhancement)

```text
@BrainAgent:Coder review the PR     →  routes directly to Coder agent
@BrainAgent:Researcher find papers  →  routes directly to Researcher agent
@BrainAgent summarize the thread    →  routes to channel's default agent
```

---

## Platform-Specific Formatting

### Discord

- Native Markdown support (bold, italic, code blocks, headers)
- Embeds for tool calls (colored sidebar, fields for tool name/args/result)
- 4000 character limit per message (split long responses across messages)
- File attachments for large outputs (code files, reports)
- Reactions for feedback (thumbs up/down on responses)

### Slack

- Block Kit for structured layouts (sections, code blocks, dividers)
- Interactive components (buttons for approve/deny, dropdowns for agent select)
- 3000 character limit per block (use multiple blocks for long responses)
- Thread-native (all responses in threads to keep channels clean)
- Slack Connect support (cross-organization channels)

---

## Server API Changes

### New Endpoints

| Method | Path                      | Description                      |
|--------|--------------------------|----------------------------------|
| GET    | `/v1/discord/status`      | Discord bot connection status    |
| POST   | `/v1/discord/start`       | Start Discord bot thread         |
| POST   | `/v1/discord/stop`        | Stop Discord bot thread          |
| GET    | `/v1/slack/status`        | Slack app connection status      |
| POST   | `/v1/slack/start`         | Start Slack app thread           |
| POST   | `/v1/slack/stop`          | Stop Slack app thread            |

These mirror the existing Telegram endpoints pattern.

### server.py Changes

```text
# In server.py, alongside existing Telegram management:

_discord_thread = None
_slack_thread = None

def _start_discord():
    """Start Discord bot as in-process thread (same pattern as Telegram)."""
    global _discord_thread
    from discord_adapter import DiscordAdapter
    adapter = DiscordAdapter(config["discord"], _engine)
    _discord_thread = threading.Thread(target=adapter.run, daemon=True)
    _discord_thread.start()

def _start_slack():
    """Start Slack app as in-process thread (same pattern as Telegram)."""
    global _slack_thread
    from slack_adapter import SlackAdapter
    adapter = SlackAdapter(config["slack"], _engine)
    _slack_thread = threading.Thread(target=adapter.run, daemon=True)
    _slack_thread.start()
```

---

## Dependencies

### Discord

```text
discord.py >= 2.3.0    # Discord API wrapper (async, gateway + REST)
                        # ~15MB installed, pure Python
```

### Slack

```text
slack-bolt >= 1.18.0   # Slack Bolt framework (Socket Mode + events)
slack-sdk >= 3.27.0    # Slack API client
                        # ~10MB installed, pure Python
```

Both are lightweight, well-maintained libraries with no heavy native dependencies.

---

## Benefits

- **Meet users where they are**: Teams using Discord/Slack don't need to context-switch
- **Team visibility**: Everyone in a channel sees agent interactions, builds shared context
- **Per-channel specialization**: Map channels to agents for focused workflows
- **Familiar UX**: Users interact with Brain Agent like any other bot in their platform
- **Low friction**: No new app to install, no new URL to bookmark — just @mention in chat
- **Audit trail**: All interactions visible in channel history

## Trade-offs

- **Two new dependencies**: discord.py and slack-bolt/slack-sdk added to requirements
- **Platform API limits**: Message length limits, rate limits, formatting constraints
- **Streaming UX**: Message editing for streaming feels less smooth than Web UI SSE
- **No rich UI**: Can't match Web UI features (agent cards, settings modal, skill browser)
  - Mitigation: Use Discord/Slack for chat, Web UI for configuration
- **Auth complexity**: Managing bot tokens, permissions, OAuth for two more platforms
- **Maintenance burden**: Platform APIs evolve — need to track breaking changes

## Effort Estimate

| Component                    | Effort  |
|-----------------------------|---------|
| Shared adapter base module   | 1 day   |
| Discord adapter (discord.py) | 2 days  |
| Slack adapter (slack.py)     | 2 days  |
| Server integration + API     | 0.5 day |
| Web UI settings tabs         | 1 day   |
| Per-channel agent mapping    | 0.5 day |
| Slack approval workflow      | 0.5 day |
| Testing + edge cases         | 1 day   |
| **Total**                   | **8.5 days** |

---

## Future Extensions

- **Slash commands**: `/brain ask <question>`, `/brain agent <name>`, `/brain schedule`
- **Reactions as commands**: React with a specific emoji to trigger agent actions
- **Voice channel support**: Discord voice → speech-to-text → Brain Agent → text-to-speech
- **Slack workflows**: Integrate with Slack Workflow Builder for complex approval chains
- **Matrix/Mattermost**: Additional open-source platform adapters using the same base
- **Unified adapter framework**: Abstract adapter interface for community-contributed platforms
