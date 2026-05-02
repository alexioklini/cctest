# Extracted from claude_cli.py — interactive REPL / TUI entry point
# Cross-module deps resolved via claude_cli namespace at runtime
import argparse, os, re, select, signal, sys, termios, threading, time, tty

from engine.context import DEFAULT_MAX_CONTEXT_TOKENS  # noqa: F401 — needed for function defaults

def _draw_status_bar(model: str, history: list[dict] | None = None,
                     max_tokens: int = DEFAULT_MAX_CONTEXT_TOKENS) -> None:
    """Draw a status bar on the last terminal line with black background."""
    cols = shutil.get_terminal_size().columns
    rows = shutil.get_terminal_size().lines

    # Context usage
    ctx_part = ""
    ctx_visible = 0
    if history is not None:
        est = _estimate_conversation_tokens(history)
        pct = min(99, int(est / max_tokens * 100))
        if pct >= 75:
            color = RED
        elif pct >= 50:
            color = YELLOW
        else:
            color = FG_GRAY
        # Show token count in k for readability
        if est >= 1000:
            tok_str = f"{est // 1000}k"
        else:
            tok_str = str(est)
        ctx_label = f"{tok_str}/{max_tokens // 1000}k"
        ctx_part = f" {DIM}│{RESET}{BG_DARK} {color}{ctx_label}{RESET}{BG_DARK}"
        ctx_visible = 4 + len(ctx_label)  # " │ Nk/Nk"

    # Agent name
    agent_part = ""
    agent_visible = 0
    if _current_agent and _current_agent.agent_id != "main":
        agent_part = f" {CYAN}{_current_agent.agent_id}{RESET}{BG_DARK} {DIM}│{RESET}{BG_DARK}"
        agent_visible = 1 + len(_current_agent.agent_id) + 3  # " name │"

    label = f" {agent_part}{FG_GRAY}Model:{RESET}{BG_DARK} {GREEN}{BOLD}{model}{RESET}{BG_DARK}{ctx_part} "
    visible_len = 1 + agent_visible + 8 + len(model) + ctx_visible + 1
    padding = max(0, cols - visible_len)
    bar = f"\033[48;5;235m{label}{' ' * padding}{RESET}"
    sys.stdout.write(f"\0337\033[{rows};1H{bar}\0338")
    sys.stdout.flush()


def _setup_scroll_region() -> None:
    """Reserve the bottom line for the status bar."""
    rows = shutil.get_terminal_size().lines
    sys.stdout.write(f"\033[1;{rows - 1}r")
    sys.stdout.write(f"\033[1;1H")
    sys.stdout.flush()


def _restore_scroll_region() -> None:
    """Restore full terminal scroll region."""
    rows = shutil.get_terminal_size().lines
    sys.stdout.write(f"\033[1;{rows}r")
    sys.stdout.write(f"\033[{rows};1H\033[K")
    sys.stdout.flush()


# --- Main ---

def main():
    parser = argparse.ArgumentParser(
        description=f"Brain Agent v{VERSION} — Agentic CLI for LLM APIs"
    )
    parser.add_argument(
        "message", nargs="?",
        help="Message to send (or use -i for interactive mode)",
    )
    parser.add_argument(
        "-m", "--model", default="claude-opus-4-5-20251101",
        help="Model to use (default: claude-opus-4-5-20251101)",
    )
    parser.add_argument(
        "-i", "--interactive", action="store_true",
        help="Interactive mode - continuous chat",
    )
    parser.add_argument(
        "-l", "--list-models", action="store_true",
        help="List available models and exit",
    )
    parser.add_argument(
        "--api-key", default="sk-Xk7kOHpIpZkLutwnyxHpRO9jn4ZwyPaS",
        help="API key for authentication",
    )
    parser.add_argument(
        "--base-url", default="http://localhost:8317/v1",
        help="Base URL for the API (default: http://localhost:8317/v1)",
    )
    parser.add_argument(
        "--max-context", type=int, default=DEFAULT_MAX_CONTEXT_TOKENS,
        help=f"Max context window in tokens (default: {DEFAULT_MAX_CONTEXT_TOKENS})",
    )
    parser.add_argument(
        "--agent", default="main",
        help="Agent ID to start with (default: 'main')",
    )

    args = parser.parse_args()

    if args.list_models:
        list_models(args.api_key, args.base_url)
        sys.exit(0)

    if args.interactive:
        _run_interactive(args)
        sys.exit(0)

    # Single-message mode
    if not args.message:
        parser.print_help()
        sys.exit(1)

    # Try SDK sidecar first for consistent tool loop
    reply = None
    try:
        import sdk_backend
        if sdk_backend.is_sidecar_running():
            # Set up agent context for system prompt
            agent_cfg = AgentConfig(args.agent)
            _thread_local.current_agent = agent_cfg
            _thread_local.memory_store = MemoryStore(args.agent)
            system_prompt = _build_system_prompt(include_memory_summary=True)
            provider_env = sdk_backend.build_provider_env(args.model)

            # Build tool defs for sidecar MCP
            tool_defs = []
            for td in TOOL_DEFINITIONS:
                if td["name"] in ("read_file", "write_file", "edit_file",
                                   "list_directory", "search_files", "execute_command",
                                   "web_fetch"):
                    continue  # SDK has native equivalents
                if td["name"] not in TOOL_DISPATCH:
                    continue
                desc = td["description"]
                if isinstance(desc, tuple):
                    desc = " ".join(desc)
                tool_defs.append({
                    "name": td["name"],
                    "description": desc[:1000],
                    "input_schema": td["input_schema"],
                })

            reply = sdk_backend.query_sync(
                prompt=args.message, model=args.model,
                system_prompt=system_prompt, max_turns=30,
                tool_defs=tool_defs,
                agent_id=args.agent,
            )
    except Exception:
        pass  # Fall through to direct API

    # Fallback: direct API call
    if reply is None:
        messages = [{"role": "user", "content": args.message}]
        reply = send_message_with_fallback(
            messages, args.model, args.api_key, args.base_url,
            silent=True)
    if reply:
        print(render_markdown(reply))


