# Multi-Messaging Frontend Framework

**Status:** Proposal
**Author:** Claude Code
**Date:** 2026-03-20

---

## Problem

Brain Agent currently supports a single Telegram bot instance as its only messaging
frontend. This creates several limitations:

1. **Single bot only** -- There is no way to run multiple Telegram bots (e.g., one
   per agent team, one for personal use and one for a shared workspace).

2. **Platform lock-in** -- No support for WhatsApp, Signal, Discord, Slack, Matrix,
   IRC, or any other messaging platform.

3. **Static configuration** -- The Telegram bot is configured once in `config.json`
   under a flat `"telegram"` key. Adding a second bot requires code changes.

4. **No dynamic management** -- The Web UI has a simple Telegram tab with start/stop
   controls. There is no way to add, remove, or configure messaging channels from the UI.

5. **No per-channel routing** -- All messages go to a single default agent. There is
   no way to map specific channels, groups, or platforms to different agents or teams.

---

## Current Telegram Architecture Analysis

### How It Works Today

The Telegram integration lives in three layers:

**1. `telegram.py` -- Bot + Polling + Message Handling**

- `TelegramBot` class: minimal Telegram Bot API client using stdlib `urllib`.
  Makes HTTP calls to `api.telegram.org/bot<token>/<method>`. Handles long
  polling via `getUpdates`, exponential backoff on errors, self-healing reconnect
  after 5 consecutive failures.

- `ChatManager` class: maps Telegram `chat_id` to Brain Agent sessions. Creates
  a `BrainAgentClient` per chat, tracks per-chat agent/model selection. Handles
  `/agent`, `/model`, `/new` commands to switch context.

- `handle_message()`: the core message flow:
  1. Gets or creates a session for the chat via `ChatManager.get_session()`
  2. Creates a `BrainAgentClient` pointed at the server
  3. Sends a placeholder "thinking" message
  4. Spawns a background thread to send typing indicators every 4s
  5. Iterates SSE events from `chat_client.chat(text)`:
     - `text_delta` -- accumulates streaming text, edits the placeholder message
       every 1s with `md_to_telegram_html()` conversion + cursor
     - `tool_call` -- counts tools, collects names
     - `done` -- captures final text, token count, model name
     - `error` -- captures error message
  6. Formats a footer with model icon, elapsed time, token count, tool summary
  7. Final edit of the placeholder message with complete HTML response

- `TelegramService` class: singleton that manages the polling loop in a daemon
  thread. Exposes `start()`, `stop()`, `running`, `bot_username`, `error`.
  On start: validates token via `getMe`, creates `ChatManager`, skips stale
  messages, enters poll loop.

- `md_to_telegram_html()`: converts LLM markdown to Telegram HTML subset
  (code blocks, inline code, bold, italic, strikethrough, links, headers).

**2. `server.py` -- Lifecycle Management**

- Imports `telegram.py` as `_telegram_mod` at module level.
- `_start_telegram_service()`: reads `config.json`, extracts `telegram.bot_token`,
  `telegram.allowed_users`, `telegram.model`, builds server URL, calls
  `telegram_service.start()`.
- `_set_telegram_enabled()`: persists `telegram.enabled` to `config.json` and
  updates in-memory `server_config["telegram_enabled"]`.
- Auto-start: if `telegram_enabled` is true, spawns a 1-second delayed thread
  to call `_start_telegram_service()` after the HTTP server is ready.
- Shutdown: calls `telegram_service.stop()` in the `finally` block.
- API: `POST /v1/services/telegram` accepts actions: start, stop, restart,
  enable, disable.
- Status: `GET /v1/services` returns `telegram.status`, `telegram.bot`,
  `telegram.enabled`.

**3. `web/index.html` -- Settings Tab**

- "Telegram" tab under Settings shows: running/stopped status, bot username,
  start/stop/restart buttons, auto-start toggle (enable/disable).
- Calls `POST /v1/services/telegram` with action payload.

### What Is Reusable vs Telegram-Specific

