#!/usr/bin/env python3
"""Brain Agent Messaging Adapters — multi-channel messaging framework.

Provides a BaseAdapter interface and ChannelManager for running multiple
messaging frontends (Telegram, Discord, etc.) simultaneously.
"""

import json
import os
import threading
import time

from client import BrainAgentClient


# --- Base Adapter ---

class IncomingMessage:
    """Platform-agnostic incoming message."""
    __slots__ = (
        "channel_id", "platform_chat_id", "platform_user_id",
        "user_display_name", "text", "is_command", "is_group",
        "is_direct", "reply_to_message_id", "attachments", "images", "raw",
    )

    def __init__(self, **kwargs):
        self.channel_id = kwargs.get("channel_id", "")
        self.platform_chat_id = kwargs.get("platform_chat_id", "")
        self.platform_user_id = kwargs.get("platform_user_id", "")
        self.user_display_name = kwargs.get("user_display_name", "")
        self.text = kwargs.get("text", "")
        self.is_command = kwargs.get("is_command", False)
        self.is_group = kwargs.get("is_group", False)
        self.is_direct = kwargs.get("is_direct", True)
        self.reply_to_message_id = kwargs.get("reply_to_message_id")
        self.attachments = kwargs.get("attachments", [])
        self.images = kwargs.get("images", [])  # [{data: base64, media_type: "image/jpeg"}]
        self.raw = kwargs.get("raw", {})


class OutgoingMessage:
    """Platform-agnostic outgoing message."""
    __slots__ = (
        "text", "platform_chat_id", "reply_to_message_id",
        "edit_message_id", "footer", "is_error", "is_placeholder",
    )

    def __init__(self, **kwargs):
        self.text = kwargs.get("text", "")
        self.platform_chat_id = kwargs.get("platform_chat_id", "")
        self.reply_to_message_id = kwargs.get("reply_to_message_id")
        self.edit_message_id = kwargs.get("edit_message_id")
        self.footer = kwargs.get("footer")
        self.is_error = kwargs.get("is_error", False)
        self.is_placeholder = kwargs.get("is_placeholder", False)


class BaseAdapter:
    """Base class for all messaging platform adapters."""

    adapter_type: str = "base"

    def __init__(self, config: dict):
        self.config = config
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._on_message = None  # callback
        self._error: str = ""
        self._identity: str = ""

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def error(self) -> str:
        return self._error

    @property
    def identity(self) -> str:
        return self._identity

    def validate_config(self) -> tuple[bool, str]:
        """Validate adapter config. Returns (ok, error_message)."""
        return True, ""

    def connect(self) -> tuple[bool, str]:
        """Connect to the platform. Returns (ok, error_or_identity)."""
        return True, ""

    def disconnect(self):
        """Disconnect from the platform."""
        pass

    def send(self, message: OutgoingMessage) -> str | None:
        """Send a message. Returns message_id for later editing."""
        return None

    def edit(self, message: OutgoingMessage):
        """Edit a previously sent message."""
        pass

    def send_typing(self, platform_chat_id: str):
        """Send a typing indicator."""
        pass

    def format_response(self, text: str, footer: dict | None = None) -> str:
        """Convert LLM markdown to platform-specific format."""
        return text

    def poll_loop(self):
        """Main event loop. Must check self._stop_event periodically."""
        pass

    def start(self, on_message) -> bool:
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
        self._identity = result
        self._thread = threading.Thread(
            target=self._safe_poll_loop, daemon=True,
            name=f"channel-{self.adapter_type}",
        )
        self._thread.start()
        return True

    def _safe_poll_loop(self):
        """Wrapper with error handling."""
        try:
            self.poll_loop()
        except Exception as e:
            self._error = str(e)

    def stop(self):
        self._stop_event.set()
        self.disconnect()
        if self._thread:
            self._thread.join(timeout=35)
            self._thread = None

    def platform_capabilities(self) -> dict:
        return {
            "edit_messages": False,
            "typing_indicator": False,
            "rich_formatting": False,
            "max_message_length": 4096,
        }


# --- Telegram Adapter ---
# Wraps the existing telegram.py classes

