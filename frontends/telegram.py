#!/usr/bin/env python3
"""Brain Agent Telegram Bot — connects to Brain Agent Server."""

import argparse
import json
import os
import sys
import threading
import time
import urllib.request
import urllib.error

from frontends.client import BrainAgentClient

# --- Markdown to Telegram HTML ---

import re
import html as html_mod


_models_config: dict = {}  # populated from server on startup

# Dedup guard: prevent same Telegram message from being processed concurrently
# (can happen when 409 Conflict causes duplicate polling loops)
_processing_messages: set[tuple[int, str]] = set()
_processing_lock = threading.Lock()


def model_icon(model: str) -> str:
    # Check server-provided config first
    cfg = _models_config.get(model)
    if cfg and cfg.get("icon"):
        return cfg["icon"]
    # Fallback to pattern matching
    m = model.lower()
    if m.startswith("crow"): return "🐦‍⬛"
    if m.startswith("claude-opus") or m == "opus": return "🟣"
    if m.startswith("claude-sonnet") or m == "sonnet": return "🟠"
    if m.startswith("claude-haiku") or m == "haiku": return "🟢"
    if "claude" in m: return "🔵"
    if "gemini" in m: return "💎"
    if "qwen" in m: return "🐼"
    if "llama" in m: return "🦙"
    if "mistral" in m: return "🌬️"
    return "🤖"


def md_to_telegram_html(text: str) -> str:
    """Convert LLM markdown to Telegram-compatible HTML."""
    # Escape HTML entities first
    text = html_mod.escape(text)

    # Code blocks: ```lang\n...\n``` → <pre><code>...</code></pre>
    def replace_code_block(m):
        lang = m.group(1) or ""
        code = m.group(2)
        if lang:
            return f'<pre><code class="language-{lang}">{code}</code></pre>'
        return f"<pre>{code}</pre>"
    text = re.sub(r"```(\w*)\n(.*?)```", replace_code_block, text, flags=re.DOTALL)

    # Inline code: `...` → <code>...</code>
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)

    # Bold: **text** → <b>text</b>
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)

    # Italic: *text* → <i>text</i>  (but not inside bold)
    text = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<i>\1</i>", text)

    # Strikethrough: ~~text~~ → <s>text</s>
    text = re.sub(r"~~(.+?)~~", r"<s>\1</s>", text)

    # Links: [text](url) → <a href="url">text</a>
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', text)

    # Headers: # text → <b>text</b> (Telegram has no headers)
    text = re.sub(r"^#{1,6}\s+(.+)$", r"<b>\1</b>", text, flags=re.MULTILINE)

    return text


# --- Telegram Bot API ---