def _print_greeting(model: str, agent_id: str = "default") -> None:
    """Print the Brain Agent startup banner."""
    cwd = os.getcwd()
    latest = CHANGELOG[0] if CHANGELOG else None

    # Color definitions for gradient brain
    C1 = "\033[38;5;213m"  # pink
    C2 = "\033[38;5;177m"  # purple
    C3 = "\033[38;5;141m"  # lavender
    C4 = "\033[38;5;105m"  # blue-purple
    C5 = "\033[38;5;69m"   # blue
    C6 = "\033[38;5;33m"   # deep blue
    CO = "\033[38;5;208m"  # orange accent

    brain = [
        f"  {C1}    ⣀⣀⣤⣤⣤⣤⣤⣤⣀⣀    {RESET}",
        f"  {C1}  ⣴⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣦  {RESET}",
        f"  {C2} ⣾⣿⣿⡟⠛⠛⣿⣿⡟⠛⠛⣿⣿⣿⣷ {RESET}",
        f"  {C2}⣸⣿⣿⡇  ⣸⣿⣿⡇  ⢸⣿⣿⣿⣇{RESET}",
        f"  {C3}⣿⣿⣿⣇  ⣿⣿⣿⣇  ⣸⣿⣿⣿⣿{RESET}",
        f"  {C3}⢿⣿⣿⣿⣦⣤⣿⣿⣿⣦⣤⣾⣿⣿⣿⡿{RESET}",
        f"  {C4} ⠻⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⠟ {RESET}",
        f"  {C5}  ⠙⢿⣿⣿⣿⣿⣿⣿⣿⣿⡿⠋  {RESET}",
        f"  {C6}    ⠉⠛⠿⣿⣿⣿⠿⠛⠉    {RESET}",
    ]

    # Title with gradient
    title = (
        f"  {C1}B{C2}r{C3}a{C4}i{C5}n{RESET} "
        f"{CO}{BOLD}Agent{RESET}"
    )
    ver = f" {DIM}v{VERSION}{RESET}"
    agent_label = f" {DIM}│{RESET} {CYAN}{BOLD}{agent_id}{RESET}" if agent_id != "main" else ""

    print()
    for line in brain:
        print(line)
    print()
    print(f"  {title}{ver}{agent_label}")
    print()

    # Info section with dim separators
    sep = f"{DIM}·{RESET}"
    print(f"  {DIM}Model{RESET}  {GREEN}{model}{RESET}")
    print(f"  {DIM}Path{RESET}   {DIM}{cwd}{RESET}")

    # Agents
    agents = list_agents()
    if len(agents) > 1:
        agent_list = []
        for a in agents:
            if a == agent_id:
                agent_list.append(f"{CYAN}{BOLD}{a}{RESET}")
            else:
                agent_list.append(f"{DIM}{a}{RESET}")
        print(f"  {DIM}Agents{RESET} {' {0} '.format(sep).join(agent_list)}")

    # Skills count
    if _current_agent:
        skills = _current_agent.list_skills()
        if skills:
            skill_names = [s["name"] for s in skills[:5]]
            more = f" {DIM}+{len(skills)-5} more{RESET}" if len(skills) > 5 else ""
            print(f"  {DIM}Skills{RESET} {DIM}{', '.join(skill_names)}{more}{RESET}")

    # Scheduled tasks count
    if _scheduler:
        schedules = _scheduler.list_all()
        active = sum(1 for s in schedules if s["enabled"])
        if schedules:
            print(f"  {DIM}Tasks{RESET}  {DIM}{active} scheduled ({len(schedules)} total){RESET}")

    print()
    print(f"  {DIM}Commands  /new /agent /model /models /tools /schedule{RESET}")
    print(f"  {DIM}Controls  Esc cancel · o toggle tools · exit quit{RESET}")

    if latest:
        print(f"\n  {DIM}↑ v{latest[0]}: {latest[2]}{RESET}")

    print()


