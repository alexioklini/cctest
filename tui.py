#!/usr/bin/env python3
"""Brain Agent — Textual TUI for interactive chat with LLM APIs."""

import argparse
import json
import os
import sys
import time

# ---------------------------------------------------------------------------
# Ensure the backend is importable from the same directory
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import claude_cli  # noqa: E402 — the backend

from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import (
    Input,
    OptionList,
    Static,
)
from textual.widgets.option_list import Option


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
VERSION = claude_cli.VERSION
SLASH_COMMANDS = list(claude_cli.SLASH_COMMANDS.keys())

BRAIN_ART = r"""
[#d787ff]    ⣀⣀⣤⣤⣤⣤⣤⣤⣀⣀    [/]
[#d787ff]  ⣴⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣦  [/]
[#af87ff] ⣾⣿⣿⡟⠛⠛⣿⣿⡟⠛⠛⣿⣿⣿⣷ [/]
[#af87ff]⣸⣿⣿⡇  ⣸⣿⣿⡇  ⢸⣿⣿⣿⣇[/]
[#8787ff]⣿⣿⣿⣇  ⣿⣿⣿⣇  ⣸⣿⣿⣿⣿[/]
[#8787ff]⢿⣿⣿⣿⣦⣤⣿⣿⣿⣦⣤⣾⣿⣿⣿⡿[/]
[#5f5fff] ⠻⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⠟ [/]
[#005fff]  ⠙⢿⣿⣿⣿⣿⣿⣿⣿⣿⡿⠋  [/]
[#0057af]    ⠉⠛⠿⣿⣿⣿⠿⠛⠉    [/]
"""

BRAIN_TITLE = (
    "[#d787ff]B[/#d787ff][#af87ff]r[/#af87ff][#8787ff]a[/#8787ff]"
    "[#5f5fff]i[/#5f5fff][#005fff]n[/#005fff] "
    "[#ff8700 bold]Agent[/#ff8700 bold]"
)


# ---------------------------------------------------------------------------
# Utility: strip ANSI from backend text
# ---------------------------------------------------------------------------
def _strip_ansi(text: str) -> str:
    """Remove ANSI escape codes so we can render with Rich markup instead."""
    import re
    text = re.sub(r"\033\[[0-9;]*[a-zA-Z]", "", text)
    text = re.sub(r"\033\[\?[0-9;]*[a-zA-Z]", "", text)
    text = re.sub(r"\033\([A-Z]", "", text)
    text = re.sub(r"\033][^\a]*\a", "", text)
    text = re.sub(r"\r", "", text)
    return text


# ---------------------------------------------------------------------------
# Chat message widgets
# ---------------------------------------------------------------------------
class UserMessage(Static):
    """A user message bubble."""

    DEFAULT_CSS = """
    UserMessage {
        margin: 1 2 0 4;
        padding: 1 2;
        background: $primary-background;
        color: $text;
        border: round $primary;
    }
    """

    def __init__(self, text: str) -> None:
        super().__init__(text)


class AssistantMessage(Static):
    """An assistant response bubble."""

    DEFAULT_CSS = """
    AssistantMessage {
        margin: 1 4 0 2;
        padding: 1 2;
        background: $surface;
        color: $text;
        border: round $secondary;
    }
    """

    def __init__(self, text: str) -> None:
        super().__init__(text)


class ToolCallMessage(Static):
    """Display for a tool invocation."""

    DEFAULT_CSS = """
    ToolCallMessage {
        margin: 0 4 0 2;
        padding: 0 1;
        color: $text-muted;
    }
    """

    def __init__(self, name: str, args: dict) -> None:
        summary_parts = [f"[bold cyan]{name}[/bold cyan]("]
        for k, v in args.items():
            val_str = str(v)
            if len(val_str) > 80:
                val_str = val_str[:77] + "..."
            summary_parts.append(f"[magenta]{k}[/magenta]={val_str}, ")
        text = "".join(summary_parts).rstrip(", ") + ")"
        super().__init__(f"[dim]  > {text}[/dim]")