class TelegramAdapter(BaseAdapter):
    """Telegram messaging adapter using existing TelegramBot."""
    adapter_type = "telegram"

    def __init__(self, config: dict):
        super().__init__(config)
        self.token = config.get("bot_token", "")
        self.bot = None
        self.bot_username = ""

    def validate_config(self) -> tuple[bool, str]:
        if not self.token:
            return False, "Missing bot_token"
        return True, ""

    def connect(self) -> tuple[bool, str]:
        from telegram import TelegramBot
        self.bot = TelegramBot(self.token)
        try:
            me = self.bot._call("getMe")
            self.bot_username = me.get("result", {}).get("username", "?")
            return True, f"@{self.bot_username}"
        except Exception as e:
            return False, f"Invalid token: {e}"

    def disconnect(self):
        self.bot = None

    def send(self, message: OutgoingMessage) -> str | None:
        if not self.bot:
            return None
        from telegram import md_to_telegram_html
        text = md_to_telegram_html(message.text) if message.text else message.text
        if message.is_placeholder:
            text = message.text  # Don't format placeholder
        mid = self.bot.send_message(int(message.platform_chat_id), text, as_html=not message.is_placeholder)
        return str(mid) if mid else None

    def edit(self, message: OutgoingMessage):
        if not self.bot or not message.edit_message_id:
            return
        from telegram import md_to_telegram_html
        text = md_to_telegram_html(message.text) if message.text else ""
        self.bot.edit_message(
            int(message.platform_chat_id),
            int(message.edit_message_id),
            text, as_html=True,
        )

    def send_typing(self, platform_chat_id: str):
        if self.bot:
            self.bot.send_action(int(platform_chat_id))

    def format_response(self, text: str, footer: dict | None = None) -> str:
        from telegram import md_to_telegram_html, model_icon
        import html as html_mod
        html = md_to_telegram_html(text)
        if footer:
            html += "\n\n<i>"
            model = footer.get("model", "")
            mi = model_icon(model) if model else ""
            html += f"{mi} {html_mod.escape(model)}" if model else ""
            html += f"  {footer.get('elapsed', '')}"
            tokens = footer.get("tokens", 0)
            if tokens:
                html += f"  {tokens:,} tok"
            tools = footer.get("tools", [])
            if tools:
                html += f"\n{len(tools)} tool{'s' if len(tools) > 1 else ''}: {', '.join(tools[:5])}"
            html += "</i>"
        return html

    def poll_loop(self):
        """Poll for Telegram updates."""
        if not self.bot:
            return
        # Skip old messages
        try:
            old = self.bot._call("getUpdates", {"offset": 0, "timeout": 0})
            old_updates = old.get("result", [])
            if old_updates:
                self.bot.offset = old_updates[-1]["update_id"] + 1
        except Exception:
            pass

        backoff = 1
        while not self._stop_event.is_set():
            try:
                updates = self.bot.get_updates(timeout=30)
                backoff = 1
                for update in updates:
                    if self._stop_event.is_set():
                        break
                    msg = update.get("message", {})
                    chat_id = msg.get("chat", {}).get("id")
                    text = msg.get("text", "").strip()
                    user_id = msg.get("from", {}).get("id")
                    user_name = msg.get("from", {}).get("first_name", "?")

                    # Handle photo messages (multimodal)
                    images = []
                    if "photo" in msg:
                        images = self._download_photos(msg)
                        if not text:
                            text = msg.get("caption", "") or "What can you see in this image?"

                    if not chat_id:
                        continue
                    if not text and not images:
                        continue

                    incoming = IncomingMessage(
                        channel_id="",  # set by Channel
                        platform_chat_id=str(chat_id),
                        platform_user_id=str(user_id) if user_id else "",
                        user_display_name=user_name,
                        text=text,
                        is_command=text.startswith("/") if text else False,
                        is_group=msg.get("chat", {}).get("type") in ("group", "supergroup"),
                        is_direct=msg.get("chat", {}).get("type") == "private",
                        images=images,
                        raw=update,
                    )
                    if self._on_message:
                        self._on_message(incoming)

            except Exception as e:
                if not self._stop_event.is_set():
                    self._error = str(e)
                    self._stop_event.wait(min(backoff, 30))
                    backoff = min(backoff * 2, 30)

    def _download_photos(self, msg: dict) -> list[dict]:
        """Download photo from Telegram and return as base64."""
        import base64
        photos = msg.get("photo", [])
        if not photos or not self.bot:
            return []
        # Get largest photo
        photo = photos[-1]
        file_id = photo["file_id"]
        try:
            file_info = self.bot._call("getFile", {"file_id": file_id})
            file_path = file_info.get("result", {}).get("file_path", "")
            if not file_path:
                return []
            url = f"https://api.telegram.org/file/bot{self.token}/{file_path}"
            import urllib.request
            with urllib.request.urlopen(url, timeout=30) as resp:
                data = resp.read()
            b64 = base64.b64encode(data).decode("ascii")
            # Determine media type from extension
            ext = file_path.rsplit(".", 1)[-1].lower() if "." in file_path else "jpg"
            media_types = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
                          "gif": "image/gif", "webp": "image/webp"}
            media_type = media_types.get(ext, "image/jpeg")
            return [{"data": b64, "media_type": media_type}]
        except Exception as e:
            print(f"  Photo download error: {e}", flush=True)
            return []

    def platform_capabilities(self) -> dict:
        return {
            "edit_messages": True,
            "typing_indicator": True,
            "rich_formatting": True,
            "max_message_length": 4096,
        }