| Component                | Reusable              | Telegram-Specific          |
|--------------------------|-----------------------|----------------------------|
| `BrainAgentClient`       | Fully reusable        | --                         |
| `ChatManager` pattern    | Session management    | `chat_id` type, no media   |
| `handle_message()` flow  | SSE iteration pattern | Telegram edit-in-place     |
| `handle_command()` flow  | Command routing logic | Telegram `/command` format  |
| `TelegramService` shape  | Thread lifecycle mgmt | Token validation, polling   |
| `md_to_telegram_html()`  | --                    | Entirely Telegram-specific |
| `model_icon()`           | Fully reusable        | --                         |
| `TelegramBot._call()`    | --                    | Telegram Bot API transport |
| Exponential backoff      | Pattern reusable      | Implementation coupled     |
| Typing indicator thread  | Pattern reusable      | `sendChatAction` specific  |

---

## Proposed Solution

### Core Concept

Replace the single `TelegramService` singleton with a **Channel Manager** that
can run multiple **channel instances**, each backed by a **messaging adapter**.
A channel is a configured instance of an adapter (e.g., "Research Telegram Bot"
is a channel using the Telegram adapter with a specific bot token).

### Architecture

```
server.py
  |
  +-- ChannelManager
  |     |
  |     +-- Channel "personal-telegram"
  |     |     +-- TelegramAdapter (token=AAA, allowed_users=[123])
  |     |     +-- agent_routing: {"default": "main"}
  |     |
  |     +-- Channel "research-discord"
  |     |     +-- DiscordAdapter (token=BBB, guild=789)
  |     |     +-- agent_routing: {"default": "Researcher", "#general": "main"}
  |     |
  |     +-- Channel "team-slack"
  |           +-- SlackAdapter (token=CCC, app_token=DDD)
  |           +-- agent_routing: {"default": "main", "#research": "Researcher"}
  |
  +-- BrainAgentClient (shared)
  +-- claude_cli.py (engine)
```

### Base Adapter Interface

```python
# channels/base.py

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable
import threading


@dataclass
class IncomingMessage:
    """Platform-agnostic incoming message."""
    channel_id: str          # Which channel instance received this
    platform_chat_id: str    # Platform-specific chat/channel/room ID
    platform_user_id: str    # Platform-specific user ID
    user_display_name: str   # Human-readable name
    text: str                # Message text content
    is_command: bool         # Whether this is a /command
    is_group: bool           # Whether this is a group chat
    is_direct: bool          # Whether this is a DM
    reply_to_message_id: str | None = None
    attachments: list[dict] = field(default_factory=list)
    raw: dict = field(default_factory=dict)  # Platform-specific raw data


@dataclass
class OutgoingMessage:
    """Platform-agnostic outgoing message."""
    text: str                          # Markdown text from LLM
    platform_chat_id: str              # Where to send
    reply_to_message_id: str | None = None
    edit_message_id: str | None = None # Edit existing message (streaming)
    footer: dict | None = None         # {model, elapsed, tokens, tools}
    is_error: bool = False
    is_placeholder: bool = False


class BaseAdapter(ABC):
    """Base class for all messaging platform adapters."""

    adapter_type: str = "base"    # Override in subclasses

    def __init__(self, config: dict):
        self.config = config
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._on_message: Callable[[IncomingMessage], None] | None = None
        self._error: str = ""

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def error(self) -> str:
        return self._error

    @abstractmethod
    def validate_config(self) -> tuple[bool, str]:
        """Validate adapter config. Returns (ok, error_message)."""
        ...

    @abstractmethod
    def connect(self) -> tuple[bool, str]:
        """Connect to the platform. Returns (ok, error_or_identity)."""
        ...

    @abstractmethod
    def disconnect(self):
        """Disconnect from the platform."""
        ...

    @abstractmethod
    def send(self, message: OutgoingMessage) -> str | None:
        """Send a message. Returns message_id for later editing."""
        ...

    @abstractmethod
    def edit(self, message: OutgoingMessage):
        """Edit a previously sent message (for streaming updates)."""
        ...

    @abstractmethod
    def send_typing(self, platform_chat_id: str):
        """Send a typing/activity indicator."""
        ...

    @abstractmethod
    def format_response(self, text: str, footer: dict | None = None) -> str:
        """Convert LLM markdown to platform-specific format."""
        ...

    @abstractmethod
    def poll_loop(self):
        """Main event loop. Must check self._stop_event periodically."""
        ...

    def start(self, on_message: Callable[[IncomingMessage], None]) -> bool:
        self._on_message = on_message
        self._stop_event.clear()
        self._error = ""
        ok, result = self.validate_config()
        if not ok:
            self._error = result
            return False
        ok, result = self.connect()
        if not ok:
            self._error = result
            return False
        self._thread = threading.Thread(
            target=self.poll_loop, daemon=True,
            name=f"channel-{self.adapter_type}",
        )
        self._thread.start()
        return True

    def stop(self):
        self._stop_event.set()
        self.disconnect()
        if self._thread:
            self._thread.join(timeout=35)
            self._thread = None

    def platform_capabilities(self) -> dict:
        """What this platform supports."""
        return {
            "edit_messages": False,
            "typing_indicator": False,
            "rich_formatting": False,
            "embeds": False,
            "file_upload": False,
            "reactions": False,
            "threads": False,
            "max_message_length": 4096,
        }
```