class ToolResultMessage(Static):
    """Display for a tool result."""

    DEFAULT_CSS = """
    ToolResultMessage {
        margin: 0 4 0 2;
        padding: 0 1;
        color: $text-muted;
    }
    """

    def __init__(self, name: str, result_str: str) -> None:
        try:
            rdata = json.loads(result_str)
        except json.JSONDecodeError:
            rdata = {}

        if rdata.get("error"):
            label = f"[bold red]Error:[/bold red] {str(rdata['error'])[:120]}"
        elif name == "read_file":
            label = f"[green]Read[/green] {rdata.get('total_lines', '?')} lines from {rdata.get('path', '?')}"
        elif name == "write_file":
            label = f"[green]Wrote[/green] {rdata.get('path', '?')} ({rdata.get('size', 0)} bytes)"
        elif name == "edit_file":
            label = f"[green]Edited[/green] {rdata.get('path', '?')} ({rdata.get('replacements', 0)} replacements)"
        elif name == "execute_command":
            ec = rdata.get("exit_code", -1)
            color = "green" if ec == 0 else "red"
            label = f"[{color}]exit {ec}[/{color}]"
            output = rdata.get("output", "")
            first_line = output.split("\n")[0][:100] if output else ""
            if first_line:
                label += f" — {first_line}"
        elif name == "exa_search":
            label = f"[green]{rdata.get('result_count', 0)} results[/green]"
        elif name == "list_directory":
            label = f"[green]{rdata.get('count', 0)} entries[/green]"
        elif name == "search_files":
            label = f"[green]{rdata.get('match_count', 0)} matches[/green]"
        elif name == "memory_store":
            label = f"[green]Stored[/green] {rdata.get('name', '')}"
        elif name == "memory_recall":
            label = f"[green]{rdata.get('count', 0)} memories[/green]"
        elif name == "delegate_task":
            label = f"[green]{rdata.get('agent', '')} responded[/green]"
        elif name == "use_skill":
            label = f"[green]Loaded skill:[/green] {rdata.get('skill', '')}"
        else:
            label = "[green]Done[/green]"

        super().__init__(f"[dim]  < {label}[/dim]")


class SystemMessage(Static):
    """System/info message (e.g. new chat, model switch)."""

    DEFAULT_CSS = """
    SystemMessage {
        margin: 1 6;
        color: $text-muted;
        text-style: italic;
    }
    """


# ---------------------------------------------------------------------------
# Welcome screen widget
# ---------------------------------------------------------------------------
class WelcomePanel(Static):
    """The welcome/greeting panel shown at startup."""

    DEFAULT_CSS = """
    WelcomePanel {
        margin: 2 4;
        padding: 1 2;
        text-align: center;
    }
    """

    def __init__(self, model: str, agent_id: str) -> None:
        lines = [BRAIN_ART, "", f"  {BRAIN_TITLE} [dim]v{VERSION}[/dim]", ""]

        # Info
        lines.append(f"  [dim]Model[/dim]  [green]{model}[/green]")
        agents = claude_cli.list_agents()
        if len(agents) > 1:
            parts = []
            for a in agents:
                if a == agent_id:
                    parts.append(f"[bold cyan]{a}[/bold cyan]")
                else:
                    parts.append(f"[dim]{a}[/dim]")
            lines.append(f"  [dim]Agents[/dim] {' | '.join(parts)}")

        if claude_cli._current_agent:
            skills = claude_cli._current_agent.list_skills()
            if skills:
                names = [s["name"] for s in skills[:5]]
                more = f" [dim]+{len(skills)-5} more[/dim]" if len(skills) > 5 else ""
                lines.append(f"  [dim]Skills[/dim] [dim]{', '.join(names)}{more}[/dim]")

        lines.append("")
        lines.append("  [dim]Commands  /new  /agent  /model  /models  /tools  /schedule  /help[/dim]")
        lines.append("  [dim]Ctrl+C to quit[/dim]")

        latest = claude_cli.CHANGELOG[0] if claude_cli.CHANGELOG else None
        if latest:
            lines.append(f"\n  [dim]v{latest[0]}: {latest[2]}[/dim]")

        super().__init__("\n".join(lines))


