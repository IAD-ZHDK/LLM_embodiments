from __future__ import annotations

import socket
import threading
from dataclasses import dataclass
from typing import Callable, Dict, Optional

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import DataTable, Footer, Header, Input, RichLog, Static


def _local_ip() -> str:
    # A UDP "connect" never sends a packet; it just asks the OS which local interface would route there.
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:
        try:
            return socket.gethostbyname(socket.gethostname())
        except Exception:
            return "unknown"


def _clip(text: str, limit: int = 44) -> str:
    text = (text or "").replace("\n", " ")
    return text if len(text) <= limit else text[: limit - 1] + "\u2026"


@dataclass
class SessionInfo:
    kind: str
    status: str = ""


_model_summary = "LLM: loading..."


class TerminalUI(App):
    """Two-pane terminal UI: a scrolling console log, and a sticky sidebar with a table of live
    sessions (frontend clients, Serial/WiFi devices) that grows as more connect - ready for
    multiple simultaneous device sessions in the future - plus a live status panel showing the
    current STT transcription, most recent prompt, and most recent LLM response."""

    CSS = """
    Horizontal { height: 1fr; }
    #console { width: 2fr; border: solid $accent; }
    #sidebar { width: 1fr; }
    #banner { height: 4; content-align: center middle; background: $accent; }
    #sessions { height: 1fr; border: solid $accent; }
    #tools { height: 8; border: solid $accent; padding: 1; overflow-y: auto; }
    #now-playing { height: auto; border: solid $accent; padding: 1; }
    #prompt-input { height: 3; }
    """
    BINDINGS = [("q", "quit_app", "Quit"), ("ctrl+c", "quit_app", "Quit")]

    def __init__(self, host: str, port: int, on_quit=None, on_prompt: Optional[Callable[[str], None]] = None) -> None:
        super().__init__()
        self.host = host
        self.port = port
        self._on_quit = on_quit
        self._on_prompt = on_prompt
        self.model_summary = _model_summary
        self.sessions: Dict[str, SessionInfo] = {}
        self.tools: list[Dict[str, str]] = []
        self.stt_text = ""
        self.last_prompt = ""
        self.last_response = ""
        self._main_thread_id = threading.get_ident()
        self._mounted = False
        self._pending_lines: list = []

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal():
            yield RichLog(id="console", highlight=True, markup=False, wrap=True, max_lines=5000)
            with Vertical(id="sidebar"):
                yield Static("", id="banner")
                yield DataTable(id="sessions")
                yield Static("Available tools\nWaiting for a device...", id="tools")
                yield Static("", id="now-playing")
        yield Input(placeholder="Type a prompt and press Enter", id="prompt-input")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#sessions", DataTable)
        table.add_columns("Session", "Status")
        table.cursor_type = "row"
        self._mounted = True
        for line in self._pending_lines:
            self._write_line(line)
        self._pending_lines.clear()
        self._refresh_table()
        self._refresh_banner()
        self._refresh_tools()
        self._refresh_now_playing()
        self.query_one("#prompt-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "prompt-input":
            return
        prompt = event.value.strip()
        if not prompt:
            return
        event.input.value = ""
        if self._on_prompt:
            self._on_prompt(prompt)

    def update_model_summary(self, summary: str) -> None:
        if threading.get_ident() != self._main_thread_id:
            self.call_from_thread(self.update_model_summary, summary)
            return
        self.model_summary = summary
        self._refresh_banner()

    def _refresh_banner(self) -> None:
        try:
            widget = self.query_one("#banner", Static)
        except Exception:
            return
        widget.update(f"Backend listening on {self.host}:{self.port}\n{self.model_summary}")

    def action_quit_app(self) -> None:
        if self._on_quit:
            self._on_quit()
        self.exit()

    def log_line(self, text: str) -> None:
        # Print calls can come from background threads (e.g. STT/TTS subprocess readers).
        if threading.get_ident() == self._main_thread_id:
            self._write_line(text)
        else:
            self.call_from_thread(self._write_line, text)

    def _write_line(self, text: str) -> None:
        if not self._mounted:
            self._pending_lines.append(text)
            return
        try:
            self.query_one("#console", RichLog).write(text)
        except Exception:
            pass

    def update_session(
        self,
        session_id: str,
        kind: Optional[str] = None,
        status: Optional[str] = None,
    ) -> None:
        if threading.get_ident() != self._main_thread_id:
            self.call_from_thread(self.update_session, session_id, kind, status)
            return

        info = self.sessions.setdefault(session_id, SessionInfo(kind=kind or session_id))
        if kind is not None:
            info.kind = kind
        if status is not None:
            info.status = status
        self._refresh_table()

    def remove_session(self, session_id: str) -> None:
        if threading.get_ident() != self._main_thread_id:
            self.call_from_thread(self.remove_session, session_id)
            return
        self.sessions.pop(session_id, None)
        self._refresh_table()

    def _refresh_table(self) -> None:
        try:
            table = self.query_one("#sessions", DataTable)
        except Exception:
            return
        table.clear()
        for session_id, info in self.sessions.items():
            table.add_row(session_id, info.status)

    def update_tools(self, tools: list[Dict[str, str]]) -> None:
        if threading.get_ident() != self._main_thread_id:
            self.call_from_thread(self.update_tools, tools)
            return
        self.tools = tools
        self._refresh_tools()

    def _refresh_tools(self) -> None:
        try:
            widget = self.query_one("#tools", Static)
        except Exception:
            return

        if not self.tools:
            widget.update("Available tools\nWaiting for a device...")
            return

        lines = ["Available tools"]
        for tool in self.tools:
            name = str(tool.get("name", "unnamed"))
            data_type = str(tool.get("dataType", "string"))
            description = str(tool.get("description", "No description."))
            lines.append(f"{name}(value: {data_type})\n  {description}")
        widget.update("\n".join(lines))

    def update_stt(self, text: str) -> None:
        if threading.get_ident() != self._main_thread_id:
            self.call_from_thread(self.update_stt, text)
            return
        self.stt_text = text
        self._refresh_now_playing()

    def update_prompt(self, text: str) -> None:
        if threading.get_ident() != self._main_thread_id:
            self.call_from_thread(self.update_prompt, text)
            return
        self.last_prompt = text
        self._refresh_now_playing()

    def update_response(self, text: str) -> None:
        if threading.get_ident() != self._main_thread_id:
            self.call_from_thread(self.update_response, text)
            return
        self.last_response = text
        self._refresh_now_playing()

    def _refresh_now_playing(self) -> None:
        try:
            widget = self.query_one("#now-playing", Static)
        except Exception:
            return
        widget.update(
            f"\U0001F3A4 STT: {self.stt_text or '(listening...)'}\n\n"
            f"\U0001F4AC Prompt: {self.last_prompt or '(none yet)'}\n\n"
            f"\U0001F916 Response: {self.last_response or '(none yet)'}"
        )


_app: Optional[TerminalUI] = None
_original_print = None


def get_app() -> Optional[TerminalUI]:
    return _app


def set_model_summary(summary: str) -> None:
    global _model_summary
    _model_summary = summary
    if _app is not None:
        _app.update_model_summary(summary)


def update_session(session_id: str, **kwargs) -> None:
    if _app is not None:
        _app.update_session(session_id, **kwargs)


def remove_session(session_id: str) -> None:
    if _app is not None:
        _app.remove_session(session_id)


def update_tools(tools: list[Dict[str, str]]) -> None:
    if _app is not None:
        _app.update_tools(tools)


def update_stt(text: str) -> None:
    if _app is not None:
        _app.update_stt(text)


def update_prompt(text: str) -> None:
    if _app is not None:
        _app.update_prompt(text)


def update_response(text: str) -> None:
    if _app is not None:
        _app.update_response(text)


def _tui_print(*args, **kwargs) -> None:
    # Textual's own renderer writes raw escape codes straight to sys.stdout to paint the screen,
    # so we must not touch that stream - intercepting print() itself avoids feeding the UI's own
    # screen-painting bytes back into itself as "log lines".
    if _app is not None:
        sep = kwargs.get("sep", " ")
        text = sep.join(str(a) for a in args)
        _app.log_line(text)
    else:
        _original_print(*args, **kwargs)


async def start(host: Optional[str] = None, port: int = 0, on_quit=None, on_prompt: Optional[Callable[[str], None]] = None) -> None:
    """Run the Textual UI on the current asyncio loop; print() is rerouted into its log pane."""
    global _app, _original_print
    import builtins

    _app = TerminalUI(host or _local_ip(), port, on_quit=on_quit, on_prompt=on_prompt)
    _original_print = builtins.print
    builtins.print = _tui_print
    try:
        await _app.run_async()
    finally:
        builtins.print = _original_print
        _app = None
