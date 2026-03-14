#!/usr/bin/env python3
"""Brain Agent TUI — Rich + prompt_toolkit frontend (Claude Code style)."""

import argparse
import json
import os
import sys
import threading
import time

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text
from rich.theme import Theme
from rich.table import Table

from prompt_toolkit import PromptSession
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.styles import Style as PTStyle

# Import backend
import claude_cli as backend

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

def print_greeting(model: str, agent_id: str):
    """Print Claude Code-style greeting."""
    # ASCII art brain — works in any monospace font
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

    # Title with Rich markup
    title = Text()
    for i, ch in enumerate("Brain"):
        title.append(ch, style=f"bold color({[213,177,141,105,69][i]})")
    title.append(" ")
    title.append("Agent", style="bold #ff8700")
    title.append(f" v{backend.VERSION}", style="dim")
    if agent_id != "main":
        title.append(" | ", style="dim")
        title.append(agent_id, style="cyan bold")
    console.print("  ", title)
    console.print()

    console.print(f"  [dim]Model[/]  [#5f87ff]{model}[/]")
    console.print(f"  [dim]Path[/]   [dim]{os.getcwd()}[/]")

    agents = backend.list_agents()
    if len(agents) > 1:
        parts = []
        for a in agents:
            if a == agent_id:
                parts.append(f"[cyan bold]{a}[/]")
            else:
                parts.append(f"[dim]{a}[/]")
        console.print(f"  [dim]Agents[/] {' · '.join(parts)}")

    if backend._current_agent:
        skills = backend._current_agent.list_skills()
        if skills:
            names = [s["name"] for s in skills[:5]]
            more = f" [dim]+{len(skills)-5}[/]" if len(skills) > 5 else ""
            console.print(f"  [dim]Skills[/] [dim]{', '.join(names)}{more}[/]")

    console.print()
    console.print("  [dim]/help for commands · Ctrl+C to cancel · Ctrl+D to quit[/]")

    latest = backend.CHANGELOG[0] if backend.CHANGELOG else None
    if latest:
        console.print(f"\n  [dim]↑ v{latest[0]}: {latest[2]}[/]")
    console.print()


# --- Tool display ---

_show_tools = True
_tool_count = 0


def display_tool_call(name: str, args: dict):
    """Display a tool call inline — one line, like Claude Code."""
    verb = backend.TOOL_VERBS.get(name, "Running")

    if name == "execute_command":
        cmd = args.get("command", "")[:80]
        detail = f"[yellow]$ {cmd}[/]"
    elif name == "exa_search":
        detail = f'[white]"{args.get("query", "")}"[/]'
    elif name in ("read_file", "write_file", "edit_file"):
        detail = f'[white]{args.get("path", "")}[/]'
    elif name == "list_directory":
        p = args.get("path", ".")
        pat = args.get("pattern", "")
        detail = f"[white]{p}/{pat}[/]" if pat else f"[white]{p}[/]"
    elif name == "search_files":
        detail = f'[magenta]/{args.get("pattern", "")}/[/] in [white]{args.get("path", ".")}[/]'
    elif name == "web_fetch":
        detail = f'[white]{args.get("url", "")}[/]'
    elif name == "delegate_task":
        detail = f'[cyan]{args.get("agent", "")}[/] [dim]{args.get("task", "")[:50]}[/]'
    elif name == "use_skill":
        detail = f'[magenta]{args.get("skill", "")}[/]'
    elif name.startswith("memory"):
        detail = f'[magenta]{args.get("query", args.get("name", ""))[:40]}[/]'
    else:
        detail = f"[dim]{str(args)[:50]}[/]"

    console.print(f"  [tool.verb]{verb}[/] [tool.name]{name}[/] {detail}")