class TelegramBot:
    """Minimal Telegram Bot API client using stdlib."""

    def __init__(self, token: str):
        self.token = token
        self.base = f"https://api.telegram.org/bot{token}"
        self.offset = 0

    def _call(self, method: str, data: dict | None = None) -> dict:
        url = f"{self.base}/{method}"
        if data:
            body = json.dumps(data).encode("utf-8")
            req = urllib.request.Request(
                url, data=body,
                headers={"Content-Type": "application/json"},
            )
        else:
            req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def get_updates(self, timeout: int = 30) -> list[dict]:
        try:
            result = self._call("getUpdates", {
                "offset": self.offset,
                "timeout": timeout,
            })
            updates = result.get("result", [])
            if updates:
                self.offset = updates[-1]["update_id"] + 1
            return updates
        except Exception as e:
            error_str = str(e)
            if "409" in error_str or "Conflict" in error_str:
                print(f"  409 Conflict: another polling instance detected, backing off 10s", flush=True)
                time.sleep(10)
            else:
                print(f"  get_updates error: {e}", flush=True)
                time.sleep(2)
            return []

    def send_message(self, chat_id: int, text: str, as_html: bool = False) -> int | None:
        """Send a message. Returns message_id for later editing."""
        if len(text) > 4000:
            text = text[:4000] + "\n\n(truncated)"
        msg = {"chat_id": chat_id, "text": text}
        if as_html:
            msg["parse_mode"] = "HTML"
        try:
            result = self._call("sendMessage", msg)
            return result.get("result", {}).get("message_id")
        except Exception as e:
            # Fallback: send without formatting
            try:
                result = self._call("sendMessage", {"chat_id": chat_id, "text": text})
                return result.get("result", {}).get("message_id")
            except Exception as e2:
                print(f"  send_message error: {e2}", flush=True)
        return None

    def edit_message(self, chat_id: int, message_id: int, text: str, as_html: bool = False):
        """Edit an existing message."""
        if len(text) > 4000:
            text = text[:4000] + "\n\n(truncated)"
        msg = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
        }
        if as_html:
            msg["parse_mode"] = "HTML"
        try:
            self._call("editMessageText", msg)
        except Exception:
            # Fallback without formatting
            try:
                self._call("editMessageText", {
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "text": text,
                })
            except Exception:
                pass

    def send_action(self, chat_id: int, action: str = "typing"):
        try:
            self._call("sendChatAction", {
                "chat_id": chat_id,
                "action": action,
            })
        except Exception:
            pass

    def download_photo(self, file_id: str) -> tuple[bytes, str] | None:
        """Download a photo by file_id. Returns (bytes, media_type) or None."""
        import base64
        try:
            file_info = self._call("getFile", {"file_id": file_id})
            file_path = file_info.get("result", {}).get("file_path", "")
            if not file_path:
                return None
            url = f"https://api.telegram.org/file/bot{self.token}/{file_path}"
            with urllib.request.urlopen(url, timeout=30) as resp:
                data = resp.read()
            ext = file_path.rsplit(".", 1)[-1].lower() if "." in file_path else "jpg"
            media_types = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
                          "gif": "image/gif", "webp": "image/webp"}
            media_type = media_types.get(ext, "image/jpeg")
            return (data, media_type)
        except Exception as e:
            print(f"  Photo download error: {e}", flush=True)
            return None


# --- Chat session management ---

class ChatManager:
    """Manages per-Telegram-chat sessions with the Brain Agent server."""

    def __init__(self, client: BrainAgentClient, default_agent: str = "main",
                 default_model: str | None = None, allowed_users: list[int] | None = None):
        self.client = client
        self.default_agent = default_agent
        self.default_model = default_model
        self.allowed_users = set(allowed_users) if allowed_users else None
        # chat_id -> session_id
        self.sessions: dict[int, str] = {}
        # chat_id -> current agent/model
        self.chat_state: dict[int, dict] = {}

    def is_allowed(self, user_id: int) -> bool:
        if self.allowed_users is None:
            return True
        return user_id in self.allowed_users

    def get_session(self, chat_id: int) -> str:
        """Get or create a session for a chat."""
        if chat_id not in self.sessions:
            state = self.chat_state.get(chat_id, {})
            agent = state.get("agent", self.default_agent)
            model = state.get("model", self.default_model)
            # Use a temporary client for session creation
            tmp = BrainAgentClient(self.client.server_url)
            sid = tmp.create_session(agent=agent, model=model)
            self.sessions[chat_id] = sid
            self.chat_state.setdefault(chat_id, {"agent": agent, "model": model})
        return self.sessions[chat_id]

    def reset_session(self, chat_id: int):
        if chat_id in self.sessions:
            old_sid = self.sessions.pop(chat_id)
            tmp = BrainAgentClient(self.client.server_url)
            tmp.session_id = old_sid
            tmp.delete_session()

    def switch_agent(self, chat_id: int, agent: str) -> dict:
        self.reset_session(chat_id)
        self.chat_state[chat_id] = {"agent": agent, "model": self.default_model}
        sid = self.get_session(chat_id)
        return {"agent": agent, "session_id": sid}


# --- Command handling ---