# --- Channel ---

class Channel:
    """A configured instance of a messaging adapter."""

    def __init__(self, channel_id: str, adapter: BaseAdapter,
                 config: dict, server_url: str):
        self.channel_id = channel_id
        self.adapter = adapter
        self.config = config
        self.server_url = server_url
        self.sessions: dict[str, str] = {}  # platform_chat_id -> session_id
        self.chat_state: dict[str, dict] = {}  # platform_chat_id -> {agent, model}
        self.stats = {"messages_in": 0, "messages_out": 0, "errors": 0}
        self._started_at: float | None = None

    @property
    def agent_routing(self) -> dict:
        return self.config.get("agent_routing", {"default": "main"})

    def resolve_agent(self, msg: IncomingMessage) -> str:
        routing = self.agent_routing
        agent = routing.get(msg.platform_chat_id)
        if agent:
            return agent
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

    def _get_session(self, chat_id: str, agent: str, model: str | None) -> str:
        """Get or create a Brain Agent session for a chat."""
        if chat_id not in self.sessions:
            client = BrainAgentClient(self.server_url)
            sid = client.create_session(agent=agent, model=model)
            self.sessions[chat_id] = sid
        return self.sessions[chat_id]

    def _reset_session(self, chat_id: str):
        if chat_id in self.sessions:
            old_sid = self.sessions.pop(chat_id)
            client = BrainAgentClient(self.server_url)
            client.session_id = old_sid
            client.delete_session()

    def _on_message(self, msg: IncomingMessage):
        """Callback from adapter when a message arrives."""
        msg.channel_id = self.channel_id
        self.stats["messages_in"] += 1

        # Auth check
        allowed = self.config.get("allowed_users")
        if allowed:
            allowed_strs = [str(u) for u in allowed]
            if msg.platform_user_id not in allowed_strs:
                self.adapter.send(OutgoingMessage(
                    text="Not authorized.",
                    platform_chat_id=msg.platform_chat_id,
                    is_error=True,
                ))
                return

        if msg.is_command:
            self._handle_command(msg)
        else:
            self._handle_chat(msg)

    def _handle_command(self, msg: IncomingMessage):
        """Handle /commands."""
        parts = msg.text.strip().split(None, 1)
        cmd = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""

        if cmd in ("/start", "/help"):
            self.adapter.send(OutgoingMessage(
                text="Brain Agent\n/new - new conversation\n/agent NAME - switch agent\n/model NAME - switch model",
                platform_chat_id=msg.platform_chat_id,
            ))
        elif cmd == "/new":
            self._reset_session(msg.platform_chat_id)
            self.adapter.send(OutgoingMessage(text="New conversation started.", platform_chat_id=msg.platform_chat_id))
        elif cmd == "/agent":
            if arg:
                self._reset_session(msg.platform_chat_id)
                self.chat_state[msg.platform_chat_id] = {"agent": arg.strip(), "model": self.config.get("default_model")}
                self.adapter.send(OutgoingMessage(text=f"Switched to agent: {arg.strip()}", platform_chat_id=msg.platform_chat_id))
            else:
                self.adapter.send(OutgoingMessage(text="Usage: /agent NAME", platform_chat_id=msg.platform_chat_id))
        elif cmd == "/model":
            if arg:
                self.chat_state.setdefault(msg.platform_chat_id, {})["model"] = arg.strip()
                self._reset_session(msg.platform_chat_id)
                self.adapter.send(OutgoingMessage(text=f"Model: {arg.strip()}", platform_chat_id=msg.platform_chat_id))
            else:
                self.adapter.send(OutgoingMessage(text="Usage: /model NAME", platform_chat_id=msg.platform_chat_id))
        else:
            self.adapter.send(OutgoingMessage(text=f"Unknown command: {cmd}", platform_chat_id=msg.platform_chat_id))

    def _handle_chat(self, msg: IncomingMessage):
        """Route message to Brain Agent server and stream response back."""
        agent = self.resolve_agent(msg)
        state = self.chat_state.get(msg.platform_chat_id, {})
        if state.get("agent"):
            agent = state["agent"]
        # Use agent's default model if no explicit model set
        model = state.get("model") or self.config.get("default_model")
        if not model:
            try:
                from claude_cli import AgentConfig
                agent_cfg = AgentConfig(agent)
                model = agent_cfg.preferred_model or agent_cfg.config.get("model", "")
            except Exception:
                pass

        sid = self._get_session(msg.platform_chat_id, agent, model)
        client = BrainAgentClient(self.server_url)
        client.session_id = sid

        start_time = time.time()

        # Send placeholder
        placeholder_id = self.adapter.send(OutgoingMessage(
            text="...",
            platform_chat_id=msg.platform_chat_id,
            is_placeholder=True,
        ))

        # Typing indicator thread
        typing_stop = threading.Event()
        def keep_typing():
            while not typing_stop.is_set():
                self.adapter.send_typing(msg.platform_chat_id)
                typing_stop.wait(4)
        typing_thread = threading.Thread(target=keep_typing, daemon=True)
        typing_thread.start()

        full_text = ""
        streaming_text = ""
        tool_names = []
        tokens = 0
        done_model = ""
        error_msg = ""
        last_edit = 0

        try:
            # Build chat payload - include images if present
            chat_payload = {"session_id": sid, "message": msg.text}
            if msg.images:
                chat_payload["images"] = msg.images

            for event_type, data in client.chat(msg.text):
                if event_type == "text_delta":
                    streaming_text += data.get("text", "")
                    now = time.time()
                    if placeholder_id and now - last_edit > 1.0 and streaming_text:
                        self.adapter.edit(OutgoingMessage(
                            text=streaming_text,
                            platform_chat_id=msg.platform_chat_id,
                            edit_message_id=placeholder_id,
                        ))
                        last_edit = now
                elif event_type == "tool_call":
                    name = data.get("name", "")
                    if name and name not in tool_names:
                        tool_names.append(name)
                elif event_type == "done":
                    full_text = data.get("text", "")
                    tokens = data.get("tokens", 0)
                    done_model = data.get("model", "")
                    break
                elif event_type == "error":
                    error_msg = data.get("message", "Unknown error")
                    break
        except Exception as e:
            error_msg = f"{type(e).__name__}: {e}"
        finally:
            typing_stop.set()
            typing_thread.join(timeout=1)

        elapsed = time.time() - start_time

        if error_msg:
            final_text = f"Error: {error_msg}"
        elif full_text:
            footer = {
                "model": done_model,
                "elapsed": f"{elapsed:.1f}s",
                "tokens": tokens,
                "tools": tool_names,
            }
            final_text = self.adapter.format_response(full_text, footer)
        else:
            final_text = "(no response)"

        if placeholder_id:
            self.adapter.edit(OutgoingMessage(
                text=final_text if not full_text else full_text,
                platform_chat_id=msg.platform_chat_id,
                edit_message_id=placeholder_id,
            ))
        else:
            self.adapter.send(OutgoingMessage(text=final_text, platform_chat_id=msg.platform_chat_id))

        self.stats["messages_out"] += 1

    def status(self) -> dict:
        return {
            "channel_id": self.channel_id,
            "type": self.adapter.adapter_type,
            "name": self.config.get("name", self.channel_id),
            "running": self.adapter.running,
            "enabled": self.config.get("enabled", True),
            "error": self.adapter.error,
            "identity": self.adapter.identity,
            "agent_routing": self.agent_routing,
            "default_model": self.config.get("default_model", ""),
            "stats": dict(self.stats),
            "uptime": int(time.time() - self._started_at) if self._started_at else 0,
        }