def display_tool_result(name: str, result_str: str):
    """Display tool result — compact one-line summary."""
    try:
        rdata = json.loads(result_str)
    except json.JSONDecodeError:
        return

    if rdata.get("error"):
        err = rdata["error"].split("\n")[0][:80]
        console.print(f"  [error]✘ {err}[/]")
        return

    if name == "execute_command":
        ec = rdata.get("exit_code", -1)
        sym, sty = ("✔", "success") if ec == 0 else ("✘", "error")
        console.print(f"  [{sty}]{sym} exit {ec}[/]")
    elif name == "exa_search":
        console.print(f"  [#5f87ff]✔ {rdata.get('result_count', 0)} results[/]")
    elif name == "read_file":
        console.print(f"  [#5f87ff]✔ {rdata.get('total_lines', 0)} lines[/]")
    elif name in ("write_file", "edit_file", "memory_store", "memory_delete"):
        console.print(f"  [#5f87ff]✔[/]")
    elif name in ("search_files", "list_directory"):
        console.print(f"  [#5f87ff]✔ {rdata.get('match_count', rdata.get('count', 0))}[/]")
    elif name in ("memory_recall", "memory_shared"):
        console.print(f"  [#5f87ff]✔ {rdata.get('count', 0)} memories[/]")
    elif name == "delegate_task":
        console.print(f"  [#5f87ff]✔ {rdata.get('agent', '')} responded[/]")
    elif name == "use_skill":
        console.print(f"  [#5f87ff]✔ loaded[/]")
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
        ("Ctrl+C", "Cancel / interrupt"),
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
    """Arrow-key inline selector. Returns item or None on Esc."""
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
            marker = "[cyan bold]❯[/]" if i == selected else " "
            style = "bold" if i == selected else "dim"
            tag = " [#af87ff](active)[/]" if items[i] == active else ""
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
                    erase()
                    return items[selected]
                elif ch == b'\x1b':
                    s1 = os.read(fd, 1) if sel.select([fd], [], [], 0.05)[0] else b''
                    if s1 == b'[':
                        s2 = os.read(fd, 1) if sel.select([fd], [], [], 0.05)[0] else b''
                        if s2 == b'A':
                            selected = (selected - 1) % n
                        elif s2 == b'B':
                            selected = (selected + 1) % n
                        else:
                            erase(); return None
                    else:
                        erase(); return None
                elif ch == b'\x03':
                    erase(); return None
                else:
                    continue
                erase()
                draw()
    except Exception:
        return None
    finally:
        try:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
        except termios.error:
            pass


# --- Streaming ---

def run_with_spinner(messages, model, api_key, base_url, api_type,
                     escape_watcher=None):
    """Send message with inline spinner, tool display. Returns reply."""
    global _tool_count
    _tool_count = 0

    # Patch display functions
    orig_call = backend._display_tool_call
    orig_result = backend._display_tool_result

    def patched_call(name, args):
        global _tool_count
        _tool_count += 1
        if _show_tools:
            display_tool_call(name, args)

    def patched_result(name, result_str):
        if _show_tools:
            display_tool_result(name, result_str)

    backend._display_tool_call = patched_call
    backend._display_tool_result = patched_result

    try:
        reply = backend.send_message_with_fallback(
            messages, model, api_key, base_url, api_type,
            silent=True, escape_watcher=escape_watcher,
        )
        return reply
    except backend.TaskCancelled:
        console.print(f"\n  [dim]✘ Cancelled[/]")
        return None
    finally:
        backend._display_tool_call = orig_call
        backend._display_tool_result = orig_result


# --- Main loop ---

