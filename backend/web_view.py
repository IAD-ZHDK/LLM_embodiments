"""Browser viewer that mirrors the terminal UI. State is fed from the tui module-level helpers and
pushed to browsers over a WebSocket; user actions are routed back to the same callbacks the TUI uses."""
from __future__ import annotations

import asyncio
import json
import re
import threading
import webbrowser
from collections import deque
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

_HTML_PATH = Path(__file__).with_name("web_view.html")
_CONSOLE_LINES = 2000
_DEVICE_LINES = 500

_lock = threading.RLock()
_loop: Optional[asyncio.AbstractEventLoop] = None
_clients: List[WebSocket] = []
_callbacks: Dict[str, Callable] = {}
_host = ""
_port = 0

_state: Dict[str, Any] = {
    "backend": "",
    "summary": "",
    "models": {
        "llm": {"current": "", "options": []},
        "stt": {"current": "", "options": []},
        "tts": {"current": "", "options": []},
    },
    "console": deque(maxlen=_CONSOLE_LINES),
    "sessions": {},
    "devices": {},
}
_thinking: Dict[str, Dict[str, str]] = {}


def configure(
    loop: asyncio.AbstractEventLoop,
    host: str,
    port: int,
    callbacks: Dict[str, Optional[Callable]],
    models: Dict[str, str],
) -> None:
    global _loop, _host, _port
    with _lock:
        _loop, _host, _port = loop, host, port
        _callbacks.update({k: v for k, v in callbacks.items() if v})
        _state["backend"] = f"{host}:{port}"
        for key, current in models.items():
            _state["models"][key]["current"] = current
            if current and current not in _state["models"][key]["options"]:
                _state["models"][key]["options"].insert(0, current)
    threading.Thread(target=_load_model_options, daemon=True).start()


def model_options(key: str) -> List[str]:
    with _lock:
        return list(_state["models"][key]["options"])


def url() -> str:
    return f"http://{_host or 'localhost'}:{_port}/viewer"


def open_window() -> None:
    # new=1 asks the browser for a new window; browsers may still open it as a tab.
    webbrowser.open(url(), new=1)


def _load_model_options() -> None:
    for key, cb_name in (("llm", "list_llm"), ("stt", "list_stt"), ("tts", "list_tts")):
        callback = _callbacks.get(cb_name)
        if not callback:
            continue
        try:
            options = list(callback())
        except Exception as exc:
            print(f"⚠️ Web viewer could not load {key.upper()} models: {exc}")
            continue
        with _lock:
            entry = _state["models"][key]
            if entry["current"] and entry["current"] not in options:
                options.insert(0, entry["current"])
            entry["options"] = options
        _emit({"type": "models", "models": _state["models"]})


def _new_device(kind: str) -> Dict[str, Any]:
    local_default = kind != "WiFi"
    return {
        "name": "",
        "kind": kind,
        "status": "connected",
        "tools": [],
        "stt": "",
        "prompt": "",
        "response": "",
        "mic_muted": False,
        "speaker_muted": False,
        "in_local": local_default,
        "out_local": local_default,
        "image": "",
        "log": deque(maxlen=_DEVICE_LINES),
    }


def _snapshot() -> Dict[str, Any]:
    with _lock:
        state = dict(_state)
        state["console"] = list(_state["console"])
        state["devices"] = {k: {**d, "log": list(d["log"])} for k, d in _state["devices"].items()}
        state["sessions"] = dict(_state["sessions"])
        return {"type": "snapshot", **state}


def _emit(message: Dict[str, Any]) -> None:
    loop = _loop
    if loop is None or not _clients:
        return
    payload = json.dumps(message)
    loop.call_soon_threadsafe(lambda: asyncio.ensure_future(_broadcast(payload)))


async def _broadcast(payload: str) -> None:
    for ws in list(_clients):
        try:
            await ws.send_text(payload)
        except Exception:
            if ws in _clients:
                _clients.remove(ws)


# ---- feed helpers (called from tui module-level functions, any thread) ----

def console(text: str) -> None:
    with _lock:
        _state["console"].append(text)
    _emit({"type": "console", "text": text})


def set_summary(summary: str) -> None:
    with _lock:
        _state["summary"] = summary
    _emit({"type": "summary", "summary": summary})


def set_model(key: str, model: str) -> None:
    with _lock:
        entry = _state["models"][key]
        entry["current"] = model
        if model not in entry["options"]:
            entry["options"].insert(0, model)
    _emit({"type": "models", "models": _state["models"]})


def update_session(session_id: str, kind: Optional[str], status: Optional[str]) -> None:
    with _lock:
        info = _state["sessions"].setdefault(session_id, {"kind": kind or session_id, "status": ""})
        if kind is not None:
            info["kind"] = kind
        if status is not None:
            info["status"] = status
        info = dict(info)
        device = _state["devices"].get(session_id)
        if device:
            device["kind"], device["status"] = info["kind"], info["status"]
    _emit({"type": "session", "id": session_id, **info})


def remove_session(session_id: str) -> None:
    with _lock:
        _state["sessions"].pop(session_id, None)
    _emit({"type": "session_removed", "id": session_id})


def add_device(session_id: str, kind: str) -> None:
    with _lock:
        if session_id in _state["devices"]:
            return
        _state["devices"][session_id] = device = _new_device(kind)
        data = {**device, "log": []}
    _emit({"type": "device_added", "id": session_id, "device": data})


def remove_device(session_id: str) -> None:
    with _lock:
        _state["devices"].pop(session_id, None)
        _thinking.pop(session_id, None)
    _emit({"type": "device_removed", "id": session_id})


