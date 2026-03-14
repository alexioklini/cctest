#!/usr/bin/env python3
"""Brain Agent Telegram Bot — connects to Brain Agent Server."""

import argparse
import json
import os
import sys
import time
import urllib.request
import urllib.error

from client import BrainAgentClient

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
        except Exception:
            return []

    def send_message(self, chat_id: int, text: str, parse_mode: str = "Markdown"):
        # Truncate if too long for Telegram (4096 chars)
        if len(text) > 4000:
            text = text[:4000] + "\n\n_(truncated)_"
        try:
            self._call("sendMessage", {
                "chat_id": chat_id,
                "text": text,
                "parse_mode": parse_mode,
            })
        except Exception:
            # Fallback without markdown if parsing fails
            try:
                self._call("sendMessage", {
                    "chat_id": chat_id,
                    "text": text,
                })
            except Exception as e:
                print(f"Error sending to {chat_id}: {e}")

    def send_action(self, chat_id: int, action: str = "typing"):
        try:
            self._call("sendChatAction", {
                "chat_id": chat_id,
                "action": action,
            })
        except Exception:
            pass


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
                   chat_id: int, text: str):
    """Handle a regular chat message."""
    # Each chat gets its own client to avoid session_id conflicts
    sid = manager.get_session(chat_id)
    chat_client = BrainAgentClient(manager.client.server_url)
    chat_client.session_id = sid

    # Send typing indicator
    bot.send_action(chat_id)

    # Stream response
    full_text = ""
    tool_count = 0
    error_msg = ""

    try:
        for event_type, data in chat_client.chat(text):
            if event_type == "tool_call":
                tool_count += 1
                bot.send_action(chat_id)
            elif event_type == "done":
                full_text = data.get("text", "")
            elif event_type == "error":
                error_msg = data.get("message", "Unknown error")
    except Exception as e:
        error_msg = f"{type(e).__name__}: {e}"
        print(f"  Error for chat {chat_id}: {error_msg}")

    if error_msg:
        bot.send_message(chat_id, f"⚠️ {error_msg}")
    elif full_text:
        # Add tool count if any were used
        if tool_count > 0:
            full_text += f"\n\n_{tool_count} tool{'s' if tool_count > 1 else ''} used_"
        bot.send_message(chat_id, full_text)
    else:
        bot.send_message(chat_id, "_(no response)_")


# --- Main loop ---

def run_bot(args):
    """Run the Telegram bot with long polling."""
    client = BrainAgentClient(args.server)

    if not client.ping():
        print(f"Cannot connect to Brain Agent server at {args.server}")
        sys.exit(1)

    status = client.status()
    print(f"Brain Agent Telegram Bot v{status.get('version', '?')}")
    print(f"Server: {args.server}")
    print(f"Agents: {', '.join(status.get('agents', []))}")

    bot = TelegramBot(args.token)

    # Load config
    allowed = None
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "agents", "main", "telegram.json")
    if os.path.exists(config_path):
        try:
            with open(config_path) as f:
                config = json.load(f)
                allowed = config.get("allowed_users")
                print(f"Allowed users: {allowed}")
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

    print("Listening for messages... (Ctrl+C to stop)\n")

    try:
        while True:
            updates = bot.get_updates(timeout=30)
            for update in updates:
                msg = update.get("message", {})
                chat_id = msg.get("chat", {}).get("id")
                user_id = msg.get("from", {}).get("id")
                text = msg.get("text", "")

                if not chat_id or not text:
                    continue

                if not manager.is_allowed(user_id):
                    bot.send_message(chat_id, "⛔ Not authorized.")
                    continue

                user_name = msg.get("from", {}).get("first_name", "?")
                print(f"[{user_name}] {text[:80]}")

                if text.startswith("/"):
                    handle_command(bot, manager, chat_id, text)
                else:
                    handle_message(bot, manager, chat_id, text)

    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        # Clean up sessions
        for chat_id in list(manager.sessions.keys()):
            manager.reset_session(chat_id)


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