def run_interactive(args):
    backend._delegate_api_key = args.api_key
    backend._delegate_base_url = args.base_url
    backend._delegate_api_type = args.api_type
    backend._delegate_fallback_model = args.model

    current_model, _ = backend._switch_agent(args.agent, args)

    backend._scheduler = backend.Scheduler()
    backend._scheduler.start()
    backend._task_runner = backend.TaskRunner()

    history = []

    pt_history = InMemoryHistory()
    completer = WordCompleter(
        ["/help", "/new", "/agent", "/model", "/models", "/tools", "/schedule"],
        sentence=True,
    )

    # Status bar data (mutable, read by toolbar)
    status = {"model": current_model, "agent": args.agent, "tokens": 0, "max": args.max_context}

    def bottom_toolbar():
        agent_id = status["agent"]
        model = status["model"]
        est = status["tokens"]
        max_t = status["max"]

        if est >= 1000:
            tok = f"{est // 1000}k"
        else:
            tok = str(est)
        tok_display = f"{tok}/{max_t // 1000}k"

        # Truncate model name to fit
        try:
            cols = os.get_terminal_size().columns
        except OSError:
            cols = 80
        max_model_len = max(10, cols - 30)
        model_short = model if len(model) <= max_model_len else model[:max_model_len - 1] + "…"

        parts = []
        parts.append(("class:tb.agent", f" {agent_id} "))
        parts.append(("class:tb.sep", "│"))
        parts.append(("class:tb.label", " Model: "))
        parts.append(("class:tb.model", f"{model_short} "))
        parts.append(("class:tb.sep", "│ "))
        parts.append(("class:tb.label", "Ctx: "))
        parts.append(("class:tb.ctx", f"{tok_display} "))
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

    def _update_status():
        status["model"] = current_model
        status["agent"] = backend._current_agent.agent_id if backend._current_agent else "main"
        status["tokens"] = backend._estimate_conversation_tokens(history)

    console.clear()
    print_greeting(current_model, args.agent)

    # Separator line width
    def _sep():
        cols = os.get_terminal_size().columns
        console.print(f"[#444466]{'─' * cols}[/]")

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
                history = []
                _update_status()
                console.rule(style="dim")
                console.print("  [dim]New conversation[/]")
                console.rule(style="dim")
                continue

            if low == "/tools":
                global _show_tools
                _show_tools = not _show_tools
                s = "[#5f87ff]visible[/]" if _show_tools else "[dim]hidden[/]"
                console.print(f"  [dim]Tool display:[/] {s}")
                continue

            if low.startswith("/agent"):
                arg = stripped[6:].strip()
                agents = backend.list_agents()
                if arg:
                    if arg not in agents:
                        console.print(f"  [dim]Creating:[/] [bold]{arg}[/]")
                    current_model, _ = backend._switch_agent(arg, args)
                    history = []
                    _update_status()
                    console.print(f"  [dim]→[/] [agent]{arg}[/] [dim]({current_model})[/]")
                else:
                    labels = []
                    for a in agents:
                        cfg = backend.AgentConfig(a)
                        m = f" [{cfg.preferred_model}]" if cfg.preferred_model else ""
                        labels.append(f"{a}{m} — {cfg.description}")
                    cur = backend._current_agent.agent_id if backend._current_agent else "main"
                    console.print()
                    choice = select_inline(agents, labels=labels, active=cur)
                    if choice:
                        current_model, _ = backend._switch_agent(choice, args)
                        history = []
                        _update_status()
                        console.print(f"  [dim]→[/] [agent]{choice}[/] [dim]({current_model})[/]")
                continue

            if low.startswith("/model") and not low.startswith("/models"):
                arg = stripped[6:].strip()
                if arg:
                    current_model = arg
                    _update_status()
                    console.print(f"  [dim]→[/] [model]{current_model}[/]")
                else:
                    models = backend.get_available_models(args.api_key, args.base_url, args.api_type)
                    if models:
                        console.print()
                        choice = select_inline(models, active=current_model)
                        if choice:
                            current_model = choice
                            _update_status()
                            console.print(f"  [dim]→[/] [model]{current_model}[/]")
                    else:
                        console.print("  [dim]No models available[/]")
                continue

            if low == "/models":
                models = backend.get_available_models(args.api_key, args.base_url, args.api_type)
                if models:
                    console.print()
                    choice = select_inline(models, active=current_model)
                    if choice:
                        current_model = choice
                        _update_status()
                        console.print(f"  [dim]→[/] [model]{current_model}[/]")
                else:
                    console.print("  [dim]No models available[/]")
                continue

            if low.startswith("/schedule"):
                _handle_schedule(stripped[9:].strip(), session)
                continue

            # --- Send message ---
            history.append({"role": "user", "content": message})

            history, _ = backend._check_and_compact(
                history, current_model, args.api_key, args.base_url,
                args.api_type, max_tokens=args.max_context,
            )

            escape_watcher = backend.EscapeWatcher()
            escape_watcher.start()
            start_time = time.time()

            console.print()
            reply_box = [None]

            def worker():
                reply_box[0] = run_with_spinner(
                    history, current_model, args.api_key, args.base_url,
                    args.api_type, escape_watcher,
                )

            t = threading.Thread(target=worker, daemon=True)
            t.start()

            try:
                with console.status(
                    "  [bold #ff8700]Thinking...[/]",
                    spinner="dots", spinner_style="#ff8700",
                ):
                    while t.is_alive():
                        t.join(timeout=0.2)
            except KeyboardInterrupt:
                escape_watcher._cancelled.set()
                t.join(timeout=5)
            finally:
                escape_watcher.stop()

            elapsed = time.time() - start_time
            reply = reply_box[0]

            if reply:
                history.append({"role": "assistant", "content": reply})
                console.print()
                console.print(Markdown(reply))
                est = backend._estimate_conversation_tokens(history)
                status["tokens"] = est
                pct = min(99, int(est / args.max_context * 100))
                console.print(f"\n  [dim]✻ {elapsed:.0f}s · {est:,}/{args.max_context//1000}k ({pct}%)[/]")
            else:
                if not escape_watcher.cancelled:
                    console.print("  [dim](no response)[/]")

    except Exception as e:
        console.print(f"[error]Error: {e}[/]")
        import traceback
        traceback.print_exc()
    finally:
        if backend._scheduler:
            backend._scheduler.stop()