# --- Channel Manager ---

class ChannelManager:
    """Manages multiple messaging channel instances."""

    def __init__(self, server_url: str):
        self.server_url = server_url
        self.channels: dict[str, Channel] = {}
        self._adapters: dict[str, type] = {}
        self._register_builtin_adapters()

    def _register_builtin_adapters(self):
        self._adapters["telegram"] = TelegramAdapter

    def register_adapter(self, adapter_type: str, cls: type):
        self._adapters[adapter_type] = cls

    def create_channel(self, channel_id: str, config: dict) -> Channel:
        adapter_type = config.get("type", "telegram")
        if adapter_type not in self._adapters:
            raise ValueError(f"Unknown adapter type: {adapter_type}")

        adapter_cls = self._adapters[adapter_type]
        creds = config.get("credentials", config)
        adapter = adapter_cls(creds)

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

    def load_from_config(self, config: dict):
        """Load channels from config.json, handling migration from old telegram format."""
        channels_cfg = config.get("channels", [])

        # Auto-migrate old telegram config
        if not channels_cfg and "telegram" in config:
            tg = config["telegram"]
            if tg.get("bot_token"):
                channels_cfg = [{
                    "id": "telegram",
                    "type": "telegram",
                    "name": "Telegram",
                    "enabled": tg.get("enabled", True),
                    "credentials": {"bot_token": tg.get("bot_token", "")},
                    "allowed_users": tg.get("allowed_users", []),
                    "agent_routing": {"default": "main"},
                    "default_model": tg.get("model", ""),
                }]

        for ch_cfg in channels_cfg:
            ch_id = ch_cfg.get("id", ch_cfg.get("name", f"channel-{len(self.channels)}"))
            try:
                self.create_channel(ch_id, ch_cfg)
            except Exception as e:
                print(f"Channel '{ch_id}' error: {e}", flush=True)


# Module-level singleton (accessed by server.py)
channel_manager: ChannelManager | None = None
