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
from prompt_toolkit.completion import WordCompleter
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
        console.print(f"\n  [dim]↑ v{latest['version']}: {latest['changes']}[/]")
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


def model_icon(model: str) -> str:
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


# --- Help ---

def print_help():
    console.print()
    t = Table(show_header=False, box=None, padding=(0, 2), pad_edge=False)
    t.add_column(style="#ff8700 bold", min_width=12)
    t.add_column(style="dim")
    for cmd, desc in [
        ("/help", "Show this help"),
        ("/new", "Start a new conversation"),
        ("/agent [name]", "Switch agent (arrow-key menu)"),
        ("/model [name]", "Switch model"),
        ("/models", "List & select models"),
        ("/tools", "Toggle tool call display"),
        ("/schedule", "Manage scheduled tasks"),
    ]:
        t.add_row(cmd, desc)
    console.print(t)
    console.print()
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

    # Create session
    client.create_session(agent=args.agent, model=args.model, max_context=args.max_context)

    current_agent = args.agent
    current_model = args.model
    show_tools = True
    token_count = 0

    # Prompt setup
    pt_history = InMemoryHistory()
    completer = WordCompleter(
        ["/help", "/new", "/agent", "/model", "/models", "/tools", "/schedule"],
        sentence=True,
    )

    def bottom_toolbar():
        if token_count >= 1000:
            tok = f"{token_count // 1000}k"
        else:
            tok = str(token_count)
        tok_display = f"{tok}/{args.max_context // 1000}k"

        try:
            cols = os.get_terminal_size().columns
        except OSError:
            cols = 80
        max_model_len = max(10, cols - 35)
        model_short = current_model if len(current_model) <= max_model_len else current_model[:max_model_len - 1] + "…"

        parts = [
            ("class:tb.agent", f" {current_agent} "),
            ("class:tb.sep", "│"),
            ("class:tb.label", " Model: "),
            ("class:tb.model", f"{model_short} "),
            ("class:tb.sep", "│ "),
            ("class:tb.label", "Ctx: "),
            ("class:tb.ctx", f"{tok_display} "),
        ]
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
    })

    session = PromptSession(
        history=pt_history, completer=completer, style=pt_style,
        complete_while_typing=False, bottom_toolbar=bottom_toolbar,
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

            if low.startswith("/agent"):
                arg = stripped[6:].strip()
                if arg:
                    resp = client.switch_agent(arg)
                    current_agent = resp.get("agent", arg)
                    current_model = resp.get("model", current_model)
                    token_count = 0
                    console.print(f"  [dim]→[/] [agent]{current_agent}[/] [dim]({current_model})[/]")
                else:
                    agents_data = client.list_agents()
                    agent_ids = [a["id"] for a in agents_data]
                    labels = [f"{a['id']} — {a.get('description', '')}" for a in agents_data]
                    console.print()
                    choice = select_inline(agent_ids, labels=labels, active=current_agent)
                    if choice:
                        resp = client.switch_agent(choice)
                        current_agent = resp.get("agent", choice)
                        current_model = resp.get("model", current_model)
                        token_count = 0
                        console.print(f"  [dim]→[/] [agent]{current_agent}[/] [dim]({current_model})[/]")
                continue

            if low.startswith("/model") and not low.startswith("/models"):
                arg = stripped[6:].strip()
                if arg:
                    current_model = arg
                    # Switch agent with new model
                    client.switch_agent(current_agent, model=current_model)
                    console.print(f"  [dim]→[/] [model]{current_model}[/]")
                else:
                    models = client.list_models()
                    if models:
                        console.print()
                        choice = select_inline(models, active=current_model)
                        if choice:
                            current_model = choice
                            client.switch_agent(current_agent, model=current_model)
                            console.print(f"  [dim]→[/] [model]{current_model}[/]")
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
                        console.print(f"  [dim]→[/] [model]{current_model}[/]")
                else:
                    console.print("  [dim]No models available[/]")
                continue

            if low.startswith("/schedule"):
                _handle_schedule(low[9:].strip(), client, session)
                continue

            # --- Send message via SSE ---
            console.print()
            start_time = time.time()
            full_text = ""
            tool_count = 0
            done_model = ""

            try:
                with console.status(
                    "  [bold #ff8700]Thinking...[/]",
                    spinner="dots", spinner_style="#ff8700",
                ):
                    for event_type, data in client.chat(stripped):
                        if event_type == "text_delta":
                            pass  # Collecting server-side (silent mode)
                        elif event_type == "tool_call":
                            tool_count += 1
                            if show_tools:
                                display_tool_call(data.get("name", ""), data.get("args", {}))
                        elif event_type == "tool_result":
                            if show_tools:
                                display_tool_result(data.get("name", ""), data.get("result", "{}"))
                        elif event_type == "done":
                            full_text = data.get("text", "")
                            token_count = data.get("tokens", 0)
                            done_model = data.get("model", "")
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
                pct = min(99, int(token_count / args.max_context * 100))
                model_tag = f"{model_icon(done_model)} {done_model}  " if done_model else ""
                console.print(f"\n  [dim]{model_tag}✻ {elapsed:.0f}s · {token_count:,}/{args.max_context//1000}k ({pct}%)[/]")
                if not show_tools and tool_count > 0:
                    console.print(f"  [dim]{tool_count} tool calls (hidden)[/]")

    except Exception as e:
        console.print(f"[error]Error: {e}[/]")
        import traceback
        traceback.print_exc()
    finally:
        client.delete_session()


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
            console.print(f"  [#5f87ff]✔ Created:[/] [bold]{name}[/]")

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