### Channel Manager

```python
# channels/manager.py

class ChannelManager:
    """Manages multiple messaging channel instances."""

    def __init__(self, server_url: str):
        self.server_url = server_url
        self.channels: dict[str, Channel] = {}   # channel_id -> Channel
        self._adapters: dict[str, type] = {}      # adapter_type -> class
        self._register_builtin_adapters()

    def _register_builtin_adapters(self):
        from channels.telegram_adapter import TelegramAdapter
        self._adapters["telegram"] = TelegramAdapter
        # Future: discord, slack, matrix, irc, whatsapp, signal

    def register_adapter(self, adapter_type: str, cls: type):
        """Register a custom adapter type."""
        self._adapters[adapter_type] = cls

    def create_channel(self, channel_id: str, config: dict) -> Channel:
        """Create and register a channel from config."""
        adapter_type = config["type"]
        if adapter_type not in self._adapters:
            raise ValueError(f"Unknown adapter type: {adapter_type}")

        adapter_cls = self._adapters[adapter_type]
        adapter = adapter_cls(config.get("credentials", {}))

        channel = Channel(
            channel_id=channel_id,
            adapter=adapter,
            config=config,
            server_url=self.server_url,
        )
        self.channels[channel_id] = channel
        return channel

    def start_channel(self, channel_id: str) -> bool:
        channel = self.channels.get(channel_id)
        if not channel:
            return False
        return channel.start()

    def stop_channel(self, channel_id: str):
        channel = self.channels.get(channel_id)
        if channel:
            channel.stop()

    def remove_channel(self, channel_id: str):
        self.stop_channel(channel_id)
        self.channels.pop(channel_id, None)

    def start_all_enabled(self):
        for cid, channel in self.channels.items():
            if channel.config.get("enabled", True):
                channel.start()

    def stop_all(self):
        for channel in self.channels.values():
            channel.stop()

    def status(self) -> list[dict]:
        return [ch.status() for ch in self.channels.values()]


class Channel:
    """A configured instance of a messaging adapter."""

    def __init__(self, channel_id: str, adapter: BaseAdapter,
                 config: dict, server_url: str):
        self.channel_id = channel_id
        self.adapter = adapter
        self.config = config
        self.server_url = server_url
        self.sessions: dict[str, str] = {}   # platform_chat_id -> session_id
        self.stats = {"messages_in": 0, "messages_out": 0, "errors": 0}
        self._started_at: float | None = None

    @property
    def agent_routing(self) -> dict:
        return self.config.get("agent_routing", {"default": "main"})

    def resolve_agent(self, msg: IncomingMessage) -> str:
        routing = self.agent_routing
        # Check specific chat/channel mapping first
        agent = routing.get(msg.platform_chat_id)
        if agent:
            return agent
        # Check user-specific mapping
        agent = routing.get(f"user:{msg.platform_user_id}")
        if agent:
            return agent
        return routing.get("default", "main")

    def start(self) -> bool:
        ok = self.adapter.start(on_message=self._on_message)
        if ok:
            self._started_at = time.time()
        return ok

    def stop(self):
        self.adapter.stop()
        self._started_at = None

    def _on_message(self, msg: IncomingMessage):
        """Callback from adapter when a message arrives."""
        self.stats["messages_in"] += 1
        # Check auth
        allowed = self.config.get("allowed_users")
        if allowed and msg.platform_user_id not in [str(u) for u in allowed]:
            self.adapter.send(OutgoingMessage(
                text="Not authorized.", platform_chat_id=msg.platform_chat_id,
                is_error=True,
            ))
            return
        if msg.is_command:
            self._handle_command(msg)
        else:
            self._handle_chat(msg)

    def _handle_chat(self, msg: IncomingMessage):
        """Route message to Brain Agent server and stream response back."""
        agent = self.resolve_agent(msg)
        # ... session management, SSE streaming, formatting ...
        # (Same pattern as current handle_message, but adapter-agnostic)

    def status(self) -> dict:
        return {
            "channel_id": self.channel_id,
            "type": self.adapter.adapter_type,
            "name": self.config.get("name", self.channel_id),
            "running": self.adapter.running,
            "enabled": self.config.get("enabled", True),
            "error": self.adapter.error,
            "agent_routing": self.agent_routing,
            "stats": self.stats,
            "uptime": int(time.time() - self._started_at)
                     if self._started_at else 0,
        }
```