def handle_command(bot: TelegramBot, manager: ChatManager,
                   chat_id: int, text: str):
    """Handle Telegram /commands."""
    parts = text.strip().split(None, 1)
    cmd = parts[0].lower()
    arg = parts[1] if len(parts) > 1 else ""

    if cmd == "/start":
        try:
            status = manager.client.status()
            version = status.get("version", "?")
            agents = ", ".join(status.get("agents", ["main"]))
        except Exception:
            version = "?"
            agents = "main"
        bot.send_message(chat_id,
            f"🧠 *Brain Agent* v{version}\n\n"
            f"Agents: {agents}\n\n"
            "Commands:\n"
            "/new — new conversation\n"
            "/agent NAME — switch agent\n"
            "/agents — list agents\n"
            "/model NAME — switch model\n"
            "/help — show help\n"
        )
        return

    if cmd == "/help":
        bot.send_message(chat_id,
            "*Commands:*\n"
            "/new — start new conversation\n"
            "/agent NAME — switch agent\n"
            "/agents — list agents\n"
            "/model NAME — switch model\n"
            "/schedule — list scheduled tasks\n"
            "/status — server status\n"
            "\nJust type a message to chat!"
        )
        return

    if cmd == "/new":
        manager.reset_session(chat_id)
        bot.send_message(chat_id, "🔄 New conversation started.")
        return

    if cmd == "/agents":
        try:
            agents = manager.client.list_agents()
            current = manager.chat_state.get(chat_id, {}).get("agent", "main")
            lines = []
            for a in agents:
                marker = "→ " if a["id"] == current else "  "
                lines.append(f"{marker}*{a['id']}* — {a.get('description', '')}")
            bot.send_message(chat_id, "Agents:\n" + "\n".join(lines))
        except Exception as e:
            bot.send_message(chat_id, f"Error: {e}")
        return

    if cmd == "/agent":
        if not arg:
            bot.send_message(chat_id, "Usage: /agent NAME")
            return
        result = manager.switch_agent(chat_id, arg.strip())
        bot.send_message(chat_id, f"Switched to agent: *{result['agent']}*")
        return

    if cmd == "/model":
        if not arg:
            bot.send_message(chat_id, "Usage: /model NAME")
            return
        manager.chat_state.setdefault(chat_id, {})["model"] = arg.strip()
        manager.reset_session(chat_id)
        bot.send_message(chat_id, f"Model set to: `{arg.strip()}`")
        return

    if cmd == "/schedule":
        try:
            schedules = manager.client.list_schedule()
            if not schedules:
                bot.send_message(chat_id, "No scheduled tasks.")
            else:
                lines = []
                for s in schedules:
                    st = "✅" if s["enabled"] else "⏸"
                    nr = s.get("next_run", "")[:16] if s.get("next_run") else "—"
                    lines.append(f"{st} *{s['name']}* ({s['schedule']}) → {nr}")
                bot.send_message(chat_id, "Scheduled tasks:\n" + "\n".join(lines))
        except Exception as e:
            bot.send_message(chat_id, f"Error: {e}")
        return

    if cmd == "/status":
        try:
            status = manager.client.status()
            bot.send_message(chat_id,
                f"*Brain Agent Server*\n"
                f"Version: {status.get('version')}\n"
                f"Sessions: {status.get('sessions')}\n"
                f"Agents: {', '.join(status.get('agents', []))}\n"
                f"Scheduled: {status.get('scheduler_tasks')}"
            )
        except Exception as e:
            bot.send_message(chat_id, f"Error: {e}")
        return

    bot.send_message(chat_id, f"Unknown command: {cmd}\nType /help for commands.")


def handle_message(bot: TelegramBot, manager: ChatManager,
                   chat_id: int, text: str, images: list[dict] | None = None):
    """Handle a regular chat message, optionally with images."""
    # Dedup: prevent concurrent processing of the same message (409 race condition)
    dedup_key = (chat_id, text[:100])
    with _processing_lock:
        if dedup_key in _processing_messages:
            return  # Skip duplicate
        _processing_messages.add(dedup_key)

    try:
        _handle_message_inner(bot, manager, chat_id, text, images)
    finally:
        with _processing_lock:
            _processing_messages.discard(dedup_key)


