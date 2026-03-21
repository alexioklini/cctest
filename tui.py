#!/usr/bin/env python3
"""Brain Agent TUI — Rich + prompt_toolkit frontend (Claude Code style).

Connects to Brain Agent Server (server.py) via HTTP/SSE.
"""

import argparse
import json
import os
import subprocess
import sys
import threading
import time

from rich.console import Console
from rich.markdown import Markdown
from rich.text import Text
from rich.theme import Theme
from rich.table import Table

from prompt_toolkit import PromptSession
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.styles import Style as PTStyle

from client import BrainAgentClient

# --- Console setup ---

THEME = Theme({
    "info": "dim",
    "success": "#5f87ff",
    "warning": "#ff8700",
    "error": "red bold",
    "tool.name": "#af87ff bold",
    "tool.verb": "#ff8700 bold",
    "agent": "#af87ff bold",
    "model": "#5f87ff",
    "dim": "dim",
})

console = Console(theme=THEME, highlight=False)

# --- Greeting ---

def print_greeting(status: dict, session_agent: str, session_model: str):
    """Print Claude Code-style greeting."""
    brain = [
        ("color(213)", r"        .---.        "),
        ("color(213)", r"       /     \       "),
        ("color(177)", r"      | () () |      "),
        ("color(177)", r"      |  ,_,  |      "),
        ("color(141)", r"     /|  |||  |\     "),
        ("color(141)", r"    / |  |||  | \    "),
        ("color(105)", r"   |  \_///\\_/  |   "),
        ("color(69)",  r"   \    ///\\    /   "),
        ("color(33)",  r"    '-.//////.-'    "),
    ]

    console.print()
    for style, line in brain:
        console.print(f"  [{style}]{line}[/]")
    console.print()

    title = Text()
    for i, ch in enumerate("Brain"):
        title.append(ch, style=f"bold color({[213,177,141,105,69][i]})")
    title.append(" ")
    title.append("Agent", style="bold #ff8700")
    title.append(f" v{status.get('version', '?')}", style="dim")
    if session_agent != "main":
        title.append(" | ", style="dim")
        title.append(session_agent, style="cyan bold")
    console.print("  ", title)
    console.print()

    console.print(f"  [dim]Model[/]  [#5f87ff]{session_model}[/]")
    console.print(f"  [dim]Path[/]   [dim]{os.getcwd()}[/]")

    agents = status.get("agents", [])
    if len(agents) > 1:
        parts = []
        for a in agents:
            if a == session_agent:
                parts.append(f"[#af87ff bold]{a}[/]")
            else:
                parts.append(f"[dim]{a}[/]")
        console.print(f"  [dim]Agents[/] {' · '.join(parts)}")

    n_sched = status.get("scheduler_tasks", 0)
    if n_sched:
        console.print(f"  [dim]Tasks[/]  [dim]{n_sched} scheduled[/]")

    console.print()
    console.print("  [dim]/help for commands · Ctrl+C to cancel · Ctrl+D to quit[/]")

    changelog = status.get("changelog", [])
    if changelog:
        latest = changelog[0]
        console.print(f"\n  [dim]^ v{latest['version']}: {latest['changes']}[/]")
    console.print()


# --- Tool display ---

TOOL_VERBS = {
    "read_file": "Reading", "write_file": "Writing", "edit_file": "Editing",
    "list_directory": "Listing", "search_files": "Searching", "execute_command": "Executing",
    "web_fetch": "Fetching", "exa_search": "Searching",
    "memory_store": "Remembering", "memory_recall": "Recalling", "memory_delete": "Forgetting",
    "memory_shared": "Shared Memory", "delegate_task": "Delegating", "use_skill": "Loading Skill",
    "task_status": "Task Status", "task_cancel": "Cancelling",
    "schedule_list": "Schedules", "schedule_history": "History",
}


_models_config: dict = {}  # populated from server on startup


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


def display_tool_call(name: str, args: dict):
    verb = TOOL_VERBS.get(name, "Running")
    if name.startswith("mcp_"):
        verb = "MCP"

    if name == "execute_command":
        detail = f"[yellow]$ {args.get('command', '')[:80]}[/]"
    elif name == "exa_search":
        detail = f'[white]"{args.get("query", "")}"[/]'
    elif name in ("read_file", "write_file", "edit_file"):
        detail = f'[white]{args.get("path", "")}[/]'
    elif name == "delegate_task":
        detail = f'[#af87ff]{args.get("agent", "")}[/] [dim]{args.get("task", "")[:50]}[/]'
    elif name == "use_skill":
        detail = f'[#af87ff]{args.get("skill", "")}[/]'
    elif name.startswith("memory"):
        detail = f'[#af87ff]{args.get("query", args.get("name", ""))[:40]}[/]'
    else:
        detail = f"[dim]{str(args)[:50]}[/]"

    console.print(f"  [tool.verb]{verb}[/] [tool.name]{name}[/] {detail}")


def display_tool_result(name: str, result_str: str):
    try:
        rdata = json.loads(result_str)
    except (json.JSONDecodeError, TypeError):
        return

    if isinstance(rdata, str):
        # Result is just text
        console.print(f"  [#5f87ff]✔[/]")
        return

    if rdata.get("error"):
        console.print(f"  [error]✘ {rdata['error'].split(chr(10))[0][:80]}[/]")
        return

    if name == "execute_command":
        ec = rdata.get("exit_code", -1)
        sym, sty = ("✔", "#5f87ff") if ec == 0 else ("✘", "red")
        console.print(f"  [{sty}]{sym} exit {ec}[/]")
    elif name == "exa_search":
        console.print(f"  [#5f87ff]✔ {rdata.get('result_count', 0)} results[/]")
    elif name in ("memory_recall", "memory_shared"):
        console.print(f"  [#5f87ff]✔ {rdata.get('count', 0)} memories[/]")
    elif name == "delegate_task":
        console.print(f"  [#5f87ff]✔ {rdata.get('agent', '')} responded[/]")
    else:
        console.print(f"  [#5f87ff]✔[/]")


# --- Slash command definitions ---

SLASH_COMMANDS = {
    # Session management
    "/help":        "Show this help",
    "/new":         "Start a new conversation",
    "/sessions":    "List and switch sessions",
    "/switch":      "Switch to session by ID",
    "/archive":     "Archive current session",
    "/delete":      "Delete current session",
    "/clear":       "Clear current session messages",
    "/incognito":   "Toggle incognito mode",
    "/title":       "Set session title",
    # Agent management
    "/agent":       "Switch agent (or: create/delete/pause/resume/config/soul)",
    "/agent create":  "Create a new agent",
    "/agent delete":  "Delete an agent",
    "/agent pause":   "Pause an agent",
    "/agent resume":  "Resume a paused agent",
    "/agent config":  "Show current agent config",
    "/agent soul":    "Show agent soul.md",
    # Model
    "/model":       "Switch model",
    "/models":      "List & select models",
    # Tools
    "/tools":       "Toggle tool call display",
    # Schedule
    "/schedule":    "Manage scheduled tasks",
    # Teams
    "/teams":       "Show team structure",
    "/teams create":  "Create a new team",
    "/teams dissolve": "Dissolve a team",
    # Skills
    "/skills":      "List installed skills",
    "/skills browse": "Search ClawHub for skills",
    "/skills install": "Install skill from URL",
    "/skills remove":  "Remove an installed skill",
    # Knowledge Graph
    "/graph":       "Show knowledge graph summary",
    "/graph discover": "Discover relationships using AI",
    "/graph stats": "Show graph statistics",
    # Memory
    "/memory":      "List memory files for current agent",
    "/memory summary": "Show memory summary",
    "/memory refresh": "Refresh memory summary",
    # Providers
    "/providers":     "List LLM providers",
    "/providers test": "Test provider connection",
    "/providers add":  "Add a new provider",
    # Status / Tasks
    "/status":      "Show server & service status",
    "/tasks":       "Show running background tasks",
    "/tasks cancel":  "Cancel a running task",
    # QMD
    "/qmd":         "Show QMD status and health",
    "/qmd reindex":   "Trigger QMD reindex",
    # Costs
    "/costs":       "Show cost summary (last 24h)",
    # Cache
    "/cache":       "Show web cache stats",
    "/cache clear": "Clear web cache",
    # Plan mode
    "/plan":        "Toggle plan mode (read-only tools)",
    # Refine
    "/refine":      "Refine text with LLM",
    # Projects
    "/projects":      "List projects for current agent",
    "/projects new":  "Create a new project",
    "/projects open": "Switch to project context",
    "/projects close": "Return to agent-level chat",
    "/projects delete": "Delete a project",
    "/projects manage": "Show project details",
    # Ingest
    "/ingest":        "Ingest file or URL into current project",
    "/ingest list":   "List ingested documents",
    "/ingest delete":  "Delete ingested document",
    "/ingest watch add": "Add watched folder",
    "/ingest watch remove": "Remove watched folder",
    "/ingest watch list": "List watched folders",
    # Workflows
    "/workflow":        "List workflows for current agent",
    "/workflow run":    "Run a workflow",
    "/workflow status": "Show running workflow executions",
    "/workflow approve": "Approve a workflow approval gate",
    "/workflow cancel":  "Cancel a running workflow",
    # Notifications
    "/notifications":    "Show recent notifications",
    # Backup & Restore
    "/backup":           "Create full backup",
    "/backup agent":     "Backup single agent",
    "/restore":          "Restore from backup file",
    # Traces & Audit
    "/traces":          "Show recent traces (last 24h)",
    "/audit":           "Show recent audit log entries",
    "/audit export":    "Export audit log as CSV",
    # MCP
    "/mcp":             "List MCP server connections",
    "/mcp connect":     "Connect to an MCP server",
    "/mcp disconnect":  "Disconnect from an MCP server",
    # Nodes
    "/nodes":           "List remote nodes",
    "/nodes add":       "Add a new remote node",
    "/nodes remove":    "Remove a remote node",
    "/nodes pause":     "Pause a remote node",
    "/nodes resume":    "Resume a paused node",
    # Channels
    "/channels":        "List messaging channels",
    "/channels start":  "Start a messaging channel",
    "/channels stop":   "Stop a messaging channel",
}


class SlashCommandCompleter(Completer):
    """Popup completer for slash commands with descriptions."""

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        if not text.startswith("/"):
            return

        for cmd, desc in SLASH_COMMANDS.items():
            if cmd.startswith(text):
                # Calculate how much text to complete (strip already-typed prefix)
                yield Completion(
                    cmd,
                    start_position=-len(text),
                    display=cmd,
                    display_meta=desc,
                )


# --- Help ---