def _handle_schedule(arg: str, session):
    sched = backend._scheduler
    if not sched:
        console.print("  [dim]Scheduler not running[/]"); return

    if not arg or arg == "list":
        schedules = sched.list_all()
        if not schedules:
            console.print("  [dim]No scheduled tasks[/]"); return
        t = Table(show_header=True, box=None, padding=(0, 1))
        t.add_column("Name", style="bold")
        t.add_column("Status")
        t.add_column("Schedule", style="dim")
        t.add_column("Agent", style="cyan")
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
        r = sched.add(name.strip(), task.strip(), sched_str.strip(), agent.strip(), model)
        if r.get("error"):
            console.print(f"  [error]{r['error']}[/]")
        else:
            console.print(f"  [#5f87ff]✔ Created:[/] [bold]{name}[/]")

    elif arg.startswith("pause "):
        r = sched.pause(arg[6:].strip())
        msg = "[dim]Paused[/]" if not r.get("error") else f"[error]{r['error']}[/]"
        console.print(f"  {msg}")
    elif arg.startswith("resume "):
        r = sched.resume(arg[7:].strip())
        msg = "[dim]Resumed[/]" if not r.get("error") else f"[error]{r['error']}[/]"
        console.print(f"  {msg}")
    elif arg.startswith(("delete ", "rm ")):
        r = sched.remove(arg.split(" ", 1)[1].strip())
        msg = "[dim]Deleted[/]" if not r.get("error") else f"[error]{r['error']}[/]"
        console.print(f"  {msg}")
    elif arg == "history":
        h = sched.get_history(limit=10)
        if not h:
            console.print("  [dim]No history[/]"); return
        for e in h:
            sty = "green" if e["status"] == "success" else "red"
            console.print(f"  [{sty}]{e['status']}[/] [bold]{e['schedule_name']}[/] [dim]{e['finished_at'][:16]}[/]")
    else:
        console.print("  [dim]/schedule [list|add|pause|resume|delete|history][/]")


def main():
    parser = argparse.ArgumentParser(description=f"Brain Agent v{backend.VERSION}")
    parser.add_argument("message", nargs="?")
    parser.add_argument("-m", "--model", default="claude-opus-4-5-20251101")
    parser.add_argument("-i", "--interactive", action="store_true")
    parser.add_argument("-l", "--list-models", action="store_true")
    parser.add_argument("--api-key", default="sk-Xk7kOHpIpZkLutwnyxHpRO9jn4ZwyPaS")
    parser.add_argument("--base-url", default="http://localhost:8317/v1")
    parser.add_argument("-t", "--api-type", choices=["anthropic", "openai"], default="anthropic")
    parser.add_argument("--max-context", type=int, default=backend.DEFAULT_MAX_CONTEXT_TOKENS)
    parser.add_argument("--agent", default="main")
    args = parser.parse_args()

    if args.list_models:
        backend.list_models(args.api_key, args.base_url, args.api_type)
        sys.exit(0)
    if args.interactive:
        run_interactive(args)
        sys.exit(0)
    if not args.message:
        parser.print_help(); sys.exit(1)

    messages = [{"role": "user", "content": args.message}]
    reply = backend.send_message_with_fallback(
        messages, args.model, args.api_key, args.base_url, args.api_type, silent=True)
    if reply:
        console.print(Markdown(reply))


if __name__ == "__main__":
    main()
