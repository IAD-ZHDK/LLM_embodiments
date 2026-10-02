from __future__ import annotations

import base64
from io import BytesIO
import re
import shutil
import socket
import subprocess
import threading
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from rich.cells import cell_len
from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.content import Content
from textual.selection import Selection
from textual.strip import Strip
from textual.widgets import Checkbox, DataTable, Footer, Header, Input, RichLog, Select, Static, TabbedContent, TabPane
from PIL import Image as PillowImage
from textual_image.widget import Image as TextualImage


class SelectableRichLog(RichLog):
    """RichLog that supports mouse text selection and copying.

    Upstream RichLog keeps only pre-rendered strips, so it never records the per-cell offsets
    Textual's selection machinery looks for and never draws a selection highlight; both are
    reconstructed here from the rendered strips.
    """

    def _render_line(self, y: int, scroll_x: int, width: int) -> Strip:
        strip = super()._render_line(y, scroll_x, width)
        selection = self.text_selection
        if selection is not None and y < len(self.lines):
            span = selection.get_span(y)
            if span is not None:
                line_text = self.lines[y].text
                start, end = span
                if end == -1:
                    end = len(line_text)
                # Selection offsets are character indices, but Strip cuts at cell columns.
                cell_length = strip.cell_length
                start_cell = min(max(cell_len(line_text[:start]) - scroll_x, 0), cell_length)
                end_cell = min(max(cell_len(line_text[:end]) - scroll_x, 0), cell_length)
                if end_cell > start_cell:
                    style = self.screen.get_component_styles("screen--selection").rich_style
                    before, selected, after = strip.divide([start_cell, end_cell, cell_length])
                    strip = Strip.join([before, selected.apply_style(style), after])
        return strip.apply_offsets(scroll_x, y)

    def get_selection(self, selection: Selection) -> tuple[str, str] | None:
        return selection.extract("\n".join(strip.text for strip in self.lines)), "\n"

    def selection_updated(self, selection: Selection | None) -> None:
        self._line_cache.clear()
        self.refresh()