def print_help():
    console.print()

    def _section(title, cmds):
        console.print(f"  [bold #ff8700]{title}[/]")
        t = Table(show_header=False, box=None, padding=(0, 2), pad_edge=False)
        t.add_column(style="#ff8700 bold", min_width=22)
        t.add_column(style="dim")
        for cmd, desc in cmds:
            t.add_row(cmd, desc)
        console.print(t)
        console.print()

    _section("Session", [
        ("/new", "Start a new conversation"),
        ("/sessions", "List and switch sessions"),
        ("/switch <id>", "Switch to session by ID"),
        ("/archive", "Archive current session"),
        ("/delete", "Delete current session"),
        ("/clear", "Clear current session messages"),
        ("/incognito", "Toggle incognito mode"),
        ("/title <text>", "Set session title"),
    ])

    _section("Agent", [
        ("/agent [name]", "Switch agent (arrow-key menu)"),
        ("/agent create <name>", "Create a new agent"),
        ("/agent delete <name>", "Delete an agent"),
        ("/agent pause <name>", "Pause an agent"),
        ("/agent resume <name>", "Resume a paused agent"),
        ("/agent config", "Show current agent config"),
        ("/agent soul", "Show agent soul.md"),
    ])

    _section("Model", [
        ("/model [name]", "Switch model"),
        ("/models", "List & select models"),
    ])

    _section("Teams", [
        ("/teams", "Show team structure"),
        ("/teams create", "Create a new team"),
        ("/teams dissolve <name>", "Dissolve a team"),
    ])

    _section("Skills", [
        ("/skills", "List installed skills"),
        ("/skills browse <query>", "Search ClawHub for skills"),
        ("/skills install <url>", "Install skill from URL"),
        ("/skills remove <slug>", "Remove an installed skill"),
    ])

    _section("Memory", [
        ("/memory", "List memory files for current agent"),
        ("/memory summary", "Show memory summary"),
        ("/memory refresh", "Refresh memory summary"),
        ("/graph", "Show knowledge graph summary"),
        ("/graph discover", "Discover relationships using AI"),
        ("/graph stats", "Show graph statistics"),
    ])

    _section("Schedule & Tasks", [
        ("/schedule", "Manage scheduled tasks"),
        ("/tasks", "Show running background tasks"),
        ("/tasks cancel <id>", "Cancel a running task"),
    ])

    _section("Providers & Services", [
        ("/providers", "List LLM providers"),
        ("/providers test <name>", "Test provider connection"),
        ("/providers add", "Add a new provider interactively"),
        ("/status", "Show server & service status"),
        ("/qmd", "Show QMD status and health"),
        ("/qmd reindex [coll]", "Trigger QMD reindex"),
    ])

    _section("Cache & Planning", [
        ("/cache", "Show web cache stats"),
        ("/cache clear", "Clear web cache"),
        ("/plan", "Toggle plan mode (read-only tools)"),
        ("/refine <text>", "Refine text with LLM"),
    ])

    _section("Projects", [
        ("/projects", "List projects for current agent"),
        ("/projects new <name>", "Create a new project"),
        ("/projects open [name]", "Switch to project context"),
        ("/projects close", "Return to agent-level chat"),
        ("/projects delete <name>", "Delete a project"),
        ("/projects manage <name>", "Show project details"),
    ])

    _section("Ingest", [
        ("/ingest <file_or_url>", "Ingest document into current project"),
        ("/ingest list", "List ingested documents"),
        ("/ingest delete <source>", "Delete ingested document"),
        ("/ingest watch add <path>", "Add watched folder"),
        ("/ingest watch remove <path>", "Remove watched folder"),
        ("/ingest watch list", "List watched folders"),
    ])

    _section("Workflows", [
        ("/workflow", "List workflows for current agent"),
        ("/workflow run <name>", "Run a workflow"),
        ("/workflow status", "Show running workflow executions"),
        ("/workflow approve <id>", "Approve a workflow approval gate"),
        ("/workflow cancel <id>", "Cancel a running workflow"),
    ])

    _section("Traces & Audit", [
        ("/traces", "Show recent traces (last 24h)"),
        ("/audit", "Show recent audit log entries"),
        ("/audit export", "Export audit log as CSV"),
    ])

    _section("MCP Servers", [
        ("/mcp", "List MCP connections"),
        ("/mcp connect <url> <name>", "Connect to MCP server"),
        ("/mcp disconnect <name>", "Disconnect MCP server"),
    ])

    _section("Other", [
        ("/tools", "Toggle tool call display"),
        ("/help", "Show this help"),
    ])

    t2 = Table(show_header=False, box=None, padding=(0, 2), pad_edge=False)
    t2.add_column(style="yellow", min_width=12)
    t2.add_column(style="dim")
    for key, desc in [
        ("Ctrl+C", "Cancel current request"),
        ("Ctrl+D", "Quit"),
        ("Tab", "Autocomplete commands"),
        ("↑ / ↓", "Input history"),
    ]:
        t2.add_row(key, desc)
    console.print(t2)
    console.print()


# --- Inline selection ---

def select_inline(items: list[str], labels: list[str] | None = None,
                  active: str | None = None) -> str | None:
    if not items:
        return None
    display = labels if labels and len(labels) == len(items) else items
    selected = 0
    if active and active in items:
        selected = items.index(active)

    import termios, select as sel

    fd = sys.stdin.fileno()
    try:
        old = termios.tcgetattr(fd)
    except termios.error:
        return None

    n = len(items)

    def draw():
        for i, label in enumerate(display):
            marker = "[#af87ff bold]❯[/]" if i == selected else " "
            style = "bold" if i == selected else "dim"
            tag = " [#5f87ff](active)[/]" if items[i] == active else ""
            console.print(f"  {marker} [{style}]{label}[/]{tag}")

    def erase():
        sys.stdout.write(f"\033[{n}A")
        for _ in range(n):
            sys.stdout.write("\r\033[K\n")
        sys.stdout.write(f"\033[{n}A")
        sys.stdout.flush()

    try:
        new = termios.tcgetattr(fd)
        new[3] = new[3] & ~(termios.ICANON | termios.ECHO)
        new[6][termios.VMIN] = 0
        new[6][termios.VTIME] = 0
        termios.tcsetattr(fd, termios.TCSANOW, new)

        draw()
        while True:
            if sel.select([fd], [], [], 0.1)[0]:
                ch = os.read(fd, 1)
                if ch in (b'\r', b'\n'):
                    erase(); return items[selected]
                elif ch == b'\x1b':
                    s1 = os.read(fd, 1) if sel.select([fd], [], [], 0.05)[0] else b''
                    if s1 == b'[':
                        s2 = os.read(fd, 1) if sel.select([fd], [], [], 0.05)[0] else b''
                        if s2 == b'A': selected = (selected - 1) % n
                        elif s2 == b'B': selected = (selected + 1) % n
                        else: erase(); return None
                    else: erase(); return None
                elif ch == b'\x03': erase(); return None
                else: continue
                erase(); draw()
    except Exception:
        return None
    finally:
        try:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
        except termios.error:
            pass


# --- Command handlers ---

def _handle_sessions(client: BrainAgentClient, current_agent: str) -> tuple[str | None, int | None]:
    """Handle /sessions — returns (new_session_id, new_token_count) or (None, None)."""
    try:
        sessions = client.list_sessions(agent=current_agent, status="active")
    except Exception as e:
        console.print(f"  [error]{e}[/]")
        return None, None

    if not sessions:
        console.print("  [dim]No active sessions[/]")
        return None, None

    ids = [s["session_id"] for s in sessions]
    labels = []
    for s in sessions:
        title = s.get("title", "Untitled")[:40]
        model = s.get("model", "?")
        msgs = s.get("message_count", 0)
        labels.append(f"{title}  [dim]{model} · {msgs} msgs[/]")

    console.print()
    choice = select_inline(ids, labels=labels, active=client.session_id)
    if choice and choice != client.session_id:
        return choice, 0
    return None, None


def _handle_switch(arg: str, client: BrainAgentClient) -> int | None:
    """Handle /switch <id> — returns new token_count or None."""
    if not arg:
        console.print("  [dim]Usage: /switch <session_id>[/]")
        return None
    try:
        messages = client.get_session_messages(arg)
        client.session_id = arg
        console.print(f"  [#5f87ff]Switched to session {arg[:8]}...[/] [dim]({len(messages)} messages)[/]")
        return 0
    except Exception as e:
        console.print(f"  [error]{e}[/]")
        return None


def _handle_archive(client: BrainAgentClient):
    try:
        r = client.session_action("archive")
        if r.get("error"):
            console.print(f"  [error]{r['error']}[/]")
        else:
            console.print("  [dim]Session archived[/]")
    except Exception as e:
        console.print(f"  [error]{e}[/]")


def _handle_delete_session(client: BrainAgentClient, current_agent: str,
                           current_model: str, max_context: int) -> int:
    """Delete current session and start new. Returns new token_count."""
    try:
        client.session_action("delete")
    except Exception:
        pass
    client.session_id = None
    client.create_session(agent=current_agent, model=current_model, max_context=max_context)
    console.print("  [dim]Session deleted. New conversation started.[/]")
    return 0


def _handle_clear(client: BrainAgentClient) -> int:
    try:
        r = client.session_action("clear")
        if r.get("error"):
            console.print(f"  [error]{r['error']}[/]")
        else:
            console.print("  [dim]Session cleared[/]")
    except Exception as e:
        console.print(f"  [error]{e}[/]")
    return 0


def _handle_incognito(client: BrainAgentClient):
    # Toggle — try incognito first, if already incognito, un_incognito
    try:
        r = client.session_action("incognito")
        if r.get("error") and "already" in r["error"].lower():
            r = client.session_action("un_incognito")
            console.print("  [dim]Incognito OFF[/]")
        elif r.get("error"):
            console.print(f"  [error]{r['error']}[/]")
        else:
            status = r.get("incognito", True)
            label = "ON" if status else "OFF"
            console.print(f"  [#ff8700]Incognito {label}[/]")
    except Exception as e:
        console.print(f"  [error]{e}[/]")


def _handle_title(arg: str, client: BrainAgentClient):
    if not arg:
        console.print("  [dim]Usage: /title <text>[/]")
        return
    try:
        r = client.session_action("rename", title=arg)
        if r.get("error"):
            console.print(f"  [error]{r['error']}[/]")
        else:
            console.print(f"  [dim]Title set:[/] [bold]{arg}[/]")
    except Exception as e:
        console.print(f"  [error]{e}[/]")