# --- Slash commands registry ---

SLASH_COMMANDS = {
    "/help":     "Show this help",
    "/about":    "Show version and changelog",
    "/new":      "Start a new conversation",
    "/agent":    "Switch agent or list agents",
    "/model":    "Switch model",
    "/models":   "List available models",
    "/tools":    "Toggle tool call display",
    "/schedule": "Manage scheduled tasks (list/add/pause/resume/delete/history)",
}


def _print_help() -> None:
    """Print help for all slash commands."""
    print()
    print(f"  {BOLD}Commands{RESET}")
    print()
    for cmd, desc in SLASH_COMMANDS.items():
        print(f"  {GREEN}{BOLD}{cmd:12s}{RESET} {DIM}{desc}{RESET}")
    print()
    print(f"  {BOLD}Schedule subcommands{RESET}")
    print()
    for sub, desc in [
        ("list", "List all scheduled tasks"),
        ("add", "Create a new scheduled task"),
        ("pause NAME", "Pause a task"),
        ("resume NAME", "Resume a task"),
        ("delete NAME", "Delete a task"),
        ("history", "Show execution history"),
    ]:
        print(f"  {GREEN}{BOLD}  {sub:16s}{RESET} {DIM}{desc}{RESET}")
    print()
    print(f"  {BOLD}Keyboard{RESET}")
    print()
    for key, desc in [
        ("Esc", "Cancel current request"),
        ("Tab", "Autocomplete slash commands"),
        ("Up/Down", "Input history"),
        ("Ctrl+A/E", "Beginning/end of line"),
        ("Ctrl+W", "Delete word backward"),
        ("Ctrl+U", "Clear line"),
        ("o", "Toggle tool output (when hidden)"),
    ]:
        print(f"  {YELLOW}{key:12s}{RESET} {DIM}{desc}{RESET}")
    print()


def _print_about() -> None:
    """Print version info and changelog."""
    print()
    print(f"  {BOLD}Brain Agent{RESET}  {GREEN}{BOLD}v{VERSION}{RESET}  {DIM}{VERSION_DATE}{RESET}")
    print()
    print(f"  {BOLD}Changelog{RESET}")
    print()
    for v, date, changes in CHANGELOG[:8]:
        print(f"  {GREEN}{BOLD}v{v}{RESET}  {DIM}{date}{RESET}")
        # Word-wrap changes at ~72 chars
        words = changes.split()
        line = "    "
        for word in words:
            if len(line) + len(word) + 1 > 76:
                print(f"{DIM}{line}{RESET}")
                line = "    " + word
            else:
                line = line + (" " if line.strip() else "") + word
        if line.strip():
            print(f"{DIM}{line}{RESET}")
        print()


def _select_menu(items: list[str], prompt: str = "Select",
                 labels: list[str] | None = None,
                 active: str | None = None) -> str | None:
    """Interactive arrow-key selection menu. Returns selected item or None on Esc/Ctrl-C."""
    if not items:
        return None
    display = labels if labels and len(labels) == len(items) else items
    selected = 0
    # Find active item
    if active and active in items:
        selected = items.index(active)

    fd = sys.stdin.fileno()
    try:
        old_settings = termios.tcgetattr(fd)
    except termios.error:
        return None

    # Count lines we'll draw so we can clean up
    menu_lines = len(items)

    try:
        new_settings = termios.tcgetattr(fd)
        new_settings[3] = new_settings[3] & ~(termios.ICANON | termios.ECHO)
        new_settings[6][termios.VMIN] = 0
        new_settings[6][termios.VTIME] = 0
        termios.tcsetattr(fd, termios.TCSANOW, new_settings)

        # Draw initial menu
        print(f"\n  {DIM}{prompt}:{RESET}")
        for i, label in enumerate(display):
            _draw_menu_item(i, label, i == selected, items[i] == active if active else False)
        sys.stdout.flush()

        while True:
            if select.select([fd], [], [], 0.1)[0]:
                ch = os.read(fd, 1)
                if ch == b'\r' or ch == b'\n':  # Enter
                    break
                elif ch == b'\x1b':  # Escape or arrow
                    seq1 = os.read(fd, 1) if select.select([fd], [], [], 0.05)[0] else b''
                    if seq1 == b'[':
                        seq2 = os.read(fd, 1) if select.select([fd], [], [], 0.05)[0] else b''
                        if seq2 == b'A':  # Up
                            selected = (selected - 1) % len(items)
                        elif seq2 == b'B':  # Down
                            selected = (selected + 1) % len(items)
                        else:
                            # Bare Esc or unknown sequence — cancel
                            _erase_menu(menu_lines + 1)
                            return None
                    else:
                        # Bare Esc — cancel
                        _erase_menu(menu_lines + 1)
                        return None
                elif ch == b'\x03':  # Ctrl-C
                    _erase_menu(menu_lines + 1)
                    return None
                else:
                    continue

                # Redraw menu
                # Move cursor up to start of menu
                sys.stdout.write(f"\033[{menu_lines}A")
                for i, label in enumerate(display):
                    sys.stdout.write(f"\r\033[K")
                    _draw_menu_item(i, label, i == selected, items[i] == active if active else False)
                sys.stdout.flush()

        # Erase menu after selection
        _erase_menu(menu_lines + 1)
        return items[selected]

    except Exception:
        return None
    finally:
        try:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        except termios.error:
            pass