def _handle_message_inner(bot: TelegramBot, manager: ChatManager,
                          chat_id: int, text: str, images: list[dict] | None = None):
    """Inner handler after dedup check."""
    sid = manager.get_session(chat_id)
    chat_client = BrainAgentClient(manager.client.server_url)
    chat_client.session_id = sid

    start_time = time.time()

    # Send initial thinking message
    msg_id = bot.send_message(chat_id, "🧠 ...")

    # Keep sending typing action in a background thread
    typing_stop = threading.Event()

    def keep_typing():
        while not typing_stop.is_set():
            bot.send_action(chat_id, "typing")
            typing_stop.wait(4)  # Telegram typing expires after 5s

    typing_thread = threading.Thread(target=keep_typing, daemon=True)
    typing_thread.start()

    full_text = ""
    streaming_text = ""
    tool_count = 0
    tool_names = []
    error_msg = ""
    last_edit = 0
    tokens = 0
    done_model = ""

    try:
        for event_type, data in chat_client.chat(text, images=images):
            if event_type == "text_delta":
                streaming_text += data.get("text", "")
                now = time.time()
                if msg_id and now - last_edit > 1.0 and len(streaming_text) > 0:
                    bot.edit_message(chat_id, msg_id,
                                     md_to_telegram_html(streaming_text) + " ▍", as_html=True)
                    last_edit = now
            elif event_type == "tool_call":
                tool_count += 1
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
        print(f"  Error: {error_msg}", flush=True)
    finally:
        typing_stop.set()
        typing_thread.join(timeout=1)

    elapsed = time.time() - start_time
    agent = manager.chat_state.get(chat_id, {}).get("agent", "main")

    if error_msg:
        if msg_id:
            bot.edit_message(chat_id, msg_id, f"⚠️ {error_msg}")
        else:
            bot.send_message(chat_id, f"⚠️ {error_msg}")
    elif full_text:
        # Build informative footer
        footer = f"\n\n<i>━━━━━━━━━━━━━━━━━━━━\n"
        mi = model_icon(done_model) if done_model else "🤖"
        footer += f"{mi} {html_mod.escape(done_model) if done_model else agent}"
        footer += f"  ⏱ {elapsed:.1f}s"
        if tokens:
            footer += f"  📊 {tokens:,} tok"
        if tool_count > 0:
            footer += f"\n🔧 {tool_count} tool{'s' if tool_count > 1 else ''}: {', '.join(tool_names[:5])}"
            if len(tool_names) > 5:
                footer += f" +{len(tool_names) - 5}"
        footer += "</i>"

        final_html = md_to_telegram_html(full_text) + footer
        if msg_id:
            bot.edit_message(chat_id, msg_id, final_html, as_html=True)
        else:
            bot.send_message(chat_id, final_html, as_html=True)
    else:
        if msg_id:
            bot.edit_message(chat_id, msg_id, "(no response)")
        else:
            bot.send_message(chat_id, "(no response)")


# --- Main loop ---

def run_bot(args):
    """Run the Telegram bot with long polling."""
    client = BrainAgentClient(args.server)

    if not client.ping():
        print(f"Cannot connect to Brain Agent server at {args.server}")
        sys.exit(1)

    status = client.status()

    # Load models config for icons
    global _models_config
    try:
        mc = client._get("/v1/models/config")
        _models_config = mc.get("models", {})
    except Exception:
        pass

    print(f"Brain Agent Telegram Bot v{status.get('version', '?')}")
    print(f"Server: {args.server}")
    print(f"Agents: {', '.join(status.get('agents', []))}")

    bot = TelegramBot(args.token)

    # Load allowed users from config.json
    allowed = None
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
    if os.path.exists(config_path):
        try:
            with open(config_path) as f:
                config = json.load(f)
                allowed = config.get("telegram", {}).get("allowed_users")
                if allowed:
                    print(f"Allowed users: {allowed}")
                else:
                    print("Allowed users: all (no whitelist)")
        except Exception:
            pass

    manager = ChatManager(
        client, default_agent=args.agent,
        default_model=args.model, allowed_users=allowed,
    )

    # Verify bot token
    try:
        me = bot._call("getMe")
        bot_name = me.get("result", {}).get("username", "?")
        print(f"Bot: @{bot_name}")
    except Exception as e:
        print(f"Invalid bot token: {e}")
        sys.exit(1)

    # Skip old messages from before this startup
    try:
        old = bot._call("getUpdates", {"offset": 0, "timeout": 0})
        old_updates = old.get("result", [])
        if old_updates:
            bot.offset = old_updates[-1]["update_id"] + 1
            print(f"Skipped {len(old_updates)} old message(s)")
    except Exception:
        pass

    print("Listening for messages... (Ctrl+C to stop)\n")

    try:
        while True:
            updates = bot.get_updates(timeout=30)
            for update in updates:
                msg = update.get("message", {})
                chat_id = msg.get("chat", {}).get("id")
                user_id = msg.get("from", {}).get("id")
                text = msg.get("text", "")

                # Handle photo messages (multimodal)
                images = []
                if "photo" in msg:
                    photo = msg["photo"][-1]
                    result = bot.download_photo(photo["file_id"])
                    if result:
                        import base64
                        photo_bytes, media_type = result
                        b64 = base64.b64encode(photo_bytes).decode("ascii")
                        images = [{"data": b64, "media_type": media_type}]
                    if not text:
                        text = msg.get("caption", "").strip() or "What can you see in this image?"

                if not chat_id or (not text and not images):
                    continue

                if not manager.is_allowed(user_id):
                    bot.send_message(chat_id, "Not authorized.")
                    continue

                user_name = msg.get("from", {}).get("first_name", "?")
                print(f"[{user_name}] {text[:80]}", flush=True)

                if text.startswith("/"):
                    handle_command(bot, manager, chat_id, text)
                else:
                    handle_message(bot, manager, chat_id, text, images=images or None)

    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        # Clean up sessions
        for chat_id in list(manager.sessions.keys()):
            manager.reset_session(chat_id)