### Telegram Adapter (Refactored)

```python
# channels/telegram_adapter.py

class TelegramAdapter(BaseAdapter):
    adapter_type = "telegram"

    def __init__(self, config: dict):
        super().__init__(config)
        self.token = config.get("bot_token", "")
        self.bot: TelegramBot | None = None  # Reuse existing TelegramBot class
        self.bot_username: str = ""

    def validate_config(self) -> tuple[bool, str]:
        if not self.token:
            return False, "Missing bot_token"
        return True, ""

    def connect(self) -> tuple[bool, str]:
        self.bot = TelegramBot(self.token)
        try:
            me = self.bot._call("getMe")
            self.bot_username = me.get("result", {}).get("username", "?")
            return True, self.bot_username
        except Exception as e:
            return False, f"Invalid token: {e}"

    def disconnect(self):
        self.bot = None

    def send(self, message: OutgoingMessage) -> str | None:
        if not self.bot:
            return None
        text = self.format_response(message.text, message.footer)
        mid = self.bot.send_message(
            int(message.platform_chat_id), text, as_html=True,
        )
        return str(mid) if mid else None

    def edit(self, message: OutgoingMessage):
        if not self.bot or not message.edit_message_id:
            return
        text = self.format_response(message.text, message.footer)
        self.bot.edit_message(
            int(message.platform_chat_id),
            int(message.edit_message_id),
            text, as_html=True,
        )

    def send_typing(self, platform_chat_id: str):
        if self.bot:
            self.bot.send_action(int(platform_chat_id))

    def format_response(self, text: str, footer: dict | None = None) -> str:
        html = md_to_telegram_html(text)   # Reuse existing function
        if footer:
            html += _build_telegram_footer(footer)
        return html

    def poll_loop(self):
        # Skip old messages, then poll -- same as current TelegramService._poll_loop
        ...

    def platform_capabilities(self) -> dict:
        return {
            "edit_messages": True,
            "typing_indicator": True,
            "rich_formatting": True,     # HTML subset
            "embeds": False,
            "file_upload": True,
            "reactions": True,
            "threads": False,
            "max_message_length": 4096,
        }
```

### Configuration Schema

```json
{
  "providers": { "...": "..." },
  "channels": [
    {
      "id": "personal-tg",
      "type": "telegram",
      "name": "Personal Telegram",
      "enabled": true,
      "credentials": {
        "bot_token": "123456:ABC-DEF..."
      },
      "allowed_users": [112233],
      "agent_routing": {
        "default": "main"
      },
      "default_model": "claude-sonnet-4-6"
    },
    {
      "id": "research-tg",
      "type": "telegram",
      "name": "Research Team Bot",
      "enabled": true,
      "credentials": {
        "bot_token": "789012:GHI-JKL..."
      },
      "allowed_users": [112233, 445566],
      "agent_routing": {
        "default": "Researcher",
        "user:445566": "crow"
      },
      "default_model": "claude-opus-4-6"
    },
    {
      "id": "team-discord",
      "type": "discord",
      "name": "Team Discord",
      "enabled": false,
      "credentials": {
        "bot_token": "DISCORD_TOKEN_HERE"
      },
      "allowed_users": [],
      "agent_routing": {
        "default": "main",
        "1234567890": "Researcher",
        "0987654321": "crow"
      },
      "guild_id": "111222333444"
    }
  ]
}
```