def _draw_menu_item(idx: int, label: str, is_selected: bool, is_active: bool):
    """Draw a single menu item line."""
    marker = f"{CYAN}{BOLD}❯{RESET}" if is_selected else " "
    active_tag = f" {GREEN}(active){RESET}" if is_active else ""
    if is_selected:
        print(f"  {marker} {BOLD}{label}{RESET}{active_tag}")
    else:
        print(f"  {marker} {DIM}{label}{RESET}{active_tag}")


def _erase_menu(lines: int):
    """Erase N lines above cursor."""
    for _ in range(lines):
        sys.stdout.write(f"\033[A\r\033[K")
    sys.stdout.flush()


def _readline(prompt: str, input_history: list[str], history_idx_ref: list[int],
              completions: list[str] | None = None) -> str | None:
    """Read a line with arrow-key history navigation and inline editing.

    Returns the entered string, or None on Ctrl-C / Ctrl-D.
    input_history is a list of previous inputs (newest last).
    history_idx_ref is a single-element list holding the current browse index.
    """
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        sys.stdout.write(prompt)
        sys.stdout.flush()

        buf = []        # current line characters
        pos = 0         # cursor position within buf
        saved_line = "" # stash current input when browsing history
        hist_idx = len(input_history)  # start past the end (= new input)

        while True:
            ch = os.read(fd, 1)

            if ch == b'\r' or ch == b'\n':  # Enter
                # Move cursor to end, print newline
                if pos < len(buf):
                    sys.stdout.write(f"\033[{len(buf) - pos}C")
                sys.stdout.write("\r\n")
                sys.stdout.flush()
                return "".join(buf)

            elif ch == b'\x03':  # Ctrl-C
                sys.stdout.write("\r\n")
                sys.stdout.flush()
                return None

            elif ch == b'\x04':  # Ctrl-D
                if not buf:
                    sys.stdout.write("\r\n")
                    sys.stdout.flush()
                    return None
                # Otherwise ignore

            elif ch == b'\x7f' or ch == b'\x08':  # Backspace
                if pos > 0:
                    buf.pop(pos - 1)
                    pos -= 1
                    # Redraw from cursor position
                    tail = "".join(buf[pos:])
                    sys.stdout.write(f"\033[D{tail} \033[{len(tail) + 1}D")
                    sys.stdout.flush()

            elif ch == b'\x15':  # Ctrl-U: clear line
                if pos > 0:
                    sys.stdout.write(f"\033[{pos}D")
                sys.stdout.write(" " * len(buf))
                if len(buf) > 0:
                    sys.stdout.write(f"\033[{len(buf)}D")
                buf.clear()
                pos = 0
                sys.stdout.flush()

            elif ch == b'\x01':  # Ctrl-A: beginning of line
                if pos > 0:
                    sys.stdout.write(f"\033[{pos}D")
                    pos = 0
                    sys.stdout.flush()

            elif ch == b'\x05':  # Ctrl-E: end of line
                if pos < len(buf):
                    sys.stdout.write(f"\033[{len(buf) - pos}C")
                    pos = len(buf)
                    sys.stdout.flush()

            elif ch == b'\x17':  # Ctrl-W: delete word backward
                if pos > 0:
                    old_pos = pos
                    # Skip trailing spaces
                    while pos > 0 and buf[pos - 1] == ' ':
                        pos -= 1
                    # Skip word
                    while pos > 0 and buf[pos - 1] != ' ':
                        pos -= 1
                    deleted = old_pos - pos
                    del buf[pos:old_pos]
                    sys.stdout.write(f"\033[{deleted}D")
                    tail = "".join(buf[pos:])
                    sys.stdout.write(f"{tail}{' ' * deleted}")
                    sys.stdout.write(f"\033[{len(tail) + deleted}D")
                    sys.stdout.flush()

            elif ch == b'\x1b':  # Escape sequence
                seq1 = os.read(fd, 1)
                if seq1 == b'[':
                    seq2 = os.read(fd, 1)

                    if seq2 == b'A':  # Up arrow
                        if hist_idx > 0:
                            if hist_idx == len(input_history):
                                saved_line = "".join(buf)
                            hist_idx -= 1
                            _replace_line(buf, pos, input_history[hist_idx])
                            buf[:] = list(input_history[hist_idx])
                            pos = len(buf)

                    elif seq2 == b'B':  # Down arrow
                        if hist_idx < len(input_history):
                            hist_idx += 1
                            if hist_idx == len(input_history):
                                _replace_line(buf, pos, saved_line)
                                buf[:] = list(saved_line)
                            else:
                                _replace_line(buf, pos, input_history[hist_idx])
                                buf[:] = list(input_history[hist_idx])
                            pos = len(buf)

                    elif seq2 == b'C':  # Right arrow
                        if pos < len(buf):
                            sys.stdout.write("\033[C")
                            pos += 1
                            sys.stdout.flush()

                    elif seq2 == b'D':  # Left arrow
                        if pos > 0:
                            sys.stdout.write("\033[D")
                            pos -= 1
                            sys.stdout.flush()

                    elif seq2 == b'H':  # Home
                        if pos > 0:
                            sys.stdout.write(f"\033[{pos}D")
                            pos = 0
                            sys.stdout.flush()

                    elif seq2 == b'F':  # End
                        if pos < len(buf):
                            sys.stdout.write(f"\033[{len(buf) - pos}C")
                            pos = len(buf)
                            sys.stdout.flush()

                    elif seq2 == b'3':  # Delete key (ESC [ 3 ~)
                        seq3 = os.read(fd, 1)  # consume '~'
                        if pos < len(buf):
                            buf.pop(pos)
                            tail = "".join(buf[pos:])
                            sys.stdout.write(f"{tail} \033[{len(tail) + 1}D")
                            sys.stdout.flush()
                # Bare Escape — ignore (don't break input)

            elif ch == b'\t':  # Tab — autocomplete
                current = "".join(buf)
                comp_list = completions or list(SLASH_COMMANDS.keys())
                if current.startswith("/"):
                    matches = [c for c in comp_list if c.startswith(current)]
                    if len(matches) == 1:
                        # Single match — complete it
                        completion = matches[0]
                        # Add space after completed command
                        if not completion.endswith(" "):
                            completion += " "
                        _replace_line(buf, pos, completion)
                        buf[:] = list(completion)
                        pos = len(buf)
                    elif len(matches) > 1:
                        # Multiple matches — find common prefix
                        prefix = os.path.commonprefix(matches)
                        if len(prefix) > len(current):
                            _replace_line(buf, pos, prefix)
                            buf[:] = list(prefix)
                            pos = len(buf)
                        else:
                            # Show options briefly below
                            sys.stdout.write(f"\n  {DIM}{' '.join(matches)}{RESET}")
                            sys.stdout.write(f"\033[A")  # move back up
                            # Redraw prompt + buffer
                            sys.stdout.write(f"\r{prompt}{''.join(buf)}")
                            sys.stdout.flush()

            elif ch >= b' ':  # Printable character
                char = ch.decode("utf-8", errors="replace")
                buf.insert(pos, char)
                pos += 1
                tail = "".join(buf[pos:])
                sys.stdout.write(f"{char}{tail}")
                if tail:
                    sys.stdout.write(f"\033[{len(tail)}D")
                sys.stdout.flush()

    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