def _handle_agent(stripped: str, client: BrainAgentClient, current_agent: str,
                  current_model: str, session) -> tuple[str, str]:
    """Handle all /agent subcommands. Returns (new_agent, new_model)."""
    arg = stripped[6:].strip()
    low_arg = arg.lower()

    if low_arg.startswith("create "):
        name = arg[7:].strip()
        if not name:
            console.print("  [dim]Usage: /agent create <name>[/]")
            return current_agent, current_model
        try:
            desc = session.prompt(HTML("  <b>Description:</b> "))
        except (KeyboardInterrupt, EOFError):
            return current_agent, current_model
        try:
            r = client.create_agent(name, desc.strip())
            if r.get("error"):
                console.print(f"  [error]{r['error']}[/]")
            else:
                console.print(f"  [#5f87ff]Created agent:[/] [agent]{name}[/]")
        except Exception as e:
            console.print(f"  [error]{e}[/]")
        return current_agent, current_model

    if low_arg.startswith("delete "):
        name = arg[7:].strip()
        if not name:
            console.print("  [dim]Usage: /agent delete <name>[/]")
            return current_agent, current_model
        try:
            r = client.delete_agent(name)
            if r.get("error"):
                console.print(f"  [error]{r['error']}[/]")
            else:
                console.print(f"  [dim]Deleted agent:[/] [bold]{name}[/]")
        except Exception as e:
            console.print(f"  [error]{e}[/]")
        return current_agent, current_model

    if low_arg.startswith("pause "):
        name = arg[6:].strip()
        try:
            r = client.pause_agent(name, paused=True)
            if r.get("error"):
                console.print(f"  [error]{r['error']}[/]")
            else:
                console.print(f"  [dim]Paused:[/] [bold]{name}[/]")
        except Exception as e:
            console.print(f"  [error]{e}[/]")
        return current_agent, current_model

    if low_arg.startswith("resume "):
        name = arg[7:].strip()
        try:
            r = client.pause_agent(name, paused=False)
            if r.get("error"):
                console.print(f"  [error]{r['error']}[/]")
            else:
                console.print(f"  [dim]Resumed:[/] [bold]{name}[/]")
        except Exception as e:
            console.print(f"  [error]{e}[/]")
        return current_agent, current_model

    if low_arg == "config":
        try:
            agents_data = client.list_agents()
            agent_info = next((a for a in agents_data if a["id"] == current_agent), None)
            if agent_info:
                t = Table(show_header=False, box=None, padding=(0, 2), pad_edge=False)
                t.add_column(style="#ff8700", min_width=16)
                t.add_column()
                for k, v in agent_info.items():
                    t.add_row(str(k), str(v))
                console.print()
                console.print(t)
                console.print()
            else:
                console.print(f"  [dim]Agent {current_agent} not found[/]")
        except Exception as e:
            console.print(f"  [error]{e}[/]")
        return current_agent, current_model

    if low_arg == "soul":
        try:
            docs = client.get_qmd_docs(collection=current_agent)
            files = docs.get("docs", docs.get("files", []))
            soul = None
            for f in files:
                fname = f.get("file", f.get("name", ""))
                if fname.endswith("soul.md"):
                    soul = f
                    break
            if soul:
                content = soul.get("content", soul.get("body", ""))
                if content:
                    console.print()
                    console.print(Markdown(content))
                    console.print()
                else:
                    # Try reading the file directly
                    console.print(f"  [dim]soul.md found but no content in API response[/]")
                    console.print(f"  [dim]Path: agents/{current_agent}/soul.md[/]")
            else:
                console.print(f"  [dim]No soul.md for {current_agent}[/]")
        except Exception as e:
            console.print(f"  [error]{e}[/]")
        return current_agent, current_model

    # Default: switch agent (existing behavior)
    if arg:
        resp = client.switch_agent(arg)
        new_agent = resp.get("agent", arg)
        new_model = resp.get("model", current_model)
        console.print(f"  [dim]->[/] [agent]{new_agent}[/] [dim]({new_model})[/]")
        return new_agent, new_model
    else:
        agents_data = client.list_agents()
        agent_ids = [a["id"] for a in agents_data]
        labels = [f"{a['id']} — {a.get('description', '')}" for a in agents_data]
        console.print()
        choice = select_inline(agent_ids, labels=labels, active=current_agent)
        if choice:
            resp = client.switch_agent(choice)
            new_agent = resp.get("agent", choice)
            new_model = resp.get("model", current_model)
            console.print(f"  [dim]->[/] [agent]{new_agent}[/] [dim]({new_model})[/]")
            return new_agent, new_model
    return current_agent, current_model


def _handle_teams(arg: str, client: BrainAgentClient, session):
    """Handle /teams commands."""
    low_arg = arg.lower().strip()

    if not low_arg:
        # Show team structure
        try:
            data = client.get_teams()
            teams = data.get("teams", [])
            standalone = data.get("standalone", [])

            if not teams and not standalone:
                console.print("  [dim]No teams configured[/]")
                return

            console.print()
            for team in teams:
                name = team.get("name", "?")
                head = team.get("head", "?")
                avatar = team.get("avatar", "")
                console.print(f"  [bold]{avatar} {name}[/]  [dim]head:[/] [agent]{head}[/]")
                members = team.get("members", [])
                for m in members:
                    console.print(f"    [dim]•[/] {m}")

            if standalone:
                console.print()
                console.print(f"  [dim]Standalone:[/] {', '.join(standalone)}")
            console.print()
        except Exception as e:
            console.print(f"  [error]{e}[/]")
        return

    if low_arg == "create":
        try:
            name = session.prompt(HTML("  <b>Team name:</b> "))
            head = session.prompt(HTML("  <b>Team head agent:</b> "))
            members_str = session.prompt(HTML("  <b>Members (comma-separated):</b> "))
            desc = session.prompt(HTML("  <b>Description:</b> ")) or ""
            avatar = session.prompt(HTML("  <b>Avatar emoji:</b> ")) or ""
        except (KeyboardInterrupt, EOFError):
            return
        members = [m.strip() for m in members_str.split(",") if m.strip()]
        try:
            r = client.team_action("create", name=name.strip(), head=head.strip(),
                                   members=members, description=desc.strip(), avatar=avatar.strip())
            if r.get("error"):
                console.print(f"  [error]{r['error']}[/]")
            else:
                console.print(f"  [#5f87ff]Team created:[/] [bold]{name}[/]")
        except Exception as e:
            console.print(f"  [error]{e}[/]")
        return

    if low_arg.startswith("dissolve "):
        name = arg[9:].strip()
        if not name:
            console.print("  [dim]Usage: /teams dissolve <name>[/]")
            return
        try:
            r = client.team_action("dissolve", name=name)
            if r.get("error"):
                console.print(f"  [error]{r['error']}[/]")
            else:
                console.print(f"  [dim]Team dissolved:[/] [bold]{name}[/]")
        except Exception as e:
            console.print(f"  [error]{e}[/]")
        return

    console.print("  [dim]/teams [create | dissolve <name>][/]")


def _handle_skills(arg: str, client: BrainAgentClient, current_agent: str):
    """Handle /skills commands."""
    low_arg = arg.lower().strip()

    if not low_arg:
        # List installed skills
        try:
            data = client.skills_list(agent=current_agent)
            skills = data.get("skills", [])
            if not skills:
                console.print("  [dim]No skills installed[/]")
                return
            t = Table(show_header=True, box=None, padding=(0, 1))
            t.add_column("Skill", style="bold")
            t.add_column("Description", style="dim")
            for s in skills:
                t.add_row(s.get("name", s.get("slug", "?")),
                          s.get("description", "")[:60])
            console.print()
            console.print(t)
            console.print()
        except Exception as e:
            console.print(f"  [error]{e}[/]")
        return

    if low_arg.startswith("browse "):
        query = arg[7:].strip()
        if not query:
            console.print("  [dim]Usage: /skills browse <query>[/]")
            return
        try:
            data = client.skills_browse(query, agent=current_agent)
            results = data.get("results", [])
            if not results:
                console.print("  [dim]No skills found[/]")
                return
            t = Table(show_header=True, box=None, padding=(0, 1))
            t.add_column("Name", style="bold")
            t.add_column("Author", style="dim")
            t.add_column("Description", style="dim")
            t.add_column("URL", style="#5f87ff")
            for s in results[:15]:
                t.add_row(s.get("name", "?"), s.get("author", "?"),
                          s.get("description", "")[:40], s.get("url", "")[:40])
            console.print()
            console.print(t)
            console.print(f"  [dim]{len(results)} results[/]")
            console.print()
        except Exception as e:
            console.print(f"  [error]{e}[/]")
        return

    if low_arg.startswith("install "):
        url = arg[8:].strip()
        if not url:
            console.print("  [dim]Usage: /skills install <url>[/]")
            return
        try:
            r = client.skills_install(url, agent=current_agent)
            if r.get("error"):
                console.print(f"  [error]{r['error']}[/]")
            else:
                console.print(f"  [#5f87ff]Skill installed:[/] [bold]{r.get('name', url)}[/]")
        except Exception as e:
            console.print(f"  [error]{e}[/]")
        return

    if low_arg.startswith("remove "):
        slug = arg[7:].strip()
        if not slug:
            console.print("  [dim]Usage: /skills remove <slug>[/]")
            return
        try:
            r = client.skills_remove(slug, agent=current_agent)
            if r.get("error"):
                console.print(f"  [error]{r['error']}[/]")
            else:
                console.print(f"  [dim]Skill removed:[/] [bold]{slug}[/]")
        except Exception as e:
            console.print(f"  [error]{e}[/]")
        return

    console.print("  [dim]/skills [browse <query> | install <url> | remove <slug>][/]")


def _handle_graph(client: BrainAgentClient, current_agent: str):
    """Handle /graph command — show text-based knowledge graph summary."""
    try:
        data = client.get_knowledge_graph(current_agent)
        nodes = data.get("nodes", [])
        edges = data.get("edges", [])

        if not nodes:
            console.print("  [dim]No memories yet[/]")
            return

        # Count by type
        type_counts = {}
        ingested_count = 0
        memory_count = 0
        for n in nodes:
            t = n.get("type", "general")
            type_counts[t] = type_counts.get(t, 0) + 1
            if t == "ingested":
                ingested_count += 1
            else:
                memory_count += 1

        # Group ingested by source
        sources = {}
        for n in nodes:
            if n.get("type") == "ingested" and n.get("source") and n["source"] != "agent":
                src = n["source"]
                sources[src] = sources.get(src, 0) + 1

        # Find top connected nodes
        conn_count = {}
        for e in edges:
            conn_count[e["from"]] = conn_count.get(e["from"], 0) + 1
            conn_count[e["to"]] = conn_count.get(e["to"], 0) + 1

        top_connected = sorted(conn_count.items(), key=lambda x: x[1], reverse=True)[:5]

        console.print()
        console.print(f"  [bold]Knowledge Graph: {current_agent}[/]")
        console.print(f"    Nodes: [#5f87ff]{len(nodes)}[/] ({memory_count} memories, {ingested_count} ingested)")
        console.print(f"    Edges: [#5f87ff]{len(edges)}[/]")

        if sources:
            src_parts = []
            for src, cnt in sorted(sources.items(), key=lambda x: x[1], reverse=True):
                src_parts.append(f"{src} ({cnt} chunks)")
            console.print(f"    Sources: {', '.join(src_parts)}")

        if top_connected:
            console.print()
            console.print("    [bold]Top connected:[/]")
            # Build name lookup
            name_map = {n["id"]: n for n in nodes}
            for nid, cnt in top_connected:
                node = name_map.get(nid, {})
                name = node.get("name", nid)
                suffix = ""
                if node.get("type") == "ingested" and node.get("source") and node["source"] != "agent":
                    suffix = f" ({node['source']})"
                console.print(f"      {name} [dim]— {cnt} connections{suffix}[/]")

        console.print()
    except Exception as e:
        console.print(f"  [error]{e}[/]")


def _handle_graph_discover(client: BrainAgentClient, current_agent: str):
    """Handle /graph discover — trigger AI relationship discovery."""
    try:
        console.print(f"  [dim]Discovering relationships for {current_agent}... this may take a minute[/]")
        result = client._post(f"/v1/agents/{current_agent}/graph/discover", {})
        console.print(f"  [#5f87ff]Discovery started.[/] Run [bold]/graph stats[/] later to see results.")
    except Exception as e:
        console.print(f"  [error]{e}[/]")


def _handle_graph_stats(client: BrainAgentClient, current_agent: str):
    """Handle /graph stats — show graph statistics."""
    try:
        stats = client._get(f"/v1/agents/{current_agent}/graph/stats")
        console.print()
        console.print(f"  [bold]Graph Stats: {current_agent}[/]")
        console.print(f"    Nodes: [#5f87ff]{stats.get('total_nodes', 0)}[/]")
        console.print(f"    Edges: [#5f87ff]{stats.get('total_edges', 0)}[/]")
        console.print(f"    Auto-discovered: [#5f87ff]{stats.get('auto_discovered_edges', 0)}[/]")
        console.print(f"    Entities tracked: [#5f87ff]{stats.get('entity_count', 0)}[/]")
        edge_types = stats.get("edge_types", {})
        if edge_types:
            console.print("    Edge types:")
            for etype, count in sorted(edge_types.items()):
                console.print(f"      {etype}: {count}")
        console.print()
    except Exception as e:
        console.print(f"  [error]{e}[/]")