**Migration path:** On first load, if the old `"telegram"` key exists and no
`"channels"` key is present, auto-migrate:

```python
def migrate_telegram_config(config: dict) -> dict:
    if "telegram" in config and "channels" not in config:
        tg = config.pop("telegram")
        config["channels"] = [{
            "id": "telegram",
            "type": "telegram",
            "name": "Telegram",
            "enabled": tg.get("enabled", True),
            "credentials": {"bot_token": tg.get("bot_token", "")},
            "allowed_users": tg.get("allowed_users", []),
            "agent_routing": {"default": "main"},
            "default_model": tg.get("model", ""),
        }]
    return config
```

### Server API Changes

New endpoints replace the current `/v1/services/telegram`:

```
GET  /v1/channels              -- List all channels with status
POST /v1/channels              -- Create a new channel
GET  /v1/channels/:id          -- Get channel detail + stats
POST /v1/channels/:id          -- Update channel config
DELETE /v1/channels/:id        -- Remove channel
POST /v1/channels/:id/start    -- Start channel
POST /v1/channels/:id/stop     -- Stop channel
POST /v1/channels/:id/restart  -- Restart channel
```

The existing `GET /v1/services` response changes from:

```json
{ "telegram": { "status": "running", "bot": "@mybot", "enabled": true } }
```

to:

```json
{
  "channels": [
    { "channel_id": "personal-tg", "type": "telegram", "name": "Personal Telegram",
      "running": true, "enabled": true, "identity": "@mybot",
      "agent_routing": {"default": "main"}, "stats": {"messages_in": 42} },
    { "channel_id": "team-discord", "type": "discord", "name": "Team Discord",
      "running": false, "enabled": false }
  ]
}
```

Backward compatibility: keep `GET /v1/services` returning a `telegram` key
derived from the first Telegram channel (if any) for one release cycle.

---

## Web UI Mockups

### Channels Settings Tab (replaces Telegram tab)

```
+------------------------------------------------------------------------+
|  Settings                                                              |
|  [Server] [QMD] [Channels] [Teams] [Providers]                        |
+------------------------------------------------------------------------+
|                                                                        |
|  Messaging Channels                                   [+ Add Channel]  |
|                                                                        |
|  +------------------------------------------------------------------+  |
|  |  @ Personal Telegram                      RUNNING    [Stop] [>>] |  |
|  |  telegram  |  Agent: main  |  42 msgs  |  Uptime: 3h 22m        |  |
|  +------------------------------------------------------------------+  |
|                                                                        |
|  +------------------------------------------------------------------+  |
|  |  @ Research Team Bot                      RUNNING    [Stop] [>>] |  |
|  |  telegram  |  Agent: Researcher  |  18 msgs  |  Uptime: 3h 22m  |  |
|  +------------------------------------------------------------------+  |
|                                                                        |
|  +------------------------------------------------------------------+  |
|  |  # Team Discord                           STOPPED   [Start] [>>] |  |
|  |  discord  |  Agent: main  |  0 msgs  |  --                       |  |
|  +------------------------------------------------------------------+  |
|                                                                        |
|  +------------------------------------------------------------------+  |
|  |  # Engineering Slack                      RUNNING    [Stop] [>>] |  |
|  |  slack  |  Agent: Coder  |  127 msgs  |  Uptime: 1d 5h          |  |
|  +------------------------------------------------------------------+  |
|                                                                        |
+------------------------------------------------------------------------+
```

### Add Channel Dialog

```
+----------------------------------------------+
|  Add Messaging Channel                    [X] |
+----------------------------------------------+
|                                                |
|  Channel Name:  [________________________]     |
|                                                |
|  Adapter Type:  [ Telegram           v ]       |
|                 +-----------------------+      |
|                 | Telegram              |      |
|                 | Discord               |      |
|                 | Slack                  |      |
|                 | Matrix                |      |
|                 | IRC                   |      |
|                 +-----------------------+      |
|                                                |
|  --- Credentials ---                           |
|  Bot Token:     [________________________]     |
|                                                |
|  --- Routing ---                               |
|  Default Agent: [ main               v ]       |
|  Default Model: [ claude-sonnet-4-6  v ]       |
|                                                |
|  --- Access Control ---                        |
|  Allowed Users: [________________________]     |
|  (comma-separated user IDs, blank = all)       |
|                                                |
|  [x] Enable on save                           |
|                                                |
|                    [Cancel]  [Create Channel]  |
+----------------------------------------------+
```