# ---------------------------------------------------------------------------
# Status bar widget
# ---------------------------------------------------------------------------
class StatusBar(Static):
    """Bottom status bar showing agent, model, and token count."""

    DEFAULT_CSS = """
    StatusBar {
        dock: bottom;
        height: 1;
        background: $surface;
        color: $text;
        padding: 0 2;
    }
    """

    agent_id: reactive[str] = reactive("main")
    model: reactive[str] = reactive("")
    token_count: reactive[int] = reactive(0)
    max_tokens: reactive[int] = reactive(claude_cli.DEFAULT_MAX_CONTEXT_TOKENS)

    def render(self) -> str:
        pct = int(self.token_count / self.max_tokens * 100) if self.max_tokens else 0
        agent_part = f"[bold cyan]{self.agent_id}[/bold cyan]"
        model_part = f"[green]{self.model}[/green]"
        ctx_part = f"[dim]{self.token_count:,} / {self.max_tokens:,} tokens ({pct}%)[/dim]"
        return f" {agent_part} | {model_part} | {ctx_part}"


# ---------------------------------------------------------------------------
# Chat input with history and tab completion
# ---------------------------------------------------------------------------
class ChatInput(Input):
    """Input widget with command history and slash-command tab completion."""

    DEFAULT_CSS = """
    ChatInput {
        dock: bottom;
        margin: 0 0;
    }
    """

    def __init__(self) -> None:
        super().__init__(placeholder="Type a message... (/help for commands)")
        self._history: list[str] = []
        self._history_idx: int = 0
        self._stash: str = ""  # stash current input when browsing history

    def add_to_history(self, text: str) -> None:
        if text.strip() and (not self._history or self._history[-1] != text.strip()):
            self._history.append(text.strip())
        self._history_idx = len(self._history)
        self._stash = ""

    def _on_key(self, event) -> None:
        if event.key == "up":
            event.prevent_default()
            event.stop()
            if self._history:
                if self._history_idx == len(self._history):
                    self._stash = self.value
                if self._history_idx > 0:
                    self._history_idx -= 1
                    self.value = self._history[self._history_idx]
                    self.cursor_position = len(self.value)
        elif event.key == "down":
            event.prevent_default()
            event.stop()
            if self._history_idx < len(self._history):
                self._history_idx += 1
                if self._history_idx == len(self._history):
                    self.value = self._stash
                else:
                    self.value = self._history[self._history_idx]
                self.cursor_position = len(self.value)
        elif event.key == "tab":
            event.prevent_default()
            event.stop()
            val = self.value.strip()
            if val.startswith("/"):
                matches = [c for c in SLASH_COMMANDS if c.startswith(val)]
                if len(matches) == 1:
                    self.value = matches[0] + " "
                    self.cursor_position = len(self.value)
                elif matches:
                    # Complete the common prefix
                    prefix = os.path.commonprefix(matches)
                    if len(prefix) > len(val):
                        self.value = prefix
                        self.cursor_position = len(self.value)


# ---------------------------------------------------------------------------
# Help screen
# ---------------------------------------------------------------------------
class HelpScreen(ModalScreen[None]):
    """Modal screen showing help."""

    DEFAULT_CSS = """
    HelpScreen {
        align: center middle;
    }
    #help-panel {
        width: 70;
        max-height: 80%;
        border: round $primary;
        background: $surface;
        padding: 1 2;
    }
    """

    BINDINGS = [
        Binding("escape", "dismiss_help", "Close"),
    ]

    def compose(self) -> ComposeResult:
        lines = ["[bold]Commands[/bold]\n"]
        for cmd, desc in claude_cli.SLASH_COMMANDS.items():
            lines.append(f"  [bold green]{cmd:12s}[/bold green] [dim]{desc}[/dim]")
        lines.append("\n[bold]Schedule subcommands[/bold]\n")
        for sub, desc in [
            ("list", "List all scheduled tasks"),
            ("add", "Create a new scheduled task"),
            ("pause NAME", "Pause a task"),
            ("resume NAME", "Resume a task"),
            ("delete NAME", "Delete a task"),
            ("history", "Show execution history"),
        ]:
            lines.append(f"  [bold green]  {sub:16s}[/bold green] [dim]{desc}[/dim]")
        lines.append("\n[bold]Keyboard[/bold]\n")
        for key, desc in [
            ("Ctrl+C", "Quit"),
            ("Escape", "Close dialogs"),
            ("Tab", "Autocomplete slash commands"),
            ("Up/Down", "Input history"),
        ]:
            lines.append(f"  [yellow]{key:12s}[/yellow] [dim]{desc}[/dim]")

        with Vertical(id="help-panel"):
            yield Static("\n".join(lines))

    def action_dismiss_help(self) -> None:
        self.dismiss(None)