def _handle_memory(arg: str, client: BrainAgentClient, current_agent: str):
    """Handle /memory commands."""
    low_arg = arg.lower().strip()

    if not low_arg:
        # List memory files via QMD docs
        try:
            data = client.get_qmd_docs(collection=current_agent)
            docs = data.get("docs", data.get("files", []))
            if not docs:
                console.print("  [dim]No memory files[/]")
                return
            t = Table(show_header=True, box=None, padding=(0, 1))
            t.add_column("File", style="bold")
            t.add_column("Indexed", style="dim")
            t.add_column("Embedded", style="dim")
            t.add_column("Current", style="dim")
            for d in docs:
                fname = d.get("file", d.get("name", "?"))
                indexed = "yes" if d.get("indexed") else "no"
                embedded = d.get("embedded_at", "-")
                if embedded and len(embedded) > 16:
                    embedded = embedded[:16]
                current = "[#5f87ff]yes[/]" if d.get("current") else "[warning]stale[/]"
                t.add_row(fname, indexed, embedded, current)
            console.print()
            console.print(t)
            console.print()
        except Exception as e:
            console.print(f"  [error]{e}[/]")
        return

    if low_arg == "summary":
        try:
            data = client.get_memory_summary(agent=current_agent)
            summary = data.get("summary", data.get("content", ""))
            if summary:
                console.print()
                console.print(Markdown(summary))
                console.print()
            else:
                console.print("  [dim]No memory summary available[/]")
        except Exception as e:
            console.print(f"  [error]{e}[/]")
        return

    if low_arg == "refresh":
        try:
            r = client.memory_summary_action(current_agent, "refresh")
            if r.get("error"):
                console.print(f"  [error]{r['error']}[/]")
            else:
                console.print("  [#5f87ff]Memory summary refresh triggered[/]")
        except Exception as e:
            console.print(f"  [error]{e}[/]")
        return

    console.print("  [dim]/memory [summary | refresh][/]")


def _handle_providers(arg: str, client: BrainAgentClient, session):
    """Handle /providers commands."""
    low_arg = arg.lower().strip()

    if not low_arg:
        try:
            providers = client.list_providers()
            if not providers:
                console.print("  [dim]No providers configured[/]")
                return
            t = Table(show_header=True, box=None, padding=(0, 1))
            t.add_column("Name", style="bold")
            t.add_column("Type", style="#5f87ff")
            t.add_column("Base URL", style="dim")
            t.add_column("Models", style="dim")
            for p in providers:
                models_count = len(p.get("models", []))
                t.add_row(p.get("name", "?"), p.get("type", "?"),
                          p.get("base_url", "?")[:40], str(models_count))
            console.print()
            console.print(t)
            console.print()
        except Exception as e:
            console.print(f"  [error]{e}[/]")
        return

    if low_arg.startswith("test "):
        name = arg[5:].strip()
        if not name:
            console.print("  [dim]Usage: /providers test <name>[/]")
            return
        try:
            r = client.provider_action("test", name=name)
            if r.get("error"):
                console.print(f"  [error]{r['error']}[/]")
            elif r.get("ok") or r.get("status") == "ok":
                console.print(f"  [#5f87ff]Provider {name} is reachable[/]")
            else:
                console.print(f"  [warning]Response: {json.dumps(r)[:80]}[/]")
        except Exception as e:
            console.print(f"  [error]{e}[/]")
        return

    if low_arg == "add":
        try:
            name = session.prompt(HTML("  <b>Provider name:</b> "))
            ptype = session.prompt(HTML("  <b>Type (openai/anthropic):</b> "))
            base_url = session.prompt(HTML("  <b>Base URL:</b> "))
            api_key = session.prompt(HTML("  <b>API Key:</b> "))
        except (KeyboardInterrupt, EOFError):
            return
        try:
            r = client.provider_action("add", name=name.strip(), type=ptype.strip(),
                                       base_url=base_url.strip(), api_key=api_key.strip())
            if r.get("error"):
                console.print(f"  [error]{r['error']}[/]")
            else:
                console.print(f"  [#5f87ff]Provider added:[/] [bold]{name}[/]")
        except Exception as e:
            console.print(f"  [error]{e}[/]")
        return

    console.print("  [dim]/providers [test <name> | add][/]")


def _handle_status(client: BrainAgentClient):
    """Handle /status command."""
    try:
        data = client.get_services()
        console.print()
        t = Table(show_header=True, box=None, padding=(0, 2), pad_edge=False)
        t.add_column("Service", style="bold")
        t.add_column("Status")
        t.add_column("Details", style="dim")

        services = data.get("services", data)
        if isinstance(services, list):
            for svc in services:
                name = svc.get("name", "?")
                status = svc.get("status", "?")
                sty = "#5f87ff" if status in ("running", "ok", "healthy") else "red"
                details = svc.get("details", svc.get("url", ""))
                t.add_row(name, f"[{sty}]{status}[/]", str(details)[:50])
        elif isinstance(services, dict):
            for name, info in services.items():
                if isinstance(info, dict):
                    status = info.get("status", "?")
                    sty = "#5f87ff" if status in ("running", "ok", "healthy") else "red"
                    details = info.get("url", info.get("details", ""))
                    t.add_row(name, f"[{sty}]{status}[/]", str(details)[:50])
                else:
                    t.add_row(name, str(info), "")

        console.print(t)
        console.print()
    except Exception as e:
        console.print(f"  [error]{e}[/]")


def _handle_tasks(arg: str, client: BrainAgentClient):
    """Handle /tasks commands."""
    low_arg = arg.lower().strip()

    if low_arg.startswith("cancel "):
        task_id = arg[7:].strip()
        if not task_id:
            console.print("  [dim]Usage: /tasks cancel <id>[/]")
            return
        try:
            r = client.cancel_task(task_id)
            if r.get("error"):
                console.print(f"  [error]{r['error']}[/]")
            else:
                console.print(f"  [dim]Task cancelled:[/] {task_id}")
        except Exception as e:
            console.print(f"  [error]{e}[/]")
        return

    # Default: list tasks
    try:
        tasks = client.list_tasks()
        if not tasks:
            console.print("  [dim]No running tasks[/]")
            return
        t = Table(show_header=True, box=None, padding=(0, 1))
        t.add_column("ID", style="dim")
        t.add_column("Agent", style="#af87ff")
        t.add_column("Status")
        t.add_column("Task", style="dim")
        for task in tasks:
            tid = str(task.get("id", "?"))[:8]
            agent = task.get("agent", "?")
            status = task.get("status", "?")
            sty = "#5f87ff" if status == "running" else "dim"
            desc = task.get("task", task.get("description", ""))[:40]
            t.add_row(tid, agent, f"[{sty}]{status}[/]", desc)
        console.print()
        console.print(t)
        console.print()
    except Exception as e:
        console.print(f"  [error]{e}[/]")


def _handle_workflow(arg: str, client: BrainAgentClient, current_agent: str):
    """Handle /workflow commands."""
    low_arg = arg.lower().strip()

    if low_arg.startswith("run "):
        wf_name = arg[4:].strip()
        if not wf_name:
            console.print("  [dim]Usage: /workflow run <name>[/]")
            return
        # Get workflow to check for required variables
        try:
            workflows = client.list_workflows(current_agent)
            wf = next((w for w in workflows if w.get("file", "").replace(".yaml", "").replace(".yml", "") == wf_name
                        or w.get("name", "").lower() == wf_name.lower()), None)
            variables = {}
            if wf and wf.get("variables"):
                console.print(f"  [bold]Workflow variables:[/]")
                for vname in wf["variables"]:
                    if vname:
                        val = console.input(f"    {vname}: ")
                        variables[vname] = val
            r = client.run_workflow(current_agent, wf_name, variables)
            if r.get("error"):
                console.print(f"  [error]{r['error']}[/]")
            else:
                eid = r.get("execution_id", "?")
                console.print(f"  [#5f87ff]Workflow started:[/] {wf_name}")
                console.print(f"  [dim]Execution ID:[/] {eid}")
                console.print(f"  [dim]Use /workflow status to monitor progress[/]")
        except Exception as e:
            console.print(f"  [error]{e}[/]")
        return

    if low_arg.startswith("status"):
        try:
            execs = client.get_executions()
            if not execs:
                console.print("  [dim]No workflow executions[/]")
                return
            for ex in execs:
                eid = ex.get("execution_id", "?")
                wname = ex.get("workflow_name", "?")
                status = ex.get("status", "?")
                current = ex.get("current_stage", "")
                total = ex.get("total_stages", 0)
                cidx = ex.get("current_stage_idx", -1) + 1

                sty_map = {
                    "running": "#5f87ff", "waiting_approval": "#ff8700",
                    "completed": "#5faf5f", "failed": "#ff5f5f",
                    "cancelled": "dim",
                }
                sty = sty_map.get(status, "dim")
                console.print(f"\n  [bold]{wname}[/] [{sty}]{status}[/]  (id: {eid})")

                # Show stage pipeline
                stages = ex.get("stages", [])
                for s in stages:
                    sn = s.get("name", "?")
                    ss = s.get("status", "pending")
                    elapsed = s.get("elapsed", 0)
                    icon = {"completed": "[#5faf5f]OK[/]", "running": "[#5f87ff]>>[/]",
                            "waiting_approval": "[#ff8700]??[/]", "error": "[#ff5f5f]ERR[/]",
                            "rejected": "[#ff5f5f]REJ[/]", "cancelled": "[dim]--[/]",
                            }.get(ss, "[dim]..[/]")
                    elapsed_str = f" {elapsed:.0f}s" if elapsed else ""
                    console.print(f"    {icon} {sn}{elapsed_str}")

                if status == "waiting_approval":
                    console.print(f"  [#ff8700]Use /workflow approve {eid} to approve[/]")
                console.print()
        except Exception as e:
            console.print(f"  [error]{e}[/]")
        return

    if low_arg.startswith("approve "):
        eid = arg[8:].strip()
        if not eid:
            console.print("  [dim]Usage: /workflow approve <execution_id>[/]")
            return
        try:
            r = client.approve_workflow(eid, "approve")
            if r.get("error"):
                console.print(f"  [error]{r['error']}[/]")
            else:
                console.print(f"  [#5faf5f]Approved:[/] {eid}")
        except Exception as e:
            console.print(f"  [error]{e}[/]")
        return

    if low_arg.startswith("reject "):
        eid = arg[7:].strip()
        if not eid:
            console.print("  [dim]Usage: /workflow reject <execution_id>[/]")
            return
        try:
            r = client.approve_workflow(eid, "reject")
            if r.get("error"):
                console.print(f"  [error]{r['error']}[/]")
            else:
                console.print(f"  [#ff5f5f]Rejected:[/] {eid}")
        except Exception as e:
            console.print(f"  [error]{e}[/]")
        return

    if low_arg.startswith("cancel "):
        eid = arg[7:].strip()
        if not eid:
            console.print("  [dim]Usage: /workflow cancel <execution_id>[/]")
            return
        try:
            r = client.cancel_workflow(eid)
            if r.get("error"):
                console.print(f"  [error]{r['error']}[/]")
            else:
                console.print(f"  [dim]Workflow cancelled:[/] {eid}")
        except Exception as e:
            console.print(f"  [error]{e}[/]")
        return

    # Default: list workflows
    try:
        workflows = client.list_workflows(current_agent)
        if not workflows:
            console.print(f"  [dim]No workflows for agent '{current_agent}'[/]")
            console.print(f"  [dim]Workflows are stored in agents/{current_agent}/workflows/*.yaml[/]")
            return
        t = Table(show_header=True, box=None, padding=(0, 1))
        t.add_column("Name", style="#af87ff")
        t.add_column("File", style="dim")
        t.add_column("Stages")
        t.add_column("Description", style="dim")
        for wf in workflows:
            t.add_row(
                wf.get("name", "?"),
                wf.get("file", "?"),
                str(wf.get("stages", 0)),
                (wf.get("description", "") or "")[:50],
            )
        console.print()
        console.print(t)
        console.print()
        console.print("  [dim]/workflow run <name> | status | approve <id> | cancel <id>[/]")
    except Exception as e:
        console.print(f"  [error]{e}[/]")