### Channel Detail View (click [>>] arrow)

```
+------------------------------------------------------------------------+
|  < Back to Channels            Personal Telegram              [Delete] |
+------------------------------------------------------------------------+
|                                                                        |
|  Status: RUNNING        Identity: @brain_agent_bot                     |
|  Uptime: 3h 22m         Started: 2026-03-20 10:15:00                  |
|                                                                        |
|  [Stop]  [Restart]                                                     |
|                                                                        |
|  --- Statistics ---                                                    |
|  Messages In:    42          Messages Out:   38                        |
|  Errors:         2           Active Chats:   3                         |
|                                                                        |
|  --- Agent Routing ---                                                 |
|  Default Agent:  main                                                  |
|  Default Model:  claude-sonnet-4-6                                     |
|  +----------------------------------------------+                     |
|  | Chat/User              | Agent              |  [+ Add Rule]        |
|  |------------------------|--------------------|                      |
|  | user:112233            | main               |  [x]                 |
|  | user:445566            | Researcher         |  [x]                 |
|  +----------------------------------------------+                     |
|                                                                        |
|  --- Access Control ---                                                |
|  Allowed Users: 112233, 445566                    [Edit]               |
|                                                                        |
|  --- Credentials ---                                                   |
|  Bot Token: 123456:ABC-D...    (masked)           [Edit]               |
|                                                                        |
|  [x] Auto-start on server boot                                        |
|                                                                        |
+------------------------------------------------------------------------+
```

---

## Workflows

### 1. Admin Adds a Second Telegram Bot for the Research Team

1. Admin opens Web UI, navigates to Settings > Channels.
2. Clicks [+ Add Channel].
3. Selects adapter type "Telegram".
4. Enters name "Research Team Bot", pastes the new bot token.
5. Sets Default Agent to "Researcher".
6. Adds allowed user IDs for the research team.
7. Clicks [Create Channel].
8. The ChannelManager validates the token via `getMe`, registers the channel.
9. Channel starts automatically (enabled by default).
10. Both Telegram bots now run independently in separate threads.
11. Config is persisted to `config.json` under `channels[]`.

### 2. Admin Adds a Discord Adapter Mapped to a Specific Agent

1. Admin clicks [+ Add Channel], selects "Discord".
2. The form shows Discord-specific fields: Bot Token, Guild ID, Intents.
3. Admin pastes the Discord bot token, enters the guild ID.
4. Sets Default Agent to "Researcher".
5. Adds a routing rule: channel `#research` maps to agent "Researcher",
   channel `#general` maps to agent "main".
6. Clicks [Create Channel].
7. ChannelManager instantiates a `DiscordAdapter`, connects via websocket
   gateway, joins the guild.
8. The channel appears in the Channels tab as "RUNNING".

### 3. Message Routing: Discord to Researcher Agent

```
Discord user sends: "Find papers on transformer attention mechanisms"
    |
    v
DiscordAdapter.poll_loop() receives MESSAGE_CREATE event
    |
    v
Adapter creates IncomingMessage:
    channel_id = "research-discord"
    platform_chat_id = "1234567890"   (Discord channel ID)
    platform_user_id = "9876543210"   (Discord user ID)
    text = "Find papers on transformer attention mechanisms"
    is_group = True
    |
    v
Channel._on_message(msg)
    |
    v
Channel.resolve_agent(msg)
    -> checks routing: "1234567890" maps to "Researcher"
    -> returns "Researcher"
    |
    v
Channel._handle_chat(msg)
    -> creates session with agent="Researcher"
    -> calls BrainAgentClient.chat(text)
    -> iterates SSE events
    -> calls adapter.send_typing() every 4s
    -> on text_delta: calls adapter.edit() with streaming text
    -> on done: calls adapter.format_response() which returns a Discord embed:
         {
           "embeds": [{
             "description": "Here are recent papers on...",
             "color": 0x7C3AED,
             "footer": {"text": "Researcher | claude-opus-4-6 | 12.3s | 2,847 tok"}
           }]
         }
    -> sends final formatted response
```

### 4. Admin Stops One Channel Without Affecting Others