# ---------------------------------------------------------------------------
# Model selection screen
# ---------------------------------------------------------------------------
class ModelSelectScreen(ModalScreen[str | None]):
    """Modal for selecting a model."""

    DEFAULT_CSS = """
    ModelSelectScreen {
        align: center middle;
    }
    #model-panel {
        width: 60;
        max-height: 80%;
        border: round $primary;
        background: $surface;
        padding: 1 2;
    }
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, models: list[str], current: str) -> None:
        super().__init__()
        self._models = models
        self._current = current

    def compose(self) -> ComposeResult:
        with Vertical(id="model-panel"):
            yield Static("[bold]Select Model[/bold]\n")
            options = []
            for m in self._models:
                label = f"  {m}  [bold cyan]<--[/bold cyan]" if m == self._current else f"  {m}"
                options.append(Option(label, id=m))
            yield OptionList(*options, id="model-list")

    @on(OptionList.OptionSelected)
    def _on_select(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option_id)

    def action_cancel(self) -> None:
        self.dismiss(None)


# ---------------------------------------------------------------------------
# Agent selection screen
# ---------------------------------------------------------------------------
class AgentSelectScreen(ModalScreen[str | None]):
    """Modal for selecting an agent."""

    DEFAULT_CSS = """
    AgentSelectScreen {
        align: center middle;
    }
    #agent-panel {
        width: 60;
        max-height: 80%;
        border: round $primary;
        background: $surface;
        padding: 1 2;
    }
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, agents: list[str], current: str) -> None:
        super().__init__()
        self._agents = agents
        self._current = current

    def compose(self) -> ComposeResult:
        with Vertical(id="agent-panel"):
            yield Static("[bold]Select Agent[/bold]\n")
            options = []
            for a in self._agents:
                cfg = claude_cli.AgentConfig(a)
                desc = cfg.description
                model_info = f" [{cfg.preferred_model}]" if cfg.preferred_model else ""
                marker = " [bold cyan]<--[/bold cyan]" if a == self._current else ""
                options.append(Option(f"  {a}{model_info} -- {desc}{marker}", id=a))
            yield OptionList(*options, id="agent-list")

    @on(OptionList.OptionSelected)
    def _on_select(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option_id)

    def action_cancel(self) -> None:
        self.dismiss(None)


# ---------------------------------------------------------------------------
# Schedule list screen
# ---------------------------------------------------------------------------
class ScheduleScreen(ModalScreen[None]):
    """Modal showing scheduled tasks."""

    DEFAULT_CSS = """
    ScheduleScreen {
        align: center middle;
    }
    #schedule-panel {
        width: 80;
        max-height: 80%;
        border: round $primary;
        background: $surface;
        padding: 1 2;
        overflow-y: auto;
    }
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def compose(self) -> ComposeResult:
        with Vertical(id="schedule-panel"):
            yield Static("[bold]Scheduled Tasks[/bold]\n")
            if not claude_cli._scheduler:
                yield Static("[dim]Scheduler not initialized[/dim]")
                return
            schedules = claude_cli._scheduler.list_all()
            if not schedules:
                yield Static("[dim]No scheduled tasks[/dim]")
            else:
                for s in schedules:
                    status = "[green]active[/green]" if s["enabled"] else "[dim]paused[/dim]"
                    next_r = s.get("next_run", "")[:16] if s.get("next_run") else "--"
                    yield Static(
                        f"  [bold]{s['name']}[/bold] [{status}] [dim]{s['schedule']}[/dim]\n"
                        f"    [dim]agent:[/dim] {s['agent']}  [dim]next:[/dim] {next_r}\n"
                        f"    [dim]task:[/dim] {s['task'][:80]}\n"
                    )
            yield Static("\n[dim]Press Escape to close[/dim]")

    def action_cancel(self) -> None:
        self.dismiss(None)


# ---------------------------------------------------------------------------
# Tools list screen
# ---------------------------------------------------------------------------
class ToolsScreen(ModalScreen[None]):
    """Modal showing available tools."""

    DEFAULT_CSS = """
    ToolsScreen {
        align: center middle;
    }
    #tools-panel {
        width: 80;
        max-height: 80%;
        border: round $primary;
        background: $surface;
        padding: 1 2;
        overflow-y: auto;
    }
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="tools-panel"):
            yield Static("[bold]Available Tools[/bold]\n")
            for td in claude_cli.TOOL_DEFINITIONS:
                name = td["name"]
                desc = td["description"].split(".")[0]
                yield Static(f"  [bold cyan]{name}[/bold cyan]\n  [dim]{desc}[/dim]\n")
            yield Static("\n[dim]Press Escape to close[/dim]")

    def action_cancel(self) -> None:
        self.dismiss(None)