def _replace_line(buf: list, pos: int, new_text: str) -> None:
    """Clear the current input line and write new_text."""
    # Move cursor to start of input
    if pos > 0:
        sys.stdout.write(f"\033[{pos}D")
    # Clear old content
    sys.stdout.write(" " * len(buf))
    if len(buf) > 0:
        sys.stdout.write(f"\033[{len(buf)}D")
    # Write new content
    sys.stdout.write(new_text)
    sys.stdout.flush()



def _switch_agent(agent_id: str, args) -> tuple[str, AgentConfig]:
    """Switch to a different agent. Returns (model, agent_config)."""
    global _current_agent, _memory_store, _mcp_manager
    agent = AgentConfig(agent_id)
    _current_agent = agent
    _memory_store = MemoryStore(agent_id=agent_id, base_dir=agent.memory_dir)

    # Load MCP servers: reuse shared manager if available, else create one
    if not _mcp_manager:
        _mcp_manager = MCPManager()
    else:
        _mcp_manager.stop_all()
    main_mcp = os.path.join(AGENTS_DIR, "main", "mcp.json")
    _mcp_manager.load_config(main_mcp)
    if agent_id != "main":
        _mcp_manager.load_config(agent.mcp_config_path)

    model = agent.preferred_model or args.model
    return model, agent