1. Admin clicks [Stop] on the "Team Discord" channel card.
2. Web UI calls `POST /v1/channels/team-discord/stop`.
3. Server calls `channel_manager.stop_channel("team-discord")`.
4. `DiscordAdapter.stop()` sets `_stop_event`, closes websocket, joins thread.
5. Channel status updates to STOPPED. Other channels continue running.
6. The Discord channel card shows STOPPED with a [Start] button.
7. The Telegram and Slack channels are unaffected -- each runs in its own
   thread with its own error handling.

---

## Platform-Specific Considerations

### Message Format Differences

| Platform  | Format              | Max Length | Editable | Threads | Media     |
|-----------|---------------------|-----------|----------|---------|-----------|
| Telegram  | HTML subset         | 4,096     | Yes      | No      | Photo/Doc |
| Discord   | Markdown + Embeds   | 2,000     | Yes      | Yes     | Files     |
| Slack     | Block Kit + mrkdwn  | 40,000    | Yes      | Yes     | Files     |
| Matrix    | HTML                | 65,536    | Yes      | Yes     | Files     |
| IRC       | Plain text          | 512       | No       | No      | No        |
| WhatsApp  | Limited formatting  | 65,536    | No       | No      | Media     |
| Signal    | Plain text          | 65,536    | No       | No      | Files     |

Each adapter's `format_response()` handles conversion. A shared utility converts
LLM markdown to an intermediate format, then each adapter converts to its native
format.

### Group Chat vs DM Handling

- **DMs**: Message always handled. Session is per-user.
- **Group chats**: Configurable per channel:
  - `"group_mode": "mention"` -- only respond when @mentioned (Discord, Slack)
  - `"group_mode": "all"` -- respond to all messages (Telegram default)
  - `"group_mode": "prefix"` -- respond to messages starting with `!brain`
  - `"group_mode": "off"` -- ignore group messages entirely

### Rate Limiting

Each adapter manages its own rate limits:

```python
class RateLimiter:
    def __init__(self, calls_per_second: float = 1.0):
        self.min_interval = 1.0 / calls_per_second
        self._last_call = 0.0
        self._lock = threading.Lock()

    def wait(self):
        with self._lock:
            now = time.time()
            elapsed = now - self._last_call
            if elapsed < self.min_interval:
                time.sleep(self.min_interval - elapsed)
            self._last_call = time.time()
```

Platform-specific defaults:
- Telegram: 30 messages/second globally, 1/second per chat
- Discord: 5 requests/5 seconds per channel
- Slack: 1 message/second per channel
- Matrix: no hard limit, but respect server rate-limit headers

### Error Isolation

Each channel runs in its own thread. A failing adapter cannot crash others:

```python
def poll_loop(self):
    while not self._stop_event.is_set():
        try:
            self._poll_once()
        except Exception as e:
            self._error = str(e)
            self.stats["errors"] += 1
            # Exponential backoff per channel, capped at 2 minutes
            delay = min(2 ** self._consecutive_errors, 120)
            self._stop_event.wait(delay)
```

The ChannelManager monitors threads and can auto-restart failed channels:

```python
def _health_check(self):
    """Periodic check -- restart channels that died unexpectedly."""
    for ch in self.channels.values():
        if ch.config.get("enabled") and not ch.adapter.running:
            if ch.adapter.error:
                print(f"Channel {ch.channel_id} died: {ch.adapter.error}")
            ch.start()  # Auto-restart
```

### Hot-Reload Config

When channel config changes via the API:

1. If only non-connection fields change (name, agent_routing, allowed_users):
   update in-memory config without restarting the adapter.
2. If credentials or connection fields change: stop the channel, apply new
   config, restart it.
3. Persist all changes to `config.json` atomically (write temp + rename).

---

## TUI Integration

Add a `/channels` command to the TUI:

```
> /channels
Messaging Channels:
  personal-tg       telegram  RUNNING  @brain_agent_bot    42 msgs
  research-tg       telegram  RUNNING  @research_bot       18 msgs
  team-discord      discord   STOPPED  --                   0 msgs
  engineering-slack  slack     RUNNING  #brain-agent       127 msgs

> /channels start team-discord
Starting team-discord... OK

> /channels stop research-tg
Stopping research-tg... OK
```

---