# ---------------------------------------------------------------------------
# Main application
# ---------------------------------------------------------------------------
class BrainApp(App):
    """Brain Agent TUI application."""

    CSS = """
    Screen {
        background: $background;
    }

    #chat-log {
        height: 1fr;
        padding: 0 0;
        scrollbar-size: 1 1;
    }

    #input-area {
        dock: bottom;
        height: auto;
        max-height: 3;
    }

    StatusBar {
        dock: bottom;
        height: 1;
        background: #1e1e2e;
        color: #cdd6f4;
        padding: 0 2;
    }

    ChatInput {
        dock: bottom;
        border: tall $primary;
    }
    """

    BINDINGS = [
        Binding("ctrl+c", "quit", "Quit", show=True),
    ]

    def __init__(
        self,
        model: str,
        api_key: str,
        base_url: str,
        api_type: str,
        agent_id: str = "main",
        max_context: int = claude_cli.DEFAULT_MAX_CONTEXT_TOKENS,
    ) -> None:
        super().__init__()
        self._model = model
        self._api_key = api_key
        self._base_url = base_url
        self._api_type = api_type
        self._agent_id = agent_id
        self._max_context = max_context
        self._history: list[dict] = []  # conversation messages
        self._show_tools = True

    def compose(self) -> ComposeResult:
        yield StatusBar(id="status-bar")
        yield VerticalScroll(id="chat-log")
        yield ChatInput()

    def on_mount(self) -> None:
        """Initialize the backend systems and show the welcome screen."""
        # Initialize backend globals for delegation
        claude_cli._delegate_api_key = self._api_key
        claude_cli._delegate_base_url = self._base_url
        claude_cli._delegate_api_type = self._api_type
        claude_cli._delegate_fallback_model = self._model

        # Initialize agent
        self._switch_agent(self._agent_id)

        # Initialize scheduler
        claude_cli._scheduler = claude_cli.Scheduler()
        claude_cli._scheduler.start()

        # Initialize task runner
        claude_cli._task_runner = claude_cli.TaskRunner()

        # Show welcome panel
        chat_log = self.query_one("#chat-log", VerticalScroll)
        chat_log.mount(WelcomePanel(self._model, self._agent_id))

        # Update status bar
        self._update_status_bar()

        # Focus the input
        self.query_one(ChatInput).focus()

    def _switch_agent(self, agent_id: str) -> None:
        """Switch to a different agent, updating backend globals."""
        claude_cli._current_agent = claude_cli.AgentConfig(agent_id)
        claude_cli._memory_store = claude_cli.MemoryStore(
            agent_id=agent_id,
            base_dir=claude_cli._current_agent.memory_dir,
        )
        if claude_cli._current_agent.preferred_model:
            self._model = claude_cli._current_agent.preferred_model
        self._agent_id = agent_id

    def _update_status_bar(self) -> None:
        """Refresh the status bar values."""
        sb = self.query_one("#status-bar", StatusBar)
        sb.agent_id = self._agent_id
        sb.model = self._model
        sb.token_count = claude_cli._estimate_conversation_tokens(
            self._history, ""
        )
        sb.max_tokens = self._max_context

    def _append_to_log(self, widget: Widget) -> None:
        """Mount a widget into the chat log and scroll to it."""
        chat_log = self.query_one("#chat-log", VerticalScroll)
        chat_log.mount(widget)
        chat_log.scroll_end(animate=False)

    # ----- Input handling -----

    @on(Input.Submitted)
    def _on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle user pressing Enter in the input."""
        text = event.value.strip()
        if not text:
            return

        input_widget = self.query_one(ChatInput)
        input_widget.add_to_history(text)
        input_widget.value = ""

        stripped = text.lower()

        # ----- Slash commands -----
        if stripped in ("exit", "quit"):
            self.exit()
            return

        if stripped == "/help":
            self.push_screen(HelpScreen())
            return

        if stripped == "/new":
            self._history = []
            self._append_to_log(SystemMessage("[dim]--- New conversation ---[/dim]"))
            self._update_status_bar()
            return

        if stripped == "/tools":
            self.push_screen(ToolsScreen())
            return

        if stripped.startswith("/agent"):
            arg = text[6:].strip()
            agents = claude_cli.list_agents()
            if arg:
                self._switch_agent(arg)
                self._history = []
                self._append_to_log(
                    SystemMessage(f"[dim]Switched to agent:[/dim] [bold]{arg}[/bold] [dim](model: {self._model})[/dim]")
                )
                self._update_status_bar()
            else:
                current = self._agent_id

                def _on_agent_selected(result: str | None) -> None:
                    if result:
                        self._switch_agent(result)
                        self._history = []
                        self._append_to_log(
                            SystemMessage(f"[dim]Switched to agent:[/dim] [bold]{result}[/bold] [dim](model: {self._model})[/dim]")
                        )
                        self._update_status_bar()

                self.push_screen(AgentSelectScreen(agents, current), _on_agent_selected)
            return

        if stripped == "/models":
            self._show_model_select()
            return

        if stripped.startswith("/model"):
            arg = text[6:].strip()
            if arg:
                self._model = arg
                self._append_to_log(
                    SystemMessage(f"[dim]Switched to:[/dim] [bold]{arg}[/bold]")
                )
                self._update_status_bar()
            else:
                self._show_model_select()
            return

        if stripped.startswith("/schedule"):
            self.push_screen(ScheduleScreen())
            return

        # ----- Normal message -----
        self._append_to_log(UserMessage(text))
        self._history.append({"role": "user", "content": text})
        self._update_status_bar()

        # Disable input while processing
        input_widget.disabled = True

        # Run the LLM call in a worker thread
        self._send_message_worker(text)

    def _show_model_select(self) -> None:
        """Fetch models and show the selection screen."""
        models = claude_cli.get_available_models(self._api_key, self._base_url, self._api_type)
        if models:
            def _on_model_selected(result: str | None) -> None:
                if result:
                    self._model = result
                    self._append_to_log(
                        SystemMessage(f"[dim]Switched to:[/dim] [bold]{result}[/bold]")
                    )
                    self._update_status_bar()

            self.push_screen(ModelSelectScreen(models, self._model), _on_model_selected)
        else:
            self._append_to_log(SystemMessage("[dim]No models available[/dim]"))

    # ----- Worker for sending messages -----

    @work(thread=True, exclusive=True, group="chat")
    def _send_message_worker(self, user_text: str) -> None:
        """Send the message using the backend, in a worker thread.

        We use silent=True so the backend collects tool-use results internally
        without printing to stdout. After completion we display everything.
        """
        start_time = time.time()

        # Track tool calls by temporarily monkey-patching display functions
        tool_events: list[tuple[str, str, dict | str]] = []  # (type, name, data)
        original_display_call = claude_cli._display_tool_call
        original_display_result = claude_cli._display_tool_result

        def _capture_tool_call(name: str, args: dict) -> None:
            tool_events.append(("call", name, args))

        def _capture_tool_result(name: str, result_str: str) -> None:
            tool_events.append(("result", name, result_str))

        claude_cli._display_tool_call = _capture_tool_call
        claude_cli._display_tool_result = _capture_tool_result

        reply = None
        error = None
        try:
            # Check context window and compact if needed
            estimated = claude_cli._estimate_conversation_tokens(self._history)
            threshold = int(self._max_context * claude_cli.COMPACT_THRESHOLD)
            if estimated >= threshold and len(self._history) > claude_cli.KEEP_RECENT_MESSAGES:
                self._history = claude_cli._compact_conversation(
                    self._history, self._model, self._api_key, self._base_url,
                    self._api_type, self._max_context,
                )
                self.call_from_thread(
                    self._append_to_log,
                    SystemMessage("[dim]Context compacted to fit window[/dim]"),
                )

            reply = claude_cli.send_message_with_fallback(
                self._history,
                self._model,
                self._api_key,
                self._base_url,
                self._api_type,
                silent=True,
                tools=True,
            )
        except claude_cli.TaskCancelled:
            error = "Cancelled"
            # Remove the user message on cancel
            if self._history and self._history[-1].get("role") == "user":
                self._history.pop()
        except Exception as e:
            error = str(e)
        finally:
            claude_cli._display_tool_call = original_display_call
            claude_cli._display_tool_result = original_display_result

        elapsed = time.time() - start_time

        # Post results back to the UI thread
        self.call_from_thread(self._on_response_complete, reply, error, elapsed, tool_events)

    def _on_response_complete(
        self,
        reply: str | None,
        error: str | None,
        elapsed: float,
        tool_events: list[tuple[str, str, dict | str]],
    ) -> None:
        """Called on the main thread after the worker finishes."""
        # Re-enable input
        input_widget = self.query_one(ChatInput)
        input_widget.disabled = False
        input_widget.focus()

        # Show tool calls/results if any
        if self._show_tools:
            for event_type, name, data in tool_events:
                if event_type == "call":
                    self._append_to_log(ToolCallMessage(name, data))
                elif event_type == "result":
                    self._append_to_log(ToolResultMessage(name, data))

        if error:
            self._append_to_log(
                SystemMessage(f"[red]{error}[/red]")
            )
        elif reply:
            # The send_message_with_fallback already appends to self._history
            # (the assistant message is added inside _handle_*_response)
            # But we need to ensure the final text reply is in history
            # Check if the last history item is an assistant message
            if not self._history or self._history[-1].get("role") != "assistant":
                self._history.append({"role": "assistant", "content": reply})

            # Clean ANSI codes from reply and render as markdown
            clean_reply = _strip_ansi(reply)
            self._append_to_log(AssistantMessage(clean_reply))

            self._append_to_log(
                SystemMessage(f"[dim]{elapsed:.1f}s[/dim]")
            )
        else:
            self._append_to_log(SystemMessage("[dim](no response)[/dim]"))

        self._update_status_bar()

        # Scroll to bottom
        chat_log = self.query_one("#chat-log", VerticalScroll)
        chat_log.scroll_end(animate=False)

    def on_unmount(self) -> None:
        """Clean up backend resources."""
        if claude_cli._scheduler:
            claude_cli._scheduler.stop()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description=f"Brain Agent TUI v{VERSION}"
    )
    parser.add_argument(
        "message", nargs="?",
        help="Initial message (optional, starts interactive mode regardless)",
    )
    parser.add_argument(
        "-m", "--model", default="claude-opus-4-5-20251101",
        help="Model to use (default: claude-opus-4-5-20251101)",
    )
    parser.add_argument(
        "-i", "--interactive", action="store_true",
        help="Interactive mode (always on for TUI, kept for CLI compat)",
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
        "-t", "--api-type", choices=["anthropic", "openai"], default="anthropic",
        help="API type: anthropic or openai (default: anthropic)",
    )
    parser.add_argument(
        "--max-context", type=int, default=claude_cli.DEFAULT_MAX_CONTEXT_TOKENS,
        help=f"Max context window in tokens (default: {claude_cli.DEFAULT_MAX_CONTEXT_TOKENS})",
    )
    parser.add_argument(
        "--agent", default="main",
        help="Agent ID to start with (default: 'main')",
    )

    args = parser.parse_args()

    if args.list_models:
        claude_cli.list_models(args.api_key, args.base_url, args.api_type)
        sys.exit(0)

    app = BrainApp(
        model=args.model,
        api_key=args.api_key,
        base_url=args.base_url,
        api_type=args.api_type,
        agent_id=args.agent,
        max_context=args.max_context,
    )
    app.run()


if __name__ == "__main__":
    main()