def clear_devices() -> None:
    for session_id in list(_state["devices"]):
        remove_device(session_id)


def device_log(session_id: str, text: str) -> None:
    with _lock:
        device = _state["devices"].get(session_id)
        if device is None:
            return
        device["log"].append(text)
    _emit({"type": "device_log", "id": session_id, "text": text})


def set_field(session_id: str, field: str, value: Any) -> None:
    with _lock:
        device = _state["devices"].get(session_id)
        if device is None:
            return
        device[field] = value
    _emit({"type": "device_field", "id": session_id, "field": field, "value": value})


def set_mutes(session_id: str, mic_muted: bool, speaker_muted: bool) -> None:
    set_field(session_id, "mic_muted", mic_muted)
    set_field(session_id, "speaker_muted", speaker_muted)


def thinking(session_id: str, text: str) -> None:
    """Mirror of the TUI behaviour: reasoning arrives cumulatively, flush at sentence boundaries."""
    with _lock:
        track = _thinking.setdefault(session_id, {"seen": "", "buffer": ""})
        seen = track["seen"] if text.startswith(track["seen"]) else ""
        track["seen"] = text
        buffer = track["buffer"] + text[len(seen):]
        flushed = []
        while True:
            match = re.search(r"[.!?\n]\s", buffer)
            if not match:
                break
            flushed.append(buffer[: match.end()])
            buffer = buffer[match.end():]
        track["buffer"] = buffer
    for chunk in flushed:
        _write_thinking(session_id, chunk)


def reset_thinking(session_id: str) -> None:
    with _lock:
        track = _thinking.setdefault(session_id, {"seen": "", "buffer": ""})
        rest = track["buffer"]
        track["seen"] = track["buffer"] = ""
    _write_thinking(session_id, rest)


def _write_thinking(session_id: str, text: str) -> None:
    text = text.strip()
    if text:
        device_log(session_id, f"\U0001F916\U0001F4AD {text}")


# ---- actions from the browser ----

def _run_blocking(func: Callable, *args: Any) -> None:
    threading.Thread(target=func, args=args, daemon=True).start()


def _change_model(key: str, model: str) -> None:
    callback = _callbacks.get(f"change_{key}")
    previous = _state["models"][key]["current"]
    if not callback or not model or model == previous:
        return
    try:
        summary = callback(model)
    except Exception as exc:
        console(f"⚠️ Could not change {key.upper()} model: {exc}")
        _emit({"type": "models", "models": _state["models"]})
        return
    set_model(key, model)
    if summary and key == "llm":
        set_summary(summary)
    console(f"{key.upper()} MODEL changed to {model}")
    _notify_tui_model(key, model)


def _notify_tui_model(key: str, model: str) -> None:
    try:
        from . import tui
    except ImportError:
        import tui
    app = tui.get_app()
    if app is not None:
        app.sync_model_from_web(key, model)


def _add_voice(voice: str) -> None:
    callback = _callbacks.get("add_tts")
    if not callback:
        return
    console(f"⬇️ Downloading Piper voice '{voice}'...")
    try:
        new_model = callback(voice)
    except Exception as exc:
        console(f"⚠️ Could not add Piper voice '{voice}': {exc}")
        return
    if new_model:
        console(f"✅ Added and selected TTS voice '{new_model}'")
        set_model("tts", new_model)
        _notify_tui_model("tts", new_model)


def _handle_action(msg: Dict[str, Any]) -> None:
    action = msg.get("action")
    sid = str(msg.get("id", ""))
    if action == "prompt":
        text = str(msg.get("text", "")).strip()
        if text and "prompt" in _callbacks:
            _callbacks["prompt"](sid or None, text)
    elif action == "mic" and sid in _state["devices"]:
        muted = bool(msg.get("value"))
        set_field(sid, "mic_muted", muted)
        if "mic" in _callbacks:
            _callbacks["mic"](sid, muted)
    elif action == "speaker" and sid in _state["devices"]:
        muted = bool(msg.get("value"))
        set_field(sid, "speaker_muted", muted)
        if "speaker" in _callbacks:
            _callbacks["speaker"](sid, muted)
    elif action == "route" and sid in _state["devices"]:
        field = "in_local" if msg.get("direction") == "in" else "out_local"
        set_field(sid, field, bool(msg.get("value")))
        device = _state["devices"][sid]
        if "route" in _callbacks:
            _callbacks["route"](sid, device["in_local"], device["out_local"])
    elif action == "model" and msg.get("kind") in _state["models"]:
        _run_blocking(_change_model, msg["kind"], str(msg.get("value", "")).strip())
    elif action == "add_voice":
        voice = str(msg.get("value", "")).strip()
        if voice:
            _run_blocking(_add_voice, voice)


def mount(app: FastAPI) -> None:
    @app.get("/viewer", response_class=HTMLResponse)
    async def viewer_page() -> HTMLResponse:
        return HTMLResponse(_HTML_PATH.read_text(encoding="utf-8"))

    @app.websocket("/viewer/ws")
    async def viewer_ws(ws: WebSocket) -> None:
        await ws.accept()
        _clients.append(ws)
        try:
            await ws.send_text(json.dumps(_snapshot()))
            while True:
                try:
                    msg = json.loads(await ws.receive_text())
                except ValueError:
                    continue
                if isinstance(msg, dict):
                    try:
                        _handle_action(msg)
                    except Exception as exc:
                        console(f"⚠️ Web viewer action failed: {exc}")
        except WebSocketDisconnect:
            pass
        finally:
            if ws in _clients:
                _clients.remove(ws)