## Implementation Phases

### Phase 1: Refactor (3-4 days)

Extract the generic messaging framework from the existing Telegram code.

- Create `channels/` package with `base.py`, `manager.py`, `telegram_adapter.py`
- Move `TelegramBot`, `md_to_telegram_html()` into the adapter module
- Refactor `ChatManager` session logic into `Channel._handle_chat()`
- Implement `ChannelManager` with create/start/stop/remove lifecycle
- Auto-migrate `config.json` from `"telegram"` to `"channels"` format
- Update `server.py`: replace `_telegram_mod.telegram_service` singleton with
  `ChannelManager`; update `GET /v1/services` response
- New API: `GET/POST /v1/channels`, `POST /v1/channels/:id/{start,stop,restart}`
- Update Web UI: rename "Telegram" tab to "Channels", show channel list
- TUI: add `/channels` command
- **Tests**: adapter contract tests, channel lifecycle tests, config migration

At the end of Phase 1, the existing single Telegram bot works exactly as before
through the new framework, plus the admin can add a second Telegram bot from the UI.

### Phase 2: Discord Adapter (2-3 days)

- Implement `DiscordAdapter` using Discord Gateway websocket API
- Discord-specific formatting: markdown passthrough + embeds for tool output
- Guild/channel scoping, mention-to-respond in group channels
- Slash command registration for `/agent`, `/model`, `/new`, etc.
- Handle Discord reconnect, heartbeat, resume
- Thread support: optionally respond in threads to keep channels clean

### Phase 3: Slack Adapter (2-3 days)

- Implement `SlackAdapter` using Slack Socket Mode (no public URL needed)
- Slack Block Kit formatting for rich responses
- Channel-to-agent mapping via Slack channel IDs
- App Home tab showing active conversations
- Slash commands: `/brain`, `/agent`, `/model`
- Thread replies for tool-heavy responses

### Phase 4: Matrix Adapter (1-2 days)

- Implement `MatrixAdapter` using Matrix client-server API
- E2EE support via libolm (optional, complex)
- Room-to-agent mapping
- HTML formatting (Matrix supports full HTML)

### Phase 5: Additional Adapters (1 day each)

- **IRC**: plain text adapter, no editing, simple prefix-based activation
- **WhatsApp**: via WhatsApp Business API or Baileys
- **Signal**: via signal-cli or signald

### Phase 6: Advanced Features (2-3 days)

- Cross-channel message forwarding (agent responds on one platform,
  notification sent to another)
- Channel-level conversation history in the Web UI
- Per-channel analytics dashboard
- Webhook adapter (generic HTTP webhook for custom integrations)

---

## Effort Summary

| Phase | Scope                          | Estimate    |
|-------|--------------------------------|-------------|
| 1     | Framework + Telegram refactor  | 3-4 days    |
| 2     | Discord adapter                | 2-3 days    |
| 3     | Slack adapter                  | 2-3 days    |
| 4     | Matrix adapter                 | 1-2 days    |
| 5     | IRC / WhatsApp / Signal        | 1 day each  |
| 6     | Advanced features              | 2-3 days    |
| **Total** | **Full multi-platform**    | **~2-3 weeks** |

Phase 1 alone delivers the framework and multi-Telegram-bot capability, which is
the highest-value milestone. Each subsequent phase is independent and can be
prioritized based on which platforms are most needed.

---

## Open Questions

1. **Credential storage**: Should bot tokens live in `config.json` (current approach)
   or move to a separate encrypted store? For now, `config.json` is sufficient since
   it is already gitignored.

2. **Webhook vs polling**: Discord and Slack support both. Socket Mode / websocket
   approaches avoid needing a public URL, which aligns with the current Telegram
   long-polling approach.

3. **Media handling**: Should adapters download incoming media (photos, files) and
   pass them to the engine? The current Telegram adapter ignores non-text messages.
   Phase 1 can continue ignoring media; later phases add per-adapter media support.

4. **Per-channel chat history**: Should channels get their own chat DB, or share
   the agent's `chats.db`? Recommendation: share the agent's DB but tag messages
   with `channel_id` for filtering.

5. **Cross-agent channels**: Can one channel route different chats to different
   agents? The proposed `agent_routing` map supports this, but session management
   needs to handle multiple agents per channel cleanly.