def _handle_qmd(arg: str, client: BrainAgentClient, current_agent: str):
    """Handle /qmd commands."""
    low_arg = arg.lower().strip()

    if low_arg.startswith("reindex"):
        coll = arg[7:].strip() or None
        try:
            r = client.qmd_action("reindex", collection=coll)
            if r.get("error"):
                console.print(f"  [error]{r['error']}[/]")
            else:
                label = coll or "all collections"
                console.print(f"  [#5f87ff]Reindex triggered:[/] {label}")
        except Exception as e:
            console.print(f"  [error]{e}[/]")
        return

    # Default: show QMD status
    try:
        data = client.get_services()
        services = data.get("services", data)
        qmd_info = None
        if isinstance(services, dict):
            qmd_info = services.get("qmd", services.get("QMD"))
        elif isinstance(services, list):
            qmd_info = next((s for s in services if s.get("name", "").lower() == "qmd"), None)

        console.print()
        if qmd_info:
            if isinstance(qmd_info, dict):
                status = qmd_info.get("status", "?")
                sty = "#5f87ff" if status in ("running", "ok", "healthy") else "red"
                console.print(f"  [bold]QMD[/]  [{sty}]{status}[/]")
                for k, v in qmd_info.items():
                    if k != "status":
                        console.print(f"  [dim]{k}:[/] {v}")
            else:
                console.print(f"  [bold]QMD[/]  {qmd_info}")
        else:
            console.print("  [dim]QMD status not available[/]")

        # Show collection health
        docs_data = client.get_qmd_docs(collection=current_agent)
        docs = docs_data.get("docs", docs_data.get("files", []))
        if docs:
            total = len(docs)
            current_count = sum(1 for d in docs if d.get("current"))
            stale = total - current_count
            console.print(f"  [dim]Collection {current_agent}:[/] {total} docs, "
                          f"[#5f87ff]{current_count} current[/]"
                          f"{f', [warning]{stale} stale[/]' if stale else ''}")
        console.print()
    except Exception as e:
        console.print(f"  [error]{e}[/]")


def _handle_costs(client: BrainAgentClient):
    """Handle /costs command — show cost summary."""
    try:
        data = client._get("/v1/costs?hours=24")
        if data.get("error"):
            console.print(f"  [error]{data['error']}[/]")
            return
        console.print()
        console.print("  [bold]Cost Summary (last 24h)[/]")
        console.print()

        by_agent = data.get("by_agent", [])
        if not by_agent:
            console.print("  [dim]No API calls recorded yet.[/]")
            console.print()
            return

        t = Table(show_header=True, box=None, padding=(0, 2))
        t.add_column("Agent", style="bold")
        t.add_column("Requests", justify="right")
        t.add_column("Tokens In", justify="right")
        t.add_column("Tokens Out", justify="right")
        t.add_column("Est. Cost", justify="right", style="#ff8700")
        for row in by_agent:
            t.add_row(
                row["agent"],
                str(row["calls"]),
                _fmt_tokens(row["tokens_in"]),
                _fmt_tokens(row["tokens_out"]),
                f"${row['cost']:.2f}",
            )
        # Total row
        t.add_row(
            "[bold]TOTAL[/]",
            str(data["total_calls"]),
            _fmt_tokens(data["total_tokens_in"]),
            _fmt_tokens(data["total_tokens_out"]),
            f"[bold]${data['total_cost']:.2f}[/]",
            style="dim",
        )
        console.print(t)

        # Top models
        by_model = data.get("by_model", [])
        if by_model:
            parts = [f"{m['model']} (${m['cost']:.2f})" for m in by_model[:4]]
            console.print(f"\n  [dim]Top models:[/] {', '.join(parts)}")
        console.print()
    except Exception as e:
        console.print(f"  [error]{e}[/]")