# --- In-process service wrapper (used by server.py) ---

class TelegramService:
    """Wraps the Telegram bot for in-process use by the server."""

    def __init__(self):
        self.running = False
        self.bot_username = ""
        self.error = ""
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def start(self, token: str, server_url: str = "http://127.0.0.1:8420",
              allowed_users: list[int] | None = None,
              default_model: str | None = None,
              default_agent: str = "main") -> bool:
        if self.running:
            return True
        self.error = ""
        self._stop_event.clear()
        try:
            bot = TelegramBot(token)
            me = bot._call("getMe")
            self.bot_username = me.get("result", {}).get("username", "")
            # Clear any stale webhook to prevent 409 conflicts
            try:
                bot._call("deleteWebhook", {"drop_pending_updates": False})
            except Exception:
                pass
        except Exception as e:
            self.error = str(e)
            return False

        client = BrainAgentClient(server_url)
        manager = ChatManager(client, default_agent=default_agent,
                              default_model=default_model, allowed_users=allowed_users)

        def _loop():
            self.running = True
            backoff = 1
            while not self._stop_event.is_set():
                try:
                    updates = bot.get_updates(timeout=30)
                    backoff = 1
                    for update in updates:
                        if self._stop_event.is_set():
                            break
                        msg = update.get("message", {})
                        chat_id = msg.get("chat", {}).get("id")
                        text = msg.get("text", "").strip()
                        user_id = msg.get("from", {}).get("id")

                        # Handle photo messages (multimodal)
                        images = []
                        if "photo" in msg:
                            photo = msg["photo"][-1]  # largest size
                            result = bot.download_photo(photo["file_id"])
                            if result:
                                import base64
                                photo_bytes, media_type = result
                                b64 = base64.b64encode(photo_bytes).decode("ascii")
                                images = [{"data": b64, "media_type": media_type}]
                            if not text:
                                text = msg.get("caption", "").strip() or "What can you see in this image?"

                        if not chat_id or (not text and not images):
                            continue
                        if manager.allowed_users and user_id not in manager.allowed_users:
                            continue
                        if text.startswith("/"):
                            handle_command(bot, manager, chat_id, text)
                        else:
                            handle_message(bot, manager, chat_id, text, images=images or None)
                except Exception as e:
                    if not self._stop_event.is_set():
                        time.sleep(min(backoff, 30))
                        backoff = min(backoff * 2, 30)
            self.running = False

        self._thread = threading.Thread(target=_loop, daemon=True, name="telegram-service")
        self._thread.start()
        return True

    def stop(self):
        self._stop_event.set()
        self.running = False
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None


# Module-level singleton (accessed by server.py as _telegram_mod.telegram_service)
telegram_service = TelegramService()


def main():
    parser = argparse.ArgumentParser(description="Brain Agent Telegram Bot")
    parser.add_argument("--token", required=True, help="Telegram Bot API token")
    parser.add_argument("--server", default="http://127.0.0.1:8420",
                        help="Brain Agent server URL")
    parser.add_argument("--agent", default="main", help="Default agent")
    parser.add_argument("-m", "--model", default=None, help="Default model")
    args = parser.parse_args()

    run_bot(args)


if __name__ == "__main__":
    main()