class DeviceCheckbox(Checkbox):
    @property
    def _button(self) -> Content:
        mark = "X" if self.value else " "
        indicator = Content.from_text(f"[{mark}]", markup=False)
        return indicator.stylize_before(self.get_visual_style("toggle--button"))


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
    """Terminal UI: a global scrolling console on the left, and a tabbed panel on the right - a
    "Main" tab with an overview table of every connected device, plus one dynamically added tab
    per connected device showing its own tools, live STT/prompt/response, and a short log."""

    CSS = """
    Horizontal { height: 1fr; }
    #tabs { height: 1fr; }
    TabPane { height: 1fr; }
        .overview-content { height: 1fr; }
    #console-pane { width: 1fr; height: 1fr; }
    #console { width: 1fr; height: 1fr; border: solid $accent; }
    #overview-right { width: 1fr; height: 1fr; }
    #overview-scroll { height: 1fr; }
    .device-content { width: 1fr; height: 1fr; }
    .device-sidebar { width: 2fr; min-width: 28; height: 1fr; overflow-x: hidden; overflow-y: scroll; }
    .device-log-column { width: 3fr; min-width: 32; height: 1fr; }
    #toolbar { height: 4; }
    #banner { width: 1fr; height: 4; content-align: center middle; background: $accent; }
    #model-controls { height: auto; }
    .model-control { height: 3; }
    .model-label { width: 12; height: 3; content-align: left middle; }
    .model-select { width: 1fr; }
    .audio-controls { height: 3; }
    .audio-controls-row { height: 1; }
    .pane-heading { height: 1; text-style: bold; color: $accent; }
    .device-name { height: 1; text-style: bold; }
    .audio-controls-row Checkbox {
        width: 1fr;
        height: 1;
        border: none;
        padding: 0;
        background: transparent;
    }
    #main-sessions { height: auto; min-height: 8; border: solid $accent; }
    #prompt-input { height: 3; }
    .device-status { height: 3; border: solid $accent; padding: 0 1; }
    .device-tools { height: auto; min-height: 5; border: solid $accent; padding: 1; }
    .device-now-playing { height: auto; border: solid $accent; padding: 1; }
    .camera-preview { width: 1fr; height: 18; min-height: 8; }
    .device-log { width: 1fr; height: 1fr; min-height: 8; border: solid $accent; }
    """
    BINDINGS = [
        ("q", "quit_app", "Quit"),
        ("ctrl+c", "quit_app", "Copy selection / Quit"),
    ]

    def __init__(
        self,
        host: str,
        port: int,
        on_quit=None,
        on_prompt: Optional[Callable[[Optional[str], str], None]] = None,
        on_toggle_tts: Optional[Callable[[str, bool], None]] = None,
        on_toggle_audio_route: Optional[Callable[[str, bool, bool], None]] = None,
        on_toggle_mic: Optional[Callable[[str, bool], None]] = None,
        model_name: str = "",
        stt_model: str = "",
        tts_model: str = "",
        stt_models: Optional[List[str]] = None,
        tts_models: Optional[List[str]] = None,
        on_list_models: Optional[Callable[[], List[str]]] = None,
        on_list_stt_models: Optional[Callable[[], List[str]]] = None,
        on_list_tts_models: Optional[Callable[[], List[str]]] = None,
        on_change_model: Optional[Callable[[str], Optional[str]]] = None,
        on_change_stt_model: Optional[Callable[[str], Optional[str]]] = None,
        on_change_tts_model: Optional[Callable[[str], Optional[str]]] = None,
        on_add_tts_model: Optional[Callable[[str], str]] = None,
    ) -> None:
        super().__init__()
        self.host = host
        self.port = port
        self._on_quit = on_quit
        self._on_prompt = on_prompt
        self._on_toggle_tts = on_toggle_tts
        self._on_toggle_audio_route = on_toggle_audio_route
        self._on_toggle_mic = on_toggle_mic
        self.model_name = model_name
        self.stt_model = stt_model
        self.tts_model = tts_model
        self.stt_models = stt_models or []
        self.tts_models = tts_models or []
        self._on_list_models = on_list_models
        self._on_list_stt_models = on_list_stt_models
        self._on_list_tts_models = on_list_tts_models
        self._on_change_model = on_change_model
        self._on_change_stt_model = on_change_stt_model
        self._on_change_tts_model = on_change_tts_model
        self._on_add_tts_model = on_add_tts_model
        self._updating_model_options = False
        self._mic_muted: Dict[str, bool] = {}
        self._speaker_muted: Dict[str, bool] = {}
        self._audio_input_local: Dict[str, bool] = {}
        self._audio_output_local: Dict[str, bool] = {}
        self.model_summary = _model_summary
        self.sessions: Dict[str, SessionInfo] = {}
        self.device_tools: Dict[str, list] = {}
        self.device_stt: Dict[str, str] = {}
        self.device_prompt: Dict[str, str] = {}
        self.device_response: Dict[str, str] = {}
        self._thinking_seen: Dict[str, str] = {}
        self._thinking_buffer: Dict[str, str] = {}
        self._device_order: list[str] = []
        self._main_thread_id = threading.get_ident()
        self._mounted = False
        self._pending_lines: list = []

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with TabbedContent(id="tabs"):
            with TabPane("Overview", id="tab-main"):
                with Horizontal(classes="overview-content"):
                    with Vertical(id="console-pane"):
                        yield Static("Global Console", classes="pane-heading")
                        yield SelectableRichLog(id="console", highlight=True, markup=False, wrap=True, min_width=0, max_lines=5000)
                    with Vertical(id="overview-right"):
                        with Horizontal(id="toolbar"):
                            yield Static("", id="banner")
                        with Vertical(id="model-controls"):
                            for label, model, options, control_id in (
                                ("LLM model", self.model_name, [self.model_name] if self.model_name else [], "model-select"),
                                ("STT model", self.stt_model, self.stt_models, "stt-model-select"),
                                ("TTS model", self.tts_model, self.tts_models, "tts-model-select"),
                            ):
                                choices = list(dict.fromkeys(([model] if model else []) + options))
                                with Horizontal(classes="model-control"):
                                    yield Static(label, classes="model-label pane-heading")
                                    yield Select(
                                        [(choice, choice) for choice in choices],
                                        value=model or Select.NULL,
                                        allow_blank=not model,
                                        id=control_id,
                                        classes="model-select",
                                    )
                            with Horizontal(classes="model-control"):
                                yield Static("Add TTS voice", classes="model-label pane-heading")
                                yield Input(
                                    placeholder="Piper voice, e.g. en_GB-alan-low - Enter to download",
                                    id="tts-model-add",
                                    classes="model-select",
                                )
                        with VerticalScroll(id="overview-scroll"):
                            yield Static("Connected devices", classes="pane-heading")
                            yield DataTable(id="main-sessions")
        yield Input(placeholder="Type a prompt and press Enter (targets the active device tab)", id="prompt-input")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#main-sessions", DataTable)
        table.add_columns("Session", "Kind", "Status")
        table.cursor_type = "row"
        self._mounted = True
        for line in self._pending_lines:
            self._write_line(line)
        self._pending_lines.clear()
        self._refresh_table()
        self._refresh_banner()
        self.query_one("#prompt-input", Input).focus()
        if any((self._on_list_models, self._on_list_stt_models, self._on_list_tts_models)):
            threading.Thread(target=self._load_model_options, daemon=True).start()

    def _load_model_options(self) -> None:
        callbacks = (
            (self._on_list_models, self.model_name, "Ollama"),
            (self._on_list_stt_models, self.stt_model, "STT"),
            (self._on_list_tts_models, self.tts_model, "TTS"),
        )
        option_lists = []
        for callback, current, label in callbacks:
            try:
                models = callback() if callback else []
            except Exception as exc:
                print(f"⚠️ Could not load {label} models: {exc}")
                models = []
            if current and current not in models:
                models.insert(0, current)
            option_lists.append(models)
        try:
            self.call_from_thread(self._set_model_options, *option_lists)
        except RuntimeError:
            pass

    def _set_model_options(
        self,
        models: List[str],
        stt_models: List[str],
        tts_models: List[str],
    ) -> None:
        if threading.get_ident() != self._main_thread_id:
            self.call_from_thread(self._set_model_options, models, stt_models, tts_models)
            return
        self._updating_model_options = True
        try:
            for selector_id, current, model_list in (
                ("model-select", self.model_name, models),
                ("stt-model-select", self.stt_model, stt_models),
                ("tts-model-select", self.tts_model, tts_models),
            ):
                try:
                    selector = self.query_one(f"#{selector_id}", Select)
                except Exception:
                    continue
                clean_models = list(dict.fromkeys(model.strip() for model in model_list if model.strip()))
                if current and current not in clean_models:
                    clean_models.insert(0, current)
                if not clean_models:
                    continue
                selector.set_options((model, model) for model in clean_models)
                if current in clean_models:
                    selector.value = current
        finally:
            self._updating_model_options = False

    def on_select_changed(self, event: Select.Changed) -> None:
        if self._updating_model_options:
            return
        model = str(event.value)
        control = {
            "model-select": ("model_name", self._on_change_model),
            "stt-model-select": ("stt_model", self._on_change_stt_model),
            "tts-model-select": ("tts_model", self._on_change_tts_model),
        }.get(event.select.id or "")
        if not control:
            return
        attribute, callback = control
        previous_model = getattr(self, attribute)
        if not model or model == previous_model or not callback:
            return
        try:
            summary = callback(model)
        except Exception as exc:
            print(f"⚠️ Could not change {attribute.replace('_', ' ')}: {exc}")
            self._updating_model_options = True
            event.select.value = previous_model or Select.NULL
            self._updating_model_options = False
            return
        setattr(self, attribute, model)
        if summary:
            self.update_model_summary(summary)
        self.log_line(f"{attribute.replace('_', ' ').upper()} changed to {model}")

    def _tab_id(self, session_id: str) -> str:
        return f"tab-{session_id}"

    def _active_device_session_id(self) -> Optional[str]:
        try:
            tabs = self.query_one("#tabs", TabbedContent)
        except Exception:
            return None
        active = tabs.active
        if not active or active == "tab-main" or not active.startswith("tab-"):
            return None
        return active[len("tab-"):]

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "tts-model-add":
            voice = event.value.strip()
            if not voice:
                return
            event.input.value = ""
            self._start_tts_download(voice)
            return
        if event.input.id != "prompt-input":
            return
        prompt = event.value.strip()
        if not prompt:
            return
        event.input.value = ""
        if self._on_prompt:
            self._on_prompt(self._active_device_session_id(), prompt)

    def _start_tts_download(self, voice: str) -> None:
        if not self._on_add_tts_model:
            return
        try:
            self.query_one("#tts-model-add", Input).disabled = True
        except Exception:
            pass
        self.log_line(f"⬇️ Downloading Piper voice '{voice}'...")
        threading.Thread(target=self._download_tts_model, args=(voice,), daemon=True).start()

    def _download_tts_model(self, voice: str) -> None:
        new_model: Optional[str] = None
        error: Optional[str] = None
        try:
            new_model = self._on_add_tts_model(voice)
        except Exception as exc:
            error = str(exc)
        try:
            self.call_from_thread(self._finish_tts_download, voice, new_model, error)
        except RuntimeError:
            pass

    def _finish_tts_download(self, voice: str, new_model: Optional[str], error: Optional[str]) -> None:
        try:
            self.query_one("#tts-model-add", Input).disabled = False
        except Exception:
            pass
        if error:
            self.log_line(f"⚠️ Could not add Piper voice '{voice}': {error}")
            return
        if not new_model:
            return
        self.log_line(f"✅ Added and selected TTS voice '{new_model}'")
        self.tts_models = list(dict.fromkeys([new_model] + self.tts_models))
        self.tts_model = new_model
        self._updating_model_options = True
        try:
            selector = self.query_one("#tts-model-select", Select)
            selector.set_options((m, m) for m in self.tts_models)
            selector.value = new_model
        except Exception:
            pass
        finally:
            self._updating_model_options = False

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        checkbox_id = event.checkbox.id or ""
        selected = bool(event.value)
        if checkbox_id.startswith("audio-in-local-"):
            session_id = checkbox_id[len("audio-in-local-"):]
            self._audio_input_local[session_id] = selected
            self._emit_audio_route_change(session_id)
        elif checkbox_id.startswith("audio-out-local-"):
            session_id = checkbox_id[len("audio-out-local-"):]
            self._audio_output_local[session_id] = selected
            self._emit_audio_route_change(session_id)
        elif checkbox_id.startswith("mic-mute-"):
            session_id = checkbox_id[len("mic-mute-"):]
            if self._mic_muted.get(session_id, False) == selected:
                return
            self._mic_muted[session_id] = selected
            if self._on_toggle_mic:
                self._on_toggle_mic(session_id, selected)
        elif checkbox_id.startswith("speaker-mute-"):
            session_id = checkbox_id[len("speaker-mute-"):]
            if self._speaker_muted.get(session_id, False) == selected:
                return
            self._speaker_muted[session_id] = selected
            if self._on_toggle_tts:
                self._on_toggle_tts(session_id, selected)

    def update_audio_mutes(self, session_id: str, mic_muted: bool, speaker_muted: bool) -> None:
        if threading.get_ident() != self._main_thread_id:
            self.call_from_thread(self.update_audio_mutes, session_id, mic_muted, speaker_muted)
            return
        self._mic_muted[session_id] = mic_muted
        self._speaker_muted[session_id] = speaker_muted
        for control_id, value in (
            (f"mic-mute-{session_id}", mic_muted),
            (f"speaker-mute-{session_id}", speaker_muted),
        ):
            try:
                self.query_one(f"#{control_id}", Checkbox).value = value
            except Exception:
                pass

    def _emit_audio_route_change(self, session_id: str) -> None:
        if not self._on_toggle_audio_route:
            return
        in_local = self._audio_input_local.get(session_id, False)
        out_local = self._audio_output_local.get(session_id, True)
        self._on_toggle_audio_route(session_id, in_local, out_local)

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
        # Mirrors the usual terminal convention: Ctrl+C copies when something is selected.
        if self._copy_selection():
            return
        if self._on_quit:
            self._on_quit()
        self.exit()

    def _copy_selection(self) -> bool:
        """Copy any selected text to the clipboard; returns True if there was a selection."""
        try:
            selected = self.screen.get_selected_text()
        except Exception:
            return False
        if not selected:
            return False

        self.copy_to_clipboard(selected)
        # copy_to_clipboard uses OSC 52, which Terminal.app ignores, so also try pbcopy on macOS.
        pbcopy = shutil.which("pbcopy")
        if pbcopy:
            try:
                subprocess.run([pbcopy], input=selected.encode("utf-8"), check=False, timeout=2)
            except Exception:
                pass
        self.screen.clear_selection()
        self.notify(f"Copied {len(selected)} character(s) to the clipboard.", timeout=3)
        return True

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

    def log_device(self, session_id: str, text: str) -> None:
        if threading.get_ident() != self._main_thread_id:
            self.call_from_thread(self.log_device, session_id, text)
            return
        try:
            self.query_one(f"#log-{session_id}", RichLog).write(text)
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
        try:
            widget = self.query_one(f"#status-{session_id}", Static)
            widget.update(f"Kind: {info.kind} | Status: {info.status}")
        except Exception:
            pass

    def remove_session(self, session_id: str) -> None:
        if threading.get_ident() != self._main_thread_id:
            self.call_from_thread(self.remove_session, session_id)
            return
        self.sessions.pop(session_id, None)
        self._refresh_table()

    def _refresh_table(self) -> None:
        try:
            table = self.query_one("#main-sessions", DataTable)
        except Exception:
            return
        table.clear()
        for session_id, info in self.sessions.items():
            table.add_row(session_id, info.kind, info.status)

    def add_device_tab(self, session_id: str, kind: str = "Device") -> None:
        if threading.get_ident() != self._main_thread_id:
            self.call_from_thread(self.add_device_tab, session_id, kind)
            return
        if session_id in self._device_order:
            return
        try:
            tabs = self.query_one("#tabs", TabbedContent)
        except Exception:
            return
        default_in_local = kind != "WiFi"
        default_out_local = kind != "WiFi"
        pane = TabPane(
            f"Device {session_id}",
            Horizontal(
                VerticalScroll(
                    Static(f"Device {session_id}", id=f"name-{session_id}", classes="device-name"),
                    Static("Audio routing", classes="pane-heading"),
                    Horizontal(
                        DeviceCheckbox("Local audio in", value=default_in_local, id=f"audio-in-local-{session_id}"),
                        DeviceCheckbox("Mute mic", id=f"mic-mute-{session_id}"),
                        classes="audio-controls-row",
                    ),
                    Horizontal(
                        DeviceCheckbox("Local audio out", value=default_out_local, id=f"audio-out-local-{session_id}"),
                        DeviceCheckbox("Mute speaker", id=f"speaker-mute-{session_id}"),
                        classes="audio-controls-row",
                    ),
                    Static("Connection", classes="pane-heading"),
                    Static(f"Kind: {kind} | Status: connected", id=f"status-{session_id}", classes="device-status"),
                    Static("Tools", classes="pane-heading"),
                    Static("Available tools\n(none yet)", id=f"tools-{session_id}", classes="device-tools"),
                    Static("Now playing", classes="pane-heading"),
                    Static("", id=f"now-{session_id}", classes="device-now-playing"),
                    Static("Latest photo", classes="pane-heading"),
                    Static("No photo captured yet", id=f"camera-status-{session_id}", classes="device-now-playing"),
                    TextualImage(id=f"camera-image-{session_id}", classes="camera-preview"),
                    id=f"scroll-{session_id}",
                    classes="device-sidebar",
                ),
                Vertical(
                    Static("Device log", classes="pane-heading"),
                    SelectableRichLog(id=f"log-{session_id}", classes="device-log", max_lines=500, wrap=True, min_width=0, markup=False, highlight=True),
                    classes="device-log-column",
                ),
                classes="device-content",
            ),
            id=self._tab_id(session_id),
        )
        tabs.add_pane(pane)
        self._audio_input_local[session_id] = default_in_local
        self._audio_output_local[session_id] = default_out_local
        self._device_order.append(session_id)
        self._refresh_device_now_playing(session_id)
        self._refresh_device_tools(session_id)

    def update_device_name(self, session_id: str, name: str) -> None:
        if threading.get_ident() != self._main_thread_id:
            self.call_from_thread(self.update_device_name, session_id, name)
            return
        try:
            self.query_one(f"#name-{session_id}", Static).update(name)
        except Exception:
            pass
        try:
            tabs = self.query_one("#tabs", TabbedContent)
            tabs.get_tab(self._tab_id(session_id)).label = name
        except Exception:
            pass

    def remove_device_tab(self, session_id: str) -> None:
        if threading.get_ident() != self._main_thread_id:
            self.call_from_thread(self.remove_device_tab, session_id)
            return
        if session_id not in self._device_order:
            return
        try:
            tabs = self.query_one("#tabs", TabbedContent)
            tabs.remove_pane(self._tab_id(session_id))
        except Exception:
            pass
        self._device_order.remove(session_id)
        self.device_tools.pop(session_id, None)
        self.device_stt.pop(session_id, None)
        self.device_prompt.pop(session_id, None)
        self.device_response.pop(session_id, None)
        self._thinking_seen.pop(session_id, None)
        self._thinking_buffer.pop(session_id, None)
        self._mic_muted.pop(session_id, None)
        self._speaker_muted.pop(session_id, None)
        self._audio_input_local.pop(session_id, None)
        self._audio_output_local.pop(session_id, None)

    def clear_devices(self) -> None:
        if threading.get_ident() != self._main_thread_id:
            self.call_from_thread(self.clear_devices)
            return
        for session_id in list(self._device_order):
            self.remove_device_tab(session_id)

    def update_tools(self, session_id: str, tools: list) -> None:
        if threading.get_ident() != self._main_thread_id:
            self.call_from_thread(self.update_tools, session_id, tools)
            return
        self.device_tools[session_id] = tools
        self._refresh_device_tools(session_id)

    def _refresh_device_tools(self, session_id: str) -> None:
        try:
            widget = self.query_one(f"#tools-{session_id}", Static)
        except Exception:
            return

        tools = self.device_tools.get(session_id) or []
        if not tools:
            widget.update("Available tools\n(none yet)")
            return

        lines = ["Available tools"]
        for tool in tools:
            name = str(tool.get("name", "unnamed"))
            data_type = str(tool.get("dataType", "string"))
            description = str(tool.get("description", "No description."))
            lines.append(f"{name}(value: {data_type})\n  {description}")
        widget.update("\n".join(lines))

    def update_stt(self, session_id: str, text: str) -> None:
        if threading.get_ident() != self._main_thread_id:
            self.call_from_thread(self.update_stt, session_id, text)
            return
        self.device_stt[session_id] = text
        self._refresh_device_now_playing(session_id)

    def update_prompt(self, session_id: str, text: str) -> None:
        if threading.get_ident() != self._main_thread_id:
            self.call_from_thread(self.update_prompt, session_id, text)
            return
        self.device_prompt[session_id] = text
        self._refresh_device_now_playing(session_id)

    def update_response(self, session_id: str, text: str) -> None:
        if threading.get_ident() != self._main_thread_id:
            self.call_from_thread(self.update_response, session_id, text)
            return
        self.device_response[session_id] = text
        self._refresh_device_now_playing(session_id)

    def update_camera_image(self, session_id: str, image_base64: str) -> None:
        if threading.get_ident() != self._main_thread_id:
            self.call_from_thread(self.update_camera_image, session_id, image_base64)
            return

        try:
            image_bytes = base64.b64decode(image_base64, validate=True)
            with PillowImage.open(BytesIO(image_bytes)) as image_file:
                image = image_file.convert("RGB")
            self.query_one(f"#camera-image-{session_id}", TextualImage).image = image
            self.query_one(f"#camera-status-{session_id}", Static).update("Photo captured")
        except Exception as exc:
            try:
                self.query_one(f"#camera-status-{session_id}", Static).update(
                    f"Photo preview unavailable: {exc}"
                )
            except Exception:
                pass

    def update_thinking(self, session_id: str, text: str) -> None:
        """Append newly streamed reasoning to the device log, flushed at sentence boundaries.

        Reasoning arrives as the full text so far, so only the unseen tail is written.
        """
        if threading.get_ident() != self._main_thread_id:
            self.call_from_thread(self.update_thinking, session_id, text)
            return

        seen = self._thinking_seen.get(session_id, "")
        if not text.startswith(seen):
            seen = ""
        self._thinking_seen[session_id] = text

        buffer = self._thinking_buffer.get(session_id, "") + text[len(seen):]
        while True:
            match = re.search(r"[.!?\n]\s", buffer)
            if not match:
                break
            self._write_thinking(session_id, buffer[: match.end()])
            buffer = buffer[match.end():]
        self._thinking_buffer[session_id] = buffer

    def reset_thinking(self, session_id: str) -> None:
        if threading.get_ident() != self._main_thread_id:
            self.call_from_thread(self.reset_thinking, session_id)
            return
        self._write_thinking(session_id, self._thinking_buffer.get(session_id, ""))
        self._thinking_seen[session_id] = ""
        self._thinking_buffer[session_id] = ""

    def _write_thinking(self, session_id: str, text: str) -> None:
        text = text.strip()
        if not text:
            return
        try:
            log = self.query_one(f"#log-{session_id}", RichLog)
        except Exception:
            return
        log.write(Text(f"\U0001F916\U0001F4AD {text}", style="grey50 italic"))

    def _refresh_device_now_playing(self, session_id: str) -> None:
        try:
            widget = self.query_one(f"#now-{session_id}", Static)
        except Exception:
            return
        stt = self.device_stt.get(session_id, "")
        prompt = self.device_prompt.get(session_id, "")
        response = self.device_response.get(session_id, "")
        widget.update(
            f"\U0001F3A4 STT: {stt or '(listening...)'}\n\n"
            f"\U0001F4AC Prompt: {prompt or '(none yet)'}\n\n"
            f"\U0001F916 Response: {response or '(none yet)'}"
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


def update_audio_mutes(session_id: str, mic_muted: bool, speaker_muted: bool) -> None:
    if _app is not None:
        _app.update_audio_mutes(session_id, mic_muted, speaker_muted)


def remove_session(session_id: str) -> None:
    if _app is not None:
        _app.remove_session(session_id)


def add_device_tab(session_id: str, kind: str = "Device") -> None:
    if _app is not None:
        _app.add_device_tab(session_id, kind)


def update_device_name(session_id: str, name: str) -> None:
    if _app is not None:
        _app.update_device_name(session_id, name)


def remove_device_tab(session_id: str) -> None:
    if _app is not None:
        _app.remove_device_tab(session_id)


def clear_devices() -> None:
    if _app is not None:
        _app.clear_devices()


def log_device(session_id: str, text: str) -> None:
    if _app is not None:
        _app.log_device(session_id, text)


def update_tools(session_id: str, tools: list) -> None:
    if _app is not None:
        _app.update_tools(session_id, tools)


def update_stt(session_id: str, text: str) -> None:
    if _app is not None:
        _app.update_stt(session_id, text)


def update_prompt(session_id: str, text: str) -> None:
    if _app is not None:
        _app.update_prompt(session_id, text)


def update_response(session_id: str, text: str) -> None:
    if _app is not None:
        _app.update_response(session_id, text)


def update_camera_image(session_id: str, image_base64: str) -> None:
    if _app is not None:
        _app.update_camera_image(session_id, image_base64)


def update_thinking(session_id: str, text: str) -> None:
    if _app is not None:
        _app.update_thinking(session_id, text)


def reset_thinking(session_id: str) -> None:
    if _app is not None:
        _app.reset_thinking(session_id)


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


async def start(
    host: Optional[str] = None,
    port: int = 0,
    on_quit=None,
    on_prompt: Optional[Callable[[Optional[str], str], None]] = None,
    on_toggle_tts: Optional[Callable[[str, bool], None]] = None,
    on_toggle_audio_route: Optional[Callable[[str, bool, bool], None]] = None,
    on_toggle_mic: Optional[Callable[[str, bool], None]] = None,
    model_name: str = "",
    stt_model: str = "",
    tts_model: str = "",
    stt_models: Optional[List[str]] = None,
    tts_models: Optional[List[str]] = None,
    on_list_models: Optional[Callable[[], List[str]]] = None,
    on_change_model: Optional[Callable[[str], Optional[str]]] = None,
    on_change_stt_model: Optional[Callable[[str], Optional[str]]] = None,
    on_change_tts_model: Optional[Callable[[str], Optional[str]]] = None,
    get_model: Optional[Callable[[], str]] = None,
    get_stt_model: Optional[Callable[[], str]] = None,
    get_tts_model: Optional[Callable[[], str]] = None,
    on_list_stt_models: Optional[Callable[[], List[str]]] = None,
    on_list_tts_models: Optional[Callable[[], List[str]]] = None,
    on_add_tts_model: Optional[Callable[[str], str]] = None,
) -> None:
    """Run the Textual UI on the current asyncio loop; print() is rerouted into its log pane."""
    global _app, _original_print
    import builtins

    if get_model:
        model_name = get_model()
    if get_stt_model:
        stt_model = get_stt_model()
    if get_tts_model:
        tts_model = get_tts_model()
    _app = TerminalUI(
        host or _local_ip(),
        port,
        on_quit=on_quit,
        on_prompt=on_prompt,
        on_toggle_tts=on_toggle_tts,
        on_toggle_audio_route=on_toggle_audio_route,
        on_toggle_mic=on_toggle_mic,
        model_name=model_name,
        stt_model=stt_model,
        tts_model=tts_model,
        stt_models=stt_models,
        tts_models=tts_models,
        on_list_models=on_list_models,
        on_list_stt_models=on_list_stt_models,
        on_list_tts_models=on_list_tts_models,
        on_change_model=on_change_model,
        on_change_stt_model=on_change_stt_model,
        on_change_tts_model=on_change_tts_model,
        on_add_tts_model=on_add_tts_model,
    )
    _original_print = builtins.print
    builtins.print = _tui_print
    try:
        await _app.run_async()
    finally:
        builtins.print = _original_print
        _app = None