def _fmt_tokens(n: int) -> str:
    """Format token count: 1234 -> '1.2K', 1234567 -> '1.2M'."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def _handle_traces(client: BrainAgentClient):
    """Handle /traces command — show recent traces."""
    try:
        data = client._get("/v1/traces?hours=24&limit=20")
        if data.get("error"):
            console.print(f"  [error]{data['error']}[/]")
            return
        traces = data.get("traces", [])
        if not traces:
            console.print("  [dim]No traces recorded yet.[/]")
            return
        console.print()
        console.print("  [bold]Recent Traces (last 24h)[/]")
        console.print()
        t = Table(show_header=True, box=None, padding=(0, 1))
        t.add_column("Time", style="dim")
        t.add_column("Agent", style="#af87ff")
        t.add_column("Type", style="bold")
        t.add_column("Name")
        t.add_column("Duration", justify="right")
        t.add_column("Status", justify="center")
        t.add_column("Spans", justify="right", style="dim")
        for tr in traces:
            ts = tr.get("started_at", "")
            if "T" in ts:
                ts = ts.split("T")[1][:8]
            dur = tr.get("duration_ms")
            dur_str = f"{dur/1000:.1f}s" if dur else "?"
            status = tr.get("status", "ok")
            status_style = "#5f87ff" if status == "ok" else "red"
            t.add_row(
                ts,
                tr.get("agent", "?"),
                tr.get("type", "?"),
                (tr.get("name", "?"))[:40],
                dur_str,
                f"[{status_style}]{status}[/]",
                str(tr.get("span_count", 0)),
            )
        console.print(t)
        console.print()
    except Exception as e:
        console.print(f"  [error]{e}[/]")


def _handle_audit(arg: str, client: BrainAgentClient):
    """Handle /audit command — show recent audit log entries."""
    if arg.startswith("export"):
        try:
            console.print("  [dim]Downloading audit CSV...[/]")
            import urllib.request
            req = urllib.request.Request(f"{client.server_url}/v1/audit/export?format=csv")
            with urllib.request.urlopen(req, timeout=10) as resp:
                csv_data = resp.read().decode("utf-8")
            out_path = "audit_log.csv"
            with open(out_path, "w") as f:
                f.write(csv_data)
            console.print(f"  [#5f87ff]Exported to {out_path}[/] ({len(csv_data)} bytes)")
        except Exception as e:
            console.print(f"  [error]{e}[/]")
        return

    try:
        data = client._get("/v1/audit?limit=20")
        if data.get("error"):
            console.print(f"  [error]{data['error']}[/]")
            return
        entries = data.get("entries", [])
        if not entries:
            console.print("  [dim]No audit entries yet.[/]")
            return
        console.print()
        console.print("  [bold]Recent Audit Log[/]")
        console.print()
        t = Table(show_header=True, box=None, padding=(0, 1))
        t.add_column("Time", style="dim")
        t.add_column("Agent", style="#af87ff")
        t.add_column("Action", style="bold")
        t.add_column("Tool", style="#ff8700")
        t.add_column("Summary")
        t.add_column("Status", justify="center")
        for entry in entries:
            ts = entry.get("timestamp", "")
            if "T" in ts:
                ts = ts.split("T")[1][:8]
            status = entry.get("result_status", "success")
            status_style = "#5f87ff" if status == "success" else "red"
            t.add_row(
                ts,
                entry.get("agent", "?"),
                entry.get("action_type", "?"),
                entry.get("tool_name", "?"),
                (entry.get("args_summary", ""))[:40],
                f"[{status_style}]{status}[/]",
            )
        console.print(t)
        console.print()
    except Exception as e:
        console.print(f"  [error]{e}[/]")


def _handle_mcp(arg: str, client: BrainAgentClient, session):
    """Handle /mcp command — manage MCP connections."""
    if arg.startswith("connect"):
        parts = arg[7:].strip().split(None, 1)
        if len(parts) < 2:
            console.print("  [dim]Usage: /mcp connect <url> <name>[/]")
            return
        url, name = parts
        try:
            result = client._post("/v1/mcp/connect", {"url": url, "name": name})
            if result.get("error"):
                console.print(f"  [error]{result['error']}[/]")
            else:
                tools = result.get("tools", [])
                console.print(f"  [#5f87ff]Connected to {name}[/] ({len(tools)} tools)")
                for tool in tools[:10]:
                    console.print(f"    [dim]{tool.get('name', '?')}[/]: {tool.get('description', '')[:60]}")
        except Exception as e:
            console.print(f"  [error]{e}[/]")
        return

    if arg.startswith("disconnect"):
        name = arg[10:].strip()
        if not name:
            console.print("  [dim]Usage: /mcp disconnect <name>[/]")
            return
        try:
            result = client._post("/v1/mcp/disconnect", {"name": name})
            if result.get("error"):
                console.print(f"  [error]{result['error']}[/]")
            else:
                console.print(f"  [dim]Disconnected from {name}[/]")
        except Exception as e:
            console.print(f"  [error]{e}[/]")
        return

    # Default: list connections
    try:
        data = client._get("/v1/mcp/connections")
        connections = data.get("connections", [])
        if not connections:
            console.print("  [dim]No MCP connections[/]")
            return
        console.print()
        console.print("  [bold]MCP Connections[/]")
        console.print()
        t = Table(show_header=True, box=None, padding=(0, 2))
        t.add_column("Name", style="bold")
        t.add_column("Transport", style="dim")
        t.add_column("Tools", justify="right")
        t.add_column("Tool Names", style="dim")
        for conn in connections:
            tools = conn.get("tools", [])
            tool_names = ", ".join(tools[:5])
            if len(tools) > 5:
                tool_names += f" +{len(tools)-5}"
            t.add_row(
                conn.get("name", "?"),
                conn.get("transport", "?"),
                str(conn.get("tool_count", len(tools))),
                tool_names,
            )
        console.print(t)
        console.print()
    except Exception as e:
        console.print(f"  [error]{e}[/]")


def _handle_schedule(arg: str, client: BrainAgentClient, session):
    if not arg or arg == "list":
        schedules = client.list_schedule()
        if not schedules:
            console.print("  [dim]No scheduled tasks[/]"); return
        t = Table(show_header=True, box=None, padding=(0, 1))
        t.add_column("Name", style="bold")
        t.add_column("Status")
        t.add_column("Schedule", style="dim")
        t.add_column("Agent", style="#af87ff")
        t.add_column("Next", style="dim")
        for s in schedules:
            st = "[#5f87ff]active[/]" if s["enabled"] else "[dim]paused[/]"
            nr = s.get("next_run", "")[:16] if s.get("next_run") else "—"
            t.add_row(s["name"], st, s["schedule"], s["agent"], nr)
        console.print(); console.print(t); console.print()

    elif arg == "add":
        try:
            name = session.prompt(HTML("  <b>Name:</b> "))
            task = session.prompt(HTML("  <b>Task:</b> "))
            sched_str = session.prompt(HTML("  <b>Schedule:</b> "))
            agent = session.prompt(HTML("  <b>Agent</b> (main): ")) or "main"
            model = session.prompt(HTML("  <b>Model</b> (current): ")) or None
        except (KeyboardInterrupt, EOFError):
            return
        r = client.schedule_action("add", name=name.strip(), task=task.strip(),
                                    schedule=sched_str.strip(), agent=agent.strip(), model=model)
        if r.get("error"):
            console.print(f"  [error]{r['error']}[/]")
        else:
            console.print(f"  [#5f87ff]Created:[/] [bold]{name}[/]")

    elif arg.startswith("pause "):
        r = client.schedule_action("pause", name=arg[6:].strip())
        msg = "[dim]Paused[/]" if not r.get("error") else f"[error]{r['error']}[/]"
        console.print(f"  {msg}")
    elif arg.startswith("resume "):
        r = client.schedule_action("resume", name=arg[7:].strip())
        msg = "[dim]Resumed[/]" if not r.get("error") else f"[error]{r['error']}[/]"
        console.print(f"  {msg}")
    elif arg.startswith(("delete ", "rm ")):
        r = client.schedule_action("delete", name=arg.split(" ", 1)[1].strip())
        msg = "[dim]Deleted[/]" if not r.get("error") else f"[error]{r['error']}[/]"
        console.print(f"  {msg}")
    elif arg == "history":
        r = client.schedule_action("history", limit=10)
        h = r.get("history", [])
        if not h:
            console.print("  [dim]No history[/]"); return
        for e in h:
            sty = "#5f87ff" if e["status"] == "success" else "red"
            console.print(f"  [{sty}]{e['status']}[/] [bold]{e['schedule_name']}[/] [dim]{e['finished_at'][:16]}[/]")
    else:
        console.print("  [dim]/schedule [list|add|pause|resume|delete|history][/]")


def _handle_projects(arg: str, client: BrainAgentClient, current_agent: str,
                     current_project: str | None, session) -> str | None:
    """Handle /projects commands. Returns new current_project or None."""
    low_arg = arg.lower().strip()

    if not low_arg:
        # List projects
        try:
            projects = client.list_projects(current_agent)
            if not projects:
                console.print("  [dim]No projects. Use /projects new <name> to create one.[/]")
                return current_project
            t = Table(show_header=True, box=None, padding=(0, 1))
            t.add_column("Name", style="bold")
            t.add_column("Description", style="dim")
            t.add_column("Docs", justify="right")
            t.add_column("Chunks", justify="right")
            t.add_column("Folders", justify="right")
            for p in projects:
                name = p.get("name", "?")
                active_marker = " [#5f87ff](active)[/]" if name == current_project else ""
                t.add_row(
                    f"{name}{active_marker}",
                    p.get("description", "")[:40],
                    str(p.get("document_count", 0)),
                    str(p.get("chunk_count", 0)),
                    str(len(p.get("watch_folders", []))),
                )
            console.print()
            console.print(t)
            console.print()
        except Exception as e:
            console.print(f"  [error]{e}[/]")
        return current_project

    if low_arg.startswith("new "):
        name = arg[4:].strip()
        if not name:
            console.print("  [dim]Usage: /projects new <name>[/]")
            return current_project
        try:
            desc = session.prompt(HTML("  <b>Description:</b> "))
        except (KeyboardInterrupt, EOFError):
            return current_project
        try:
            r = client.create_project(current_agent, name, desc.strip())
            if r.get("error"):
                console.print(f"  [error]{r['error']}[/]")
            else:
                console.print(f"  [#5f87ff]Project created:[/] [bold]{name}[/]")
        except Exception as e:
            console.print(f"  [error]{e}[/]")
        return current_project

    if low_arg.startswith("open"):
        name = arg[4:].strip()
        if not name:
            # Show selection menu
            try:
                projects = client.list_projects(current_agent)
                if not projects:
                    console.print("  [dim]No projects[/]")
                    return current_project
                names = [p.get("name", "") for p in projects]
                labels = [f"{p.get('name', '')} — {p.get('description', '')}" for p in projects]
                console.print()
                choice = select_inline(names, labels=labels, active=current_project)
                if choice:
                    console.print(f"  [#5f87ff]Project:[/] [bold]{choice}[/]")
                    return choice
            except Exception as e:
                console.print(f"  [error]{e}[/]")
            return current_project
        console.print(f"  [#5f87ff]Project:[/] [bold]{name}[/]")
        return name

    if low_arg == "close":
        console.print("  [dim]Project context cleared[/]")
        return None

    if low_arg.startswith("delete "):
        name = arg[7:].strip()
        if not name:
            console.print("  [dim]Usage: /projects delete <name>[/]")
            return current_project
        try:
            confirm = session.prompt(HTML(f"  Delete project <b>{name}</b>? [y/N] "))
        except (KeyboardInterrupt, EOFError):
            return current_project
        if confirm.strip().lower() != "y":
            console.print("  [dim]Cancelled[/]")
            return current_project
        try:
            r = client.delete_project(current_agent, name)
            if r.get("error"):
                console.print(f"  [error]{r['error']}[/]")
            else:
                console.print(f"  [dim]Project deleted:[/] [bold]{name}[/]")
                if current_project == name:
                    return None
        except Exception as e:
            console.print(f"  [error]{e}[/]")
        return current_project

    if low_arg.startswith("manage "):
        name = arg[7:].strip()
        if not name:
            console.print("  [dim]Usage: /projects manage <name>[/]")
            return current_project
        try:
            p = client.get_project(current_agent, name)
            console.print()
            console.print(f"  [bold]{p.get('name', name)}[/]")
            console.print(f"  [dim]{p.get('description', '')}[/]")
            console.print()

            # Documents
            docs = p.get("documents", [])
            if docs:
                console.print(f"  [bold]Documents[/] ({len(docs)})")
                dt = Table(show_header=True, box=None, padding=(0, 1))
                dt.add_column("Source", style="bold")
                dt.add_column("Type", style="#5f87ff")
                dt.add_column("Chunks", justify="right")
                dt.add_column("Ingested", style="dim")
                for d in docs:
                    dt.add_row(
                        d.get("source", "?")[:40],
                        d.get("source_type", "?"),
                        str(d.get("chunks", 0)),
                        d.get("ingested_at", "")[:16],
                    )
                console.print(dt)
                console.print()

            # Watch folders
            folders = p.get("watch_folders", [])
            if folders:
                console.print(f"  [bold]Watched Folders[/] ({len(folders)})")
                for f in folders:
                    console.print(f"    {f.get('path', '?')}  [dim]{f.get('pattern', '*')} "
                                  f"{'(recursive)' if f.get('recursive') else ''}  "
                                  f"{f.get('file_count', 0)} files[/]")
                console.print()

            # Tags
            tags = p.get("tags", [])
            if tags:
                console.print(f"  [dim]Tags:[/] {', '.join(tags)}")
                console.print()
        except Exception as e:
            console.print(f"  [error]{e}[/]")
        return current_project

    console.print("  [dim]/projects [new <name> | open [name] | close | delete <name> | manage <name>][/]")
    return current_project


def _handle_ingest(arg: str, client: BrainAgentClient, current_agent: str,
                   current_project: str | None, session):
    """Handle /ingest commands."""
    low_arg = arg.lower().strip()

    if not arg.strip():
        console.print("  [dim]Usage: /ingest <file_or_url> | list | delete <source> | watch [add|remove|list][/]")
        return

    if low_arg == "list":
        try:
            docs = client.list_ingested(current_agent, project=current_project)
            if not docs:
                scope = f"project '{current_project}'" if current_project else f"agent '{current_agent}'"
                console.print(f"  [dim]No ingested documents in {scope}[/]")
                return
            t = Table(show_header=True, box=None, padding=(0, 1))
            t.add_column("Source", style="bold")
            t.add_column("Type", style="#5f87ff")
            t.add_column("Chunks", justify="right")
            t.add_column("Size", justify="right", style="dim")
            t.add_column("Ingested", style="dim")
            for d in docs:
                size = d.get("source_size", 0)
                size_str = f"{size / 1_000_000:.1f}MB" if size >= 1_000_000 else f"{size / 1_000:.0f}KB" if size >= 1000 else str(size)
                t.add_row(
                    d.get("source", "?")[:40],
                    d.get("source_type", "?"),
                    str(d.get("chunks", 0)),
                    size_str,
                    d.get("ingested_at", "")[:16],
                )
            console.print()
            console.print(t)
            console.print()
        except Exception as e:
            console.print(f"  [error]{e}[/]")
        return

    if low_arg.startswith("delete "):
        source = arg[7:].strip()
        if not source:
            console.print("  [dim]Usage: /ingest delete <source>[/]")
            return
        try:
            confirm = session.prompt(HTML(f"  Delete all chunks from <b>{source}</b>? [y/N] "))
        except (KeyboardInterrupt, EOFError):
            return
        if confirm.strip().lower() != "y":
            console.print("  [dim]Cancelled[/]")
            return
        try:
            r = client.delete_ingested(current_agent, source, project=current_project)
            if r.get("error"):
                console.print(f"  [error]{r['error']}[/]")
            else:
                deleted = r.get("deleted", 0)
                console.print(f"  [dim]Deleted {deleted} chunks from {source}[/]")
        except Exception as e:
            console.print(f"  [error]{e}[/]")
        return

    if low_arg.startswith("watch"):
        watch_arg = arg[5:].strip()
        watch_low = watch_arg.lower().strip()

        if not watch_low or watch_low == "list":
            try:
                folders = client.list_watch_folders(current_agent, project=current_project)
                if not folders:
                    console.print("  [dim]No watched folders[/]")
                    return
                t = Table(show_header=True, box=None, padding=(0, 1))
                t.add_column("Path", style="bold")
                t.add_column("Pattern", style="dim")
                t.add_column("Files", justify="right")
                t.add_column("Chunks", justify="right")
                for f in folders:
                    recursive_tag = " (recursive)" if f.get("recursive") else ""
                    t.add_row(
                        f.get("path", "?"),
                        f"{f.get('pattern', '*')}{recursive_tag}",
                        str(f.get("file_count", 0)),
                        str(f.get("chunk_count", 0)),
                    )
                console.print()
                console.print(t)
                console.print()
            except Exception as e:
                console.print(f"  [error]{e}[/]")
            return

        if watch_low.startswith("add "):
            path_str = watch_arg[4:].strip()
            if not path_str:
                console.print("  [dim]Usage: /ingest watch add <path> [--pattern *.pdf] [--recursive][/]")
                return
            # Parse flags
            parts = path_str.split()
            folder_path = parts[0]
            pattern = "*"
            recursive = False
            tags_list: list[str] = []
            i = 1
            while i < len(parts):
                if parts[i] == "--pattern" and i + 1 < len(parts):
                    pattern = parts[i + 1]; i += 2
                elif parts[i] == "--recursive":
                    recursive = True; i += 1
                elif parts[i] == "--tags" and i + 1 < len(parts):
                    tags_list = [t.strip() for t in parts[i + 1].split(",")]; i += 2
                else:
                    i += 1
            try:
                r = client.add_watch_folder(current_agent, folder_path,
                                            pattern=pattern, recursive=recursive,
                                            project=current_project, tags=tags_list or None)
                if r.get("error"):
                    console.print(f"  [error]{r['error']}[/]")
                else:
                    console.print(f"  [#5f87ff]Added watch:[/] {folder_path} [dim]({pattern})[/]")
            except Exception as e:
                console.print(f"  [error]{e}[/]")
            return

        if watch_low.startswith("remove "):
            path_str = watch_arg[7:].strip()
            if not path_str:
                console.print("  [dim]Usage: /ingest watch remove <path>[/]")
                return
            try:
                r = client.remove_watch_folder(current_agent, path_str,
                                               project=current_project)
                if r.get("error"):
                    console.print(f"  [error]{r['error']}[/]")
                else:
                    console.print(f"  [dim]Watch removed:[/] {path_str}")
            except Exception as e:
                console.print(f"  [error]{e}[/]")
            return

        console.print("  [dim]/ingest watch [list | add <path> | remove <path>][/]")
        return

    # Default: ingest a file or URL
    target = arg.strip()
    try:
        if target.startswith("http://") or target.startswith("https://"):
            console.print(f"  [dim]Fetching URL...[/]")
            r = client.ingest_url(current_agent, target, project=current_project)
        else:
            # Expand ~ and resolve path
            target = os.path.expanduser(target)
            if not os.path.isabs(target):
                target = os.path.abspath(target)
            if not os.path.exists(target):
                console.print(f"  [error]File not found: {target}[/]")
                return
            console.print(f"  [dim]Ingesting {os.path.basename(target)}...[/]")
            r = client.ingest_file(current_agent, target, project=current_project)

        if r.get("error"):
            console.print(f"  [error]{r['error']}[/]")
        else:
            chunks = r.get("chunks", 0)
            source = r.get("source", target)
            console.print(f"  [#5f87ff]Done.[/] {chunks} chunks ingested from {source}")
    except Exception as e:
        console.print(f"  [error]{e}[/]")


# --- Main loop ---

def run_interactive(args):
    client = BrainAgentClient(args.server)

    # Check server is running
    if not client.ping():
        console.print(f"[error]Cannot connect to server at {args.server}[/]")
        console.print("[dim]Start the server first: python3 server.py --base-url URL --api-key KEY -t openai -m MODEL[/]")
        sys.exit(1)

    # Get server status
    status = client.status()

    # Load models config for icons
    global _models_config
    try:
        mc = client._get("/v1/models/config")
        _models_config = mc.get("models", {})
    except Exception:
        pass

    # Create session
    client.create_session(agent=args.agent, model=args.model, max_context=args.max_context)
    session_max_context = client.max_context or args.max_context

    current_agent = args.agent
    current_model = args.model
    current_project: str | None = None
    show_tools = True
    token_count = 0
    plan_mode = False
    custom_commands = []
    # Load custom commands
    try:
        data = client._get(f"/v1/agents/{current_agent}/commands")
        custom_commands = data.get("commands", [])
    except Exception:
        pass

    # Prompt setup
    pt_history = InMemoryHistory()
    completer = SlashCommandCompleter()

    def bottom_toolbar():
        if token_count >= 1000:
            tok = f"{token_count // 1000}k"
        else:
            tok = str(token_count)
        tok_display = f"{tok}/{session_max_context // 1000}k"

        try:
            cols = os.get_terminal_size().columns
        except OSError:
            cols = 80
        max_model_len = max(10, cols - 35)
        model_short = current_model if len(current_model) <= max_model_len else current_model[:max_model_len - 1] + "…"

        parts = [
            ("class:tb.agent", f" {current_agent} "),
        ]
        if current_project:
            parts.extend([
                ("class:tb.sep", "·"),
                ("class:tb.project", f" {current_project} "),
            ])
        parts.extend([
            ("class:tb.sep", "│"),
            ("class:tb.label", " Model: "),
            ("class:tb.model", f"{model_short} "),
            ("class:tb.sep", "│ "),
            ("class:tb.label", "Ctx: "),
            ("class:tb.ctx", f"{tok_display} "),
        ])
        if plan_mode:
            parts.extend([
                ("class:tb.sep", "│ "),
                ("class:tb.plan", "[PLAN] "),
            ])
        return parts

    pt_style = PTStyle.from_dict({
        "prompt": "#ff8700 bold",
        "bottom-toolbar":              "bg:default #888888 noreverse",
        "bottom-toolbar.text":         "bg:default noreverse",
        "tb.agent": "bg:default #af87ff bold noreverse",
        "tb.label": "bg:default #666666 noreverse",
        "tb.model": "bg:default #5f87ff noreverse",
        "tb.sep":   "bg:default #444444 noreverse",
        "tb.ctx":   "bg:default #ff8700 noreverse",
        "tb.project": "bg:default #22d3ee bold noreverse",
        "tb.plan":  "bg:default #3b82f6 bold noreverse",
        "completion-menu":             "bg:#333333 #cccccc",
        "completion-menu.completion":  "bg:#333333 #cccccc",
        "completion-menu.completion.current": "bg:#555555 #ffffff bold",
        "completion-menu.meta":        "bg:#333333 #888888",
        "completion-menu.meta.current": "bg:#555555 #aaaaaa",
    })

    session = PromptSession(
        history=pt_history, completer=completer, style=pt_style,
        complete_while_typing=True, bottom_toolbar=bottom_toolbar,
    )

    def _sep():
        cols = os.get_terminal_size().columns
        console.print(f"[#444466]{'─' * cols}[/]")

    # Greeting
    console.clear()
    print_greeting(status, current_agent, current_model)

    try:
        while True:
            _sep()
            try:
                message = session.prompt(HTML("<prompt>❯ </prompt>"))
            except KeyboardInterrupt:
                continue
            except EOFError:
                console.print("[dim]Bye![/]")
                break

            stripped = message.strip()
            if not stripped:
                continue
            _sep()
            low = stripped.lower()

            if low in ("exit", "quit"):
                console.print("[dim]Bye![/]")
                break

            if low == "/help":
                print_help(); continue

            if low == "/new":
                # Create new session with same agent/model
                client.delete_session()
                client.create_session(agent=current_agent, model=current_model,
                                      max_context=args.max_context)
                token_count = 0
                console.rule(style="dim")
                console.print("  [dim]New conversation[/]")
                console.rule(style="dim")
                continue

            if low == "/tools":
                show_tools = not show_tools
                s = "[#5f87ff]visible[/]" if show_tools else "[dim]hidden[/]"
                console.print(f"  [dim]Tool display:[/] {s}")
                continue

            # --- Session management ---

            if low == "/sessions":
                result = _handle_sessions(client, current_agent)
                new_sid, new_tc = result
                if new_sid:
                    client.session_id = new_sid
                    token_count = new_tc or 0
                    console.print(f"  [#5f87ff]Switched to session {new_sid[:8]}...[/]")
                continue

            if low.startswith("/switch"):
                arg = stripped[7:].strip()
                new_tc = _handle_switch(arg, client)
                if new_tc is not None:
                    token_count = new_tc
                continue

            if low == "/archive":
                _handle_archive(client)
                continue

            if low == "/delete":
                token_count = _handle_delete_session(client, current_agent, current_model,
                                                     args.max_context)
                continue

            if low == "/clear":
                token_count = _handle_clear(client)
                continue

            if low == "/incognito":
                _handle_incognito(client)
                continue

            if low.startswith("/title"):
                arg = stripped[6:].strip()
                _handle_title(arg, client)
                continue

            # --- Agent management ---

            if low.startswith("/agent"):
                old_agent = current_agent
                current_agent, current_model = _handle_agent(
                    stripped, client, current_agent, current_model, session)
                if current_agent != old_agent:
                    current_project = None  # clear project on agent switch
                token_count = 0
                continue

            # --- Model ---

            if low.startswith("/model") and not low.startswith("/models"):
                arg = stripped[6:].strip()
                if arg:
                    current_model = arg
                    client.switch_agent(current_agent, model=current_model)
                    console.print(f"  [dim]->[/] [model]{current_model}[/]")
                else:
                    models = client.list_models()
                    if models:
                        console.print()
                        choice = select_inline(models, active=current_model)
                        if choice:
                            current_model = choice
                            client.switch_agent(current_agent, model=current_model)
                            console.print(f"  [dim]->[/] [model]{current_model}[/]")
                    else:
                        console.print("  [dim]No models available[/]")
                continue

            if low == "/models":
                models = client.list_models()
                if models:
                    console.print()
                    choice = select_inline(models, active=current_model)
                    if choice:
                        current_model = choice
                        client.switch_agent(current_agent, model=current_model)
                        console.print(f"  [dim]->[/] [model]{current_model}[/]")
                else:
                    console.print("  [dim]No models available[/]")
                continue

            # --- Teams ---

            if low.startswith("/teams"):
                arg = stripped[6:].strip()
                _handle_teams(arg, client, session)
                continue

            # --- Skills ---

            if low.startswith("/skills"):
                arg = stripped[7:].strip()
                _handle_skills(arg, client, current_agent)
                continue

            # --- Knowledge Graph ---

            if low.startswith("/graph"):
                graph_arg = stripped[6:].strip().lower()
                if graph_arg == "discover":
                    _handle_graph_discover(client, current_agent)
                elif graph_arg == "stats":
                    _handle_graph_stats(client, current_agent)
                else:
                    _handle_graph(client, current_agent)
                continue

            # --- Memory ---

            if low.startswith("/memory"):
                arg = stripped[7:].strip()
                _handle_memory(arg, client, current_agent)
                continue

            # --- Providers ---

            if low.startswith("/providers"):
                arg = stripped[10:].strip()
                _handle_providers(arg, client, session)
                continue

            # --- Status ---

            if low == "/status":
                _handle_status(client)
                continue

            # --- Tasks ---

            if low.startswith("/tasks"):
                arg = stripped[6:].strip()
                _handle_tasks(arg, client)
                continue

            # --- Workflows ---

            if low.startswith("/workflow"):
                arg = stripped[9:].strip()
                _handle_workflow(arg, client, current_agent)
                continue

            # --- QMD ---

            if low.startswith("/qmd"):
                arg = stripped[4:].strip()
                _handle_qmd(arg, client, current_agent)
                continue

            # --- Costs ---

            if low.startswith("/costs"):
                _handle_costs(client)
                continue

            # --- Traces ---

            if low.startswith("/traces"):
                _handle_traces(client)
                continue

            # --- Audit ---

            if low.startswith("/audit"):
                arg = stripped[6:].strip()
                _handle_audit(arg, client)
                continue

            # --- MCP ---

            if low.startswith("/mcp"):
                arg = stripped[4:].strip()
                _handle_mcp(arg, client, session)
                continue

            # --- Projects ---

            if low.startswith("/projects"):
                arg = stripped[9:].strip()
                result = _handle_projects(arg, client, current_agent,
                                          current_project, session)
                current_project = result
                continue

            # --- Ingest ---

            if low.startswith("/ingest"):
                arg = stripped[7:].strip()
                _handle_ingest(arg, client, current_agent, current_project, session)
                continue

            # --- Schedule ---

            if low.startswith("/schedule"):
                _handle_schedule(low[9:].strip(), client, session)
                continue

            # --- Cache ---

            if low.startswith("/cache"):
                arg = stripped[6:].strip().lower()
                if arg == "clear":
                    try:
                        client._post("/v1/cache/clear")
                        console.print("  [#5f87ff]Cache cleared[/]")
                    except Exception as e:
                        console.print(f"  [error]{e}[/]")
                else:
                    try:
                        stats = client._get("/v1/cache/stats")
                        console.print(f"  [bold]Web Cache[/]")
                        console.print(f"  [dim]Entries:[/] {stats.get('entries', 0)}/{stats.get('max_entries', 0)}")
                        console.print(f"  [dim]Hit rate:[/] {stats.get('hit_rate', 0)}% ({stats.get('hits', 0)} hits, {stats.get('misses', 0)} misses)")
                        console.print(f"  [dim]TTL:[/] {stats.get('ttl', 0)}s")
                    except Exception as e:
                        console.print(f"  [error]{e}[/]")
                continue

            # --- Plan Mode ---

            if low == "/plan":
                plan_mode = not plan_mode
                label = "[#3b82f6]ON[/]" if plan_mode else "[dim]OFF[/]"
                console.print(f"  [dim]Plan mode:[/] {label}")
                continue

            # --- Refine ---

            if low.startswith("/refine"):
                refine_text = stripped[7:].strip()
                if not refine_text:
                    console.print("  [dim]Usage: /refine <text>[/]")
                else:
                    try:
                        r = client._post("/v1/refine", {"text": refine_text, "context": current_agent})
                        if r.get("error"):
                            console.print(f"  [error]{r['error']}[/]")
                        else:
                            refined = r.get("refined", refine_text)
                            console.print(f"\n  [bold]Refined:[/]")
                            console.print(Markdown(refined))
                            console.print()
                    except Exception as e:
                        console.print(f"  [error]{e}[/]")
                continue

            # --- Notifications ---

            if low == "/notifications":
                try:
                    r = client._get("/v1/notifications")
                    notifs = r.get("notifications", [])
                    unread = r.get("unread", 0)
                    if not notifs:
                        console.print("  [dim]No notifications[/]")
                    else:
                        console.print(f"\n  [bold]Notifications[/] ({unread} unread)\n")
                        for n in notifs[:15]:
                            sev = n.get("severity", "info")
                            icon = "[red]!![/]" if sev in ("critical", "error") else "[#ff8700]![/]" if sev == "warning" else "[#5f87ff]i[/]"
                            agent_str = f"  {n['agent']}" if n.get("agent") else ""
                            console.print(f"  {icon} {n.get('created_at', '')[:16]}  {n.get('event_type', ''):<18}{agent_str}  {n.get('title', '')}")
                        console.print()
                    # Mark all read
                    try:
                        client._post("/v1/notifications/read", {})
                    except Exception:
                        pass
                except Exception as e:
                    console.print(f"  [error]{e}[/]")
                continue

            # --- Backup ---

            if low.startswith("/backup"):
                arg = stripped[7:].strip()
                try:
                    if arg.startswith("agent "):
                        agent_name = arg[6:].strip()
                        r = client._post("/v1/backup", {"type": "agent", "agent": agent_name})
                    else:
                        r = client._post("/v1/backup", {"type": "full"})
                    if r.get("error"):
                        console.print(f"  [error]{r['error']}[/]")
                    else:
                        path = r.get("path", "")
                        size = r.get("size_bytes", 0)
                        console.print(f"  [#5f87ff]Backup created: {path}[/]")
                        console.print(f"  [dim]Size: {size / 1024:.0f} KB[/]")
                except Exception as e:
                    console.print(f"  [error]{e}[/]")
                continue

            if low.startswith("/restore"):
                fpath = stripped[8:].strip()
                if not fpath:
                    console.print("  [dim]Usage: /restore <path-to-backup.tar.gz>[/]")
                else:
                    try:
                        r = client._post("/v1/restore", {"path": fpath, "strategy": "merge"})
                        if r.get("error"):
                            console.print(f"  [error]{r['error']}[/]")
                        else:
                            console.print(f"  [#5f87ff]Restore complete[/]")
                            if r.get("imported"):
                                imp = r["imported"]
                                console.print(f"  [dim]Agents: {imp.get('agents', [])}, Memories: {imp.get('memories', 0)}[/]")
                    except Exception as e:
                        console.print(f"  [error]{e}[/]")
                continue

            # --- Nodes ---

            if low.startswith("/nodes"):
                arg = stripped[6:].strip()
                try:
                    if not arg or arg == "list":
                        nodes = client.list_nodes()
                        if not nodes:
                            console.print("  [dim]No remote nodes configured.[/]")
                        else:
                            table = Table(show_header=True, header_style="bold", box=None)
                            table.add_column("Name", style="bold")
                            table.add_column("Status")
                            table.add_column("OS", style="dim")
                            table.add_column("CPU")
                            table.add_column("RAM")
                            table.add_column("Tags", style="dim")
                            for n in nodes:
                                status_style = "#5f87ff" if n["status"] == "connected" else "dim"
                                dot = "[green]●[/]" if n["status"] == "connected" else "[dim]○[/]"
                                cpu = f"{n.get('cpu_percent', 0):.0f}%" if n.get("cpu_percent") is not None else "-"
                                mem = f"{n.get('mem_used_gb', 0):.1f}/{n.get('mem_total_gb', 0):.0f}G" if n.get("mem_total_gb") else "-"
                                table.add_row(
                                    n["name"],
                                    f"{dot} [{status_style}]{n['status']}[/]",
                                    n.get("os", "")[:20],
                                    cpu, mem,
                                    ", ".join(n.get("tags", [])),
                                )
                            console.print(table)
                    elif arg.startswith("add "):
                        parts = arg[4:].strip().split(None, 1)
                        name = parts[0]
                        desc = parts[1] if len(parts) > 1 else ""
                        r = client.node_action("add", name=name, description=desc)
                        console.print(f"  [#5f87ff]Node '{name}' created[/]")
                        console.print(f"  [dim]Token:[/] {r.get('token', '')}")
                        console.print(f"  [dim]Install:[/] {r.get('install_command', '')}")
                    elif arg.startswith("remove "):
                        name = arg[7:].strip()
                        client.node_action("remove", name=name)
                        console.print(f"  [#5f87ff]Node '{name}' removed[/]")
                    elif arg.startswith("pause "):
                        name = arg[6:].strip()
                        client.node_action("pause", name=name)
                        console.print(f"  [#5f87ff]Node '{name}' paused[/]")
                    elif arg.startswith("resume "):
                        name = arg[7:].strip()
                        client.node_action("resume", name=name)
                        console.print(f"  [#5f87ff]Node '{name}' resumed[/]")
                    else:
                        console.print("  [dim]Usage: /nodes [list|add|remove|pause|resume][/]")
                except Exception as e:
                    console.print(f"  [error]{e}[/]")
                continue

            # --- Channels ---

            if low.startswith("/channels"):
                arg = stripped[9:].strip()
                try:
                    if not arg or arg == "list":
                        channels = client.list_channels()
                        if not channels:
                            console.print("  [dim]No messaging channels configured.[/]")
                        else:
                            for ch in channels:
                                dot = "[green]●[/]" if ch.get("running") else "[dim]○[/]"
                                status = "RUNNING" if ch.get("running") else "STOPPED"
                                stats = ch.get("stats", {})
                                console.print(
                                    f"  {dot} [bold]{ch.get('channel_id', '')}[/] "
                                    f"[dim]{ch.get('type', '')}[/]  "
                                    f"[{'#5f87ff' if ch.get('running') else 'dim'}]{status}[/]  "
                                    f"[dim]{ch.get('identity', '')}[/]  "
                                    f"[dim]{stats.get('messages_in', 0)} msgs[/]"
                                )
                    elif arg.startswith("start "):
                        ch_id = arg[6:].strip()
                        client.channel_start(ch_id)
                        console.print(f"  [#5f87ff]Channel '{ch_id}' started[/]")
                    elif arg.startswith("stop "):
                        ch_id = arg[5:].strip()
                        client.channel_stop(ch_id)
                        console.print(f"  [#5f87ff]Channel '{ch_id}' stopped[/]")
                    else:
                        console.print("  [dim]Usage: /channels [list|start|stop][/]")
                except Exception as e:
                    console.print(f"  [error]{e}[/]")
                continue

            # --- Send message via SSE ---
            console.print()
            start_time = time.time()
            full_text = ""
            tool_count = 0
            tool_output_lines = 0
            done_model = ""
            done_cost = None
            fallback_notice = ""

            try:
                with console.status(
                    "  [bold #ff8700]Thinking...[/]",
                    spinner="dots", spinner_style="#ff8700",
                ):
                    for event_type, data in client.chat(stripped, mode="plan" if plan_mode else None, project=current_project):
                        if event_type == "text_delta":
                            pass  # Collecting server-side (silent mode)
                        elif event_type == "tool_call":
                            tool_count += 1
                            if show_tools:
                                display_tool_call(data.get("name", ""), data.get("args", {}))
                        elif event_type == "tool_result":
                            if show_tools:
                                display_tool_result(data.get("name", ""), data.get("result", "{}"))
                                if tool_output_lines > 0:
                                    console.print(f"    [dim]{tool_output_lines} output lines[/]")
                                    tool_output_lines = 0
                        elif event_type == "tool_output":
                            tool_output_lines += 1
                        elif event_type == "fallback":
                            if data.get("status") == "switch":
                                fallback_notice = f"Fallback: {data.get('from','')} -> {data.get('to','')} ({data.get('reason','unavailable')})"
                        elif event_type == "done":
                            full_text = data.get("text", "")
                            token_count = data.get("tokens", 0)
                            done_model = data.get("model", "")
                            done_cost = data.get("cost")
                            if data.get("fallback_model"):
                                fallback_notice = f"Fallback: {data.get('original_model', done_model)} -> {data['fallback_model']}"
                                done_model = data["fallback_model"]
                        elif event_type == "error":
                            console.print(f"  [error]{data.get('message', 'Unknown error')}[/]")
                            break

            except KeyboardInterrupt:
                try:
                    client.cancel()
                except Exception:
                    pass
                console.print("\n  [dim]✘ Cancelled[/]")
                continue

            elapsed = time.time() - start_time

            if full_text:
                console.print()
                console.print(Markdown(full_text))
                pct = min(99, int(token_count / session_max_context * 100))
                model_tag = f"{model_icon(done_model)} {done_model}  " if done_model else ""
                cost_tag = f" · ${done_cost:.2f}" if done_cost is not None else ""
                if fallback_notice:
                    console.print(f"\n  [warning]{fallback_notice}[/]")
                console.print(f"\n  [dim]{model_tag}✻ {elapsed:.0f}s · {token_count:,}/{session_max_context//1000}k ({pct}%){cost_tag}[/]")
                if not show_tools and tool_count > 0:
                    console.print(f"  [dim]{tool_count} tool calls (hidden)[/]")

    except Exception as e:
        console.print(f"[error]Error: {e}[/]")
        import traceback
        traceback.print_exc()
    finally:
        client.delete_session()


# --- Entry point ---

def main():
    parser = argparse.ArgumentParser(description="Brain Agent TUI")
    parser.add_argument("message", nargs="?")
    parser.add_argument("-i", "--interactive", action="store_true")
    parser.add_argument("-m", "--model", default="claude-opus-4-5-20251101")
    parser.add_argument("--agent", default="main")
    parser.add_argument("--max-context", type=int, default=131072)
    parser.add_argument("--server", default="http://127.0.0.1:8420",
                        help="Brain Agent server URL (default: http://127.0.0.1:8420)")
    args = parser.parse_args()

    if args.interactive:
        run_interactive(args)
        sys.exit(0)

    if args.message:
        client = BrainAgentClient(args.server)
        if not client.ping():
            console.print(f"[error]Cannot connect to server at {args.server}[/]")
            sys.exit(1)
        client.create_session(agent=args.agent, model=args.model)
        full_text = ""
        for event_type, data in client.chat(args.message):
            if event_type == "done":
                full_text = data.get("text", "")
        if full_text:
            console.print(Markdown(full_text))
        client.delete_session()
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