def _run_interactive(args):
    """Run the interactive TUI chat loop."""
    global _memory_store, _current_agent
    global _delegate_fallback_model, _delegate_api_key, _delegate_base_url

    history = []
    input_history = []   # list of previous user inputs for arrow-key recall
    history_idx = [0]    # mutable ref for current position

    # Store API config for delegation
    _delegate_api_key = args.api_key
    _delegate_base_url = args.base_url
    _delegate_fallback_model = args.model

    # SDK session tracking (for resume across turns)
    _run_interactive._sdk_sid = None

    # Initialize agent
    current_model, _ = _switch_agent(args.agent, args)

    # Start scheduler
    global _scheduler
    _scheduler = Scheduler()
    _scheduler.start()

    # Ensure memory summary schedules
    try:
        ensure_memory_summary_schedules()
    except Exception:
        pass

    # Initialize background task runner
    global _task_runner
    _task_runner = TaskRunner()

    # Clear screen and move cursor to top
    sys.stdout.write("\033[2J\033[H")
    sys.stdout.flush()

    _setup_scroll_region()
    _draw_status_bar(current_model, history, args.max_context)

    # Handle terminal resize
    def _on_resize(signum, frame):
        _setup_scroll_region()
        _draw_status_bar(current_model, history, args.max_context)

    old_sigwinch = signal.signal(signal.SIGWINCH, _on_resize)

    # Startup greeting
    _print_greeting(current_model, args.agent)

    try:
        while True:
            # Read input with history support
            message = _readline(f"\n{BOLD}{GREEN}❯{RESET} ", input_history, history_idx)
            if message is None:
                print(f"{DIM}Bye!{RESET}")
                break

            stripped = message.strip().lower()

            if stripped in ("exit", "quit"):
                print(f"{DIM}Bye!{RESET}")
                break

            if stripped == "/help":
                _print_help()
                continue

            if stripped == "/about":
                _print_about()
                continue

            if stripped == "/new":
                history = []
                print(f"\n{DIM}{'─' * 40}{RESET}")
                print(f"{DIM}  New chat started{RESET}")
                print(f"{DIM}{'─' * 40}{RESET}")
                _draw_status_bar(current_model, history, args.max_context)
                continue

            if stripped == "/tools":
                global _show_tools
                _show_tools = not _show_tools
                state = f"{GREEN}visible{RESET}" if _show_tools else f"{DIM}hidden{RESET}"
                print(f"  {DIM}Tool display:{RESET} {state}")
                continue

            if stripped.startswith("/agent"):
                arg = message.strip()[6:].strip()
                agents = list_agents()
                if arg:
                    if arg not in agents:
                        print(f"  {DIM}Creating new agent:{RESET} {BOLD}{arg}{RESET}")
                    current_model, _ = _switch_agent(arg, args)
                    history = []
                    print(f"  {DIM}Switched to agent:{RESET} {BOLD}{arg}{RESET} {DIM}(model: {current_model}){RESET}")
                else:
                    # Build labels with descriptions
                    labels = []
                    for aid in agents:
                        cfg = AgentConfig(aid)
                        model_info = f" [{cfg.preferred_model}]" if cfg.preferred_model else ""
                        labels.append(f"{aid}{model_info} — {cfg.description}")
                    current_aid = _current_agent.agent_id if _current_agent else "main"
                    choice = _select_menu(agents, "Select agent", labels=labels, active=current_aid)
                    if choice:
                        current_model, _ = _switch_agent(choice, args)
                        history = []
                        print(f"  {DIM}Switched to agent:{RESET} {BOLD}{choice}{RESET} {DIM}(model: {current_model}){RESET}")
                _draw_status_bar(current_model, history, args.max_context)
                continue

            if stripped.startswith("/schedule"):
                arg = message.strip()[9:].strip()
                if not _scheduler:
                    print(f"  {DIM}Scheduler not running{RESET}")
                    continue

                if arg == "" or arg == "list":
                    # List schedules
                    schedules = _scheduler.list_all()
                    if not schedules:
                        print(f"\n  {DIM}No scheduled tasks{RESET}")
                    else:
                        print()
                        for s in schedules:
                            status = f"{GREEN}active{RESET}" if s["enabled"] else f"{DIM}paused{RESET}"
                            next_r = s.get("next_run", "")[:16] if s.get("next_run") else "—"
                            print(f"  {BOLD}{s['name']}{RESET} [{status}] {DIM}{s['schedule']}{RESET}")
                            print(f"    {DIM}agent:{RESET} {s['agent']}  {DIM}next:{RESET} {next_r}")
                            print(f"    {DIM}task:{RESET} {s['task'][:60]}")
                    continue

                elif arg == "add":
                    # Interactive add
                    print(f"\n  {DIM}Add scheduled task{RESET}")
                    name = _readline(f"  {DIM}Name:{RESET} ", [], [0])
                    if not name or not name.strip():
                        continue
                    name = name.strip()
                    task = _readline(f"  {DIM}Task:{RESET} ", [], [0])
                    if not task or not task.strip():
                        continue
                    task = task.strip()
                    schedule = _readline(f"  {DIM}Schedule (every Xm/Xh/Xd, daily HH:MM, weekly DOW HH:MM):{RESET} ", [], [0])
                    if not schedule or not schedule.strip():
                        continue
                    schedule = schedule.strip()
                    agent = _readline(f"  {DIM}Agent (default: main):{RESET} ", [], [0])
                    agent = agent.strip() if agent and agent.strip() else "main"
                    model_in = _readline(f"  {DIM}Model (default: current):{RESET} ", [], [0])
                    model_val = model_in.strip() if model_in and model_in.strip() else None

                    result = _scheduler.add(name, task, schedule, agent, model_val)
                    if result.get("error"):
                        print(f"  {RED}{result['error']}{RESET}")
                    else:
                        print(f"  {GREEN}✔ Created:{RESET} {BOLD}{name}{RESET} — next run: {result.get('next_run', '')[:16]}")
                    continue

                elif arg.startswith("pause "):
                    name = arg[6:].strip()
                    result = _scheduler.pause(name)
                    if result.get("error"):
                        print(f"  {RED}{result['error']}{RESET}")
                    else:
                        print(f"  {DIM}Paused:{RESET} {name}")
                    continue

                elif arg.startswith("resume "):
                    name = arg[7:].strip()
                    result = _scheduler.resume(name)
                    if result.get("error"):
                        print(f"  {RED}{result['error']}{RESET}")
                    else:
                        print(f"  {GREEN}Resumed:{RESET} {name} — next: {result.get('next_run', '')[:16]}")
                    continue

                elif arg.startswith("delete ") or arg.startswith("rm "):
                    name = arg.split(" ", 1)[1].strip()
                    result = _scheduler.remove(name)
                    if result.get("error"):
                        print(f"  {RED}{result['error']}{RESET}")
                    else:
                        print(f"  {DIM}Deleted:{RESET} {name}")
                    continue

                elif arg == "history":
                    history_items = _scheduler.get_history(limit=10)
                    if not history_items:
                        print(f"\n  {DIM}No execution history{RESET}")
                    else:
                        print()
                        for h in history_items:
                            status_color = GREEN if h["status"] == "success" else RED
                            print(f"  {status_color}{h['status']}{RESET} {BOLD}{h['schedule_name']}{RESET} {DIM}({h['finished_at'][:16]}){RESET}")
                            if h.get("result"):
                                preview = h["result"][:80].replace("\n", " ")
                                print(f"    {DIM}{preview}{RESET}")
                    continue

                else:
                    print(f"  {DIM}Usage: /schedule [list|add|pause NAME|resume NAME|delete NAME|history]{RESET}")
                    continue

            if stripped == "/models":
                models = get_available_models(args.api_key, args.base_url)
                if models:
                    choice = _select_menu(models, "Select model", active=current_model)
                    if choice:
                        current_model = choice
                        print(f"  {DIM}Switched to:{RESET} {BOLD}{current_model}{RESET}")
                        _draw_status_bar(current_model, history, args.max_context)
                else:
                    print(f"  {DIM}No models available{RESET}")
                continue

            if stripped.startswith("/model"):
                arg = message.strip()[6:].strip()
                models = get_available_models(args.api_key, args.base_url)
                if arg:
                    current_model = arg
                    print(f"  {DIM}Switched to:{RESET} {BOLD}{current_model}{RESET}")
                elif models:
                    choice = _select_menu(models, "Select model", active=current_model)
                    if choice:
                        current_model = choice
                        print(f"  {DIM}Switched to:{RESET} {BOLD}{current_model}{RESET}")
                else:
                    print(f"  {DIM}No models available. Use: /model <name>{RESET}")
                _draw_status_bar(current_model, history, args.max_context)
                continue

            # Custom slash commands (from agent's commands.json + .claude/commands/*.md)
            if message.strip().startswith("/"):
                cmd_name = message.strip().split()[0][1:].lower()
                cmd_args = message.strip()[len(cmd_name)+2:].strip()
                agent = getattr(_thread_local, 'current_agent', None) or _current_agent
                if agent:
                    for cmd in agent.load_commands():
                        if (cmd.get("name", "").lower() == cmd_name or
                                cmd.get("slug", "").lower() == cmd_name):
                            message = AgentConfig.expand_command(cmd, cmd_args)
                            print(f"  {DIM}Running /{cmd_name}{RESET}")
                            break

            if not message.strip():
                continue

            # Save to input history (dedup consecutive)
            if not input_history or input_history[-1] != message.strip():
                input_history.append(message.strip())

            # Send message
            history.append({"role": "user", "content": message})
            _reset_tool_tracking()

            # Check context window and compact if needed
            history, was_compacted = _check_and_compact(
                history, current_model, args.api_key, args.base_url,
                max_tokens=args.max_context,
            )

            # Start spinner and escape watcher
            spinner = Spinner(current_model)
            escape_watcher = EscapeWatcher()
            spinner.start()
            escape_watcher.start()

            cancelled = False
            reply = None
            try:
                # Try SDK sidecar first
                _sdk_ok = False
                try:
                    import sdk_backend
                    if sdk_backend.is_sidecar_running():
                        system_prompt = _build_system_prompt(include_memory_summary=True)
                        # Build tool defs (skip SDK-native tools)
                        _SDK_NATIVE = {"read_file", "write_file", "edit_file",
                                       "list_directory", "search_files",
                                       "execute_command", "web_fetch"}
                        tool_defs = []
                        for td in TOOL_DEFINITIONS:
                            if td["name"] in _SDK_NATIVE or td["name"] not in TOOL_DISPATCH:
                                continue
                            desc = td["description"]
                            if isinstance(desc, tuple):
                                desc = " ".join(desc)
                            tool_defs.append({
                                "name": td["name"],
                                "description": desc[:1000],
                                "input_schema": td["input_schema"],
                            })
                        agent = getattr(_thread_local, 'current_agent', None) or _current_agent
                        agent_id = agent.agent_id if agent else "main"

                        meta = sdk_backend.query_sync(
                            prompt=message, model=current_model,
                            system_prompt=system_prompt, max_turns=30,
                            tool_defs=tool_defs,
                            agent_id=agent_id,
                            cancel_fn=lambda: escape_watcher.cancelled,
                            sdk_session_id=getattr(_run_interactive, '_sdk_sid', None),
                            return_metadata=True,
                        )
                        if meta is not None:
                            reply = meta.get("text")
                            # Track SDK session for resume on next turn
                            if meta.get("sdk_session_id"):
                                _run_interactive._sdk_sid = meta["sdk_session_id"]
                            _sdk_ok = True
                        elif escape_watcher.cancelled:
                            cancelled = True
                            _sdk_ok = True
                except Exception:
                    pass  # Fall through to direct API

                # Fallback: direct API call
                if not _sdk_ok and not cancelled:
                    reply = send_message_with_fallback(
                        history, current_model, args.api_key, args.base_url,
                        silent=True, escape_watcher=escape_watcher)
            except TaskCancelled:
                cancelled = True
                reply = None
            finally:
                elapsed = spinner.stop()
                escape_watcher.stop()

            if cancelled:
                # Remove the user message that was cancelled
                history.pop()
                print(f"\n{DIM}✘ Cancelled (Esc){RESET}")
                _draw_status_bar(current_model, history, args.max_context)
                continue

            if reply:
                history.append({"role": "assistant", "content": reply})

                # Render formatted response
                print()
                rendered = render_markdown(reply)
                print(rendered)

                # Completion message
                verb = spinner.verb.rstrip("ing")
                # Make past tense
                past = spinner.verb[:-3] + "ed" if spinner.verb.endswith("ing") else spinner.verb
                if spinner.verb == "Thinking":
                    past = "Thought"
                elif spinner.verb == "Weaving":
                    past = "Woven"
                elif spinner.verb == "Computing":
                    past = "Computed"
                elif spinner.verb == "Brewing":
                    past = "Brewed"
                elif spinner.verb == "Baking":
                    past = "Baked"
                elif spinner.verb == "Crafting":
                    past = "Crafted"
                elif spinner.verb == "Conjuring":
                    past = "Conjured"
                elif spinner.verb == "Composing":
                    past = "Composed"
                elif spinner.verb == "Contemplating":
                    past = "Contemplated"
                elif spinner.verb == "Pondering":
                    past = "Pondered"

                print(f"\n{DIM}✻ {past} for {elapsed:.0f}s{RESET}")
            else:
                print(f"\n{DIM}(no response){RESET}")

            # Restore status bar after response
            _draw_status_bar(current_model, history, args.max_context)

    finally:
        if _scheduler:
            _scheduler.stop()
        if _mcp_manager:
            _mcp_manager.stop_all()
        signal.signal(signal.SIGWINCH, old_sigwinch)
        _restore_scroll_region()


if __name__ == "__main__":
    main()
